"""Offline-first local store — the device's own copy of the world.

Today the app does synchronous HTTP on every read and every write. With no
signal a read falls back to `mock` (the user is shown *sample data* that looks
real) and a write returns False and is gone. This module is the fix: a small
SQLite database on the device becomes the thing the UI reads from and writes
to, and the network becomes a background reconciler that is allowed to be
absent.

Two tables carry it:

    cache   endpoint path -> the last JSON we had for it, and when.
            Reads answer from here instantly, online or not, and a refresh
            happens behind the UI rather than in front of it.

    outbox  writes the user has made that the server has not accepted yet.
            A write lands here FIRST and is applied to `cache` in the same
            transaction, so the screen updates with the radio off and the
            queued change survives the app being killed.

Nothing in here touches the network. `flush(send_fn)` is handed the transport
by its caller, which is why the whole thing is testable offline.


CONFLICTS — read this before changing anything
----------------------------------------------
The dashbored server owns FoodZen, journal, garage and trips, and it writes
*whole collections*: POST {kind, data} replaces the entire list. There is no
merge, no field-level patch, and no revision check anywhere in the stack
(see agedashii_tofu/api.py `_foodzen_apply`, which is already a read-modify-write).

So this is the situation that has to have an answer: the phone is in a tunnel,
you tick something off the grocery list, and meanwhile the website adds two
items to that same list. An hour later the phone gets wifi and flushes.

**The rule chosen here is last-flush-wins at whole-collection granularity, and
what is lost is every website edit made to that collection since the phone
last read it — all of them, not just the conflicting field.** Those two items
the website added are deleted. Not merged, not backed up, not recoverable from
the server, which keeps no history.

That is the honest cost, and it is accepted for three reasons:

  1. This is a single-human system. The realistic "concurrent writer" is the
     same person with a browser tab open, not a second user. The window needs
     her to edit the same collection in both places while the phone is
     offline, and the phone to flush second.
  2. The alternative that actually merges means teaching the server about
     revisions and per-item operations. That is a server change, and until it
     exists the app cannot merge no matter how clever it is here — the wire
     format is "here is the whole list".
  3. Silently *dropping* the phone's write, the other direction, is worse.
     The user watched it apply on screen.

Two things narrow the blast radius, both in this module:

  - Coalescing. Ten offline edits to one collection collapse into one queued
    write carrying the final state (`coalesce_key`), so the flush clobbers the
    server once with a current value rather than replaying a stale sequence.
  - Opt-in detection. Every queued write remembers `base_rev`, the hash of the
    server copy it was computed from. A `send_fn` that cares can GET the
    collection first, notice the server moved, and raise `Conflict` — the item
    is then parked with its payload intact for a human to decide, and nothing
    is overwritten. The store never discards a payload on its own.

Append-style endpoints (`/api/garage/records/add`, `/api/journal/new`) are the
one place where retry is not free: they are not idempotent, so a crash in the
gap between "the server accepted it" and "the row is deleted" duplicates the
record. The gap is one commit wide and the server assigns the ids, so this is
documented rather than defended against; whole-collection writes, which are
the common case, are idempotent by replacement and safe to retry forever.


POISON MESSAGES
---------------
An item that can never succeed must not hold the queue hostage, and an item
that fails only because there is no signal must not be punished for it. So
failures are split:

  - `Unreachable` (no network): the drain stops immediately and **no attempt
    is counted**. A week in a faraday cage costs the queue nothing. Anything
    else would park every pending write for being offline, which is precisely
    the bug this module exists to remove.
  - Any other failure: attempt count goes up, the error text is kept, and the
    item is retried later with exponential backoff (5s, 10s, 20s ... capped at
    5 minutes).
  - After `max_attempts` (default 5) the item is **parked**, not deleted. It
    stops being retried, stops blocking, stays visible in `pending()` and in
    `stats()`, and comes back with `retry_parked()`. Data is only ever removed
    from this database by a successful send or by an explicit `discard()`.

Head-of-line blocking is per path, not global: a failing or backing-off item
blocks later writes **to the same path** (sending #2 before #1 would resurrect
the state #1 replaced) and nothing else. Writes to other collections sail past
it. That is what stops one poisoned item freezing the whole app.


THREADING
---------
Flet runs handlers on more than one thread, so this is built for it:

  - One sqlite3 connection per thread (`threading.local`), all onto the same
    file, in WAL mode. WAL is the point: the UI thread can read while a
    background thread is mid-write, instead of blocking on it.
  - Writers are serialised by SQLite itself; `busy_timeout` makes a contended
    writer wait rather than raise "database is locked".
  - Every mutation is one `BEGIN IMMEDIATE` transaction, so the queued write
    and its local effect become visible together — the UI can never see a
    pending item whose change has not been applied, or the reverse.
  - `flush()` holds a process-wide lock, so two threads cannot drain the same
    queue and send anything twice.
  - `synchronous=FULL`, deliberately, against the usual advice for phones.
    NORMAL can lose the last commits to a power cut, and the last commit is
    exactly the write the user just made — the thing we promised to keep.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

SCHEMA_VERSION = 1

DEFAULT_TTL = 30.0          # seconds a cached read is considered fresh
DEFAULT_MAX_ATTEMPTS = 5    # failures before an item is parked as poison
BACKOFF_BASE = 5.0          # first retry delay, doubling thereafter
BACKOFF_CAP = 300.0

# Outbox lifecycle. 'pending' is the only state flush() will send; success
# deletes the row outright (the queue is a queue, not a log — once the server
# has it, `cache` is the record of what happened).
PENDING = "pending"
PARKED = "parked"       # gave up after max_attempts; kept for inspection
CONFLICT = "conflict"   # send_fn saw the server move; kept, never clobbered

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    path       TEXT PRIMARY KEY,
    body       TEXT NOT NULL,   -- canonical JSON, so `rev` is reproducible
    rev        TEXT NOT NULL,   -- sha256 of body; the base for conflict checks
    fetched_at REAL NOT NULL,
    source     TEXT NOT NULL    -- 'server' = came off the wire, 'local' = our own unsynced write
);

CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,  -- also the send order
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'pending',
    coalesce_key TEXT,
    base_rev     TEXT,
    next_try_at  REAL NOT NULL DEFAULT 0,
    -- {cache_path: rev} this write applied locally, so a successful send can
    -- relabel exactly those entries 'server' — and only if they have not been
    -- edited again since (the rev would no longer match).
    local_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS outbox_send_order ON outbox (state, id);

-- The invariant behind coalescing, enforced by the database rather than by
-- everyone remembering to check: at most one *pending* write per key.
CREATE UNIQUE INDEX IF NOT EXISTS outbox_one_pending_per_key
    ON outbox (coalesce_key) WHERE coalesce_key IS NOT NULL AND state = 'pending';
"""


