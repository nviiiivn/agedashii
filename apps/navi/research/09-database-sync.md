# Section 9: database and sync

Research only. Nothing here is a pick, and `CHOICES.md` is untouched.

Checked 2026-09-26/27.

Three sub-questions, in the order the section lists them:

- **(a)** the on-phone store
- **(b)** the ride log format
- **(c)** sync to dashbored

**Method notes.** Repository facts (licence, latest release, stars, last push,
archived) came from `gh repo view` and `gh api` against GitHub's own API, not
from a summarising page fetch — so they are verified in a way the figures in
`00-already-checked.md` were not. Where a licence file was the question, the
repository tree was searched recursively for `licen|copying|notice` and the file
read. Anything I could not confirm is marked **unverified**.

---

## The two facts that decide most of this

Before the options, two things I read rather than assumed, because they change
what the options even mean.

### 1. What her server actually is

`/home/nvii/dashboredapp-server/dashbored/dashbored-v3.py` — 15,996 lines of
**Python standard-library `http.server` / `socketserver`**. Its imports are
`http.server, json, os, subprocess, time, urllib.parse, threading, socketserver,
urllib.request, shlex, re`. There is no Postgres, no MongoDB, no MySQL, no
CouchDB, and **no database of any kind behind rides**:

```
RIDES_DIR = os.path.expanduser(os.environ.get("RIDES_DIR", "~/www/dashbored/dashbored-rides"))

def rides_list():            # os.listdir, filter *.gpx, os.stat for size/mtime
def rides_read(ride_id):     # open() + parse_gpx()
```

`/api/rides/upload?filename=` writes a file to that directory. Rides are `.gpx`
files on a disk.

This single fact rules out PowerSync, ElectricSQL and CouchDB/Couchbase
replication before any licence question, because every one of them requires a
source database she does not run.

### 2. Her server silently discards every derived channel

`parse_gpx` in the same file:

```python
item = {'lat': float(lat), 'lon': float(lon), 'ele': None, 'time_iso': None, 'time_ms': None}
```

It reads `trkpt/@lat`, `@lon`, `g:ele` and `g:time`. Nothing else. The
`<extensions>` block that `telemetry.py:to_gpx` fills with `navi:speed_ms`,
`navi:heading`, `navi:g_long`, `navi:g_lat`, `navi:lean_deg`, `navi:yaw_dps`,
`navi:grade_pct` and `navi:rpm_est` is never looked at.

And then `rides_read` recomputes speed from the points:

```python
d = haversine(pts[i-1], pts[i])
mps = d / (dt_ms/1000.0)
pts[i]['speed_mph'] = round(mps * 2.23694, 2)
```

That is exactly the recomputed-from-positions speed that `telemetry.py`'s module
docstring says is worse than the receiver's Doppler figure — *"they are good at
low speed and they are good immediately"*. The Doppler speed is in the file she
uploads, and the server throws it away and does arithmetic instead.

`api.py:ride_upload`'s docstring says *"it is exactly what was happening, and the
telemetry sat on the phone while the server got a plain track."* Half of that is
fixed: the telemetry now reaches the server. The other half is not: the server
cannot see it.

**So the weakest link in section 9 is not the sync mechanism. It is the wire
format and the server's 24-line GPX parser.** No sync product on the list
improves this; a schema change and ~20 lines in `parse_gpx` do.

---

## (a) On-phone store

### Summary table

