"""Where a finished ride goes, and whether it has gone yet.

Hers: "what happens when i upload/uploaded rides? where do they get saved to?
moto?"

THE ANSWER, read out of the code on 2026-09-27:

- A ride goes to the dashboard server - Washi's `config.SERVER_URL` - as one
  GPX file, POST /api/rides/upload. The dashboard writes it into its rides
  folder, ~/www/dashbored/dashbored-rides/ on the Pi (`RIDES_DIR`,
  dashbored-v3.py:21), named from the ride's start: "2026-09-27 13:50" is
  saved as 2026-09-27-13-50.gpx. Sending the same ride again replaces that
  file.
- Moto does not get a copy. It lists that same folder through /api/rides,
  under Trips > Rides (agedashii_moto/pages/_trips_parts.py:99-105), so a ride
  on the dashboard is a ride in Moto.
- Stopping a ride sends it. With no signal Washi's outbox keeps it on the phone
  - and nothing in Navi drained that outbox, so a ride queued in a canyon stayed
  queued until the NEXT upload happened to push it out. `refresh()` drains it,
  and the Rides page calls `refresh()` when it opens.

ONE DOOR. The two surfaces used to build their own uploads. Ride sent the ride
in memory, which is only the last `telemetry.WINDOW` samples - ten minutes at
1 Hz - so every longer ride lost its beginning. Rides rebuilt the ride from the
log on disk, which until the telemetry fix held no sensor or audio channel, and
sending again OVERWROTE the richer file on the server with the poorer one.
`send()` is now the only way a ride leaves the phone: it reads the whole log,
and it writes down what happened.

WHAT IS REMEMBERED. `sent.json`, beside the logs, one entry per log file:

    {"ride-20260927-135012.jsonl": {
        "state": "sent", "at": 1790000000.0,
        "server_file": "2026-09-27-13-50.gpx", "message": "uploaded"}}

`at` is unix seconds. `state` is one of

    sent          the server said it has it
    queued        on this phone in Washi's outbox, not on the server yet
    failed        not sent and not queued - `message` says why
    on_dashboard  seen in the server's own list of rides, which outranks the
                  other three because it is the server saying so
    missing       was on the dashboard, and its list no longer has it

and a ride with no entry has never been sent from this phone.

"SEEN ON THE SERVER" HAS TO MEAN THIS SEND. The server keeps one file per
name, so a ride that went up once without its sensors and was then re-sent
has a file on the server either way. A queued or failed re-send is only
promoted when the server's copy is at least as new as the send
(`CLOCK_SLACK_S` allows for the phone and the Pi disagreeing about the time),
and the list is always fetched fresh - Washi's cached copy would otherwise
answer "yes" from before the send, with no signal at all.
"""
import json
import os
import re
import tempfile
import threading
import time

from agedashii_washi import api as M
from agedashii_washi import config

from . import live, telemetry

SENT_FILE = "sent.json"

#: How often the Rides page may ask the server for its list. Opening the page,
#: expanding a ride and sending one all rebuild it, and each rebuild asking the
#: network again would be the old "every repaint does I/O" mistake.
CONFIRM_EVERY_S = 60.0

#: What `api.ride_upload` appends when the write was queued rather than sent
#: (" - held on this device, will sync"). Matched rather than re-implemented,
#: and pinned by a test that goes through the real `ride_upload`.
QUEUED_WORDS = "will sync"

#: Where the dashboard keeps them. Not something the app can ask - it is the
#: server's own setting - so it is written down, with where it was read.
SERVER_FOLDER = "~/www/dashbored/dashbored-rides"

#: How far the phone's clock and the Pi's may disagree before a server copy
#: is judged older than the send it is being matched against.
CLOCK_SLACK_S = 300.0

#: What the Rides page says when the fresh list could not be had.
UNREACHABLE = "could not reach the dashboard to check"

_S = {"checked_at": 0.0}

#: `sent.json` is read, changed and written back by an upload thread and by
#: the Rides page's check thread, which can finish together. Without this, one
#: of the two answers is lost - or, with a shared temp file, the record torn.
_LOCK = threading.RLock()


# ------------------------------------------------------------------ naming