class StoreError(Exception):
    """Base for this module."""


class Unreachable(StoreError):
    """No network. Raised by a `send_fn` to abort the drain without penalty.

    This is the difference between "we are offline" and "this write is bad",
    and getting it wrong is what turns a tunnel into a parked queue.
    """


class Conflict(StoreError):
    """The server's copy moved since this write was computed.

    A `send_fn` raises this after comparing `item.base_rev` with what the
    server currently holds. The item is parked with its payload intact instead
    of overwriting whatever the other client wrote.
    """


class CachedValue(NamedTuple):
    """A cached read, with enough provenance for the UI to be honest about it."""

    path: str
    data: Any
    rev: str
    fetched_at: float
    source: str

    @property
    def is_local(self) -> bool:
        """True when this reflects our own write that the server has not seen."""
        return self.source == "local"

    def age(self, now: Optional[float] = None) -> float:
        return (time.time() if now is None else now) - self.fetched_at

    def is_stale(self, ttl: float = DEFAULT_TTL, now: Optional[float] = None) -> bool:
        return self.age(now) >= ttl


class OutboxItem(NamedTuple):
    """One queued write, as handed to `send_fn`."""

    id: int
    method: str
    path: str
    payload: Any
    created_at: float
    attempts: int
    last_error: str
    state: str
    coalesce_key: Optional[str]
    base_rev: Optional[str]
    next_try_at: float
    local: dict   # {cache_path: rev} this write already applied on the device


