# Section 8 — Telemetry and sensors

Research for `CHOICES.md` §8. **Nothing here is a decision.** Options, licences,
evidence, and the problems each one carries.

Researched 2026-09-26. The spec this must reproduce is
`apps/navi/agedashii_navi/{telemetry,sensors,audio,dsp,gears,bikes}.py`.

**Read the confidence rules at the bottom.** Everything marked *unverified* was
not confirmed by opening the primary source.

---

## 0. What the spec actually requires

Pulled out of the Python first, because most candidates fail against a number
rather than against an opinion.

### The three-kind labelling contract

`telemetry.CHANNELS` labels every channel `measured` / `derived` / `estimated`,
and the UI reads that table rather than a hard-coded list. Whatever is picked
must keep the distinction structurally, not by convention. Present count: 20
measured, 8 derived, 2 estimated (`exhaust_hz`, `rpm_est`).

### The lean angle

```
lean_from(speed_ms, yaw_dps):
    None if speed_ms < MIN_LEAN_SPEED_MS (5.0 m/s)
    a_lat = speed_ms * radians(yaw_dps)
    return degrees(atan(a_lat / G))          # G = 9.80665
```

Inputs are GPS speed and GPS-heading difference. `yaw_dps` comes from
`heading_delta`, wrapped to (-180, 180]. Sample rate 1 Hz.

### The audio chain, exactly

| Stage | Where | Constant |
|---|---|---|
| capture | `audio.py` | 8000 Hz, mono, PCM16, `suppress_noise=False`, `auto_gain=False` |
| window | `audio.WINDOW_SAMPLES` | 4096 samples = **512 ms** |
| cadence | `audio.RPM_EVERY_S` | recompute at most **1 Hz**; loudness every chunk |
| silence gate | `audio.QUIET_DBFS` | −55 dBFS |
| high-pass | `dsp.highpass` | one-pole, 18 Hz, at the **original** rate |
| decimate | `dsp.decimate` | box-car mean, factor 8, to **1000 Hz** |
| window fn | `dsp.hann` | Hann, cached per length |
| transform | `dsp.spectrum` | naive DFT, band bins only, cached trig tables |
| pick | `dsp.harmonic_pick` | harmonic product, **3** harmonics, out-of-band harmonic → band median |
| candidate floor | `dsp.MIN_CANDIDATE_FRACTION` | **2 %** of the strongest bin's power |
| contrast gate | `audio.MIN_CONTRAST` | **8.0** power ratio vs band median (≈ 9 dB), max of 3 bins around the peak |
| range gate | `bikes.plausible` | idle×0.75 … redline×1.25 |
| continuity gate | `audio.MAX_RPM_JUMP_FRACTION` | reject a jump > **60 %** of redline in 1 s |
| meter | `dsp.band_energy` / `band_levels` | engine band only, 3 sub-bands at 0–25 / 25–60 / 60–100 % of band |

Resolution: 512 samples at 1000 Hz → bin width **1.953 Hz**. On the CB750
(2 firings/rev) that is **58.6 rpm**.

### The search bands, computed from `bikes.py`

| Bike | firings/rev | idle Hz | redline Hz | `band_hz()` |
|---|---|---|---|---|
| CB750 (inline four) | 2.0 | 33.3 | 316.7 | **25.0 – 395.8 Hz** |
| EX250 (parallel twin) | 1.0 | 21.7 | 233.3 | **16.25 – 291.7 Hz** |
| "other" (single) | 0.5 | 10.0 | 75.0 | **15.0 – 93.8 Hz** |

**These three numbers decide most of sub-question (b), and they are the reason
the obvious answer is wrong.** See §2.1.

### What consumes RPM

`gears.py` needs road speed *and* engine speed to name a gear, refuses below
3 m/s (clutch), matches within 12 % and reports a confidence that falls to 0 at
the tolerance edge. `gears.best_gear_for` needs **no** microphone. So the
microphone gates the gear *indicator* but not the gear *advice*.

---

## 1. Sub-question (a) — motion and lean

### 1.1 Search log

Seven web searches plus four GitHub searches. Under each, the results returned,
with one line on in/out. Pages actually opened are marked **[opened]**.

**A1 — `madgwick filter android kotlin library github`**