def server_name(ride_name):
    """The file the dashboard will save a ride called `ride_name` as.

    The same transformation as the dashboard's upload handler
    (dashbored-v3.py, /api/rides/upload): the name as sent, lowercased, every
    run of characters outside [a-zA-Z0-9-_] turned into one "-", trimmed.
    """
    sent_as = str(ride_name or "").replace(" ", "-")
    slug = re.sub(r"[^a-zA-Z0-9-_]+", "-", sent_as.lower()).strip("-")
    return f"{slug or 'ride'}.gpx"


def where():
    """One paragraph for the Rides page: where a sent ride ends up."""
    server = getattr(config, "SERVER_URL", "") or "the dashboard (no address set)"
    return (f"Stopping a ride sends it to the dashboard at {server}. It is "
            f"saved on the Pi in {SERVER_FOLDER}, one .gpx per ride, and Moto "
            "shows the same rides under Trips > Rides - it reads that folder, "
            "it does not keep a copy of its own. With no signal a ride waits "
            "on this phone and goes up the next time this page opens with "
            "signal.")


# ------------------------------------------------------------------ records

def _file(directory=None):
    folder = directory if directory is not None else live.ride_dir()
    return os.path.join(folder, SENT_FILE)


def _key(log_path, ride_name=""):
    return os.path.basename(log_path) if log_path else f"memory:{ride_name}"


def records(directory=None):
    """Everything `sent.json` says, or {} when there is nothing yet."""
    path = _file(directory)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"[navi] uploads: cannot read {path}: {exc}", flush=True)
        return {}
    return data if isinstance(data, dict) else {}


def _save(data, directory=None):
    path = _file(directory)
    folder = os.path.dirname(path) or "."
    tmp = None
    try:
        os.makedirs(folder, exist_ok=True)
        # A temp file of its own, in the same folder so the replace is atomic.
        handle, tmp = tempfile.mkstemp(dir=folder, prefix="sent.",
                                       suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        # Replace, not rewrite in place: a battery death mid-write must leave
        # the old record rather than half a file.
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"[navi] uploads: cannot write {path}: {exc}", flush=True)
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass                     # it was never made, or is gone
        return False


def record(log_path, state, message="", server_file="", when=None,
           directory=None, ride_name=""):
    """Write down what happened to one ride. Returns the entry."""
    with _LOCK:
        data = records(directory)
        key = _key(log_path, ride_name)
        entry = dict(data.get(key) or {})
        entry.update({"state": state, "message": str(message or "")})
        if server_file:
            entry["server_file"] = server_file
        if state in ("on_dashboard", "missing"):
            entry["seen_at"] = float(when if when is not None else time.time())
        else:
            entry["at"] = float(when if when is not None else time.time())
        data[key] = entry
        _save(data, directory)
        return entry


def status(log_path, directory=None, data=None):
    """What is known about one ride, or None if it has never been sent."""
    data = records(directory) if data is None else data
    return data.get(_key(log_path))


def describe(entry):
    """(words, palette key) for one ride's upload state, in her terms."""
    if not entry:
        return "not sent from this phone", "text3"
    state = entry.get("state")
    where_on = entry.get("server_file") or "the dashboard"
    if state == "on_dashboard":
        return f"on the dashboard - {where_on}", "good"
    if state == "sent":
        when = time.strftime("%a %H:%M", time.localtime(entry.get("at") or 0))
        return f"sent {when} - {where_on}", "good"
    if state == "queued":
        return "waiting on this phone for signal", "warn"
    if state == "missing":
        return (f"not on the dashboard any more - {where_on} has gone",
                "warn")
    return f"not sent: {entry.get('message') or 'no reason given'}", "bad"


# -------------------------------------------------------------------- send

def whole_ride(log_path=None, ride=None):
    """(ride, note): the WHOLE ride from its log, or the memory copy if not.

    The log is the ride; the memory copy is at most the last
    `telemetry.WINDOW` samples. The memory copy is used for what the log does
    not have: all of it when there is no log, and the rows past the log's end
    when the log stopped early - a full disk, an I/O error - so a log cut off
    ten minutes in does not become the whole ride on the server. Whatever is
    still missing is said in the note.
    """
    if log_path and os.path.isfile(log_path):
        loaded = telemetry.load_ride(log_path)
        if loaded.samples:
            if ride is None or ride.count <= loaded.count:
                return loaded, ""
            last_t = loaded.samples[-1].get("t") or 0.0
            loaded.samples.extend(r for r in ride.samples
                                  if (r.get("t") or 0.0) > last_t)
            loaded.count = len(loaded.samples)
            missing = ride.count - loaded.count
            return loaded, (f" - {missing} of {ride.count} points missing: "
                            "the log on this phone stopped early"
                            if missing > 0 else "")
    if ride is None or not ride.samples:
        return None, ""
    note = ""
    if ride.count > len(ride.samples):
        note = (f" - only the last {len(ride.samples)} of {ride.count} "
                "points: the log on this phone could not be read")
    return ride, note