class FlushResult(NamedTuple):
    sent: int = 0
    failed: int = 0
    parked: int = 0
    skipped: int = 0
    aborted: bool = False   # True when we stopped early because we are offline

    @property
    def ok(self) -> bool:
        return not self.aborted and self.failed == 0 and self.parked == 0


def default_db_path() -> Path:
    """Where the database lives when the caller does not say.

    FLET_APP_STORAGE_DATA is Flet's durable, app-private directory and is the
    process CWD in a packaged build; it survives app updates and is not
    something the OS may purge (unlike FLET_APP_STORAGE_CACHE, which would be
    exactly the wrong home for an outbox). Outside Flet — a test run, a REPL —
    it is unset, so fall back to a dotdir rather than scribbling in CWD.

    The dotdir is named after the product, which was renamed from NVII-DASH.
    A copy installed before the rename keeps its whole cache and, more to the
    point, its unsent outbox in the old directory - so an existing database
    there wins over starting an empty one beside it.
    """
    base = os.getenv("FLET_APP_STORAGE_DATA")
    if base:
        return Path(base) / "store.sqlite3"
    legacy = Path.home() / ".nvii-dash" / "store.sqlite3"
    if legacy.is_file():
        return legacy
    return Path.home() / ".agedashii" / "store.sqlite3"