| Option | Licence | Latest | Stars | Android | KMP | Codegen | Verdict |
|---|---|---|---|---|---|---|---|
| Room | Apache-2.0 ✅ | 2.8.5, 2026-09-09 | androidx 6.1k | yes | **yes**, stable 2.7.0 | KSP required | In |
| SQLDelight | Apache-2.0 ✅ | 2.4.0, 2026-09-18 | 6,886 | yes | **yes**, longest track record | Gradle plugin required | In |
| Plain SQLite (`androidx.sqlite`) | Apache-2.0 ✅ | sqlite 2.5.0+ KMP | — | yes | **yes** | **none** | In |
| Plain SQLite (`android.database.sqlite`) | Apache-2.0 ✅ | platform | — | yes | **no — strike** | none | In, Android-only |
| SpatiaLite wrappers | **NO LICENCE FILE** ❌ | 2.2.3, 2026-09-25 | 7 | yes | no | no | **Ruled out** |
| ObjectBox | bindings Apache-2.0, **core closed** ⚠️ | 5.4.2, 2026-05-06 | 4,624 | yes | **no — strike** (#601 open since 2018) | annotation processor | Out |
| Couchbase Lite | **Couchbase CE Agreement**, not OSI ⚠️ | see below | — | yes | via Kotbase only | no | Out |
| DuckDB | MIT ✅ | v1.5.5, 2026-07-22 | 41,723 | **experimental, build it yourself** | no Kotlin | no | Out on the phone, in on Tower |

### The durability question, answered from primary sources

Her rule: *a ride must survive a battery death or a low-memory kill.* Those are
two different failures and SQLite treats them differently.

**sqlite.org/wal.html**, verbatim:

> Writers sync the WAL on every transaction commit if PRAGMA synchronous is set
> to FULL but omit this sync if PRAGMA synchronous is set to NORMAL.

> The downside to this configuration is that transactions are no longer durable
> and might rollback following a power failure or hard reset.

**developer.android.com/topic/performance/sqlite-performance-best-practices**,
verbatim, recommending `NORMAL`:

> In this setting, a commit can return before the data is stored in a disk. If a
> device shutdown occurs, such as on loss of power or a kernel panic, the
> committed data might be lost. However, because of logging, your database isn't
> corrupted.
>
> **If only your app crashes, your data still reaches the disk.** For most apps,
> this setting yields performance improvements at no material cost.

So:

- **Low-memory kill / force-stop / app crash** → survives under either setting.
- **Battery death** → survives only under `synchronous=FULL`.

And the loss window under `NORMAL` is not one row. On the SQLite forum, Dan
Kennedy states that under `NORMAL` a committed transaction goes into the WAL with
no sync until a checkpoint, so a power failure can lose **every transaction
sitting in the WAL**, not just the last (forum post, not the docs — recorded as
such). With the default 1000-page autocheckpoint that is potentially minutes of
ride.

In WAL mode `FULL` and `EXTRA` are equivalent, so `FULL` is the ceiling.

Her `store.py` already sets `synchronous=FULL` and explains why:

> `synchronous=FULL`, deliberately, against the usual advice for phones. NORMAL
> can lose the last commits to a power cut, and the last commit is exactly the
> write the user just made.

**That reasoning is correct and independently confirmed by both primary sources
above.** It also means Android's own default recommendation is the wrong one for
her, which is worth knowing because Room and most tutorials will hand it to her.

### Room

- <https://developer.android.com/jetpack/androidx/releases/room> · source in
  <https://github.com/androidx/androidx>
- **Licence: Apache-2.0.** Verified — `gh` reports `apache-2.0` for
  `androidx/androidx` and `LICENSE.txt` opens with "Apache License / Version 2.0,
  January 2004".
- **Latest: 2.8.5, released 2026-09-09.** androidx monorepo 6,101 stars, pushed
  2026-09-27.
- **KMP: yes, and stable.** Introduced 2.7.0-alpha01 (2024-05-01), stable in
  2.7.0 (2025-04-09). Platforms: Android, iOS, JVM desktop, native macOS, native
  Linux, plus watchOS and tvOS from 2.8.0-alpha01.
- **Code generator: required.** KSP (or KAPT), plus the `androidx.room` Gradle
  plugin for the schema directory.
- Driver: `BundledSQLiteDriver` from `androidx.sqlite:sqlite-bundled` is the
  recommended KMP choice — SQLite compiled from source, so the version and query
  planner are the same on every device, at the cost of binary size.
- Write throughput: no independent benchmark found. Room is a thin generated
  layer over the same SQLite; its cost is a data-class allocation per row.
- **Fit with her rules:** good, with one caveat. Room owns the connection, so
  `synchronous=FULL` has to be set through a callback rather than simply written
  where she writes it now, and Android's own documentation will keep telling her
  to use `NORMAL`.
- **Known problems:** Kotlin codegen is stricter than Java — abstract properties
  as DAO getters are disallowed, nullable collection return types are disallowed,
  type-argument nullability is enforced (`Flow<T?>` where `Flow<T>` used to do).
  Requires Kotlin 2.0+.

### SQLDelight

- <https://github.com/sqldelight/sqldelight>
- **Licence: Apache-2.0.** Verified by `gh` and by the repo's own statement.
- **Latest release 2.4.0 on 2026-09-18**; last push 2026-09-25. **6,886 stars,
  579 forks.** Healthy.
- Android yes. KMP yes, and it has been KMP-ready longer than Room: Android,
  Native (iOS/macOS/Linux/Windows), JVM, JS. Dialects beyond SQLite (MySQL,
  PostgreSQL, HSQL/H2) on the JVM.
- **Code generator: required.** A Gradle plugin compiles `.sq` files into Kotlin
  and verifies schema, statements and migrations at compile time.
- Speed: it is a generator, not a runtime engine — the generated code calls the
  same Android driver, with reflection-free generated mappers. No published
  Android benchmark against a raw cursor. One data point in the other direction,
  on iOS only: touchlab/SQLiter#39 reports C++ roughly 5× faster on inserts and
  7× on selects than a KMM+SQLDelight path at 100,000 records, traced toward
  SQLiter, the iOS driver. **That does not transfer to Android**, which uses a
  different driver, and is recorded only so it is not discovered later and
  mistaken for a general result.
- **Fit with her rules: the best of the three.** She writes the SQL, so the
  schema in `store.py` ports across verbatim, `BEGIN IMMEDIATE` stays
  `BEGIN IMMEDIATE`, and the PRAGMAs stay where she put them. Nothing has an
  opinion about her transactions.
- **Known problems:** migrations are hand-written `.sqm` files with no helper
  API; one practitioner reported spending three days migrating a KMP project off
  it and concluding the mistake was choosing by feature list rather than by which
  language the team thinks in.

### Plain SQLite

Two different things wear this name.

**`androidx.sqlite` (the driver layer, KMP):**

- <https://developer.android.com/kotlin/multiplatform/sqlite>
- Apache-2.0. KMP from 2.5.0; JS/WasmJS from 2.7.0. Three interfaces —
  `SQLiteDriver`, `SQLiteConnection`, `SQLiteStatement` — and implementations in
  `sqlite-bundled` (compiled from source) or `sqlite-framework` (platform).
- **No code generator at all.** Open a connection, prepare, bind, step.
- Recent additions that matter here: `BundledSQLiteDriver.addExtension`, and an
  API for asking whether a connection has an active transaction (SQLite
  transactions do not nest, so this is what you need when hand-rolling
  `BEGIN`/`COMMIT` versus `SAVEPOINT`/`RELEASE` — which is exactly what
  `store.py:_tx` does).
- **Fit: the closest thing to what she already has.** `store.py` is hand-written
  SQL against `sqlite3` with her own transaction discipline; this is the same
  code shape in Kotlin, on every platform, with no generator in the way.
- Known problem: no compile-time SQL checking. And a linker conflict on native
  targets if something else also links system `sqlite3` ("library not found for
  -lsqlite3"), because `sqlite-bundled` statically links its own.
- Middle grounds exist if bare SQL feels too bare:
  `eygraber/sqldelight-androidx-driver` (MIT ✅, 0.2.2 on 2026-08-05, 96 stars,
  pushed 2026-09-25) puts SQLDelight on this driver; SQLiteNow generates from a
  Gradle plugin with no IDE plugin.

**`android.database.sqlite.SQLiteDatabase` (the platform class):** Apache-2.0,
works today, zero dependencies — and **Android-only, which is the strike §2 asks
me to record.** It also uses whatever SQLite the device ships, which varies.

### SpatiaLite — ruled out on licence

Every usable Android wrapper has **no licence file**. This is her hard
disqualifier, so they are out regardless of quality.

| Repo | Licence | Latest | Stars | Last push |
|---|---|---|---|---|
| [dalgarins/android-spatialite](https://github.com/dalgarins/android-spatialite) | **NONE** ❌ | 2.2.3, 2026-09-25 | 7 | 2026-09-25 |
| [svkostadinoff/android-spatialite](https://github.com/svkostadinoff/android-spatialite) (was sevar83) | **NONE** ❌ | 2.0.1, 2019-08-30 | 108 | 2019-11-15 |
| [iulian0512/android_spatialite](https://github.com/iulian0512/android_spatialite) | **NONE** ❌ | 2.0.10, 2025-09-25 | 8 | 2025-09-25 |
| [Raintee-CN/SpatiaTile-Android](https://github.com/Raintee-CN/SpatiaTile-Android) | Apache-2.0 ✅ | none | 1 | 2026-09-24 |

Verification detail: a recursive tree search of the first three for
`licen|copying|notice` returns only **vendored dependency** licences —
`lib/src/main/jni/ndk-modules/freexl/freexl/COPYING`,
`.../libspatialite/libspatialite/COPYING`, and for the sevar83fork also GEOS,
libiconv and xz COPYING files. The wrapper code itself, which is the part she
would compile, carries nothing. `iulian0512` has no licence path at all.

`SpatiaTile-Android` is the only one that is actually licensed (Apache-2.0), but
it has 1 star, 0 forks, no release, and its first push is this month. Not
something to put a ride log inside.

Separate consideration if SpatiaLite ever returns for §7: upstream libspatialite
is an MPL/GPL/LGPL tri-licence and GEOS is LGPL (**unverified — I did not open
those files**). Geo queries belong in §7 anyway; nothing about the telemetry
write path wants them.

### ObjectBox — out, on two counts

- <https://github.com/objectbox/objectbox-java>
- **Licence is split, and this is the trap.** The Java/Kotlin bindings are
  **Apache-2.0** (verified: `gh` reports `apache-2.0`, `LICENSE.txt` present).
  The **native core is closed source** under the **"ObjectBox Binary Licence"**.
  From objectbox.io/faq, fetched: runtime use of "Java Bindings" is "Apache
  License, Version 2.0" and runtime use of "Native Libraries" is "ObjectBox
  Binary Licence"; the FAQ says *"Therefore, we open sourced the ObjectBox
  language bindings under the Apache 2.0 license"* and *"If you need access to
  the core, please reach out to us individually."*
  - The **exact text of the Binary Licence is unverified** —
    `docs.objectbox.io/faq/objectbox-binary-license` returns 404.
  - A third-party issue thread (breezy-weather#677) states the Gradle plugin is
    GPLv3 — **unverified**.
  - Practical consequence recorded there: apps depending on it were removed from
    F-Droid's main repo without notice, because the shipped `.so` is proprietary.
    The request to open the core (objectbox-java#560) dates from 2018-09-18 and
    is unresolved.
- 5.4.2 released 2026-05-06; last push 2026-08-03; 4,624 stars, 311 forks.
  Active.
- **KMP: no. This is the §2 strike.** objectbox-java#601, "Kotlin-Native support
  for multiplatform development", open since November 2018, labelled
  "enhancement" and "help wanted", 77+ 👍, no milestone. iOS has a Swift/Flutter
  library and **no Kotlin binding**. "Multiplatform" in ObjectBox's own copy
  means Android + JVM on Linux/Windows/macOS, i.e. JVM targets, not
  Kotlin/Native.
- Speed: the only head-to-head numbers are **vendor-run** — ObjectBox's own CRUD
  benchmark claims it beats Room and Realm on create, read, update and delete,
  on a Galaxy S9+ running Android 10, against **SQLite 3.22.0 with Room 2.3.0**
  and Realm 10.6.1. Those are years-old versions and SQLDelight is not in the
  comparison. Directional at best. Their licence forbidding benchmarking is
  Garmin's, not theirs — but the point stands that nobody independent has
  published this.
- **Crash-durability guarantees: unverified.** I found no ObjectBox statement
  about power-loss behaviour that I could open and quote. For a store whose whole
  job here is surviving a battery death, an unverifiable durability story in a
  closed binary is disqualifying on its own.
- **Fit with her rules: bad.** A closed-source engine writing the ride log means
  that when a file is wrong she cannot read the code that wrote it. It also ships
  its own engine, so the APK grows, where Room/SQLDelight ride on SQLite.

### Couchbase Lite — out

- The repo named in most tutorials,
  [couchbase/couchbase-lite-android](https://github.com/couchbase/couchbase-lite-android),
  is **archived** — verified `isArchived: true`, last release 2.5.2 on
  2019-07-02, last push 2021-05-20. Anyone finding it first will be six years out
  of date.
- The live Community-Edition root is
  [couchbase/couchbase-lite-java-ce-root](https://github.com/couchbase/couchbase-lite-java-ce-root)
  — licence reported as **"Other"**, 57 stars, pushed 2026-09-16.
- [couchbase-lite-core](https://github.com/couchbase/couchbase-lite-core) (the
  C++ engine) — licence **"Other"**, 264 stars, pushed 2026-09-25. **I could not
  read its LICENSE file** (the contents API returned nothing decodable).
  **Unverified — and for a licence question that is not good enough.**
  [couchbase-lite-c](https://github.com/couchbase/couchbase-lite-c) is Apache-2.0
  (verified), 130 stars.
- The Community Edition is governed by the **"Couchbase, Inc. Community Edition
  License Agreement"** — free deployment, provided "as is" with no services or
  support, excluding XDCR "and any other excluded features described in Couchbase
  documentation", with Peer-to-Peer Sync being Enterprise-only. Breach obliges
  payment of the Enterprise fee. Not an OSI licence. Enterprise pricing is
  quote-only and forum posters report being unable to get a number.
  *(Summarised from couchbase.com/community-license-agreement via search; I did
  not open the full agreement text — treat the clause list as partial.)*
- **KMP: only through a third-party wrapper.**
  [jeffdgr8/kotbase](https://github.com/jeffdgr8/kotbase) — **Apache-2.0 ✅**,
  124 stars, latest release 3.2.4-1.2.0 on **2025-10-25**, last push 2026-07-11.
  One maintainer, tracking CBL 3.2. Genuinely good work; a single point of
  failure.
- **Fit: wrong shape.** The only reason to accept a bespoke licence and a
  third-party KMP wrapper is replication — and replication needs Sync Gateway,
  which (c) rules out. Without it, this is a JSON document store being asked to
  hold 29 numeric channels at 1 Hz.

### DuckDB — wrong end of the problem

- <https://github.com/duckdb/duckdb> · **MIT ✅**, v1.5.5 on 2026-07-22, pushed
  2026-09-25, **41,723 stars**. `duckdb/duckdb-java` (JDBC) MIT, v1.5.5.1 on
  2026-08-03, 127 stars.
- **Android: experimental.** Its own documentation, fetched: *"DuckDB has
  experimental support for Android. Please use the latest `main` branch of DuckDB
  instead of the stable versions."* No prebuilt artifact, no Maven package — you
  build `libduckdb.so` per ABI with the NDK. Android binaries have been in CI
  since mid-2024 (PR #12550, `armeabi-v7a` and `arm64-v8a`).
- Two documented Android traps: the `org.duckdb:duckdb_jdbc` artifact compiles
  but fails at runtime with `NoClassDefFoundError: [Ljava/nio/file/attribute/FileAttribute;`
  (java.nio API-level mismatch); and extension installs fail fetching
  `extensions.duckdb.org/.../linux_arm64_android/...`, marked expected behaviour
  (#13337) — so extensions must be statically linked at build time.
- **Speed: fast at the wrong thing.** It is a columnar analytical engine. No
  source claims it is good at a steady single-row append, and its own positioning
  is analysis. A ride log is write-once-per-second, read-rarely, append-only.
- **Where it genuinely belongs:** on Tower, reading the finished archive.
  DuckDB's JSON extension reads `.jsonl` directly, so a session's rides can be
  queried in place with no conversion and **no Android build at all**. That keeps
  every DuckDB advantage and pays none of its Android costs.

---

## (b) Ride log format

### Summary table

| Format | Licence | Survives battery death mid-write? | Append cost | Carries all 29 channels? | Android cost |
|---|---|---|---|---|---|
| **JSON Lines** (now) | n/a | **Yes** — torn last line is skipped; `read_log` already does | one write, O(1) | yes, and unknown keys pass through | none |
| **SQLite** | public domain | **Yes**, with `synchronous=FULL` | one txn | yes | none (already there) |
| **Parquet** | Apache-2.0 | **No — footer is written at close** | buffers a row group in RAM | yes | Hadoop dependency chain |
| **Garmin FIT** | **proprietary Garmin EULA** ⚠️ | **No** — needs a repair pass | one record | **11 natively, 18 as developer fields** | official Java SDK |

### JSON Lines — the status quo, and it holds up

`telemetry.py` opens in append mode, writes a header line, sample lines,
incident lines and a footer line, flushes every `FLUSH_EVERY = 5`, and
`read_log` skips any line that will not parse. The docstring's claim —
*"a torn file is still a readable file"* — is the format's own stated selling
point, not a hopeful reading of it:

- Appending is a single file write: open in append mode, serialise, newline. A
  JSON array would require parse → push → re-serialise the whole file.
- A producer does not need to know where the end is; the stream is valid at 1
  line or 1 billion.
- Truncation tolerance, where a truncated JSON array leaves nothing usable.

Costs, honestly:

- Key names repeat on every line. Her longest channel name is
  `yaw_gyro_dps` and there are 29 of them, so the keys are a real fraction of the
  bytes. Gzip largely cancels it — reported 5–10× / 70–95% reduction, and
  jsonlines.org itself recommends `.jsonl.gz` or `.jsonl.bz2`.
- No index. A time-range query over a four-hour ride is a linear scan. At
  ~14,400 samples that is nothing; it is only a problem if she ever wants to
  query across a season.
- MIME type is unsettled (`application/jsonl` vs `application/x-ndjson`).

Everything her code does with it — `tail`-able, `grep`-able, concatenable with
`cat`, diffable, readable in six months with no tooling — is a property of the
format, not an accident.

### SQLite as the log itself

- Licence: public domain. Already in the app for the outbox.
- **Durability:** see the quoted primary sources under (a). WAL +
  `synchronous=FULL` survives a battery death; `NORMAL` does not, and can lose
  the whole WAL rather than the last row.
- **Throughput:** one blog benchmark claims ~70k–100k single-row write
  transactions/second under WAL+NORMAL (one machine, not a phone — unverified
  for eMMC). Her rate is 1 Hz for GPS fixes, with accelerometer and barometer
  merged into the latest row rather than making rows of their own. Either format
  is three orders of magnitude clear of the requirement. **Throughput does not
  decide this.**
- One real performance caveat worth carrying: PowerSync's engineering blog states
  that *"tables with secondary indexes can see up to 5x reduction in insert
  performance."* So: one index on the timestamp, not five.
- **What it buys:** `dist_m`, `max_lean_deg`, `climb_m` and the rest become SQL
  rather than Python attributes maintained by hand in `_track_maxima`; a
  time-range query becomes an index seek; one store instead of two.
- **What it costs:** not human-readable, not `tail`-able, not diffable. Needs a
  migration story that JSONL does not, because `feed_extra` currently accepts
  unknown keys and *"a channel added to `CHANNELS` later should not need an edit
  here to be recorded"* — a fixed table schema removes that property unless
  extras go into a JSON column. And SQLite gives no append-only guarantee at the
  database layer; anyone with write access can `DELETE FROM`, so append-only stays
  a code-layer promise either way.

### Parquet — fails the crash rule at the format level

- <https://github.com/apache/parquet-java> — Apache-2.0 ✅, 1.18.1 on
  2026-09-13, 3,083 stars, pushed 2026-09-27. Healthy project; wrong tool.
- **A Parquet file is row groups followed by a footer that holds the schema and
  the row-group offsets, and the footer is written at close.** A crash before
  close leaves real page data at the start and no footer at the end, and no
  reader can find its way in. This is not a library bug — it is the format.
  Corroborating evidence: an Apache Arrow enhancement request asks for a
  `FlushWithFooter` that, unlike `Close`, would leave the writer's metadata
  intact so later row groups still get an inclusive footer — i.e. **the
  capability she needs does not exist today.**
- Writing one row at a time produces thousands of single-row row groups: the
  footer balloons and statistics stop being useful. The standard pattern is
  new-file-per-batch plus background compaction, which means a ride is many files
  needing a second process.
- Row groups buffer in RAM before flushing (general guidance is 100–300 MB
  uncompressed, far beyond a phone), so the memory profile is the opposite of
  what a low-memory kill wants.
- Android: `parquet-hadoop` pulls in a large part of Hadoop. APK size and
  desugaring are the practical blockers; there is no Android artifact.
- **Verdict: out on the phone, correct on Tower.** Convert the JSONL archive to
  Parquet after the fact if a season of rides ever needs columnar scans.

### Garmin FIT — it does not lose her data, but 18 of 29 channels become developer fields

**The licence first, because it is worse than "has an SDK" suggests.**

Read in full from `garmin/fit-java-sdk/LICENSE.txt`: the **"Flexible and
Interoperable Data Transfer (FIT) Protocol License Agreement"**, a proprietary
Garmin EULA, not an open-source licence.

- Grant: *"non-exclusive, royalty-free, non-transferable, non-sublicensable,
  limited license to use the Licensed Technology for Licensee's internal business
  purposes."*
- **2(c)** — may not *"rent, lease, lend, sell, sublicense, assign, distribute,
  publish, transfer or otherwise make available the Licensed Technology, or any
  features or functionality of the Licensed Technology, to any third party for
  any reason."*
- **2(d)** — may not distribute it or derivatives *"so that any part of it
  becomes subject to any license that requires that the Licensed Technology or
  any of Garmin's other intellectual property be disclosed or distributed in
  source code form, or that others have the right to modify it."*
  → **incompatible with GPL, and with publishing a repository that vendors the
  SDK.** Note that §1 of `CHOICES.md` lists OsmAnd (GPLv3) as a fork candidate;
  those two cannot be in the same binary.
- **2(f)** — may not use it *"for purposes of benchmarking or a competitive
  analysis."*
- **2(g)** — may not use it *"in connection with the design, construction,
  maintenance, operation, or use of any hazardous environments, systems or
  applications, any safety response systems or other safety-critical applications
  or any other use or application in which the use or failure of the Licensed
  Technology could lead to personal injury or physical or property damage."*
  A handlebar-mounted motorcycle dashboard sits uncomfortably against that
  sentence. **Not legal advice — recorded because it is in the text she would be
  agreeing to.**
- **§4** — the Licensed Technology is Garmin's **Confidential Information**.

Repos, verified:

| Repo | Licence | Latest | Stars |
|---|---|---|---|
| [garmin/fit-java-sdk](https://github.com/garmin/fit-java-sdk) | FIT EULA ("Other") | 21.217.0, 2026-09-22 | 36 |
| [garmin/fit-c-sdk](https://github.com/garmin/fit-c-sdk) | FIT EULA ("Other") | 21.217.0, 2026-09-22 | 5 |
| [garmin/fit-javascript-sdk](https://github.com/garmin/fit-javascript-sdk) | FIT EULA ("Other") | 21.217.0, 2026-09-22 | 77 |
| [garmin/fit-python-sdk](https://github.com/garmin/fit-python-sdk) | **licence file location unverified** | 21.217.0, 2026-09-22 | 165 |
| [muktihari/fit](https://github.com/muktihari/fit) (Go, third party) | BSD-3-Clause ✅ | v0.28.4, 2026-09-11 | 181 |

**Can FIT carry lean angle and estimated RPM? I checked the SDK source rather
than trusting a summary.**

I downloaded `src/main/java/com/garmin/fit/RecordMesg.java` from
`garmin/fit-java-sdk@main` (21.217.0) and enumerated every field of the `record`
message. There are **84**:

```
absolute_pressure accumulated_power activity_type air_time_remaining altitude
ascent_rate ball_speed battery_soc cadence cadence256 calories cns_load
combined_pedal_smoothness compressed_accumulated_power compressed_speed_distance
core_temperature current_stress cycle_length cycle_length16 cycles depth
device_index distance ebike_assist_level_percent ebike_assist_mode
ebike_battery_level ebike_travel_range enhanced_altitude
enhanced_respiration_rate enhanced_speed flow fractional_cadence gps_accuracy
grade grit heart_rate left_pco left_pedal_smoothness left_power_phase
left_power_phase_peak left_right_balance left_torque_effectiveness motor_power
n2_load ndl_time next_stop_depth next_stop_time po2 position_lat position_long
power pressure_sac resistance respiration_rate right_pco right_pedal_smoothness
right_power_phase right_power_phase_peak right_torque_effectiveness rmv
saturated_hemoglobin_percent saturated_hemoglobin_percent_max
saturated_hemoglobin_percent_min speed speed_1s stance_time
stance_time_balance stance_time_percent step_length stroke_type temperature
time128 time_from_course time_to_surface timestamp total_cycles
total_hemoglobin_conc total_hemoglobin_conc_max total_hemoglobin_conc_min
vertical_oscillation vertical_ratio vertical_speed volume_sac zone
```

**There is no lean angle, roll, pitch, yaw, gyroscope, engine-RPM or sound-level
field. There is no heading or course field either.** A `gh search code` for
`lean_angle` and for `absolute_lean` across the entire repository returns **zero
hits**.

*(A web summary claimed `absolute_lean_angle`, field 108, exists in the record
message. That claim is **false** for SDK 21.217.0. It is recorded here so nobody
re-derives it from the same page.)*

Mapping her `ORDER` onto FIT:

**Native home (11):**

| Her channel | FIT `record` field | Note |
|---|---|---|
| `t` | `timestamp` | |
| `lat` | `position_lat` | semicircles, needs conversion |
| `lon` | `position_long` | semicircles |
| `speed_ms` | `speed` / `enhanced_speed` | |
| `alt_m` | `altitude` / `enhanced_altitude` | |
| `acc_m` | `gps_accuracy` | |
| `grade_pct` | `grade` | |
| `dist_m` | `distance` | |
| `pressure_hpa` | `absolute_pressure` | Pa, so ×100 |
| `climb_mps` | `vertical_speed` | |
| `battery_pct` | `battery_soc` | |

**Needs a Developer Data Field (18):** `heading`, `g_long`, `yaw_dps`, `g_lat`,
`lean_deg`, `g_vert`, `g_peak`, `g_bump`, `tilt_deg`, `pitch_deg`,
`yaw_gyro_dps`, `gyro_peak`, `alt_baro_m`, `compass_deg`, `loudness_db`,
`engine_db`, `exhaust_hz`, `rpm_est`.

And `rpm_est` cannot borrow `cadence`: `cadence` is a `UINT8` in rpm (max 254)
and `cadence256` is a `UINT16` at scale 256 (max ≈255.99 rpm). Her range is
600–12,000 rpm. Neither fits.

**So the honest answer to the sub-question: FIT would not make her lose data,
but it would make 18 of 29 channels invisible to every tool that reads FIT.**
Developer Data Fields are the officially sanctioned mechanism — introduced in FIT
Protocol V2, declared by `developer_data_id` and `field_description` messages,
self-describing, and printed by FitCSVTool. But 18 of them means 18
`field_description` messages, and no standard tool knows what any of them mean.
Asked directly about overlaying a recorded lean-angle field, the Garmin forum
answer was *"What you see is what you get"* — you cannot add other data types,
file a feature request. Garmin Connect generally requires the contributing app to
be **published** before it will display custom-field data at all
(`fit_contributions.json` inside the `.IQ` file carries the presentation
metadata), which a personal app will never be.

**Crash safety: strictly worse than JSONL.** A FIT file is header + records +
a 2-byte file CRC. The 14-byte header carries **the size of the data that
follows** — an encoder reserves that field and fills it in at the end. The SDK's
integrity check requires that total file size equals header size + data size +
CRC size *and* that the recomputed CRC match the stored one. A battery death
mid-ride therefore produces a file that **fails the integrity check**. The data
records themselves survive, because they are self-contained, so recovery is
possible — the documented procedure is to re-emit a clean header, a valid
`file_id`, at least one synthesised `session` and `activity`, then a fresh data
size and CRC. That is a repair tool she would have to write. `read_log` already
does the equivalent in three lines by skipping the torn line.

**Ecosystem evidence that this is not the format for this job:** RaceChrono, the
closest commercial analogue, exports **.vbo (VBOX)** for Circuit Tools and CSV,
and treats FIT as an **import** source (pulling heart rate from a `.fit` while
using GoPro GPS/video) — not an export target. Its lean angle is a *calculated*
channel, from the IMU when the phone is on a holder or from GPS when it is not:
the same two methods, with the same trade-off, as
`00-already-checked.md` records for Ride-Track versus Navi.

Kotlin/Android FIT code in the wild is close to nonexistent: the only Kotlin
result the searches surfaced was `bltavares/malhado` (imports `.fit` into Health
Connect, 0 stars).

### GPX — one concrete improvement available today

Her `to_gpx` uses `xmlns:navi="https://alola.lol/navi/1"` and emits
`<navi:speed_ms>`, `<navi:lean_deg>` and the rest. Honest and lossless, and no
third-party tool reads a word of it.

Two findings:

1. **Garmin's `TrackPointExtension` v2** —
   `http://www.garmin.com/xmlschemas/TrackPointExtension/v2` — carries **`speed`
   and `course` natively**. v1 does not (sources disagree; the validation-driven
   Locus Map thread says v2, so v2 is the safe target). So `speed_ms` and
   `heading` could travel in a namespace that BaseCamp, Strava and everything
   else already understands, with the `navi:` namespace kept for the 18 channels
   that genuinely have nowhere standard to live. Two gotchas: the extension is an
   XSD **sequence**, so element order matters (`atemp, hr, cad, speed, course`);
   and Garmin Connect emits these with an `ns3:` prefix, so any reader must match
   on namespace URI + local name, never on the prefix string. OsmAnd shipped a
   bug for exactly this (osmand#6701: a bare unnamespaced `<speed>` that BaseCamp
   refused).
2. **Her server discards all of it anyway** — see "the two facts" at the top.
   `parse_gpx` reads four things per point and `rides_read` recomputes speed by
   haversine. Fixing `parse_gpx` to read the extensions is worth more than any
   format change on the phone.

---

## (c) Sync to her server

### Summary table

| Option | Licence of the part she runs | Needs a server she does not run? | Postgres→SQLite? | New daemons | Verdict |
|---|---|---|---|---|---|
| **Her outbox + WorkManager** | hers + androidx Apache-2.0 ✅ | **no** | n/a | **0** | In |
| PowerSync | client Kotlin SDK: **NO LICENCE FILE** ❌; service: **FSL-1.1-ALv2** ⚠️ | **yes** — Service + Postgres/Mongo/MySQL + Mongo for bucket storage | yes, its whole pitch | ~3 | **Ruled out** |
| ElectricSQL | Apache-2.0 ✅ | **yes** — Postgres + Electric | **no longer** — read-path only, PGlite not SQLite | 2 | Out |
| CouchDB / Couchbase repl. | CouchDB Apache-2.0 (unverified); Couchbase CE Agreement ⚠️ | **yes** — a server speaking the replication protocol | no | 1–2 | Out |
| Automerge (CRDT) | core MIT ✅; **automerge-java: NO LICENCE FILE** ❌ | no | no | 0 | **Ruled out**, and unnecessary |
| Turso / libSQL | MIT ✅ | **yes** — Turso Cloud or libsql-server | no | 1 | Out |

### Her own outbox + WorkManager

- Licence: hers, plus `androidx.work` Apache-2.0.
- **Needs a server she does not run: no.** It already talks to the 16k-line
  `http.server` she has.
- **Operational weight: zero new processes.** Nothing to install on the Pi.
- It is also, on the evidence, already right. `store.py` implements: WAL,
  `synchronous=FULL`, one `BEGIN IMMEDIATE` per mutation so the queued write and
  its local effect commit together, `coalesce_key` with a **unique partial index**
  (`WHERE coalesce_key IS NOT NULL AND state = 'pending'`) enforcing one pending
  write per key in the database rather than in everyone's memory, `base_rev` for
  opt-in conflict detection, `Unreachable` split from every other failure so a
  week with no signal costs the queue nothing, **per-path** head-of-line blocking
  rather than global, park-not-delete after `max_attempts`, and backoff
  5s→10s→20s… capped at 300s.
- The independent 2026 write-ups on offline-first Android describe that exact
  architecture and name three things it does not yet have:
  1. **Idempotency keys** on the append-style endpoints. Her own docstring
     already identifies this as the one unfixed hole: *"a crash in the gap
     between 'the server accepted it' and 'the row is deleted' duplicates the
     record."* The standard fix is a client-generated key the server stores
     alongside the response, returning the stored response on a repeat. For
     `/api/rides/upload` this is nearly free: the filename is already unique per
     ride, so the server refusing (or no-op'ing) a duplicate filename closes it.
  2. **Client-generated stable ids**, so a retried create cannot become two rows.
  3. **Tombstones** for deletes.
  Also called out as the anti-pattern she has already avoided: keeping pending
  requests in memory behind an OkHttp retry interceptor, where a force-close
  loses them forever.
- **WorkManager's known problems, and why they do not bite here.** WorkManager
  guarantees *eventual* execution once constraints are met, not timing: it
  persists work in its own SQLite database, survives process death and reboot,
  and re-registers after boot — but Doze defers it to maintenance windows, and
  OEM battery managers kill background work, block `BOOT_COMPLETED`, ignore
  exact alarms and delay broadcasts. One developer's conclusion after a year on a
  24/7 monitoring app: never trust a single mechanism, because every Android
  background API has an OEM that breaks it.
  For her case this is harmless. A ride reaching the server twenty minutes late
  on home wifi is fine; the outbox survives whatever the OEM does, because
  durability lives in her SQLite file and not in WorkManager. A
  `NetworkType.UNMETERED` constraint plus a manual "sync now" — which
  `api.sync()` already is — covers it. The trap to avoid is *polling*, which
  fails under Doze and in the Restricted bucket.
- Sizing: a four-hour ride at 1 Hz is roughly 14,400 samples. `ride_upload`
  already uses `timeout=120` and is correctly **never coalesced** — *"each
  recording is its own file and its own afternoon."*

### PowerSync — ruled out on licence, twice over

**The client SDK she would compile into her app has no licence file.**

`powersync-ja/powersync-kotlin`, verified three ways:

- `gh repo view` → `"licenseInfo": null`
- `gh api repos/powersync-ja/powersync-kotlin/license` → `404 Not Found`
- recursive tree search of `main` for `licen|copying|notice` → **nothing**
- README grep for "licen" → **nothing**

127 stars, v1.15.1 on 2026-09-07, last push 2026-09-23. Actively developed and
unlicensed. By her rule — no licence file means all rights reserved by default —
**that is a hard disqualifier**, exactly as it was for Ride-Track in
`00-already-checked.md`.

**And the licence of the part she would self-host is a trap.**
`powersync-ja/powersync-service` reports "Other"; the file reads:

> **Functional Source License, Version 1.1, ALv2 Future License**
> Abbreviation: FSL-1.1-ALv2
> Copyright 2023-2026 Journey Mobile, Inc.

Source-available, not open source. A "Permitted Purpose is any purpose other
than a Competing Use", and permitted purposes explicitly include *"for your
internal use and access"* — so she *may* run it for herself — with an
irrevocable Apache-2.0 grant taking effect two years after each version ships.
The marketing phrase "Open Edition… free, source-available, self-hosted" is
doing real work there. Related repos are inconsistent:
`powersync-sqlite-core` is Apache-2.0 ✅ and `powersync-js` is Apache-2.0 ✅,
while the Kotlin client has nothing.

**Needs a server she does not run: yes, and more than one.**

- The **PowerSync Service** is mandatory — it is the component that replicates.
- It needs a **source database** it can read changes from: Postgres via logical
  replication (`wal_level = logical`, a publication named `powersync`, a
  replication role), MongoDB change streams, or MySQL binlog. **She has none of
  these.** Rides are `.gpx` files in a directory.
- The Service itself stores sync-bucket state and operation history in
  **MongoDB** by default, regardless of the source database (a Postgres
  bucket-storage option exists). It maintains its own monotonic `op_id` because
  Postgres LSNs are not reliably monotonic across overlapping transactions.
- Logical replication is downstream-only, so **writes still need a backend API
  she writes herself**, plus a token endpoint for the client.

**Postgres→SQLite: yes**, and that is genuinely what it is for. It is simply
answering a question she does not have.

**Weight for one phone pushing rides to one file server on home wifi:** the
PowerSync Service, plus MongoDB, plus a Postgres that does not exist, plus a
backend connector, plus a token endpoint — to replace an HTTP POST that already
works offline.

The Kotlin SDK is also worth knowing for its own caveats: it loads a SQLite
extension and sets up JNI bindings per platform; a single SQLite database file
only; as of v1.13.0 the Room integration is in beta and native x86_64 Apple
targets were dropped.

### ElectricSQL — out

- <https://github.com/electric-sql/electric> — **Apache-2.0 ✅** (verified),
  10,373 stars, pushed 2026-09-09. Licence is clean; the product is not what the
  section describes.
- **The 2024 rewrite changed what it is.** The original ElectricSQL was
  bidirectional CRDT-based active-active sync between Postgres and on-device
  SQLite — which is what `CHOICES.md` §9 is describing. Old release posts now
  carry deprecation notices pointing at "Electric Next": Postgres-centric,
  shape-based, HTTP, **read-path only**. Electric's own position is that offline
  writes must be implemented separately, by queueing mutations locally and
  replaying them against your API — i.e. **you still write the outbox she already
  has.**
- SQLite is no longer the centrepiece; PGlite is the de facto local store for new
  deployments, with SQLite supported through adapters.
- **Needs a server she does not run: yes** — Postgres plus the Electric service.
- **No Kotlin or Android client.**
- No presence, no OT, no collaborative-editing primitives.

### CouchDB / Couchbase replication — out

**CouchDB.** The PouchDB FAQ is unambiguous:

> No, your backend needs to speak the CouchDB replication protocol.

Every document must carry a `_rev`, and replication works by sequence numbers,
not timestamps. Her backend is `http.server` serving GPX files. Adopting this is
**a server rewrite, not a sync library**. Apache CouchDB itself is Apache-2.0
(**unverified — I did not open its LICENSE**). Conflicts are not resolved for
you: both revisions are stored under the same id and the application decides.
CouchDB also has no document-level access control; the usual workaround is a
database per user.

**Couchbase.** Replication needs **Sync Gateway** as well as Couchbase Lite.
Community Edition Sync Gateway is free to self-host under the Couchbase
Community Edition License (not an OSI licence); Enterprise is free for
development and testing and requires a paid subscription in production. Delta
sync, inter-Sync-Gateway replication and config groups have historically been
Enterprise-only (**current split unverified**), and Peer-to-Peer Sync is
Enterprise-only. Official CE Dockerfiles in `couchbase/docker` reach 3.1.0 /
3.0.3 in what the search surfaced; **current CE version availability
unverified**. Public port 4984, admin 4985.

Either route means a new daemon on the Pi and a document model for a numeric
append stream. Out on both the server requirement and the weight.

### CRDTs (Automerge) — ruled out on licence, and not needed anyway

- Core: <https://github.com/automerge/automerge> — **MIT ✅**, 6,623 stars,
  v3.5.0 (js) on 2026-09-16, pushed 2026-09-25. Healthy.
- **The JVM/Android binding has no licence file.**
  `automerge/automerge-java`, verified: `licenseInfo: null`; recursive tree
  search of `main` and `master` for `licen|copying|notice` → **nothing**; README
  has no licence mention. 48 stars, **v0.0.9** on 2026-04-22 — and the version
  number is honest about maturity. **Hard disqualifier.**
- **No Kotlin Multiplatform support.** automerge-java issue #2 asks for it and is
  unresolved. The Automerge 2.0 announcement says mobile developers *"will need
  to bind directly via C"*. So the KMP path is: build `automerge-c` for each
  Android ABI with `cargo-ndk`, then wrap it with UniFFI. The core being MIT
  makes that legally clean — it is just a substantial amount of work.
- Alternatives recorded:
  - [CharlieTap/synk](https://github.com/CharlieTap/synk) — a KMP CRDT library,
    **Apache-2.0 ✅**, 103 stars, but **last release 0.38 on 2023-03-22 and last
    push 2023-11-22. Abandoned.**
  - [loro-dev/loro](https://github.com/loro-dev/loro) — MIT ✅, 6,170 stars,
    active (2026-09-21), but no Kotlin binding surfaced.
  - [y-crdt/y-crdt](https://github.com/y-crdt/y-crdt) — licence "Other"
    (**unverified**), 2,165 stars, v0.28.0 on 2026-09-17.
- **And the deeper point: she does not have the problem a CRDT solves.** A CRDT
  answers "two devices edited the same thing and both edits must survive". A ride
  log is **append-only and single-writer** — the phone is the only thing that ever
  writes a sample, and a ride is immutable once `stop()` returns. There is no
  conflict to merge.
  The one real conflict in the system is the one `store.py` already documents in
  detail: whole-collection FoodZen/journal/garage writes racing a browser tab,
  with last-flush-wins at whole-collection granularity. **A CRDT cannot fix that
  either**, because the wire format is literally "here is the whole list" and the
  server keeps no history. As the docstring says: *"until it exists the app
  cannot merge no matter how clever it is here."* That is a server change, and it
  is outside section 9.

### Turso / libSQL — surfaced, recorded, out

Not on her list; found via the awesome-local-first sweep, recorded so it is not
rediscovered later.

- <https://github.com/tursodatabase/libsql> — **MIT ✅**, 17,237 stars, pushed
  2026-09-16.
- <https://github.com/tursodatabase/libsql-android> — **MIT ✅**, but **22 stars,
  no releases, last push 2025-07-07**, flagged technical preview, docs say full
  Kotlin support is "coming".
- Offline writes exist now (`SyncStrategy.FAIL_ON_CONFLICT`, more planned), and
  embedded replicas give a local SQLite file that syncs.
- **Needs a server she does not run: yes** — Turso Cloud or a `libsql-server`.
- Turso has since been rearchitected ("databases are now files rather than
  processes") and *"the legacy libSQL SDKs have been replaced by new
  Turso-native SDKs"* — so the `tech.turso.libsql:libsql:0.1.0` artifact may be
  superseded (**unverified**).

---

## Claims I could not confirm, or found false

Recorded so nothing here has to be re-derived, and so a wrong number does not
get quoted back later.

| Claim | Source | Status |
|---|---|---|
| "Room 3.0 released March 2026, makes KMP first-class alongside JS and WASM" | docs.bswen.com blog (page itself returned HTTP 403; claim came via search summary) | **False.** The official androidx releases page says the latest Room is **2.8.5, 2026-09-09**. There is no Room 3.0. |
| "`absolute_lean_angle` (field 108) exists in the FIT record message" | search summary of a FIT-reference site | **False** for SDK 21.217.0. I enumerated all 84 `record` fields from `RecordMesg.java` and `gh search code` for `lean_angle`/`absolute_lean` across `garmin/fit-java-sdk` returns zero hits. |
| ObjectBox Binary Licence exact terms | `docs.objectbox.io/faq/objectbox-binary-license` | **Unverified** — 404. Only the FAQ's summary line ("Native Libraries → ObjectBox Binary Licence") is confirmed. |
| ObjectBox Gradle plugin is GPLv3 | breezy-weather#677 issue thread | **Unverified** (third party). |
| ObjectBox crash / power-loss durability guarantees | — | **Unverified.** No quotable statement found. Disqualifying on its own for this use. |
| `couchbase-lite-core` licence (reported "Other") | GitHub contents API returned nothing decodable | **Unverified.** |
| Couchbase CE Agreement full clause list; current CE Sync Gateway version and CE/EE feature split | couchbase.com summary via search | **Partial** — I did not open the full agreement. |
| Apache CouchDB licence (Apache-2.0) | general knowledge | **Unverified** — LICENSE not opened. |
| "~70k–100k SQLite write transactions/sec under WAL+NORMAL" | one blog benchmark, one machine | **Unverified for a phone's eMMC.** Directionally irrelevant either way: her rate is 1 Hz. |
| "Under `synchronous=NORMAL` a power failure can lose every transaction in the WAL" | SQLite **forum** post attributed to Dan Kennedy | Consistent with sqlite.org/wal.html and the Android doc, but it is a forum post, not documentation. |
| ObjectBox CRUD benchmark figures | objectbox.io, vendor-run | **Vendor-run**, tested against SQLite 3.22.0 / Room 2.3.0 / Realm 10.6.1 (old), SQLDelight absent. Directional only. |
| `parquet-hadoop` transitive size / APK impact on Android | — | **Unverified — not measured.** The dependency chain is documented; the megabytes are not. |
| Turso-native Kotlin SDK status after the rearchitecture | turso.tech | **Unverified.** |
| `garmin/fit-python-sdk` licence file location | `gh` reports `licenseInfo: null` | **Unverified.** The Java, C and JavaScript SDKs all carry the same FIT EULA, so the Python one almost certainly does too, but I did not open it. |
| TrackPointExtension: is `speed` in v1 or only v2? | Garmin schema pages vs Locus Map thread | Sources **disagree**. The validation-driven thread says v2, so v2 is the safe target. |

---

## Full search log

Every search run, the results considered, and one line on why each is in or out.
"Opened" means fetched and read, not judged from the title. Repository facts
marked ✔gh were taken from the GitHub API rather than from a page.

### (a) On-phone store

**a1. `SQLDelight android Kotlin Multiplatform SQLite`** *(the §9 "search:" line)*

| # | Result | In / out |
|---|---|---|
| 1 | medium.com — "From SQLDelight to Room in KMP Projects" | In — establishes that the migration goes both ways and is non-trivial. |
| 2 | kotlinlang.org — official Ktor + SQLDelight KMP tutorial | **Opened.** In — confirms per-platform drivers (`android-driver`, `native-driver`, `sqlite-driver`) and the iOS linker flag gotcha. |
| 3 | touchlab.co — "SQLite/SQLDelight Kotlin Multiplatform" | In — SQLiter is the Kotlin/Native driver under SQLDelight on Apple/Windows. |
| 4 | sqldelight.github.io — official docs (2.0.2 / 2.3.2) | In — platform and dialect matrix. |
| 5 | **github.com/sqldelight/sqldelight** | **Opened + ✔gh.** In — Apache-2.0, 6,886 stars, 2.4.0 on 2026-09-18, Gradle plugin required. |

**a2. `Room vs SQLDelight Kotlin Multiplatform 2026 which to choose`**

| # | Result | In / out |
|---|---|---|
| 1 | medium.com — "KMP Part 4: Room Multiplatform vs SQLDelight" | In — annotation-first vs SQL-first framing. |
| 2 | proandroiddev.com — "Comparing Realm, SQLDelight, and Room" (2025) | In — notes Room and SQLDelight both ride the bundled SQLite while Realm/ObjectBox ship their own engines (app size). |
| 3 | **docs.bswen.com — "Room vs SQLDelight for KMP in 2026"** | **Out — fetch returned HTTP 403, and its central claim (Room 3.0, March 2026) is contradicted by the official releases page.** Recorded in the false-claims table. |
| 4 | medium.com — "Database Solutions for KMP/CMP" | In, weakly — migrations are Room's advantage, explicit SQL is SQLDelight's. |
| 5 | stackshare "android room vs sqlfluff/sqlgate/sql tabs" | Out — keyword-matched noise, not comparable products. |

**a3. `ObjectBox Android core closed source proprietary binary license`**

| # | Result | In / out |
|---|---|---|
| 1 | **objectbox.io/faq** | **Opened.** In, decisive — Java bindings Apache-2.0, **Native Libraries under the ObjectBox Binary Licence**; "if you need access to the core, reach out to us individually". |
| 2 | github.com/breezy-weather/breezy-weather#677 | In as context — F-Droid removals over the proprietary `.so`; claims the Gradle plugin is GPLv3 (unverified). |
| 3 | objectbox.io OSS licensing-matrix PDF | Noted, not opened. |
| 4 | mvnrepository `io.objectbox:objectbox-android` | In — the Android artifact is listed Apache-2.0, which is exactly the half-truth that makes this a trap. |
| 5 | **github.com/objectbox/objectbox-java** | **✔gh.** In — Apache-2.0, `LICENSE.txt` present, 4,624 stars, 5.4.2 on 2026-05-06. |

**a4. `Couchbase Lite Android community edition license cost enterprise`**

| # | Result | In / out |
|---|---|---|
| 1 | couchbase.com/community-license-agreement | In — CE is free, "as is", excludes XDCR and other excluded features; breach obliges the EE fee. |
| 2 | docs.couchbase.com/mobile/3.1.8 & 3.2.2 — "Enterprise Edition License Agreement" | In — the docs for the mobile SDK are themselves published under the EE agreement. |
| 3 | couchbase.com/forums "Price of Couchbase Lite Enterprise Edition" | In — no public price; P2P Sync is the usual reason people need EE. |
| 4 | couchbase.com/pricing | In — mobile subscription is quote-only. |
| 5 | docker-library/docs couchbase/license.md | Out — server-side, not mobile. |

**a5. `SpatiaLite Android library gradle spatialite-android maintained`**

| # | Result | In / out |
|---|---|---|
| 1 | **dalgarins/android-spatialite** | **✔gh + tree search. OUT — no licence file.** 2.2.3 on 2026-09-25, 7 stars; SQLite 3.49.1, Spatialite 4.3.0a, AGP 9/NDK r27d. Technically the best-maintained fork and still disqualified. |
| 2 | **svkostadinoff/android-spatialite** (was sevar83) | **✔gh. OUT — no licence file**, and dead: last push 2019-11-15. |
| 3 | **Raintee-CN/SpatiaTile-Android** | **✔gh.** Apache-2.0 ✅ but 1 star, 0 forks, no release, first push this month. Out on maturity. |
| 4 | **iulian0512/android_spatialite** | **✔gh. OUT — no licence file.** 8 stars. |
| 5 | gaia-gis.it — official SpatiaLite Android tutorial | In as background; upstream licensing unverified. |

**a6. `DuckDB Android ARM build embed mobile app`**

| # | Result | In / out |
|---|---|---|
| 1 | **duckdb.org/docs/current/dev/building/android** | **Opened.** In, decisive — *"experimental support… use the latest `main` branch instead of the stable versions"*; no prebuilt artifact or Maven package. |
| 2 | duckdb/duckdb#13337 "extension on android (arm)" | In — extension downloads fail for `linux_arm64_android`, marked expected; statically link instead. |
| 3 | duckdb/duckdb#12550 "Build Android Binaries" | In — `armeabi-v7a` and `arm64-v8a` in CI since mid-2024, `libduckdb.so` only. |
| 4 | duckdb/duckdb#1307 "Provide Android Packages" | In — the JDBC artifact fails at runtime with `NoClassDefFoundError` on `java.nio` `FileAttribute`. |
| 5 | duckdb.org unofficial-and-unsupported-platforms | **Fetch returned only a redirect notice** — superseded by result 1. |

**a7. `SQLite WAL synchronous NORMAL FULL durability power loss Android telemetry insert rate`**

| # | Result | In / out |
|---|---|---|
| 1 | **sqlite.org/wal.html** | **Opened.** In, decisive — the FULL-vs-NORMAL sync sentence and "transactions are no longer durable and might rollback following a power failure or hard reset". |
| 2 | **developer.android.com SQLite performance best practices** | **Opened.** In, decisive — recommends NORMAL and states plainly that power loss can lose committed data while an app crash cannot. |
| 3 | sqlite.org/forum — "Process vs OS level durability (sync=NORMAL, WAL)" | In with a caveat — the "you can lose the whole WAL" detail; a forum post, not docs. |
| 4 | powersync.com/blog — "SQLite Optimizations For Ultra High-Performance" | In — the "secondary indexes can cost up to 5x insert performance" figure. |
| 5 | avi.im / agwa.name — "SQLite commits are not durable under default settings", "SQLite's Durability Settings are a Mess" | In as corroboration of the same tradeoff. |

**a8. `Android database benchmark insert performance Room ObjectBox Realm SQLDelight comparison`**

| # | Result | In / out |
|---|---|---|
| 1 | greenrobot.org / objectbox.io CRUD benchmarks | In, heavily caveated — **vendor-run**, Galaxy S9+, SQLite 3.22.0 / Room 2.3.0 / Realm 10.6.1, **SQLDelight absent**. |
| 2 | github.com/objectbox/objectbox-performance | In — a runnable harness; also vendor-owned and SQLDelight-free. |
| 3 | github.com/AlexeyZatsepin/Android-ORM-benchmark | In — independent harness; warns about lazy loading skewing reads. |
| 4 | proandroiddev.com — Realm / SQLDelight / Room (2025) | In — qualitative only; notes ObjectBox does not support KMP. |
| 5 | redwerk.com — SQLite vs Realm vs ObjectBox | Out — no methodology given. |

**Conclusion carried forward: there is no published, independent, current
benchmark of write throughput across these four. Both SQLite-backed options are
the same engine with generated glue; the honest answer is that at 1 Hz none of
this is decided by throughput.**

**a9. `ObjectBox Kotlin Multiplatform support roadmap issue`**

| # | Result | In / out |
|---|---|---|
| 1 | **objectbox-java#601** | In, decisive — open since Nov 2018, "enhancement" + "help wanted", 77+ 👍, no milestone. **The KMP strike.** |
| 2 | docs.objectbox.io/kotlin-support | In — "full Kotlin support for Android", i.e. Android, not Kotlin/Native. |
| 3 | docs.objectbox.io/faq | In — supported languages list has no KMP entry. |
| 4 | objectbox-docs/kotlin-support.md | In — same. |
| 5 | scribd "Kotlin Roadmap" | Out — unrelated. |

**a10. `Kotbase Couchbase Lite Kotlin Multiplatform`**

| # | Result | In / out |
|---|---|---|
| 1 | **github.com/jeffdgr8/kotbase** | **✔gh.** In — Apache-2.0 ✅, 124 stars, 3.2.4-1.2.0 on 2025-10-25, pushed 2026-07-11. One maintainer. |
| 2 | kotbase.dev — platforms / installation | In — CE artifact `dev.kotbase:couchbase-lite`, EE `…-ee` needs the Couchbase Maven repo and a production licence. |
| 3 | couchbase.com/forums announcement threads | In — Couchbase acknowledges it; it is not a Couchbase product. |
| 4 | MyUNiDAYS/couchbaselite-kotlin-sdk | Out — older, less complete alternative. |
| 5 | couchbase.com/forums "CBL for a KMP project" | In — confirms no first-party KMP SDK exists. |

**a11. `androidx.sqlite bundled SQLite driver KMP use without Room raw SQL`**

| # | Result | In / out |
|---|---|---|
| 1 | **developer.android.com/kotlin/multiplatform/sqlite** | In, decisive — the three driver interfaces, `sqlite-bundled` vs `sqlite-framework`, KMP from 2.5.0, **no Room required**. |
| 2 | developer.android.com/jetpack/androidx/releases/sqlite | In — `addExtension`, the active-transaction API, minSdk 21→23, watchOS/tvOS. |
| 3 | eygraber/sqldelight-androidx-driver | **✔gh.** In — MIT ✅, 96 stars, 0.2.2 on 2026-08-05; SQLDelight on the androidx driver. |
| 4 | wsoh.released.at — "BundledSQLiteDriver, a new look at SQLite" | In — bundled vs system SQLite, and the `-lsqlite3` linker conflict. |
| 5 | SQLiteNow (mobiletoly) | Noted — another thin generator, Gradle-plugin only. |

**a12. `SQLDelight performance overhead compared to raw SQLite cursor Android`**

| # | Result | In / out |
|---|---|---|
| 1 | **touchlab/SQLiter#39** | In, caveated — C++ ~5× faster inserts / ~7× selects vs KMM+SQLDelight at 100k rows, traced to **SQLiter, the iOS driver**. Does not transfer to Android. |
| 2 | Darkyenus/sqlitelite | In — argues stock Android bindings add API overhead; many-small-queries workloads benefit most (numbers unverified). |
| 3 | medium.com/lalafo-engineering — "Tuning your SQLite with SQLDelight & SQLBrite" | In — practical tuning, no head-to-head. |
| 4 | greenDAO slides — FastCursor | In — a custom cursor gave ~30%, and up to 117% with other changes; i.e. the driver layer is where overhead lives. |
| 5 | Xamarin SQLite performance study | In — reflection ~65% of read cost, automapper ~5.2%. SQLDelight uses **no** reflection, so it sits at the low end. |

### (b) Ride log format

**b1. `Garmin FIT SDK developer fields custom data fields lean angle`**

| # | Result | In / out |
|---|---|---|
| 1 | developer.garmin.com/fit | In — Developer Data Fields are the sanctioned mechanism; existing Profile fields must not be redefined. |
| 2 | **forums.garmin.com — "Is it possible to overlay a custom Fit Data Field onto the Map?"** | In, decisive — asked about a recorded **lean angle** field, the answer was *"What you see is what you get."* |
| 3 | forums.garmin.com — "Find Developer Field in FIT File" | In — `field_description` messages; FitCSVTool prints developer data. |
| 4 | **github.com/garmin/fit-c-sdk**, **fit-javascript-sdk** | **✔gh.** In — official SDKs, 21.217.0 on 2026-09-22, licence "Other". |
| 5 | muktihari/fit (Go) | **✔gh.** In — BSD-3-Clause ✅, 181 stars, v0.28.4 on 2026-09-11; proof the format is implementable without Garmin's SDK, but Go. |

**b2. `Parquet Java Android write streaming append rows library size`**

| # | Result | In / out |
|---|---|---|
| 1 | baeldung.com — "Introduction to Java Parquet (Parquet-MR)" | In — `parquet-avro` + `parquet-hadoop`, i.e. the Hadoop chain lands in the APK. |
| 2 | learn.microsoft.com Q&A — "Append row to existing parquet file" | In — appending produced a new file each interval, 12 files/hour; compaction is the workaround. |
| 3 | apcamargo/parquet-stream-writer | In — the shape of a streaming writer: batches, shard rotation on a byte threshold. |
| 4 | Parquet.NET (mobile-capable) | Out — .NET, not her stack. |
| 5 | kylebarron/parquet-wasm#542 | In — same conclusion from the WASM side. |

**b3. `Parquet file cannot be appended incrementally footer written at close crash`**

| # | Result | In / out |
|---|---|---|
| 1 | **mail-archive.com — Arrow dev list on appending / "Corrupt footer"** | In, decisive — Parquet files are immutable, the footer is at the end and written on close. |
| 2 | **mail-archive.com — "[I] Enable writing of Parquet footer without closing file [arrow]"** | In, decisive — the streaming-durability capability she would need is an **open request**, not a feature. |
| 3 | github.com/…/wadjet#972 "writes after Close… corrupt the finalized file" | In — `Close` is not idempotent; a second footer invalidates the magic. |
| 4 | xitongsys/parquet-go#103 "Incrementally loaded parquet files" | In — same footer constraint in another language. |
| 5 | parquetkit.com / viewparquet.com | In — confirms new-file-plus-compaction as the standard pattern, and that one row per row group destroys the footer and statistics. |

**b4. `GPX speed extension gpxtpx TrackPointExtension Garmin speed not in GPX 1.1 schema`**

| # | Result | In / out |
|---|---|---|
| 1 | **www8.garmin.com/xmlschemas/TrackPointExtensionv2.xsd** | In, decisive — *"data fields that cannot be represented in track points in GPX 1.1 instances"*; root element is a child of `trkpt/extensions`. |
| 2 | **help.locusmap.eu — "Invalid GPX 1.1 export"** | In, decisive — `speed` and `course` are **v2**; the extension is a sequence so order matters (atemp, hr, cad, speed, course). |
| 3 | **osmandapp/OsmAnd#6701** | In — a bare unnamespaced `<speed>` made BaseCamp refuse the file. The exact mistake to avoid. |
| 4 | gpxanalyzer.com — sensor data in GPX | In — Garmin Connect emits `ns3:` prefixes; parse by namespace URI, never prefix. |
| 5 | logiqx.github.io/gps-wizard, en.wikipedia GPX | In — v1 vs v2 feature split; Garmin only shipped speed/course support around 2015. |

**b5. `SQLite as logging format append-only sensor log vs JSON lines crash safe`**

| # | Result | In / out |
|---|---|---|
| 1 | dev.to — "Why SQLite is Perfect for Edge Device Data Logging" | In — resist normalisation; one flat table indexed on time; batch in one transaction. |
| 2 | arxiv 2604.10842 — "Resilient Write" | In as the counter-case — deliberately chose append-only `.jsonl` over SQLite for human readability, diffability and zero dependencies. |
| 3 | ricardoanderegg.com — "Using SQLite for logging and ad-hoc profiling" | In — the honest caveat: SQLite gives no append-only guarantee, only code discipline. |
| 4 | tersesystems.com — "Ad-hoc structured log analysis with SQLite and DuckDB" | In, and it is the "you may not have to choose" answer: keep JSONL, query it later with DuckDB's JSON extension or `sqlite-lines`. |
| 5 | dev.to — JSON→SQLite failure library | In — concurrent writes corrupted a JSON *document*; not applicable to append-mode JSONL, and recorded so it is not over-read. |

**b6. `motorcycle telemetry FIT file lean angle RaceChrono export format custom channels`**

| # | Result | In / out |
|---|---|---|
| 1 | **racechrono.com changelogs v10.2.3 (Android / iOS)** | In, decisive — exports **.vbo** and CSV; FIT appears as an **import** source. |
| 2 | **racechrono.com/forum "Lean angle channel on v8.0"** | In — lean angle is a *calculated* channel, from IMU if the phone is on a holder, else from GPS. The same trade-off as Ride-Track vs Navi. |
| 3 | racechrono.com/forum "Video Export Lean Angle gauge not working" | In as a quality note; not reproducible in 8.0.7. |
| 4 | racechrono custom-channel docs | In — custom channels are OBD-II/CAN PIDs, plus fast/slow channel rates. |
| 5 | goprotelemetryextractor.com — Telemetry Overlay | Out for format purposes; relevant only as a video-overlay path. |

**b7. `FIT file header data size CRC truncated incomplete file recover partial activity`**

| # | Result | In / out |
|---|---|---|
| 1 | **fitfilelab.com / datanumen FIT repair guides** | In, decisive — truncation after a battery death is the normal failure; the closing session message and CRC are missing, and strict parsers refuse the file. |
| 2 | **fitfileeditor.com/skill** | **Opened.** In, decisive — header + records + 2-byte CRC; integrity check is `total size == header + data size + CRC`; **reserve the data size in the header and fill it in at the end**. |
| 3 | garmin/fit-python-sdk README | In — `Read` returns messages *and* a list of errors, which is how you salvage a torn file. |
| 4 | rseki-sonix/fit-ios-broken-header | In — a real repo of "wrong data size and wrong CRC" files. |
| 5 | openzigs/onyourleft#31 "Implement the FIT activity file encoder" | In — the encode order: reserve data size, definition before first data message, then CRC. |

**b8. `FIT profile record message fields list speed cadence power altitude no lean angle field`**

| # | Result | In / out |
|---|---|---|
| 1 | **garmin/fit-java-sdk `RecordMesg.java` (downloaded and read)** | In, decisive — **all 84 `record` fields enumerated; no lean/roll/pitch/yaw/gyro/engine-rpm/heading.** `gh search code` for `lean_angle` and `absolute_lean` → zero hits. |
| 2 | hexdocs.pm/fit_decoder — FIT record fields reference | In — corroborates the core field numbers. |
| 3 | dgaff/fitsdk `RecordMesg.java` (mirror) | In — same field numbering. |
| 4 | peateasea.de — Perl FIT analysis | In — its 49-field extraction is an older profile; useful to show the profile grows. |
| 5 | **fitfileeditor.com/skill** (re-read for this question) | In — "does not mention native fields for lean angle, roll, or engine RPM… designed primarily for athletic activities rather than motorsports". |

**b9. `JSON Lines ndjson telemetry log format advantages streaming append gzip size overhead`**

| # | Result | In / out |
|---|---|---|
| 1 | ndjson.com/advantages + jsonlines.org | In — append is one file write; a stream is valid at 1 line or 1 billion; truncation tolerance; `.jsonl.gz` / `.jsonl.bz2` recommended by the spec. |
| 2 | changethisfile.com / kaijuconverter guides | In — flat memory on read; `wc`/`head`/`tail`/`split`/`grep` work; `cat` merges files. |
| 3 | medium.com — "NDJSON Explained" | In — gzip 5–10×; store `.jsonl.gz` and decompress on the fly. |
| 4 | powerdrill.ai — "Mastering JSONL Files" | In — 85–95% gzip reduction; compressed JSONL ≈ compressed CSV. |
| 5 | jsonpost.com / adhdecode | In — encoding rules (UTF-8, no BOM, `\n`, `null` valid but a blank line is not); add a `type` field for mixed event kinds — which her `kind` field already is. |

### (c) Sync

**c1. `PowerSync self-hosted requires PowerSync service Postgres logical replication architecture`**

| # | Result | In / out |
|---|---|---|
| 1 | **docs.powersync.com/intro/setup-guide + powersync.com/sync-postgres** | In, decisive — the Service is mandatory; `wal_level = logical`, a publication named `powersync`, a replication role. |
| 2 | **powersync-ja/self-host-demo** | In — the self-host reference; the Service also needs **MongoDB** for bucket storage regardless of source DB. |
| 3 | powersync.com/blog — "Postgres Logical Replication Challenges" | In — why it keeps its own `op_id`: LSNs are not reliably monotonic across overlapping transactions. |
| 4 | queryplane.com — ElectricSQL vs PowerSync vs Replicache | In — third-party confirmation that writes go to your own backend, not through replication. |
| 5 | athulanilkumar.com — "PowerSync in the Wild" | In — practitioner account of the same component list. |

**c2. `ElectricSQL 2024 pivot dropped local-first SQLite sync read only`**

| # | Result | In / out |
|---|---|---|
| 1 | **electric-sql.com/blog/2024/01/24 v0.9** | In, decisive — carries a **deprecation notice**: this describes an old version that is no longer active. |
| 2 | electric.ax/blog/2023/09/20 — introducing v0.6 | In — documents what the section's description was based on: bidirectional CRDT Postgres↔SQLite. |
| 3 | news.ycombinator.com #37584049 — "Postgres to SQLite active-active sync" | In — historical framing, now obsolete. |
| 4 | tekko.id — "Local-First Sync with ElectricSQL" | In — read-path only; offline writes must be implemented separately. |
| 5 | findalternatives.net "Best ElectricSQL Alternatives 2026" | Weak, but consistent: PGlite is the de facto local store now, not SQLite. |
| — | **github.com/electric-sql/electric** | **✔gh.** Apache-2.0 ✅, 10,373 stars, pushed 2026-09-09. Licence clean; product mismatched. |

**c3. `CouchDB replication protocol requires CouchDB compatible server PouchDB Android offline sync`**

| # | Result | In / out |
|---|---|---|
| 1 | **pouchdb.com/faq.html** | In, decisive — *"No, your backend needs to speak the CouchDB replication protocol."* |
| 2 | pouchdb.com/guides/replication.html | In — sequence-number-based, not timestamp-based; `_rev` on every document. |
| 3 | neighbourhood.ie — "Offline-First with CouchDB and PouchDB in 2025" | In — current reference implementation; sync is *only* the replication protocol. |
| 4 | vovopap.com — "challenges of building an offline-first mobile application" | In — no document-level access control; database-per-user is the workaround. |
| 5 | adhdecode.com — CouchDB vs PouchDB sync architecture | In — conflicts are stored, not resolved; the app decides. |

**c4. `Automerge Kotlin Android JNI automerge-kotlin bindings`**

| # | Result | In / out |
|---|---|---|
| 1 | **automerge/automerge-java#2 "Kotlin multiplatform support?"** | In, decisive — no official KMP support; still an open ask. |
| 2 | **automerge.org/blog/automerge-2** | In — *"developers building mobile applications will need to bind directly via C."* |
| 3 | mvpfactory.io — CRDTs for offline-first mobile sync | In — the DIY path: `automerge-c` + cargo-ndk + expect/actual or UniFFI. |
| 4 | discuss.kotlinlang.org — Rust→Kotlin JNI Gradle plugin thread | In — confirms the boilerplate is a known unsolved annoyance. |
| 5 | zsqw123/kn-jvm | Noted — KSP-based JNI generation; not needed if the answer is "don't". |
| — | **github.com/automerge/automerge-java** | **✔gh + tree search. OUT — no licence file anywhere, no README licence mention.** 48 stars, v0.0.9 on 2026-04-22. |

**c5. `offline-first sync android sqlite outbox pattern own server best practice 2026`** *(the §9 "search:" line)*

| # | Result | In / out |
|---|---|---|
| 1 | medium.com — "Offline-First Android System Design: A Complete Guide" | In — local DB is the source of truth; network is a background reconciler. Exactly `store.py`'s docstring. |
| 2 | tiwariashuism.medium.com — "Offline-First Android Architecture" (Jul 2026) | In, most useful — the outbox row shape, and the anti-pattern of in-memory OkHttp retry queues losing writes on force-close. |
| 3 | educba.com — "Offline-First: Outbox, Idempotency & Conflict Resolution" | In — **idempotency keys**, the one gap `store.py`'s docstring already admits. |
| 4 | sqliteforum.com — "Building Offline-First Applications with SQLite Sync Queues" | In — queued→sending→acknowledged→retrying→conflict→failed; single-flight sync. `store.py` has the states and the flush lock. |
| 5 | codingpancake.com — "Building Offline-First Mobile Sync Engines" | In — WorkManager/BackgroundTasks flushing the outbox; tombstones; incremental pull cursors. |

**c6. `WorkManager reliability Doze battery optimization deferred upload OEM killed background work`**

| # | Result | In / out |
|---|---|---|
| 1 | **proandroiddev.com — "Beyond Doze… Including OEM Realities"** | In, decisive — OEMs kill foreground services, block `BOOT_COMPLETED`, ignore exact alarms, delay broadcasts. |
| 2 | outcomeschool.substack.com — "How Does WorkManager Guarantee Task Execution?" | In — persists in its own SQLite DB, survives reboot, re-registers; guarantees execution, **not timing**. |
| 3 | dev.to — "What Android OEMs do to background apps, and the 11 layers I built" | In — a year of field evidence: never trust one mechanism. |
| 4 | dev.to — "WorkManager Pitfalls: hidden battery drain" | In — audit `PeriodicWorkRequest` intervals; profile wake-ups. |
| 5 | medium.com — background location tracking | In — polling fails under Doze and in the Restricted bucket; push or foreground work instead. |

**c7. `awesome local-first software list sync engines comparison android kotlin`** *(awesome-list sweep)*

| # | Result | In / out |
|---|---|---|
| 1 | **p32929/awesome-local-first** and **alexanderop/awesome-local-first** | In — the sync-engine section is Electric, WatermelonDB, TanStack DB, LiveStore, Triplit (acquired by Supabase 2025), InstantDB, Zero, RxDB, PowerSync, Y-Sweet, Turso Sync. **Almost entirely web/JS.** Only PowerSync and Turso have any Android story. |
| 2 | powersync.com/blog — "Build Local-First KMP Apps With PowerSync" | In — the KMP SDK's own caveats: single SQLite file, extension loaded via a custom open factory + JNI. |
| 3 | **CharlieTap/synk** | **✔gh.** Apache-2.0 ✅, 103 stars — **last release 2023-03-22, last push 2023-11-22. Abandoned.** |
| 4 | zignuts.com — offline-first Android 2026 guide | In — Room/DataStore + Flow + WorkManager; the hand-rolled shape. |
| 5 | wikipedia "Comparison of file synchronization software" | Out — file-sync products, not app sync. |

**c8. `PowerSync kotlin multiplatform SDK android setup`** *(the §9 "search:" line)*

| # | Result | In / out |
|---|---|---|
| 1 | **docs.powersync.com/client-sdk-references/kotlin-multiplatform** | In — targets Android, JVM, iOS, macOS, watchOS; needs a backend connector for a token *and* for applying writes. |
| 2 | **github.com/powersync-ja/powersync-kotlin** | **✔gh + tree search + license API. OUT — NO LICENCE FILE.** 127 stars, v1.15.1 on 2026-09-07. |
| 3 | central.sonatype.com `com.powersync:core-android` | In — an Android-only AAR exists. |
| 4 | releases.powersync.com — Kotlin SDK v1.13.0 | In — Room integration in beta; native x86_64 Apple targets removed (breaking). |
| 5 | powersync-kotlin demos READMEs | In — every demo is Supabase-backed, i.e. Postgres. |
| — | **powersync-ja/powersync-service LICENSE (read)** | **FSL-1.1-ALv2**, © 2023-2026 Journey Mobile, Inc. Internal use permitted; Apache-2.0 after two years. The licence trap, confirmed. |

**c9. `Couchbase Sync Gateway community edition self-host license Docker`**

| # | Result | In / out |
|---|---|---|
| 1 | couchbase/docker `community/sync-gateway/3.1.0/README.md` | In — CE Dockerfile installs `couchbase-sync-gateway-community_*.rpm`, exposes 4984. |
| 2 | couchbase.com/community-license-agreement | In — CE free to deploy; EE needs a production subscription. |
| 3 | hub.docker.com couchbase/sync-gateway | In — admin port 4985 reports the edition. |
| 4 | catalog.redhat.com — Sync Gateway EE | Out — EE only. |
| 5 | older CE Dockerfiles (1.x, 2.1.1) | Out — obsolete; recorded only because the search surfaced them above current ones. |

**c10. `Turso libSQL embedded replica Android Kotlin sync offline`**

| # | Result | In / out |
|---|---|---|
| 1 | **github.com/tursodatabase/libsql-android** | **✔gh.** MIT ✅ but 22 stars, **no releases, last push 2025-07-07**, technical preview. Out on maturity. |
| 2 | docs.turso.tech/sdk/kotlin/reference | In — `Libsql.open()` for memory / file / embedded replica; `syncInterval`; AGP-only, full Kotlin "coming". |
| 3 | turso.tech/blog — "Offline Sync Public Beta" / "Introducing Offline Writes" | In — offline writes now exist, `SyncStrategy.FAIL_ON_CONFLICT`. |
| 4 | turso.tech/blog — "Turso Goes Mobile" | In — but notes the rearchitecture: legacy libSQL SDKs replaced by Turso-native ones (**unverified**). |
| 5 | tursodatabase/embedded-replica-examples | Noted. Requires Turso Cloud or a libsql-server either way → out. |

**c11 / c12. GitHub topic sweeps** *(required by the method; recorded for completeness)*

- `gh search repos --topic offline-first --sort stars` → meetily, react-boilerplate,
  yjs, gun, memvid, automerge-classic, workbox, LEANN, berty, anytype-ts, loro,
  tagspaces, sw-precache, UpUp, offline-plugin. **Nothing Android/Kotlin
  relevant** — the topic is dominated by JS/PWA and CRDT-for-text projects.
- `gh search repos --topic offline-first --sort updated` → freshly-pushed
  single-author apps (MkulimaLink, Curio, PaynEat, BeatBridge, EduBoard, Sibit…),
  mostly 0 stars. **Nothing to adopt.**
- `gh search repos --topic local-first --sort stars` → open-design, career-ops,
  siyuan, logseq, Codewhale, openhuman, Trilium, VoiceStudio, rxdb,
  super-productivity. **Note: "local-first" on GitHub in 2026 is mostly
  AI-tooling and note-taking apps, not sync engines.** rxdb is the only sync
  engine and it is JS.
- `gh search repos "offline sync" --language kotlin --sort stars` → PixelPlayer,
  qlarr-surveys/backend, iCaldAV, and a long tail of forks and toy apps.
  **No reusable Kotlin sync library.**
- `gh search repos "fit file garmin"` → two results total:
  `L1cardo/fit-file-comparison-tool` (JS, 0 stars) and `bltavares/malhado`
  (Kotlin, imports `.fit` into Health Connect, 0 stars). **The Kotlin FIT
  ecosystem is the official Garmin Java SDK and nothing else.**

---

## Cross-cutting notes

- **Three candidates fell to the no-licence rule**, all of them otherwise usable:
  the SpatiaLite Android wrappers, `powersync-kotlin`, and `automerge-java`. That
  makes three in this section plus Ride-Track in `00-already-checked.md`. The rule
  is earning its keep.
- **One licence trap confirmed exactly as the brief predicted**: PowerSync is
  open-core. The SQLite extension is Apache-2.0, the JS client is Apache-2.0, the
  **self-hosted service is FSL-1.1-ALv2**, and the **Kotlin client she would
  actually ship has no licence at all**.
- **One more trap, of a different kind**: ObjectBox looks Apache-2.0 on Maven
  Central and in its repository, and the native engine that does the work is a
  closed binary under a licence whose text I could not retrieve.
- **The FIT licence is the most restrictive document in this section** and the
  least likely to be read. Clause 2(d) alone means the SDK cannot coexist with
  GPL code — which matters because `CHOICES.md` §1 lists OsmAnd (GPLv3) as a fork
  candidate.
- **Section 9 is where the phone is already ahead of the server.** `store.py` and
  `telemetry.py` both do the right thing; `dashbored-v3.py:parse_gpx` reads four
  fields per point and recomputes speed by haversine. Whatever gets picked here,
  that parser is the thing standing between her lean-angle data and anything
  looking at it.