def send(log_path=None, ride=None, note="", directory=None):
    """Send one ride to the dashboard. Blocking - run it on a thread.

    Returns (ok, message). "ok" includes queued: the ride is safe on this
    phone and will go when there is signal, and the message says which.
    """
    whole, caveat = whole_ride(log_path, ride)
    if whole is None:
        return False, "nothing in that ride to send"
    name = whole.name or (ride.name if ride is not None else "ride")
    server_file = server_name(name.replace(" ", "-"))
    try:
        ok, message = M.ride_upload(
            name.replace(" ", "-"), whole.to_gpx(),
            note or f"{whole.count} points")
    except Exception as exc:                          # noqa: BLE001 - say it
        ok, message = False, f"{type(exc).__name__}: {exc}"
    message = f"{message}{caveat}"
    state = ("queued" if ok and QUEUED_WORDS in message
             else "sent" if ok else "failed")
    record(log_path, state, message, server_file, directory=directory,
           ride_name=name)
    if ok and state == "sent":
        message = f"{message} - on the dashboard as {server_file}"
    return ok, message


# ------------------------------------------------------------------ confirm

def confirm(listing, log_paths, directory=None):
    """Match the server's FRESH list against this phone's rides.

    `listing` is the dashboard's /api/rides rows: {"id", "mtime", ...}.
    Returns how many rides changed state.

    - never sent from here (uploaded before this record existed): on the
      dashboard if its name is there - matched by the name the server would
      have given it, read from the first line of its log
    - sent: the server already said yes, so being listed confirms it
    - queued or failed: only when the server's copy is at least as new as
      the send, or an OLDER copy of the same ride would turn it green
    - on the dashboard but no longer listed: missing, and it says so
    """
    rows = {}
    for row in listing or ():
        if isinstance(row, dict) and row.get("id"):
            rows[str(row["id"])] = row
    changed = 0
    with _LOCK:
        data = records(directory)
        for path in log_paths:
            entry = data.get(_key(path)) or {}
            state = entry.get("state")
            server_file = (entry.get("server_file")
                           or server_name(telemetry.header_name(path) or ""))
            found = rows.get(server_file[:-4])
            if found is None:
                if state == "on_dashboard":
                    record(path, "missing", entry.get("message", ""),
                           server_file, directory=directory)
                    changed += 1
                continue
            if state == "on_dashboard":
                continue
            if state in ("queued", "failed"):
                mtime = found.get("mtime")
                try:
                    newer = float(mtime) >= float(entry.get("at") or 0.0) \
                        - CLOCK_SLACK_S
                except (TypeError, ValueError):
                    newer = False
                if not newer:
                    continue
            record(path, "on_dashboard", entry.get("message", ""),
                   server_file, directory=directory)
            changed += 1
    return changed


def refresh(log_paths, directory=None, force=False):
    """Push anything queued, then ask the server what it has. Blocking.

    Returns (changed, note): how many rides changed state, and `UNREACHABLE`
    when the fresh list could not be had, so the page says so rather than
    leaving the last answer looking current.

    The list is fetched directly, NOT through `api.rides_list`: that reads
    Washi's cache, which with no signal hands back whatever it last held -
    so "is it on the dashboard?" would be answered from before the send.
    """
    now = time.time()
    if not force and now - _S["checked_at"] < CONFIRM_EVERY_S:
        return 0, ""
    _S["checked_at"] = now
    try:
        if M.pending_count():
            M.sync()
        got = M._fetch("/api/rides", timeout=8)        # noqa: SLF001 - fresh
    except Exception as exc:                          # noqa: BLE001 - say it
        print(f"[navi] uploads: refresh failed: {type(exc).__name__}: {exc}",
              flush=True)
        return 0, UNREACHABLE
    listing = got.get("rides") if isinstance(got, dict) else None
    if not isinstance(listing, list):
        return 0, UNREACHABLE
    return confirm(listing, log_paths, directory), ""