def canonical(data: Any) -> str:
    """JSON with a stable byte-for-byte form, so a hash of it means something."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def revision(data: Any) -> str:
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


class Store:
    """Local source of truth: a read cache and a durable write queue.

    Safe to share across threads; see THREADING in the module docstring.
    """

    def __init__(self, db_path=None, *, ttl: float = DEFAULT_TTL,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 backoff_base: float = BACKOFF_BASE,
                 backoff_cap: float = BACKOFF_CAP,
                 probe: Optional[Callable[[], bool]] = None,
                 busy_timeout: float = 5.0):
        path = Path(db_path) if db_path is not None else default_db_path()
        if str(path) == ":memory:":
            # Per-thread connections each get their *own* private in-memory
            # database, so this would silently behave as several empty stores.
            raise ValueError("Store needs a real file; use a tempfile in tests")
        self.path = path
        self.ttl = ttl
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._probe = probe

        self._busy_timeout = busy_timeout
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._online = False
        self._online_detail = "not checked yet"
        self._online_at = 0.0
        self._refreshing: set[str] = set()   # paths with a background fetch in flight
        self._closed = False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))

    # ------------------------------------------------------------ plumbing
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        if self._closed:
            raise StoreError("store is closed")
        # check_same_thread=False only so close() can tidy up connections it
        # did not create; each connection is still used by one thread alone.
        conn = sqlite3.connect(self.path, timeout=self._busy_timeout,
                               check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout * 1000)}")
        conn.execute("PRAGMA foreign_keys=ON")
        self._local.conn = conn
        with self._conns_lock:
            self._conns.append(conn)
        return conn

    @contextlib.contextmanager
    def _tx(self):
        """One write transaction.

        IMMEDIATE, not DEFERRED: the lock is taken up front, so a read-then-
        write (coalescing looks before it inserts) cannot be overtaken between
        the two halves by another thread.
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        conn.commit()

    def close(self) -> None:
        self._closed = True
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        self._local = threading.local()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------------- online status
    @property
    def online(self) -> bool:
        """Whether the server was reachable as of the last probe or request.

        This is a *belief*, not a fact — it is only as fresh as whatever last
        updated it. Callers use it to decide whether to bother trying, never
        to decide whether data is good; `CachedValue.source` answers that.
        """
        with self._state_lock:
            return self._online

    @property
    def status(self) -> dict:
        with self._state_lock:
            return {"online": self._online, "detail": self._online_detail,
                    "at": self._online_at}

    def set_online(self, ok: bool, detail: str = "") -> bool:
        with self._state_lock:
            self._online = bool(ok)
            self._online_detail = detail or ("connected" if ok else "offline")
            self._online_at = time.time()
            return self._online

    def check_online(self) -> bool:
        """Run the injected probe and record what it said.

        Injected rather than hardwired to config.probe() so tests (and the
        flush path) never depend on a socket.
        """
        if self._probe is None:
            return self.online
        try:
            result = self._probe()
        except Exception as e:  # noqa: BLE001 - a probe that raises just means "no"
            return self.set_online(False, f"{type(e).__name__}: {e}")
        if isinstance(result, tuple):       # (ok, detail), as config.probe returns
            ok, detail = (list(result) + [""])[:2]
            return self.set_online(bool(ok), str(detail))
        return self.set_online(bool(result))

    # --------------------------------------------------------------- cache
    def put(self, path: str, data: Any, *, source: str = "server",
            fetched_at: Optional[float] = None) -> str:
        """Store a response for `path`. Returns its revision hash."""
        body = canonical(data)
        rev = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self._tx() as conn:
            self._put(conn, path, body, rev, source, fetched_at)
        return rev

    @staticmethod
    def _put(conn, path, body, rev, source, fetched_at=None) -> None:
        conn.execute(
            "INSERT INTO cache (path, body, rev, fetched_at, source) VALUES (?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET body=excluded.body, rev=excluded.rev, "
            "fetched_at=excluded.fetched_at, source=excluded.source",
            (path, body, rev, time.time() if fetched_at is None else fetched_at, source))

    def get(self, path: str) -> Optional[CachedValue]:
        """The cached value for `path`, or None if we have never had one.

        None means "nothing known", which is distinct from a cached JSON null —
        the caller needs that difference to decide between showing an empty
        list and showing nothing at all.
        """
        row = self._conn().execute(
            "SELECT path, body, rev, fetched_at, source FROM cache WHERE path=?",
            (path,)).fetchone()
        if row is None:
            return None
        return CachedValue(row["path"], json.loads(row["body"]), row["rev"],
                           row["fetched_at"], row["source"])

    def get_data(self, path: str, default: Any = None) -> Any:
        hit = self.get(path)
        return default if hit is None else hit.data

    def is_stale(self, path: str, ttl: Optional[float] = None,
                 now: Optional[float] = None) -> bool:
        """True if `path` is missing or older than the TTL. Missing counts as stale."""
        hit = self.get(path)
        return True if hit is None else hit.is_stale(self.ttl if ttl is None else ttl, now)

    def forget(self, path: str) -> bool:
        with self._tx() as conn:
            return conn.execute("DELETE FROM cache WHERE path=?", (path,)).rowcount > 0

    def clear_cache(self) -> int:
        """Drop every cached read. The outbox is untouched — it is not a cache."""
        with self._tx() as conn:
            return conn.execute("DELETE FROM cache").rowcount

    def read(self, path: str, fetch_fn: Optional[Callable[[], Any]] = None, *,
             ttl: Optional[float] = None, background: bool = True) -> Optional[CachedValue]:
        """Answer from cache now; refresh behind the UI if it is stale.

        This is the read path the pages should use. It never blocks on the
        network: `fetch_fn` runs on a daemon thread and the fresh value shows
        up on the *next* read. Pass background=False to refresh inline (tests,
        and a deliberate pull-to-refresh).
        """
        hit = self.get(path)
        stale = hit is None or hit.is_stale(self.ttl if ttl is None else ttl)
        if fetch_fn is not None and stale and (self.online or hit is None):
            if background:
                self._refresh_async(path, fetch_fn)
            else:
                self.refresh(path, fetch_fn)
                hit = self.get(path)
        return hit

    def refresh(self, path: str, fetch_fn: Callable[[], Any]) -> bool:
        """Fetch and cache `path` synchronously. True if the cache was updated.

        A fetch that fails or returns None leaves the previous value in place:
        stale data the user has seen before beats an empty screen, and it is
        labelled stale either way.
        """
        try:
            data = fetch_fn()
        except Exception as e:  # noqa: BLE001 - transport errors are expected here
            # Same rule as _flush: a StoreError's message was written for a
            # human to read in Settings, so do not staple a class name to the
            # front of it. Anything else is a surprise and says what it was.
            self.set_online(False, str(e) if isinstance(e, StoreError)
                            else f"{type(e).__name__}: {e}")
            return False
        if data is None:
            return False
        self.put(path, data, source="server")
        self.set_online(True, "connected")
        return True

    def _refresh_async(self, path: str, fetch_fn: Callable[[], Any]) -> None:
        # One in-flight refresh per path. A page rebuild can call read() for
        # the same endpoint several times in a frame, and without this each
        # one would start its own thread and its own request.
        with self._state_lock:
            if path in self._refreshing:
                return
            self._refreshing.add(path)

        def run():
            try:
                self.refresh(path, fetch_fn)
            finally:
                with self._state_lock:
                    self._refreshing.discard(path)

        threading.Thread(target=run, name=f"store-refresh:{path}", daemon=True).start()

    # -------------------------------------------------------------- outbox
    def enqueue(self, method: str, path: str, payload: Any = None, *,
                local: Optional[dict] = None, coalesce_key: Optional[str] = None,
                base_rev: Optional[str] = None) -> int:
        """Queue a write and apply it locally, atomically. Returns the item id.

        `local` is {cache_path: new_value} — the whole-collection result the
        caller already computed. Writing it to `cache` in the same transaction
        as the outbox row is what makes the UI update with the radio off: the
        next read sees the new value, marked source='local'.

        `coalesce_key` replaces the pending write with the same key instead of
        queueing another. Whole-collection writes are replacements, so only the
        last one matters; without this, an afternoon of ticking checkboxes
        becomes an afternoon of redundant POSTs when signal returns.
        """
        now = time.time()
        body = canonical(payload)
        with self._tx() as conn:
            # base_rev is the server copy this write was derived from, kept for
            # optional conflict detection. Only a 'server' row can be that
            # baseline: a 'local' row is our own unsynced write, and treating
            # it as the baseline would make every conflict check pass.
            if base_rev is None and local and len(local) == 1:
                only = next(iter(local))
                row = conn.execute(
                    "SELECT rev FROM cache WHERE path=? AND source='server'",
                    (only,)).fetchone()
                base_rev = row["rev"] if row else None

            applied = {}
            for cache_path, value in (local or {}).items():
                cbody = canonical(value)
                crev = hashlib.sha256(cbody.encode("utf-8")).hexdigest()
                self._put(conn, cache_path, cbody, crev, "local", now)
                applied[cache_path] = crev
            local_json = json.dumps(applied, sort_keys=True)

            item_id = None
            if coalesce_key is not None:
                row = conn.execute(
                    "SELECT id, base_rev FROM outbox WHERE coalesce_key=? AND state=?",
                    (coalesce_key, PENDING)).fetchone()
                if row is not None:
                    # Keep the row's id, so its place in the send order (and
                    # therefore its ordering against other collections) does
                    # not jump around, and keep the ORIGINAL base_rev: the
                    # newest payload is still derived from that same server
                    # state, everything since has been local. Attempts reset —
                    # this is new data, not the payload that kept failing.
                    item_id = row["id"]
                    conn.execute(
                        "UPDATE outbox SET payload_json=?, updated_at=?, attempts=0, "
                        "last_error='', next_try_at=0, base_rev=COALESCE(base_rev, ?), "
                        "local_json=? WHERE id=?",
                        (body, now, base_rev, local_json, item_id))

            if item_id is None:
                cur = conn.execute(
                    "INSERT INTO outbox (method, path, payload_json, created_at, "
                    "updated_at, state, coalesce_key, base_rev, local_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (method.upper(), path, body, now, now, PENDING, coalesce_key,
                     base_rev, local_json))
                item_id = cur.lastrowid
        return item_id

    def pending(self, *, state: str = PENDING, limit: Optional[int] = None) -> list:
        """Queued items in send order. state=None for every state."""
        sql = ("SELECT id, method, path, payload_json, created_at, attempts, "
               "last_error, state, coalesce_key, base_rev, next_try_at, local_json "
               "FROM outbox")
        args: list = []
        if state is not None:
            sql += " WHERE state=?"
            args.append(state)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [self._item(r) for r in self._conn().execute(sql, args)]

    @staticmethod
    def _item(row) -> OutboxItem:
        return OutboxItem(row["id"], row["method"], row["path"],
                          json.loads(row["payload_json"]), row["created_at"],
                          row["attempts"], row["last_error"], row["state"],
                          row["coalesce_key"], row["base_rev"], row["next_try_at"],
                          json.loads(row["local_json"]))

    def pending_count(self) -> int:
        """How many writes are waiting. This is the number to put on screen."""
        return self._conn().execute(
            "SELECT COUNT(*) FROM outbox WHERE state=?", (PENDING,)).fetchone()[0]

    def stats(self) -> dict:
        rows = self._conn().execute(
            "SELECT state, COUNT(*) n FROM outbox GROUP BY state").fetchall()
        out = {PENDING: 0, PARKED: 0, CONFLICT: 0}
        out.update({r["state"]: r["n"] for r in rows})
        out["cached"] = self._conn().execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        out["online"] = self.online
        return out

    def retry_parked(self) -> int:
        """Put parked and conflicted items back in the queue. Returns how many.

        The human's "try again" button. Attempts reset, because whatever was
        wrong (server bug, bad data, someone else's edit) has presumably been
        dealt with by the time anyone presses it.

        The revived row loses its coalesce_key. Without that it could collide
        with a pending write for the same collection queued while it sat
        parked, and future edits should coalesce onto that current write, not
        onto a revived older one. Nothing is lost by it: the revived row keeps
        its lower id, so it is sent first and the newer write then overwrites
        it — which for whole-collection writes lands on the newest state.
        """
        with self._tx() as conn:
            return conn.execute(
                "UPDATE outbox SET state=?, attempts=0, next_try_at=0, last_error='', "
                "coalesce_key=NULL WHERE state IN (?, ?)",
                (PENDING, PARKED, CONFLICT)).rowcount

    def discard(self, item_id: int) -> bool:
        """Delete a queued write. The only way data leaves here unsent."""
        with self._tx() as conn:
            return conn.execute("DELETE FROM outbox WHERE id=?", (item_id,)).rowcount > 0

    # --------------------------------------------------------------- flush
    def flush(self, send_fn: Callable[[OutboxItem], Any], *,
              now: Optional[float] = None, limit: Optional[int] = None) -> FlushResult:
        """Drain the outbox in order through `send_fn`. Never raises.

        `send_fn(item)` reports success by returning None or True, or a
        (ok, message) tuple as the api module's write helpers already do. It
        reports failure by returning False/(False, msg), or by raising:

            Unreachable  -> we are offline. Stop, count no attempt against
                            anybody, leave the queue exactly as it was.
            Conflict     -> the server moved under this write. Park it with
                            the payload intact rather than overwrite.
            anything else-> ordinary failure: count it, back off, retry later,
                            park it once it has burned max_attempts.

        Items are only ever removed on success. See POISON MESSAGES above.
        """
        # Serialised: two threads draining at once would both read the same
        # pending rows and send every one of them twice.
        with self._flush_lock:
            return self._flush(send_fn, time.time() if now is None else now, limit)

    def _flush(self, send_fn, now: float, limit) -> FlushResult:
        sent = failed = parked = skipped = 0
        aborted = False
        # Per-path head-of-line blocking. Sending write #2 to a path whose
        # write #1 just failed would push a state the server never saw the
        # predecessor of — for whole-collection writes that silently resurrects
        # whatever #1 removed. Other paths are independent and carry on.
        blocked: set[str] = set()

        for item in self.pending(state=PENDING, limit=limit):
            if item.path in blocked:
                skipped += 1
                continue
            if item.next_try_at > now:
                skipped += 1
                blocked.add(item.path)   # and so is everything behind it
                continue

            try:
                result = send_fn(item)
                ok, detail = _interpret(result)
                if not ok:
                    raise StoreError(detail or "write rejected")
            except Unreachable as e:
                # Deliberately no attempt increment: being offline is not the
                # write's fault, and charging it would park the whole queue.
                self._note_error(item.id, str(e) or "unreachable", bump=False)
                self.set_online(False, str(e) or "unreachable")
                aborted = True
                break
            except Conflict as e:
                self._park(item.id, CONFLICT, str(e) or "server copy changed")
                blocked.add(item.path)
                parked += 1
                continue
            except Exception as e:  # noqa: BLE001 - any transport/server error
                msg = str(e) if isinstance(e, StoreError) else f"{type(e).__name__}: {e}"
                attempts = item.attempts + 1
                if attempts >= self.max_attempts:
                    self._park(item.id, PARKED, msg, attempts=attempts)
                    parked += 1
                else:
                    self._note_error(item.id, msg, bump=True,
                                     next_try_at=now + self._backoff(attempts))
                    failed += 1
                blocked.add(item.path)
                continue

            with self._tx() as conn:
                conn.execute("DELETE FROM outbox WHERE id=?", (item.id,))
                # The write landed, so what we applied locally is now what the
                # server holds: relabel it 'server' so it can serve as a
                # conflict baseline again. Relabelling beats deleting — the
                # screen stays populated instead of flashing back to old data.
                # The rev guard matters: if the user edited again between the
                # send and now, the cache holds something newer that the
                # server has NOT seen, and calling that 'server' would make
                # the next conflict check lie.
                for cache_path, crev in (item.local or {}).items():
                    conn.execute("UPDATE cache SET source='server' "
                                 "WHERE path=? AND rev=? AND source='local'",
                                 (cache_path, crev))
            sent += 1
            self.set_online(True, "connected")

        return FlushResult(sent, failed, parked, skipped, aborted)

    def _backoff(self, attempts: int) -> float:
        return min(self.backoff_base * (2 ** (attempts - 1)), self.backoff_cap)

    def _note_error(self, item_id: int, msg: str, *, bump: bool,
                    next_try_at: Optional[float] = None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE outbox SET last_error=?, updated_at=?, "
                "attempts=attempts+?, next_try_at=COALESCE(?, next_try_at) WHERE id=?",
                (msg[:500], time.time(), 1 if bump else 0, next_try_at, item_id))

    def _park(self, item_id: int, state: str, msg: str,
              attempts: Optional[int] = None) -> None:
        with self._tx() as conn:
            if attempts is None:
                conn.execute("UPDATE outbox SET state=?, last_error=?, updated_at=? "
                             "WHERE id=?", (state, msg[:500], time.time(), item_id))
            else:
                conn.execute("UPDATE outbox SET state=?, last_error=?, attempts=?, "
                             "updated_at=? WHERE id=?",
                             (state, msg[:500], attempts, time.time(), item_id))

    def mark_synced(self, *paths: str) -> int:
        """Relabel locally-applied cache entries as server truth.

        Called once a flush has confirmed the server holds these collections,
        so a later conflict check has a real baseline again.
        """
        if not paths:
            return 0
        qs = ",".join("?" * len(paths))
        with self._tx() as conn:
            return conn.execute(
                f"UPDATE cache SET source='server' WHERE source='local' AND path IN ({qs})",
                paths).rowcount


def _interpret(result: Any) -> tuple:
    """Normalise what a send_fn returned into (ok, detail).

    api.py's write helpers already return (ok, message) and its transport
    helpers return None on failure, so accepting both shapes here is what lets
    them be passed in with no wrapper.
    """
    if result is None or result is True:
        return True, ""
    if result is False:
        return False, "send_fn returned False"
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
        return bool(result[0]), str(result[1])
    return True, ""