| # | Result | In / out |
|---|---|---|
| 1 | [maddevsio/mad-location-manager](https://github.com/maddevsio/mad-location-manager) | **In as reference.** MIT, 576★, pushed 2025-06-13, Java. GPS+accel Kalman, *can* swap in Madgwick. Solves a different problem (position smoothing), not attitude. |
| 2 | [IdeoG/android-madgwick](https://github.com/IdeoG/android-madgwick) **[opened]** | **OUT — no LICENCE file.** Java, 11★, last push **2017-10-19**. Dead demo. |
| 3 | [WindSekirun/KalmanKt](https://github.com/WindSekirun/KalmanKt) | Out. MIT but 17★, last push 2019-01-20, and it is a *geolocation* Kalman filter, not AHRS. |
| 4 | [github.com/topics/madgwick-filter](https://github.com/topics/madgwick-filter) **[opened]** | Browsing surface only. No Android/Kotlin library of substance. |
| 5 | [bjohnsonfl/Madgwick_Filter](https://github.com/bjohnsonfl/Madgwick_Filter) | **In as a port source.** MIT, 215★, pushed 2024-03-30, C. ~200 lines; the thing you port if you hand-roll. |

Also surfaced: [maddevsio/sensor-fusion-demo](https://github.com/maddevsio/sensor-fusion-demo) — **OUT, no LICENCE file** (the search summary claimed MIT; the repo has no licence file. The claim is wrong.) Last push 2019-04-11.

**A2 — `android TYPE_ROTATION_VECTOR vs TYPE_GAME_ROTATION_VECTOR accuracy drift`**

| # | Result | In / out |
|---|---|---|
| 1 | YouTube "Game Rotation Vector vs Gyro Integration" | Out, video, not citable. |
| 2 | [developer.android.com position sensors](https://developer.android.com/develop/sensors-and-location/sensors/sensors_position) **[opened]** | **In — primary source.** Extracted in §1.2. |
| 3 | [source.android.com sensor types](https://source.android.com/devices/sensors/sensor-types) | In — the "Y axis reference is allowed to drift by the same order of magnitude as gyro Z drift" wording. Read via search extract, page **not opened**. |
| 4 | info448 lecture notes | Out, teaching material. |
| 5 | [RoNIN arXiv 1905.12853](https://arxiv.org/pdf/1905.12853) | **In as a number.** Game Rotation Vector fusion drifts **10–20° over 10 min**; their filtering got it to 5–10°. Not opened directly; figure from the search extract → **unverified**. |

**A3 — `motorcycle lean angle estimation GPS yaw rate versus IMU complementary filter comparison`**

| # | Result | In / out |
|---|---|---|
| 1 | Boniolo & Savaresi, *Lean Angle Estimation with Frequency Separation* (ResearchGate) | **In as theory.** Complementary filter designed on error frequency separation, gyros + wheel speed. Paywalled, **not opened** → unverified. |
| 2 | *Roll Angle Estimation for Motorcycles: Video vs Inertial* (ResearchGate) | In as theory; not opened. |
| 3 | *High-Performance Motorbike Lean Angle Estimation* (ResearchGate) | In as theory; not opened. |
| 4 | [Hackaday: lean angle calculation method](https://hackaday.io/project/13438-motorcycle-lean-angle-video-overlay/log/45115-lean-angle-calculation-method) **[opened]** | **In — independent confirmation.** See §1.3. |
| 5 | [Racelogic: IMU for motorcycle applications](https://en.racelogic.support/knowledge-bases/general-kb/installing-and-configuring-an-imu-for-motorcycle-applications/) | **Attempted, HTTP 403.** Commercial-vendor guidance read only via search extract → **unverified**, but see §1.3 for the one claim that matters. |

**A4 — `android kotlin sensor fusion lean angle roll pitch rotation vector motorcycle dashboard github`**

| # | Result | In / out |
|---|---|---|
| 1 | [braydenzhao/MotorcycleLeanAngleSensor](https://github.com/braydenzhao/MotorcycleLeanAngleSensor) **[licence checked]** | **OUT AS CODE — no LICENCE file, confirmed by listing the repo root.** This closes the gap `00-already-checked.md` left open. Still in as reading. |
| 2 | [KalebKE/FSensor](https://github.com/KalebKE/FSensor) **[opened, licence opened]** | **IN. The find of sub-question (a).** §1.4. |
| 3 | [kplatfoot/android-rotation-sensor-sample](https://github.com/kplatfoot/android-rotation-sensor-sample) | In as UI reference only. MIT, 118★, last push **2017-12-08**. Artificial-horizon custom view. |
| 4 | [markec12345678/OsmAndmarkec](https://github.com/markec12345678/OsmAndmarkec) | **Out as code** (licence `NOASSERTION` — an OsmAnd fork, so GPLv3 inherited; 0★, one person). **In as a tuning datapoint:** α = 0.02 complementary blend, gyro trusted for cornering, rotation vector for drift, clamped ±60°, 5-sample moving average, and *no unit tests on the fusion*. Pushed 2026-09-17. |
| 5 | [cqeta1564/MotoLogger](https://github.com/cqeta1564/MotoLogger) **[licence checked]** | **OUT — no LICENCE file.** Dart/ESP32. In as evidence for (c): BNO085 running Game Rotation Vector at 100 Hz off-phone, explicitly to escape engine vibration and alternator magnetics. |

Also: [apacha/sensor-fusion-demo](https://github.com/apacha/sensor-fusion-demo) — **OUT, no LICENCE file** (141★; the search summary said MIT — wrong, verified by listing the root).

**A5 — `android ROTATION_VECTOR sensor inconsistent across manufacturers unreliable fusion quality`**

| # | Result | In / out |
|---|---|---|
| 1 | KalebKE/FSensor | Already in. Its README states the case directly — quoted in §1.4. |
| 2 | arXiv 2405.17368 (uncalibrated IMUs + video, knee kinematics) | Out, off-domain. |
| 3 | RoNIN | Already counted. |
| 4 | XDA "why are rotation vector fusion sensors missing" | **In as a hazard.** A device with gyro + accel exposing *neither* `ROTATION_VECTOR` nor `GAME_ROTATION_VECTOR`. Forum, not opened → unverified, but it is the reason a null check is mandatory. |
| 5 | AccelerationExplorer wiki (same author as FSensor) | In as background. Not opened. |

**A6 — `MEMS gyroscope accelerometer engine vibration aliasing motorcycle phone mount error`**

| # | Result | In / out |
|---|---|---|
| 1 | [PMC10490716 — Smartphone MEMS accel/gyro measurement errors](https://pmc.ncbi.nlm.nih.gov/articles/PMC10490716/) | **In.** Consumer/smartphone-grade MEMS is the worst tier; sold uncalibrated; vibration-induced errors listed explicitly. Not opened → unverified. |
| 2 | US20080105050A1 — accelerometer-derived gyro VRE compensation | In as mechanism. Not opened. |
| 3 | PMC11154544 — frequency-dependent VRE in capacitive MEMS accelerometers | In as mechanism. Not opened. |
| 4 | AliExpress "wiki" on mount vibration | **Out.** Retail marketing. Its "0.8 G RMS" threshold is not a citable number. |
| 5 | PMC6022183 — vibration-induced errors in tuning-fork gyros with imbalance | In as mechanism. Not opened. |

The load-bearing claim, from 1–3 and 5 collectively: **vibration rectification
error** turns a zero-mean engine buzz into a *persistent DC offset* on both
accelerometer and gyro. On an inclinometer — which is exactly what a lean gauge
is — that is a false lean that does not average out. Mechanism verified across
four independent sources; magnitude on her phones **unverified**.

**A7 — `redundant sensor estimators cross-check plausibility voting automotive signal validation two independent methods`**

| # | Result | In / out |
|---|---|---|
| 1 | [MOTOR: Sensor plausibility checks explained](https://www.motor.com/2026/02/sensor-plausibility-checks-diagnostics/) | **In.** Names the two families: *physical redundancy* (two sensors compared) and *analytical redundancy* (a model predicts, the measurement is checked against it). Not opened → unverified. |
| 2–4 | USPTO 8155909 / 10183678 / 9702742 | In as prior art: "plausibility window", 2-of-3 voter matrix, and the rule that with N sensors you can *detect* N−1 failures but only *isolate* if at most N−2 fail. Not opened. |
| 5 | ResearchGate — robust plausibility check for an adaptive vehicle observer | In as theory. Not opened. |

**GitHub searches run**

- `topic:sensor-fusion topic:android`, sorted by stars — top hits MBCompass (GPL-3.0, 605★), SensaGram (GPL-3.0, 76★), igorinov/variometer (Apache-2.0, 12★, barometric VSI — worth a look for her `climb_mps`). Nothing motorcycle.
- `"sensor fusion android" language:kotlin`, sorted by stars — **zero results**.
- `"lean angle motorcycle"` repo search — **zero results**. There is no lean-angle library.
- `topic:android-sensors`, sorted by updated — best of 20: [ricknout/compose-sensors](https://github.com/ricknout/compose-sensors) (Apache-2.0, 104★, but last push **2023-05-25**), SensorServer (447★), sensify-android (MIT, 224★, last push 2023-06-27). All wrappers; none does fusion.
- Plus `topic:sensor-fusion` sorted by updated — returned generic ML/robotics noise, nothing usable. Logged for completeness.

### 1.2 Option: raw `SensorManager`

| | |
|---|---|
| Link | <https://developer.android.com/develop/sensors-and-location/sensors/sensors_position> |
| Licence | Platform API. No licence question. |
| Android | Yes, by definition. API 1+. |
| Offline | Yes, absolutely. No network path exists. |
| Speed | Cheapest possible. Callbacks on a sensor thread, no allocation if you reuse arrays. |
| Cost | You write the fusion. |

Sensors available and what each fuses (verified on the page):

- `TYPE_ACCELEROMETER` — hardware, gravity included.
- `TYPE_GYROSCOPE` — hardware, rad/s.
- `TYPE_MAGNETIC_FIELD` — hardware. **Rate-limited if targeting API 31+.**
- `TYPE_MAGNETIC_FIELD_UNCALIBRATED` — no hard-iron correction; the docs say it is *smoother and more reliable for custom fusion* because it has no calibration jumps.
- `TYPE_ORIENTATION` — **deprecated** since API 8, sensor type deprecated API 20, "reliable only when roll angle is 0". Never use it.
- `getRotationMatrix()` + `getOrientation()` — accel + mag only, no gyro. The docs warn accuracy is "diminished… due to heavy processing".
- `remapCoordinateSystem()` — the mount-orientation fix.

**Fit with her rules.** Works with no signal: yes. Fast: yes. Labelling: it is
the only option where the labelling is *forced* to be honest, because you can
see every input. The microphone rule is untouched.

**Known problems.** `getOrientation` returns pitch clamped to ±90° — a Euler
singularity a leaning bike can reach. Everything must be done in quaternions or
you get gimbal-lock artefacts at the exact moment that matters.

### 1.3 Option: the fused `ROTATION_VECTOR` family

Three variants, all platform-supplied, all free.

| Type | Fuses | Absolute heading | Drift |
|---|---|---|---|
| `ROTATION_VECTOR` | accel + gyro + **mag** | yes | yaw bounded by the magnetometer; reports its own heading accuracy in `values[4]` (radians), must be right 95 % of the time |
| `GAME_ROTATION_VECTOR` | accel + gyro, **no mag** | no | yaw drifts at gyro-Z drift rate; pitch/roll stay bounded by gravity |
| `GEOMAGNETIC_ROTATION_VECTOR` | accel + **mag**, no gyro | yes | low power, docs state *lower accuracy* |

**Why this is not free.** Verified, from FSensor's own README: *"The behavior of
stock sensor fusions can vary greatly between devices and manufacturers."* These
are **virtual sensors** — the fusion algorithm is the OEM's, not Android's. There
is no specified accuracy, no way to see the algorithm, and (per the XDA report)
no guarantee the type exists at all on a given handset.

**The measured drift figure.** RoNIN: 10–20° over 10 minutes on Game Rotation
Vector. *Unverified* (search extract, paper not opened). Even taken loosely, it
is the yaw axis only — pitch and roll do not walk, because gravity pins them.

**What `00-already-checked.md` already established and this confirms.**
MotorcycleLeanAngleSensor picks `GAME_ROTATION_VECTOR` with a fallback to
`ROTATION_VECTOR`, precisely so a tank-bag clasp cannot corrupt it. That is the
right choice for a *lean* gauge, because lean is roll, and roll is the axis the
magnetometer does not help with and can only poison. Its code is unusable —
**no licence, now verified** — but that reasoning is free.

**The independent confirmation of her formula.** The Hackaday project
(**opened**) derives lean from GPS speed and GPRMC track bearing as

```
radius     = speed / angular_velocity
lean (deg) = arctan(speed² / (radius · g))
```

Substituting radius: `arctan(v² / ((v/ω)·g)) = arctan(v·ω/g)` — **algebraically
identical to `telemetry.lean_from`.** Two people arrived at the same expression
from opposite directions. Her physics is not in question.

One difference worth having: that project runs GPS at **5 Hz, deliberately
chosen over 1 Hz "for better results"**, with 0.2 s between points. Her
telemetry is 1 Hz. Android's `FusedLocationProvider` / `LocationManager` will
give 5 Hz on most handsets; a 1 Hz heading difference is a 1-second average of
yaw rate, which smears the entry and exit of every corner. This is a cheap win
independent of every other choice here.

The Racelogic page (403, **unverified**) reportedly makes a point that does
matter and that neither her code nor Ride-Track handles: **a gyro's yaw rate is
in the gyro's frame, so when the bike leans, gyro-Z is no longer vertical yaw.**
If a gyro yaw rate is ever cross-checked against a GPS yaw rate, it must first
be rotated by the current attitude, or the two disagree *more* the harder she
corners — i.e. exactly where the cross-check is needed. Treat as a design
warning to verify, not as a fact.

### 1.4 Option: a Madgwick/Mahony AHRS filter — and the library that ships one

**[KalebKE/FSensor](https://github.com/KalebKE/FSensor)**

| | |
|---|---|
| Licence | **Apache-2.0 — verified by opening the `license` file** (lowercase filename, which is why the GitHub API reported it correctly but a `LICENSE` grep would miss it) |
| Stars / forks | 217 / 51 |
| Language | **Kotlin Multiplatform** (`commonMain` / `androidMain` / `iosMain`) |
| Last commit | 2026-05-25 |
| Last **release** | **3.0.7, 2024-08-25** |
| Distribution | JitPack, `com.github.KalebKE:FSensor:3.x.y`; SPM for iOS |
| Android | Yes |
| Offline | Yes. No network code. |

On `master` it ships, all in `commonMain` and all with tests in `commonTest`:
`MadgwickFusion(beta)`, `MahonyFusion(kp, ki)`, `EkfFusion(...)`,
`ComplementaryFusion(timeConstant)`, `KalmanFusion(...)`, a `GpsKalmanFilter`,
`Quaternion`/`RotationMath`, `TiltCompensation`, WGS84↔ECEF↔ENU conversion, and
mean/median/low-pass smoothing filters.

Critically, the algorithms are usable with **no Android platform layer at all**:

```kotlin
val fusion = MadgwickFusion()
fusion.update(acceleration, magnetic, gyroscope, dt)
fusion.getOrientation()   // [azimuth, pitch, roll] radians
fusion.getQuaternion()    // [w, x, y, z]
```

That is the same "core with no platform imports, unit tested" rule her Python
follows and that Ride-Track independently arrived at. It drops straight into a
pure-Kotlin core.

**The problem, and it is a real one.** The released artifact is **not** the
library the README describes. Verified by listing the `3.0.7` tag's tree: tag
3.0.7 is **Java**, package `com.tracqi.fsensor.rotation.fusion`, and contains
only `ComplimentaryRotation` and `KalmanRotation` — **no Madgwick, no Mahony, no
EKF.** JitPack's `latestOk` for the repo is 3.0.7. So:

- Want Complementary or Kalman → use release 3.0.7, Java API, fine.
- Want Madgwick/Mahony/EKF or the KMP core → you must depend on a **commit hash
  via JitPack**, or vendor the files (Apache-2.0 permits it, with attribution).

Also note: last release 2024-08-25, last commit 2026-05-25. Twenty-one months of
unreleased work. Active author, stale publishing.

**Other Madgwick paths.** `bjohnsonfl/Madgwick_Filter` (MIT, C, 215★, 2024-03-30)
is the clean port source if you want ~200 lines of your own Kotlin with your own
tests. The algorithm is short and the beta gain needs tuning per device — that
tuning is the real cost, not the code.

**What a filter buys, in her terms.** Lean while stationary and below 5 m/s —
the hole `MIN_LEAN_SPEED_MS` admits to. Transients and counter-steer, which a
1 Hz GPS difference cannot see at all.

**What it costs.** (i) A known mount orientation, or a calibration step —
MotorcycleLeanAngleSensor's answer was averaging one second of samples upright.
(ii) Per-device gain tuning. (iii) **Vibration rectification error**: a
persistent false offset from engine buzz, worst exactly where her bikes live (an
unbaffled inline four). (iv) It measures the *phone*, and `sensors.py` already
says the right thing — a phone strapped to a leaning bike measures the resultant
of gravity and cornering force, which in a steady corner points along the
centreline, so roll reads near **zero**. A filter fixes the *drift*; it does not
fix the *geometry*. The filter's roll is the bike's lean only while the lateral
acceleration is accounted for separately.

That last point is the one most likely to be got wrong. A Madgwick filter run on
a bike does not output lean. It outputs attitude relative to the gravity-plus-
centripetal resultant, which is what "down" means to an accelerometer on a bike.

### 1.5 Is running both and cross-checking established practice?

**Yes, and it is more established than "cross-checking" implies.** Two separate
literatures say so, and they say slightly different things.

**Vehicle dynamics: fusion, not comparison.** The Boniolo/Savaresi line of work
is a *complementary filter designed on frequency separation* — GPS-derived lean
is the low-frequency reference (unbiased, slow, noisy, dies at low speed), gyro
integration is the high-frequency term (smooth, fast, biased, drifts). Neither
is a check on the other; each supplies the band the other cannot. The consensus
in the search results: pure gyro integration is not viable alone, and
GPS/kinematic lean alone is valid only for steady-state cornering. That is
exactly the pair of failure modes `00-already-checked.md` identified. Papers
**not opened** → the specific formulations are unverified; the shape of the
result is corroborated by four independent sources.

**Automotive functional safety: comparison, and it has a vocabulary.** What she
described is *analytical redundancy* — a second, model-based estimate of the same
quantity, with a residual and a plausibility window. Standard practice, patented
many times over, and the standards context is that plausibility checking is a
cornerstone of functional safety because redundant sensors alone would not meet
a SIL. With two estimators you can *detect* a disagreement but not *isolate*
which one is wrong; you need three to vote. Sources **not opened** → unverified
in detail.

**What this means for Navi, concretely.** Two estimators give three channels, not
one, and all three are honest:

| Channel | Kind | Source |
|---|---|---|
| `lean_deg` | `derived` | `atan(v·ω/g)` from GPS — unchanged, still the trustworthy one above 5 m/s |
| `lean_imu_deg` | `derived` | attitude filter roll, with the cornering resultant accounted for |
| `lean_agree` | `derived` | the residual between them, or a dash when only one is available |

Where they disagree by more than a window, show a dash. That is her existing
rule — `harmonic_pick` returns `None`, `compass_deg()` returns `None` when the
field is implausible, `gears.estimate` returns `None` rather than guessing —
applied to lean. **It also gives the first automatic detector for the phone
being knocked out of its mount**, which `sensors.py` currently wants but cannot
do: a sudden step in the residual with no step in either input is a mount
failure, not a corner.

### 1.6 (a) summary

| Option | Licence | Active | Android | Offline | Speed | Works stopped | Needs calibration | Verdict |
|---|---|---|---|---|---|---|---|---|
| Raw `SensorManager` | platform | n/a | yes | yes | fastest | only if you write the filter | no | Floor. You need it regardless. |
| `ROTATION_VECTOR` | platform | n/a | yes | yes | free (hardware/HAL) | yes | no | Free, but OEM-dependent and unspecified; magnetometer poisons roll near the bike. |
| `GAME_ROTATION_VECTOR` | platform | n/a | yes | yes | free | yes | no | Better for roll — no magnetometer. Yaw drifts 10–20°/10 min (unverified). |
| **FSensor** | **Apache-2.0 ✓** | commit 2026-05, release 2024-08 | yes | yes | Kotlin, no allocation in `update` | yes | yes, per device | Madgwick/Mahony/EKF exist **only on unreleased master**. |
| Hand-rolled Madgwick | MIT source ✓ | port | yes | yes | ~200 lines | yes | yes | Full control, your own tests, your own beta tuning. |
| MotorcycleLeanAngleSensor | **NONE ✗** | 2026-09-22 | yes | yes | — | yes | yes | **Ruled out as code.** Read it. |
| apacha/sensor-fusion-demo | **NONE ✗** | 2025-08-31 | yes | yes | — | — | — | **Ruled out.** (Search claimed MIT; wrong.) |
| IdeoG/android-madgwick | **NONE ✗** | 2017-10-19 | yes | — | — | — | — | **Ruled out**, and dead. |
| mad-location-manager | MIT ✓ | 2025-06-13 | yes | yes | — | n/a | no | Wrong problem (position, not attitude). Worth reading for GPS smoothing. |

---

## 2. Sub-question (b) — audio to RPM

### 2.1 The finding that reframes this whole sub-question

Two independent physical facts, both verified, collide with her band table:

**Fact 1 — the CDD only guarantees mic flatness down to 100 Hz.** From the
Android CDD §5.11 (**opened**), the requirements to report
`PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED`:

- "approximately flat amplitude-versus-frequency characteristics in the
  mid-frequency range: specifically **±10 dB from 100 Hz to 7000 Hz**"
- "amplitude levels in the low frequency range: specifically from **±20 dB from
  5 Hz to 100 Hz**"
- ±30 dB, 7000 Hz – 22 kHz
- 1 kHz at 94 dB SPL → RMS 520 (16-bit); SNR > 60 dB; THD < 1 % at 1 kHz / 90 dB
- "Only a level multiplier… **No other signal processing is allowed**", including
  gain control, filtering, or echo cancellation.

**Fact 2 — phone MEMS mics roll off inside her band.** The low-frequency
roll-off (−3 dB re 1 kHz) of a MEMS transducer is set by its back-chamber volume,
and "because most MEMS microphones are used for voice applications focused on
frequencies above 80 Hz, they're generally designed with limited back volume and
therefore have less-than-desirable low-frequency response." Typical MEMS cut-off
**15–25 Hz** versus ~7.5 Hz for an electret; and a real Knowles datasheet
specifies **nominal LFRO 75 Hz**, with 100 Hz already at −1.5 dB nominal and as
much as −3.5 dB. Sources: Infineon MEMS-mic KB article and a Knowles datasheet,
read via search extract — **not opened, so the specific numbers are unverified**;
the mechanism is corroborated by two patents and a PMC paper.

**Now put her bands against that.**

| Bike | band | fraction of band below 100 Hz | fundamental at idle |
|---|---|---|---|
| CB750 | 25.0 – 395.8 Hz | 20 % | **33.3 Hz** |
| EX250 | 16.25 – 291.7 Hz | 30 % | **21.7 Hz** |
| single | 15.0 – 93.8 Hz | **100 %** | **10.0 Hz** |

**Why this breaks harmonic product spectrum specifically.** HPS multiplies power
at *f*, *2f*, *3f*. If the mic attenuates 20 Hz by 15 dB relative to 60 Hz — well
within a ±20 dB spec and consistent with a 75 Hz LFRO — then the EX250's true
fundamental at idle arrives ~15 dB *below* its own second harmonic before any
engine physics is involved. Two of her gates then work against her:

- `MIN_CANDIDATE_FRACTION = 0.02` — a candidate must hold 2 % of the strongest
  bin's **power**, i.e. ≈ 14 % of peak amplitude, ≈ −17 dB. A 15 dB electrical
  attenuation on top of an exhaust fundamental that is already quieter than its
  own second harmonic (which her own docstring says is "the ordinary case") puts
  the real fundamental under the floor.
- `MIN_CONTRAST = 8.0` against the band median — the median is dominated by the
  100–300 Hz part of the band, where the mic *is* flat. The attenuated
  fundamental is measured against an un-attenuated background.

The failure mode is not a wrong number. It is `rpm_rejected = "no clear
fundamental"` at idle and low revs on the twin, and the gauge showing a dash —
which is *honest*, and is also the one operating point where a tachometer is
easiest and most useful. **Not measured on her phones. This is a prediction from
two verified specs, and it is the single most important thing in this file to
test before writing any code.** The test is ten seconds long: record the EX250
at idle on `UNPROCESSED`, and look at whether 21.7 Hz is visible above 43.3 Hz.

**And this is why rpm-boss's method is not just an alternative — it is the
structural answer to the problem.** rpm-boss band-passes **60–2000 Hz**,
rectifies, and low-passes into an *envelope*, then autocorrelates the envelope.
The acoustic content it uses is 60–2000 Hz, squarely inside the flat region. The
pulse rate it recovers is a **modulation** rate of 12–15 Hz on the EX250 at idle,
not a 21.7 Hz acoustic component. **The microphone's low-frequency roll-off never
touches it.** Verified from `docs/dsp.md` and `src/dsp/envelope.ts` (both
opened).

That is a genuine, physically-grounded advantage, and it is not the advantage the
rpm-boss README advertises.

### 2.2 Search log

Eight web searches plus two GitHub searches.

**B1 — `Android MediaRecorder.AudioSource.UNPROCESSED PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED flat frequency response`**

| # | Result | In / out |
|---|---|---|
| 1 | [CDD 5.11 unprocessed audio](https://android.googlesource.com/platform/compatibility/cdd/+/refs/heads/nougat-dev/5_multimedia/5_11_unprocessed-audio.md) **[opened]** | **In — primary source.** Quoted in §2.1. Note: this is the *nougat-dev* branch, i.e. the Android 7 CDD, the release that introduced the source. Current-CDD wording **not checked** → treat as the floor, not necessarily today's exact text. |
| 2 | [developer.android.com MediaRecorder overview](https://developer.android.com/media/platform/mediarecorder) | **In.** Most sources including DEFAULT apply processing; query the property first; **fallback is `VOICE_RECOGNITION`**; you may still request UNPROCESSED without the property but with no guarantee. Read via extract, page **not opened**. |
| 3 | expo/expo PR #38271 (expose AudioSource on Android) | Out of scope (Expo), but confirms the API surface is the bottleneck people hit. |
| 4 | Medium — AudioRecord guide part 2 | Out. Tutorial. |
| 5 | javatips.net AudioSource examples | Out. Code dump. |

**B2 — `android microphone automatic gain control noise suppression ruins engine sound frequency analysis AudioRecord`**

| # | Result | In / out |
|---|---|---|
| 1 | [source.android.com: Configure preprocessing effects](https://source.android.com/docs/core/audio/implement-pre-processing) **[opened]** | **In — primary source.** See §2.3. |
| 2 | nextcloud/spreed #3252 — disable AGC for practising music | In as corroboration that AGC is the recurring complaint for non-speech audio. Not opened. |
| 3 | bigbluebutton #4873 — disable microphone AutoGainControl | Same. Not opened. |
| 4 | EP1211671A2 — AGC with noise suppression | **In as mechanism.** Per-band noise estimate from successive minima during "noise only" periods → **a steady engine note has stable per-band minima and is therefore classified as noise.** Not opened → unverified. |
| 5 | Picovoice — noise suppression in Android | Out. Vendor blog. |

**B3 — `TarsosDSP Android module removed 2.x "TarsosDSP-Android" gradle dependency`**

| # | Result | In / out |
|---|---|---|
| 1 | [0110.be TarsosDSP tag](https://0110.be/tags/TarsosDSP) | In as author's own record. Not opened. |
| 2 | [0110.be: TarsosDSP on Android](https://0110.be/posts/TarsosDSP_on_Android_-_Audio_Processing_in_Java_on_Android) | **In.** Author's stated Android method is **drop a JAR in `libs/`**, not a Gradle coordinate. Not opened → unverified, but corroborated by the Maven checks in §2.4. |
| 3 | koendv/TarsosDSPKit — ships `TarsosDSP-Android-2.4.jar` | **In as evidence.** Real projects vendor the JAR. |
| 4 | junyoung-jamong blog — how to use TarsosDSP in Android | Out. Tutorial. |
| 5 | [TarsosDSP issue #193](https://github.com/JorenSix/TarsosDSP/issues/193) **[opened]** | **In.** "java: package android.media does not exist". **Still open, created 2020-11-09, no activity since 2020-11-09.** Six years unanswered. |

**B4 — `TarsosDSP android kotlin real time pitch detection AudioDispatcher microphone`**

| # | Result | In / out |
|---|---|---|
| 1 | [AnandaAp/TarsosDSPAndroid](https://github.com/AnandaAp/TarsosDSPAndroid) **[licence checked]** | **Out.** GPL-3.0 (has `license.txt`), but **1★ and last push 2015-03-27**. Dead. |
| 2 | Medium — pitch detection in Android using TarsosDSP | **In for the canonical call shape**: `AudioDispatcherFactory.fromDefaultMicrophone(22050, 1024, 0)` → `PitchProcessor(FFT_YIN, …)`. Not opened. |
| 3 | 0110.be | already counted. |
| 4 | b4x forum | Out. |
| 5 | TarsosDSP issue #23 | Out, ancient. |

**B5 — `kotlin FFT library multiplatform pure kotlin real time android github stars`**

| # | Result | In / out |
|---|---|---|
| 1 | AAkira/Kotlin-Multiplatform-Libraries | Browsing surface. No audio DSP entry found. |
| 2 | Kotlin/multiplatform-library-template | Out. |
| 3 | letalvoj/fft-opencl-kotlin | Out. Aparapi/OpenCL, desktop, needs native libs. |
| 4 | xsoophx/Kymatik | Out for this. JVM, `.wav`-file oriented, low activity, licence **unverified**. |
| 5 | nourome/KotlinSawtti | Out. Author says the port is incomplete. Reference only. |

Conclusion: **there is no maintained Kotlin/KMP FFT library worth depending on.**
A radix-2 Cooley–Tukey is 100–200 lines with precomputed twiddles and reused
`FloatArray`s — which is how her `dsp.spectrum` already works, minus the O(n²).

**B6 — `Oboe AAudio input recording android low latency microphone unprocessed input preset`**

| # | Result | In / out |
|---|---|---|
| 1 | [oboe #728](https://github.com/google/oboe/issues/728) **[opened]** | **In.** Galaxy S8, Android 9: `LowLatency` + `Generic` gave **−63 dB vs −29 dB** input level; `VoiceRecognition` was consistent at −42 dB. **Still open, created 2019-12-16.** |
| 2 | oboe discussion #982 — input preset semantics | In. `Unprocessed` "has no automatic gain control, so recorded volume may be very low." |
| 3 | [oboe #1074](https://github.com/google/oboe/issues/1074) **[opened]** | **In.** "Shouldn't oboe check the availability of input preset `Unprocessed`?" Closed 2020-11-18. Oboe does **not** validate the property. |
| 4 | [oboe #1006](https://github.com/google/oboe/issues/1006) **[opened]** | **In — the hazard, from a real shipped app.** A tuner app on Samsung SM-G965F (Android 10) where `Unprocessed` produced **silence**. Created 2020-09-07, closed 2021-07-07. |
| 5 | docs.rs oboe `InputPreset` | In. `setInputPreset()` needs **API 28+**; `VoicePerformance` is API 29+ and falls back to `VoiceRecognition` below. |

**B7 — `smartphone microphone low frequency response 20 Hz 50 Hz rolloff MEMS mic limitation`** — results 1–5 and the conclusion are in §2.1. All read via search extract; **none opened** → the numbers are unverified, the mechanism is corroborated four ways.

**B8 — `Android AudioRecord 8000 Hz sample rate resampling native 48000 framework which rates guaranteed`**

| # | Result | In / out |
|---|---|---|
| 1 | audio-evolution manual — project sample rate | In. "mobile devices have a single native sample rate… on most modern hardware 48000 Hz." Not opened. |
| 2 | [source.android.com: sample rate conversion](https://source.android.com/docs/core/audio/src) | **In.** Since Android 5.0 resamplers are Kaiser-windowed-sinc FIR; **44.1↔48 is generated at higher quality than arbitrary conversion**, so 8000 Hz takes the generic path; resamplers add passband ripple and aliasing harmonic noise and "should not be used unnecessarily." Not opened → unverified. |
| 3 | alibabacloud Android resample notes | Out. |
| 4 | comp.dsp — "Android microphone sampled at 8000 Hz" | **In as a hazard.** A 4050 Hz tone producing FFT peaks at **4050 and 3950 Hz** — a Nyquist mirror consistent with internal rate conversion. Forum, not opened → unverified. |
| 5 | developer.android.com NDK sampling-audio | In. Pick the device rate; >48 kHz decreases quality because a resampler is used. |

**Consequence for her code.** `audio.SAMPLE_RATE = 8000`. Only 44100 is
historically guaranteed; 8000 on a 48 kHz device means the *framework's* generic
resampler sits in her capture path, ahead of her own `highpass` and her own
box-car `decimate` — the two stages she wrote *because* she wanted to control
exactly this. rpm-boss does not try 8000 at all: `RATES = { 16000, 48000, 44100 }`
(verified in `RawAudioPlugin.java`). Her `decimate` already handles any input
rate (`factor = rate // target`), so opening at 48000 and decimating by 48 is a
one-constant change and strictly better.

**GitHub searches run**

- `"harmonic product spectrum"` — ten results, best 10★. All Python/MATLAB/C hobby code; **every single one with no licence**, except `Jklein64/twang` (GPL-3.0, JUCE plugin) and `peppperrroni/PitchDSP` (C, no licence). **There is no JVM, Kotlin or Android harmonic-product-spectrum implementation in existence.** Her `dsp.harmonic_pick` is the artefact; it has to be ported, not found.
- `"rpm microphone engine"` — zero results.

### 2.3 Does Android's default mic processing corrupt engine-note analysis?

**Yes, and it is architectural, not incidental.** Three verified layers:

**1. The effects are declared per `AudioSource` in a vendor file.** From
`source.android.com/docs/core/audio/implement-pre-processing` (**opened**),
`/vendor/etc/audio_effects.xml` contains entries like:

```xml
<stream type="voice_communication"><apply effect="aec"/><apply effect="ns"/></stream>
<stream type="camcorder"><apply effect="agc"/></stream>
```

and: *"By declaring the `AudioSource` configuration in this manner, the framework
automatically requests the use of those effects from the HAL."* **Your choice of
source decides what happens to the signal before you ever see a byte.** The page
lists no default requirement for `MIC` or `UNPROCESSED` — meaning for `MIC` it is
vendor-dependent, which is the worst possible answer.

**2. Only `VOICE_RECOGNITION` has a documented guarantee.** The same page:
*"For the `VOICE_RECOGNITION` use case, don't enable the noise suppression
preprocessing effect"*, effects *"disabled by default"*. That is the only source
with a stated requirement other than UNPROCESSED — and the CDD's UNPROCESSED
requirement is conditional on the OEM *claiming* support.

**3. The suppression algorithm is designed to remove exactly what she wants.**
EP1211671A2: per-band noise level is estimated by averaging successive minima
during periods when substantially only noise is present. A steady engine note at
constant throttle has *stable per-band minima*. It is, to the algorithm, noise.
And AGC destroys the amplitude relationship her `band_energy` meter exists to
measure — the whole point of that meter, per her `dsp.py` docstring, is that it
"tracks the throttle instead of the wind."

**Corroborated by the one project that actually did it.** rpm-boss's
`RawAudioPlugin.java` (**opened**), verbatim:

> *"Android will not give a web page unprocessed audio: with echo cancellation on
> the platform gates a steady engine note about a second in, and turning it off
> produced silence on one of the two browsers tested. AudioRecord on UNPROCESSED
> bypasses that entire chain."*

"Gates a steady engine note about a second in" is the noise-estimator converging.
That is a first-hand observation from someone who shipped the app.

**The fallback chain, and it must be a chain.** rpm-boss:

```java
SOURCES = { UNPROCESSED, VOICE_RECOGNITION, MIC };
RATES   = { 16000, 48000, 44100 };
```

tried as a nested loop, *and it reports back which source actually opened*, with
the comment: *"UNPROCESSED is optional: a device may fail to open it, or accept
it and quietly route to MIC."* Combined with Oboe #1006 (UNPROCESSED → silence on
a Samsung) and Oboe #1074 (Oboe does not check the property), the requirement is
unambiguous:

1. Query `AudioManager.getProperty(PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED)` —
   returns a `String`, compare to `"true"`, and handle `null`.
2. Try `UNPROCESSED`; fall back to `VOICE_RECOGNITION` (the documented no-AGC,
   no-NS source); fall back to `MIC`.
3. Additionally attempt `AutomaticGainControl.setEnabled(false)`,
   `NoiseSuppressor`, `AcousticEchoCanceler` on the session id. Advisory — OEMs
   may ignore them.
4. **Record which source opened, and put it in the ride log.** This is her
   labelling rule applied to the capture path: an `rpm_est` recorded through
   `MIC` with vendor AGC is a different quality of number from one recorded
   through `UNPROCESSED`, and the log currently cannot tell them apart.
5. **Detect the silent-UNPROCESSED case.** If the level is at the floor for the
   first second, reopen on the next source. Her `QUIET_DBFS = -55.0` gate is
   already the detector; it just needs to trigger a reopen instead of a dash.

Her Flet code already passes `suppress_noise=False, auto_gain=False` — the right
intent. What `flet_audio_recorder` does with that is **unverified** (it wraps the
Dart `record` package; not investigated). Going native replaces a request with a
choice of source plus a report of what was granted.

### 2.4 Option: TarsosDSP

| | |
|---|---|
| Link | <https://github.com/JorenSix/TarsosDSP> |
| Licence | **GPL-3.0 — verified by opening `LICENSE`** |
| Stars / forks | 2192 / 498 |
| Last commit | 2026-06-18 |
| Latest version | **2.5, released 2023-01-09** (verified: `mvn.0110.be` metadata `lastUpdated 20230110145517`) |
| Tags | `v2.3`, `TarsosDSPv2.2`, then oddly-named older tags. **No GitHub releases at all.** |
| Open issues | 113 |
| Android | **Partly — see below** |
| Offline | Yes. "without any other external dependencies." |
| Java level | 11 |

**Distribution, verified by direct HTTP.**

- `be.tarsos.dsp` is **not on Maven Central.** `solrsearch?q=g:"be.tarsos.dsp"` →
  `numFound: 0`. `repo1.maven.org/maven2/be/tarsos/dsp/core/maven-metadata.xml`
  → **404**.
- It lives in the author's own repo, `https://mvn.0110.be/releases`. Listing that
  path returns exactly two artifacts: **`core/` and `jvm/`.**
- There is **no `android` artifact published anywhere.** The Android audio I/O is
  a JAR from the author's release directory, which real projects vendor by hand
  (`koendv/TarsosDSPKit` ships `TarsosDSP-Android-2.4.jar`).
- Issue #193, "java: package android.media does not exist", is **open and
  untouched since 2020-11-09.**

So using TarsosDSP on Android in 2026 means: a third-party Maven repo for the
core, plus a hand-vendored JAR of unknown version for the mic, whose build
breakage has been an open issue for six years. That is not a dependency, it is a
maintenance liability.

**Can it express her chain?** Class list verified from the repo tree.

| Her stage | TarsosDSP | Verdict |
|---|---|---|
| band-limiting | `filters/BandPass`, `HighPass`, `LowPassFS`, `LowPassSP`, `util/BiQuadFilter` | **Yes.** But `BandPass` is a crude two-pole resonator (`R = 1 - 3*bw`, verified in source), not the Butterworth cascade rpm-boss uses. Her own one-pole 18 Hz high-pass is equally crude and equally adequate. |
| decimation | `Resampler` (libresample4j port) | Yes — heavier than her box-car, and a real anti-alias filter. |
| Hann window | `util/fft/HannWindow` + 11 others | **Yes.** |
| FFT | `util/fft/FFT`, `FloatFFT` (JTransforms-derived) | **Yes**, and a real O(n log n) FFT where hers is O(n²). |
| harmonic product spectrum | **nothing** | **No.** Not present, and per the GitHub search, not present anywhere on the JVM. |
| contrast vs band median | **nothing** | **No.** Hand-written. |
| candidate floor | **nothing** | **No.** Hand-written. |
| octave-error guard | — | No. Hers is the continuity gate; YIN's is its own threshold. |
| pitch, as an alternative | `Yin`, `FastYin`, `McLeodPitchMethod`, `AMDF`, `DynamicWavelet`, `Goertzel`, `GeneralizedGoertzel` | Yes — but see below. |

**Why YIN/MPM are not a drop-in for her picker.** Verified by reading
`Yin.java`: YIN takes `(sampleRate, bufferSize, threshold=0.20)` and searches
**every lag in the buffer**. There is no minimum or maximum frequency parameter.
Her whole method is *band-restricted* search — `bikes.band_hz()` exists so that
"every extra hertz is another chance for wind noise to win." YIN cannot be told
the band; you would have to band-pass hard in front of it and accept whatever it
returns. It also gives no equivalent of `contrast` (it has `getProbability()` and
`isPitched()`, which are YIN's own dip depth, not a ratio against the band's
background), and it has no harmonic-product logic, so the octave error her whole
`harmonic_pick` docstring is about is back on the table.

`GeneralizedGoertzel` is the interesting one nobody mentions: a Goertzel
evaluates the DFT at *specific* frequencies at O(n) each. For a band-restricted
search over ~190 bins, a Goertzel bank is arguably a better fit to her problem
than either an FFT or YIN. Not pursued further — flagging it.

**Licence consequence.** GPL-3.0 is a licence, so it is not disqualified. But
linking it makes the whole app GPL-3.0 on distribution. For a personal APK
side-loaded onto her own phone, no obligation is triggered — GPL duties attach to
*distribution*. If it ever goes on Play, the app's source goes with it.
Additionally, its own README describes several borrowed components as GPL'd
(libresample4j, Minim windows, BeatRoot, RasmusDSP) — so you cannot cherry-pick
files out of it and claim a cleaner licence than GPL-3.0 for those parts.

One README claim is **wrong and worth correcting**: TarsosDSP states its FFT is
"by Piotr Wendykier and is included in his **GPL'd** JTransforms library."
**JTransforms' current `LICENSE` file, opened directly, is 2-clause BSD.** (Older
JTransforms was tri-licensed MPL/LGPL/GPL, which is presumably where that
sentence comes from.) If the FFT is the only thing wanted, take it from
JTransforms under BSD, not from TarsosDSP under GPL.

### 2.5 Option: JTransforms / kissfft / Oboe — a hand-rolled pipeline

**JTransforms**

| | |
|---|---|
| Link | <https://github.com/wendykierp/JTransforms> |
| Licence | **BSD-2-Clause — verified by opening `LICENSE`.** (GitHub reports `NOASSERTION` because the text has no SPDX header; the API's guess is wrong, the file is not.) |
| Stars / forks | 430 / 94 |
| Last commit | 2025-12-05; `pom.xml` says `3.3-SNAPSHOT` |
| Released artifact | **`com.github.wendykierp:JTransforms:3.1`, published 2015-09-08** — a decade old. Also `org.openimaj:JTransforms:1.3.10` (2020) and `edu.emory.mathcs:JTransforms:2.4` (2012). |
| Android | **Unverified.** Pure Java, `release 11`, no `javax.sound` — should work. Multithreaded by default (`ConcurrencyUtils`), which on a phone wants turning off. |
| Offline | Yes. |
| Speed | The reference pure-Java FFT. Far faster than her O(n²) DFT. |

**Fit.** Gives exactly one stage — the transform. Her `highpass`, `decimate`,
`hann`, `harmonic_pick`, `contrast`, `band_energy`, `band_levels` all port as-is.
That is ~250 lines of pure arithmetic with no platform imports, which is the best
possible shape for the "core with no platform dependencies, unit tested" rule,
and her existing Python tests port with it. The only real question is whether to
depend on a 2015 artifact or vendor the two files needed (BSD-2 permits it).

**kissfft**

| | |
|---|---|
| Link | <https://github.com/mborgerding/kissfft> |
| Licence | **BSD-3-Clause — verified by opening `COPYING`**, which carries an explicit `SPDX-License-Identifier: BSD-3-Clause`. (GitHub says `NOASSERTION`; wrong again.) |
| Stars / forks | 1987 / 364 |
| Last commit | 2026-08-12 |
| Android | Via NDK/JNI only. C, plus a `kissfft.hh` C++ header and `kissfft_i32.hh` fixed-point. |
| Offline | Yes. |
| Speed | Fast, tiny, fixed-point variant available. |
| Cost | **An NDK build, a JNI boundary, and four ABIs, to accelerate a 512-point transform that runs once per second.** |

**Verdict shape.** 512-point real FFT, once per second. That is microseconds in
Java. kissfft buys nothing here and costs a native build. It is the right answer
only if the analysis rate goes up by two orders of magnitude.

**Oboe**

| | |
|---|---|
| Link | <https://github.com/google/oboe> |
| Licence | **Apache-2.0** (API-reported; `LICENSE` file **not opened** → unverified, though Apache-2.0 for a Google repo is not in serious doubt) |
| Stars / forks | 4115 / 639 |
| Latest release | **1.11.0, 2026-09-15** — actively maintained |
| Android | Yes. AAudio on API 27+, OpenSL ES below. |
| Offline | Yes. |
| What it is | **Capture, not analysis.** C++ wrapper for low-latency I/O. |

**Does she need it?** Her analysis window is 512 ms and recomputes at 1 Hz. The
latency Oboe exists to remove is irrelevant at that cadence. What Oboe *would*
give is `InputPreset::Unprocessed` from C++ and a well-trodden device-quirk path
— but with the caveats verified above: `setInputPreset` needs **API 28+**, Oboe
**does not check** `PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED` (#1074), and
`Unprocessed` produced **silence** on at least one Samsung (#1006), plus severe
level differences between performance modes on another (#728). Plain
`AudioRecord` from Kotlin — which is what rpm-boss uses, deliberately, and its
comment says why — gives the same source selection with no NDK and with the
fallback loop under your own control.

**Assessment:** Oboe is the right tool for a synth or a tuner. For a 1 Hz
tachometer it is a native toolchain bought for nothing.

### 2.6 Option: rpm-boss

| | |
|---|---|
| Link | <https://github.com/AregShahbazian/rpm-boss> |
| Licence | **MIT — verified by opening `LICENSE`** ("Copyright (c) 2026 Areg Shahbazian") |
| Stars / forks | **0 / 0** |
| Created | **2026-09-10.** Last push **2026-09-15.** Sixteen days old at time of writing. |
| Language | **TypeScript** — a Vite/React web app wrapped in **Capacitor 8**, plus one native Java plugin |
| Android | Yes, via Capacitor. Signed APK and AAB scripts, JDK 21 required. |
| Offline | **Yes, emphatically.** "Runs entirely on the device: no backend, no account, no network." |
| Tests | **Yes, and this is the point.** `npm test` grades the analysis against seven real recordings with independently verified pulse counts. |

**What it actually does** (verified from `docs/dsp.md`, `src/dsp/envelope.ts`,
`src/dsp/analyse.ts`, `src/dsp/autocorr.ts`, `src/dsp/biquad.ts`,
`src/dsp/types.ts`, `RawAudioPlugin.java` — all opened):

```
band-pass 60–2000 Hz (2nd-order Butterworth HP ∘ LP, zero-phase filtfilt
   with scipy-compatible odd extension)
 → rectify (abs)
 → low-pass 150 Hz (four-stroke) → the combustion envelope
 → autocorrelate the envelope over 1 s windows, search 600 rpm … MAX_RPM ±10 %
 → median across windows
 → cross-check by counting envelope peaks
 → confidence = peak-above-trough contrast; refuse below 0.45
 → optional octave resolution against a user-supplied rpm range
 → draw a mark on every combustion counted
```

Its biquad coefficients are stated to come out identical to
`scipy.signal.butter(2, f, fs=sr, output='sos')`, and there is a Python reference
implementation (`scripts/reference/analyse.py`) that the TypeScript mirrors and
the fixtures grade. That is a better testing discipline than most commercial
audio code.

**The test corpus, which is the real asset** (`audio/combustion-counts.md`,
opened):

| file | duration | pulses | rpm (autocorr) |
|---|---|---|---|
| sample-1 … sample-7 | 6.4 – 15.9 s | 75 – 212 | **1397 – 1797** |

Plus five deliberate non-engine clips with expected confidences (white noise
0.22, quiet room 0.21, rumble 0.21, speech 0.00 — all refused) and one **known,
documented failure**: a steady 1 kHz tone scores **1.77 and is wrongly
accepted**, because "a flat envelope correlates with itself at every lag." They
kept it in the repo so the limitation is demonstrable rather than theoretical.

**Where it does not fit her bikes, verified from the source.**

1. **`RevsPerPulse = 1 | 2`.** A literal union type: 2 for a four-stroke single,
   1 for a two-stroke. **An inline four needs 0.5 and a parallel twin needs 1.0.**
   The twin fits by accident; the CB750 does not fit at all without widening the
   type.
2. **`MAX_RPM = 12_000` → `MAX_RATE = 100` pulses/s.** CB750 at 9500 rpm fires
   **316.7** times a second; EX250 at 14000 fires **233.3**. Both are outside the
   search range as shipped.
3. **`ENVELOPE_HZ = 150`.** The smoothing low-pass. At 316.7 pulses/s the ripple
   the autocorrelation must find *is removed by the smoothing filter.* This is
   not speculation — they hit exactly this on a two-stroke and wrote it down:
   "past 6,000 the reading fell to a subharmonic."
4. **`MIN_ANALYSIS_S = 2`, `WINDOW_S = 1`.** Two seconds minimum, 1 s windows,
   median across windows. Her chain answers from **512 ms** and updates at 1 Hz.
   rpm-boss is inherently slower to follow the throttle.
5. **Aimed at the wrong engines.** README: "Targets simple engines first: single
   cylinder, not running too smoothly, such as a motorcycle or generator at
   idle." Every fixture is a **single cylinder at idle, 1400–1800 rpm**. There is
   no multi-cylinder recording, and no recording at road speed, anywhere in the
   corpus.
6. **Capacitor/TypeScript.** The DSP is TS; the whole stack is a WebView. Taking
   the *method* means reimplementing it (MIT permits it freely); taking the *code*
   means adopting Capacitor.

**But 1–3 are not deep problems.** `rateScale(revsPerPulse) = 2/revsPerPulse`
already threads the engine's firing rate through *every* constant — the
smoothing, the searched range — not just the final multiply. Widen the type to
include 0.5 and it generalises: for an inline four, `rateScale = 4`, envelope
smoothing 600 Hz, max rate 400 pulses/s. The architecture anticipated this. It is
a small, well-localised change, and the seven fixtures keep it honest while you
make it.

Item 5 is the real unknown, and it is not a licence or a code problem but a
**physics** one: at 6000 rpm an unbaffled inline four fires 200 times a second,
and successive combustion pulses overlap heavily. Whether an envelope still has
recoverable ripple at that rate on a four is **unverified and untested by anyone
in this file.** Her `bikes.py` docstring already knows the shape of this problem
from the other side ("a four-cylinder would have been silently capped around
6000 rpm").

**What is unambiguously worth taking, at zero licence cost.**

- **The UNPROCESSED source-selection loop and the "report which source opened"
  discipline.** ~60 lines, MIT, directly liftable.
- **The seven verified recordings plus five noise clips.** MIT-licensed test
  fixtures for exactly this problem. Her `audio.analyse()` is pure and takes
  `(samples, rate, machine, previous_rpm)` — it can be run against these files
  today, in Python, before a line of Kotlin is written. Her chain has never been
  tested on a real engine; theirs has been tested on seven. **This is the
  cheapest high-value thing in this entire document.**
- **The "draw a mark on every combustion" idea.** Their reasoning: "The method
  fails by a factor of two when it fails… a comb that skips every other beat is
  obvious to a person and invisible in a number." That is her own
  measured/derived/estimated philosophy taken one step further — showing the
  *evidence* for the estimate, not just a confidence. She has no equivalent.
- **The octave-resolution-against-a-known-range trick.** She has the range
  already (`bikes.plausible`), used only as a reject. rpm-boss uses it to *fix*
  the answer: if the estimate does not fit, try half and double, and at most one
  can fit. Strictly more useful than rejecting.

**Convergence worth noting.** Two independent implementations, neither knowing
the other, both concluded: refuse rather than invent; gate on a contrast/
confidence measure; treat the octave error as *the* failure mode; carry the raw
frequency/rate alongside the rpm so a wrong engine profile can be recomputed.
Her `MIN_CONTRAST = 8.0` and their `MIN_CONFIDENCE = 0.45` are the same idea in
different units.

### 2.7 (b) summary

| Option | Licence | Active | Android | Offline | Expresses her chain | Speed | Verdict |
|---|---|---|---|---|---|---|---|
| **TarsosDSP** | **GPL-3.0 ✓** | commit 2026-06; **v2.5 = 2023-01** | **Partial** — no published Android artifact, JAR vendored by hand, #193 open since 2020 | yes | filters + FFT + windows **yes**; HPS, contrast, floor **no** | good | Gives 4 of 8 stages, brings GPL-3.0 and a broken Android story. |
| **JTransforms** | **BSD-2 ✓** (opened; API's `NOASSERTION` is wrong) | commit 2025-12; **artifact 3.1 = 2015** | pure Java, **unverified on Android** | yes | the FFT only — but that is the only stage she is missing | fastest pure-Java FFT | Smallest honest dependency. One stage, permissive, no platform coupling. |
| **kissfft** | **BSD-3 ✓** (SPDX in `COPYING`) | 2026-08-12 | NDK/JNI only | yes | FFT only | fastest, fixed-point available | Buys an NDK build to speed up a 512-point transform run once a second. |
| **Oboe** | Apache-2.0 (unverified) | **1.11.0, 2026-09-15** | yes | yes | **capture, not analysis** | irrelevant at 1 Hz | Solves latency she does not have; #1006/#1074/#728 say it does not even solve UNPROCESSED reliably. |
| **rpm-boss** | **MIT ✓** | created **2026-09-10**, 0★ | yes (Capacitor) | yes | **a different, arguably better chain** | slower (2 s min) | Method + UNPROCESSED code + **7 verified recordings**. Constants are single-cylinder; type is `1 \| 2`. |
| Plain `AudioRecord` + her own DSP ported | platform + BSD FFT | n/a | yes | yes | **exactly, by construction** | fine | The only option that reproduces the spec without reinterpretation. |
| AnandaAp/TarsosDSPAndroid | GPL-3.0 ✓ | **2015-03-27, 1★** | yes | — | — | — | Dead. |
| every HPS repo on GitHub | **NONE ✗** (10 of 10) | — | no | — | — | — | **Ruled out.** No JVM HPS exists. |

---

## 3. Sub-question (c) — other inputs

### 3.1 OBD-II: verified out, for both bikes

The brief said verify rather than assume. Verified, and it is not close.

**1993 Honda CB750 Nighthawk (CB750A)**

- Fully carbureted. OEM parts catalogues list a "Carburetor Components" group
  (Partzilla, RevZilla, CMSNL for the 1993 (P) USA model).
- Ignition is a standalone **ICM** with a pulse generator, not an engine
  computer. The factory manual's troubleshooting points at "faulty ignition
  control module / pulse generator". An ICM **stores no trouble codes and has no
  serial output.**
- Maintenance schedule is carb sync, idle speed, choke, fuel screw — no sensor
  data anywhere.
- Honda's own diagnostic tooling postdates the bike: PGM-FI arrived late 1980s
  and **HDS was introduced for MY2004.**
- OBD-II was a passenger-car emissions mandate; motorcycles were never swept in.

**Conclusion: no port, no ECU, nothing to interrogate.** *(Sourced from parts
catalogues, the service manual via ManualsLib, and a Honda owners' forum thread;
pages read via search extract, **not opened** → the individual citations are
unverified, but four independent sources agree and the mechanical fact — a
carburettor — is not disputable.)*

**2006 Kawasaki Ninja 250R (EX250-F6F)**

- Carbureted: twin Keihin CVK30. Kawasaki's microfiche lists "Carburetor Parts"
  for the EX250-F6F.
- The EX250-F ran essentially unchanged 1988–2007 with a **CDI ignition box**,
  not an ECU.
- **KDS** (Kawasaki Diagnostic System) exists *only on fuel-injected* Kawasakis.
  The "Self Diagnosis System Connector" is a 4-pin (twins) or 6-pin (fours) plug
  on FI models. **The EX250-F harness has neither that connector nor an FI
  warning light.**
- Even where KDS exists it is **KWP2000 over a K-line**, not OBD-II; DIY adapters
  remap pins to an OBD-II socket and need a KWP2000-capable scanner.
- The first Ninja-250-class bike with real diagnostics is the fuel-injected
  **Ninja 300 (2013+)**. Even the 2008+ EX250J is still carbureted.

**Conclusion: no port, no ECU.** *(Same sourcing caveat: forums, a parts
microfiche and an adapter vendor's product page, read via extract, not opened →
unverified individually, consistent across five sources.)*

**And an ELM327 cannot help.** It is a protocol translator, not a data source.
It will power up and pair and never open a session, because nothing is
transmitting. Verified in principle across six troubleshooting threads (cars —
the search returned no motorcycle-specific result, which is itself informative).

**So `kotlin-obd-api` is correct, well-maintained, and useless here.**

| | |
|---|---|
| Link | <https://github.com/eltonvs/kotlin-obd-api> |
| Licence | **Apache-2.0** (API-reported, `LICENSE` file present; file **not opened** → unverified) |
| Stars / forks | 234 / 38 |
| Last commit | 2026-07-31. Version **1.4.1** via JitPack. CI green. |
| Language | Pure Kotlin, **platform-agnostic** — takes an `InputStream` and an `OutputStream`, so transport is yours (Bluetooth, Wi-Fi, USB) |
| Offline | Yes, once connected to a dongle. |
| Android | Yes (Kotlin/JVM). |

It is genuinely the right library for this job — connection-agnostic, tested,
maintained, and its `InputStream`/`OutputStream` shape would slot into a
`TelemetrySource` abstraction cleanly. There is simply no ECU on either bike for
it to talk to. **Out, on the hardware, not on the software.**

Worth recording for a future bike: `topic:obd2 language:kotlin`, opened, lists
ten repos; the only motorcycle one is **[sertels/MotoTelemetryApp](https://github.com/sertels/MotoTelemetryApp)**
(MIT, 2★, pushed 2026-08-15) — "live OBD-II/UDS engine data, GPS/IMU lean &
G-force tracking, and ride logging over Bluetooth ELM327" for a Voge 900DSX. MIT
and structurally the closest thing to Navi's telemetry half that is actually
licensed for reuse. **Not opened beyond its description.** If a fuel-injected
bike ever appears, start there.

### 3.2 BLE: Kable vs Nordic (and Nordic vs Nordic)

| | Kable | Nordic Android-BLE-Library | Nordic Kotlin-BLE-Library |
|---|---|---|---|
| Link | [JuulLabs/kable](https://github.com/JuulLabs/kable) | [NordicSemiconductor/Android-BLE-Library](https://github.com/NordicSemiconductor/Android-BLE-Library) | [NordicSemiconductor/Kotlin-BLE-Library](https://github.com/NordicSemiconductor/Kotlin-BLE-Library) |
| Licence | **Apache-2.0** | **BSD-3-Clause** | **BSD-3-Clause** |
| Licence verified | API-reported, file not opened → **unverified** | API-reported → **unverified** | `LICENSE` file present → **unverified** |
| Stars | 1198 | **2415** | 528 |
| Language | Kotlin, **multiplatform** (Android/iOS/JS) | Java (+ `ble-ktx`) | Kotlin |
| Last commit | **2026-09-25** | 2026-07-10 | **2026-09-26** |
| Latest release | **0.45.0, 2026-09-15** | **2.11.0, 2025-09-11** | **2.0.0-beta05, 2026-08-07** (v1 line: `1.1.0`) |
| Distribution | Maven Central | **Maven Central**, `no.nordicsemi.android:ble:2.11.0` | Maven Central, per-module |
| Android | yes | yes | yes |
| Offline | yes | yes | yes |
| API style | coroutines / `Flow` | callbacks + `BleManager`, automatic retries, service discovery | `suspend` / `Flow` throughout |

**The deprecation, verified by opening the README of Android-BLE-Library:**

> *"We're working on a new version of Bluetooth LE library, which eventually will
> replace this one: Kotlin BLE Library / version 2.0. Mind, that version 2 is not
> backward compatible with version 1 or with this library. **Currently, the
> library is in the early development stage. It is not recommended for production
> use**…"*

So all three carry a defect:

- **Android-BLE-Library** — most proven at absorbing Android's BLE quirks
  (2415★, `BleManager` with automatic retries), on Maven Central, but officially
  superseded and Java-first. Version still shipping (2.11.0, 2025-09-11).
- **Kotlin-BLE-Library** — the designated successor, and its **v2 is explicitly
  not production-ready by its own author.** The v1 line (1.1.0) is Beta, and a
  full rewrite is landing. Its standout feature is **mock peripherals** — define
  a simulated device with services and advertising, then test with no hardware.
  For an ESP32 tacho that does not exist yet, that is not a nicety; it is how you
  write the code before you build the box.
- **Kable** — cleanest Kotlin, active weekly, Apache-2.0, and the only one that
  is multiplatform. Version **0.45.0** is the honest signal: pre-1.0. Community
  reports (kotlinlang Slack, libhunt; **not opened** → unverified) mention
  dropped packets when subscribing to notify, which for a tachometer notify
  stream is precisely the wrong bug.

**Note on `dotintent/awesome-ble` (opened, CC0-1.0):** it lists FastBle, the
Nordic SDK, RxAndroidBle and SmartGattLib, and does **not** list Kable, BLESSED,
or Nordic Kotlin-BLE-Library. The canonical awesome-list is stale. Do not treat
its silence as a judgement.

Also surfaced and licence-verified: **[weliem/blessed-kotlin](https://github.com/weliem/blessed-kotlin)**
— MIT, 91★, pushed 2026-06-02, 100 % Kotlin, Android 9+, queues commands and
handles bonding. Smaller and less known than the above; a real third option.

**What this actually has to carry.** One device, one service, one characteristic,
notify, ~10–100 Hz. Every library above is enormous overkill for that. The
deciding factors are not features but: (i) does it reconnect reliably when the
bike is started and the ESP32 powers up after the phone, and (ii) can you test
it without the hardware. That second question has exactly one answer, and it is
Nordic Kotlin-BLE-Library's mock peripherals — from the library its own author
says is not production-ready.

### 3.3 Inductive pickup → ESP32 → BLE

**This is the only option that gives exact RPM at any revs, and it is also the
only one that requires building hardware.**

Three firmware references, all found, all **ruled out as code**:

| Repo | Licence | Stars | Last push | Why it matters |
|---|---|---|---|---|
| [iliaskk/esp32-digital-tachometer](https://github.com/iliaskk/esp32-digital-tachometer) | **NONE ✗** (root listed) | 0 | 2026-09-03 | The closest match. `IRAM_ATTR` ISR for microsecond spark capture, scoped critical sections isolating ISR writes from main-loop reads, **handles the 71-minute `micros()` rollover** so it does not lock up when the engine stops, PC817/4N35 optocoupler isolation. Single-cylinder, SPI TFT output, no BLE. |
| [rverzinkevicius/tacho_speedo](https://github.com/rverzinkevicius/tacho_speedo) | **NONE ✗** | 10 | 2020-08-26 | ESP8266. **The inductive coupling method: wrap a few turns of wire around the plug lead — never connect to it.** 4N35 from the pickup coil; circuit can be omitted if the pickup signal stays under 3.3 V. |
| [robkwhitehouse/ESP32-Arduino-Tachometer](https://github.com/robkwhitehouse/ESP32-Arduino-Tachometer) | **NONE ✗** | 1 | 2021-01-17 | Bluetooth **Classic SPP**, not BLE. IR sensor front end. Shows the wireless-to-Android path. |

Also: `cqeta1564/MotoLogger` — **no licence**, but it is the strongest evidence
for the *approach*: an ESP32 with a **BNO085** 9-DoF coprocessor running Game
Rotation Vector at 100 Hz, chosen explicitly because it is "immune to engine
vibration and alternator magnetic interference." That is the same conclusion §1.4
reached about vibration rectification error, arrived at by someone who mounted
hardware on a bike. It reads RPM over CAN, which her bikes do not have.

**No project combines inductive pickup + ESP32 + BLE GATT.** None. The build is:
ISR/pulse-timing logic (design, not code, from iliaskk), inductive coupling
(design from rverzinkevicius), BLE notify (NimBLE-Arduino — licence **not
checked**), and the Android side from §3.2. Every firmware licence is absent, so
none of it can be copied — but **a circuit is not copyrightable code**, and the
ISR *approach* is public knowledge. The `micros()` rollover and the need for a
comparator or Schmitt trigger plus software debounce (an inductive pickup on a
plug lead rings badly) are the two lessons worth carrying.

**Against her rules.** Works with no signal: yes, better than anything else here
— it is a wire. Fast: yes, exact at any revs, no DSP, no window. Labelling: this
is the big one — **RPM from an ignition pickup is `measured`, not `estimated`.**
It would be the first genuinely measured engine-speed channel in the ride log,
and it would make `gears.estimate` exact rather than confidence-weighted, at all
revs, with the microphone off. Microphone rule: **it removes the microphone from
the critical path entirely** — the mic becomes a loudness meter and a fallback,
which is what her `audio.py` docstring wants it to be.

**Against her constraints.** It is a soldering iron, an optocoupler, a box, and a
power tap on a 33-year-old bike, ×2 because two bikes. It is the highest-value
and highest-effort option in this entire document. Everything in §1 and §2 is
software; this is not.

### 3.4 (c) summary

| Option | Licence | Active | Android | Offline | Works on her bikes | Verdict |
|---|---|---|---|---|---|---|
| **kotlin-obd-api** | Apache-2.0 (unverified) | 2026-07-31, v1.4.1 | yes | yes | **NO — neither bike has an ECU or a port** | Right library, no hardware. Out on the bikes. |
| ELM327 dongle | n/a (hardware) | n/a | yes | yes | **NO** | Nothing to translate. |
| MotoTelemetryApp | MIT ✓ | 2026-08-15, 2★ | yes | yes | no (Voge/BMW F900) | Bookmark for a future FI bike. |
| **Kable** | Apache-2.0 (unverified) | **2026-09-25, 0.45.0** | yes | yes | n/a | Cleanest Kotlin + KMP, pre-1.0, reported notify packet loss (unverified). |
| **Nordic Android-BLE-Library** | BSD-3 (unverified) | 2026-07-10, 2.11.0 | yes | yes | n/a | Most proven, Maven Central, **officially superseded**, Java-first. |
| **Nordic Kotlin-BLE-Library** | BSD-3 (unverified) | **2026-09-26** | yes | yes | n/a | Successor; **v2 "not recommended for production" per its author**; **mock peripherals** are uniquely valuable here. |
| blessed-kotlin | MIT ✓ | 2026-06-02, 91★ | yes | yes | n/a | Smaller third option, 100 % Kotlin. |
| **ESP32 + inductive pickup** | **all 3 firmware refs: NONE ✗** | 2026-09-03 (best) | via BLE | yes | **YES, on both** | Only `measured` RPM available. Circuit is reusable, firmware is not. Requires hardware. |
| MotoLogger (ESP32 + BNO085) | **NONE ✗** | 2026-09-17 | — | — | needs CAN | Out as code. Confirms off-phone IMU as the vibration answer. |

---

## 4. Cross-cutting findings

**Four repos in `00-already-checked.md` now have verified licences, and three are
worse than hoped.** Root directories listed directly:

| Repo | Was | Is |
|---|---|---|
| `braydenzhao/MotorcycleLeanAngleSensor` | "not verified" | **NO licence file. Out as code.** |
| `khoa083/map-offline-navigation` (Kblack) | "not verified" | **GPL-3.0.** |
| `francescobuzle-a11y/navmaster` | "check this first — everything else is moot" | **NO licence file. Out as code.** §1's leading candidate is unusable. |
| `apacha/sensor-fusion-demo` (new) | — | **NO licence file**, despite a search summary claiming MIT. |

Not my section, but navmaster is the one `00-already-checked.md` said to check
before anything else, and the answer changes §1. Flagging, not deciding.

**Licence metadata from the GitHub API is wrong in both directions.** Verified
counter-examples: JTransforms reports `NOASSERTION` but its `LICENSE` is
**BSD-2-Clause**; kissfft reports `NOASSERTION` but `COPYING` carries an explicit
`SPDX-License-Identifier: BSD-3-Clause`; FSensor's file is `license` lowercase.
Conversely, search-engine summaries claimed MIT for two repos with no licence file
at all. **Open the file. Every time.** The brief's rule was right.

**"Latest commit" and "latest release" are different questions, and the gap
decides things.** FSensor: commit 2026-05-25, release 2024-08-25, and **the
Madgwick/Mahony/EKF classes exist only in the gap.** TarsosDSP: commit
2026-06-18, version 2.5 from 2023-01-09. JTransforms: commit 2025-12-05, artifact
from **2015**. In all three cases the documented library is not the published
library.

**Her 8 kHz capture rate should change regardless of every other pick.** Only
44100 was ever platform-guaranteed; 8000 on a 48 kHz device inserts the
framework's *generic-ratio* resampler ahead of her own filter chain, and there is
a field report of Nyquist mirror images from exactly that path. `dsp.decimate`
already takes any input rate. Open at the device rate, decimate herself.

**Her 1 Hz GPS should probably change too.** The one independent implementation of
her exact lean formula chose **5 Hz over 1 Hz** for better results. A 1 Hz
heading difference is a one-second average of yaw rate, which flattens corner
entry and exit — the part of a ride a lean gauge exists to show.

**Both remaining new channels are honest additions to `CHANNELS`, and both are
`derived`:** `lean_imu_deg` (filter roll, cornering-resultant corrected) and
`lean_agree` (the residual, dashed when only one estimator is live). Plus one
`measured` string that is currently invisible and should not be: **which audio
source actually opened.**

---

## 5. Confidence note

**Opened directly and quoted:** Android CDD §5.11 (nougat-dev branch);
`source.android.com/docs/core/audio/implement-pre-processing`;
`developer.android.com` position-sensors page; Oboe issues #728, #1006, #1074;
TarsosDSP issue #193, `LICENSE`, `README.md`, and the full class tree, plus
`Yin.java` and `BandPass.java` source; JTransforms `LICENSE` and `pom.xml`;
kissfft `COPYING`; FSensor `license`, `README.md`, the `master` tree and the
**`3.0.7` tag tree**; Nordic Android-BLE-Library and Kotlin-BLE-Library READMEs;
`kotlin-obd-api` README; `dotintent/awesome-ble`; `github.com/topics/obd2?l=kotlin`;
`github.com/topics/android-sensors`; the Hackaday lean-angle log; and for
rpm-boss: `LICENSE`, `README.md`, `docs/dsp.md`, `docs/android.md`,
`audio/combustion-counts.md`, `audio/testing/README.md`,
`RawAudioPlugin.java`, `src/dsp/analyse.ts`, `envelope.ts`, `biquad.ts`,
`types.ts`, `autocorr.ts`.

**Verified by direct HTTP, not by reading a page:** absence of `be.tarsos.dsp` on
Maven Central (`numFound: 0`, plus a 404 on `repo1.maven.org`); the two-artifact
listing at `mvn.0110.be/releases/be/tarsos/dsp/`; JTransforms' three Maven
Central coordinates and dates; FSensor's JitPack `latestOk` = 3.0.7; the root
directory listing (hence licence-file presence or absence) of nine repositories.

**Every star count, licence field, commit date and release date** came from the
GitHub REST API or `gh`, on 2026-09-26. Star counts move.

**Read only via search-result extracts, page not opened — treat as unverified:**
the RoNIN 10–20°/10min drift figure; every MEMS-microphone roll-off number
(Infineon, Knowles); every academic lean-angle paper (Boniolo/Savaresi and the
two comparison studies, all paywalled); the Racelogic IMU guidance (**HTTP 403**);
all automotive plausibility-check patents; EP1211671A2; the VRE papers; every
motorcycle forum and parts-catalogue citation behind §3.1; the community reports
of Kable notify packet loss; `MediaRecorder` overview and
`source.android.com/docs/core/audio/src`.

**Stated as predictions, not findings, and needing a measurement before they
decide anything:**

1. That her EX250's 21.7 Hz idle fundamental (and CB750's 33.3 Hz) falls below
   the phone's microphone roll-off far enough to be rejected by
   `MIN_CANDIDATE_FRACTION` or `MIN_CONTRAST`. Derived from two verified specs
   (CDD ±20 dB below 100 Hz; MEMS LFRO 15–75 Hz). **Test: record either bike at
   idle on `UNPROCESSED` and look.**
2. That an envelope-autocorrelation chain still has usable ripple on an inline
   four at 200–317 combustions per second. Nobody in this file has tested it;
   rpm-boss's own corpus stops at a single cylinder at idle.
3. That a Madgwick filter's roll, cornering-resultant corrected, agrees with
   `atan(v·ω/g)` closely enough for a residual gate to be meaningful. Depends on
   vibration rectification error magnitude on her specific phone and mount, which
   nothing here measures.

**Search count:** 7 web + 4 GitHub for (a); 8 web + 2 GitHub for (b); 5 web +
2 topic-page fetches for (c). Where fewer than five results of a search were
opened, the unopened ones are named in the log with the reason.

**No `CHOICES.md` section was edited. Nothing here is a pick.**
