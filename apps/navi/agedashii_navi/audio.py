"""The exhaust note: loudness now, engine speed if it can be found.

Her ask: "a sort of 'volume' meter slash pseudo rpm".

Two different confidences in one module, and the difference matters:

**Loudness is a measurement.** RMS of the PCM window, in dBFS. It is exactly as
trustworthy as the microphone, and it is what the meter shows.

**RPM is an estimate, and it is allowed to fail.** The firing frequency of a
four-stroke is `rpm / 60 * cylinders / 2`, so the fundamental of the exhaust note
maps back to engine speed - but only while the engine is the loudest thing in the
20-200 Hz band. At 70 mph in a helmet it usually is not, because wind noise is
broadband and enormous. So `latest()` reports no `rpm_est` whenever the band has
no clear peak, and the gauge shows a dash rather than a number. A tachometer that
invents a plausible figure out of wind noise is worse than no tachometer.

WHY THIS NEEDS NO CUSTOM EXTENSION

`flet-audio-recorder` has an `on_stream` handler that delivers raw PCM16 chunks
(`AudioRecorderStreamEvent.chunk`). That is a live audio buffer from an official
package - precisely what was assumed to be missing when the RPM channel was
first written off as "absent".

COST

8 kHz mono, deliberately. The fundamental of interest tops out near 200 Hz, so
8 kHz is twenty times Nyquist and the cheapest rate any recorder will agree to.
The DFT is a few hundred bins over a 1024-sample window and runs at most once per
`RPM_EVERY_S`, so the microphone costs a few milliseconds a second rather than a
core.

The microphone is OFF unless she turns it on. It is a microphone.
"""
import array
import math
import time

import flet as ft

from . import bikes, dsp

#: What the recorder is asked for. Mono, because a stereo stream doubles the
#: bytes to carry the same information about one engine.
SAMPLE_RATE = 8000
CHANNELS = 1
#: Samples in the analysis window. 4096 at 8 kHz is 512 ms, which after
#: decimation to 1 kHz leaves 512 samples and a bin just under 2 Hz wide - about
#: 59 rpm on an inline four. A shorter window cannot separate two engine speeds a
#: rider would call different.
WINDOW_SAMPLES = 4096
#: How often the RPM estimate is recomputed. Loudness updates on every chunk.
RPM_EVERY_S = 1.0
#: How long a reading may stand after the last window that ACCEPTED it.
#:
#: A rejected window used to leave the previous figure up for ever, and that
#: figure was also `previous_rpm` - the continuity reference every later window
#: is judged against. So once a stale number was standing it rejected the REAL
#: engine speed as "an implausible jump from the last reading", for as long as
#: it stood, and the gauge held a wrong number that defended itself.
#:
#: Two seconds rides out one bad window in a corner without letting a dead
#: reading become the thing that keeps the live one out.
RPM_HOLD_S = 2.0
#: Below this the band is silence, not an engine, and no RPM is claimed.
QUIET_DBFS = -55.0
#: Full scale for a 16-bit sample.
FULL_SCALE = 32768.0

#: The most an engine can plausibly change in one second, as a fraction of its
#: redline. A pick implying more than this is a bad pick, not a throttle blip:
#: nothing spins up 60% of its rev range in a second, and the classic octave
#: error is a 100% jump.
MAX_RPM_JUMP_FRACTION = 0.6

#: How far the fundamental must stand above the band's background before it is
#: believed, as a power ratio. An unbaffled four measures in the hundreds.
#:
#: Pure noise does NOT measure near 1, which is what this comment used to say.
#: Measured twice on 2026-09-27, 200 windows of uniform white noise each
#: (4096 samples at 8 kHz, cb750): contrast p50 3.7-4.1, p90 7.0-7.3, p95
#: 8.0, p99 11-12, and 10-11 of the 200 still passed this gate and returned an
#: RPM. So 8.0 sits at about the 95th percentile of noise: it rejects MOST
#: noise, not all of it, and a reading can still be wind.
#:
#: That is a deliberate trade, and hers: raising it would also reject a quiet
#: engine at idle, and she would rather switch the microphone off than lose the
#: idle reading ("yeah thats fine i might turn off that in settings").
MIN_CONTRAST = 8.0

#: The multi-band meter she asked for, as fractions of the engine's own band.
#: Named rather than fixed in hertz, so the same three bands mean the same thing
#: on a twin and on a four.
BAND_SPLITS = (("low", 0.0, 0.25), ("mid", 0.25, 0.6), ("high", 0.6, 1.0))

STATE = {}
_M = {"recorder": None, "page": None}


def reset():
    STATE.clear()
    STATE.update({
        # Everything the microphone is loud, including wind. What the old meter
        # showed, kept because clipping matters regardless of where it came from.
        "loudness_db": None,      # dBFS, -90 (silence) to 0 (clipping)
        "loudness_norm": None,    # 0.0 - 1.0, for a meter
        "loudness_peak": 0.0,
        # Only the engine's band. This is the one that tracks the throttle, and
        # the reason her peak-EQ suggestion mattered.
        "engine_db": None,
        "engine_norm": None,
        "engine_peak": 0.0,
        # The multi-band meter, [(label, 0.0-1.0)].
        "bands": [],
        "rpm_est": None,
        "rpm_hz": None,
        "rpm_at": 0.0,
        "rpm_ok_at": 0.0,         # when the reading now standing was ACCEPTED
        "rpm_rejected": "",       # why the last pick was thrown away, if it was
        "chunks": 0,
        "listening": False,
        "error": "",
        "_buf": array.array("f"),
    })


reset()


def pcm16_to_floats(chunk):
    """Signed 16-bit little-endian bytes to floats in -1.0 .. 1.0.

    An odd trailing byte is dropped rather than raising: a stream chunk can split
    a sample across a boundary, and losing one sample in 8000 is not worth an
    exception on the audio path.
    """
    if not chunk:
        return array.array("f")
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return array.array("f")
    raw = array.array("h")
    raw.frombytes(bytes(chunk[:usable]))
    return array.array("f", [sample / FULL_SCALE for sample in raw])


def loudness_dbfs(samples):
    """RMS of a window, in dBFS. -90.0 for silence rather than -inf."""
    if not samples:
        return None
    total = 0.0
    for sample in samples:
        total += sample * sample
    rms = math.sqrt(total / len(samples))
    if rms <= 1e-6:
        return -90.0
    return max(-90.0, 20.0 * math.log10(rms))


def _normalise(dbfs):
    """dBFS to 0..1 for a meter, over the range a bike actually occupies.

    -60 dB is a quiet room and -6 is a loud pipe near the top of the rev range,
    so the meter is scaled to that span rather than the full 90 dB - most of
    which would be dead travel at the bottom.
    """
    if dbfs is None:
        return None
    return max(0.0, min(1.0, (dbfs + 60.0) / 54.0))


def analyse(samples, rate, machine=None, previous_rpm=None):
    """One window in, everything the audio gauges need out. Pure and testable.

    Returns a dict:

        engine_db   power in the engine's band alone, in dB
        bands       [(label, 0.0-1.0)] for the multi-band meter
        hz          the fundamental that was picked, or None
        rpm         what that means for this engine, or None
        rejected    why a pick was discarded, when one was

    Kept free of module state and of the recorder so a test can hand it a
    synthetic engine note and assert on the answer - which is the only way the
    harmonic picking and the octave-error guard can be proved at all.
    """
    out = {"engine_db": None, "bands": [], "hz": None, "rpm": None,
           "rejected": "", "contrast": 0.0}
    lo_hz, hi_hz = bikes.band_hz(machine)
    # High-pass FIRST, at the original rate, where the cutoff is meaningful and
    # the mount thump still lives.
    filtered = dsp.highpass(samples, rate)
    narrow, narrow_rate = dsp.decimate(filtered, rate)
    bins = dsp.spectrum(narrow, narrow_rate, lo_hz, hi_hz)
    if not bins:
        return out

    # Referenced to the window length so the figure does not grow with a longer
    # window; it is a level, not a total.
    out["engine_db"] = dsp.to_db(dsp.band_energy(bins) / max(1, len(bins)))

    span = hi_hz - lo_hz
    edges = [(label, lo_hz + span * lo, lo_hz + span * hi)
             for label, lo, hi in BAND_SPLITS]
    levels = dsp.band_levels(bins, edges)
    loudest = max((power for _label, power in levels), default=0.0) or 1.0
    # Normalised against the loudest band rather than an absolute reference: the
    # meter's job is to show WHERE the energy is, and an absolute scale would sit
    # at the bottom at idle and off the top at full throttle.
    out["bands"] = [(label, power / loudest) for label, power in levels]

    hz = dsp.harmonic_pick(bins)
    if hz is None:
        return out
    # The gate that separates MOST wind from an engine. Broadband noise still
    # produces a largest harmonic product and therefore a confident-looking
    # RPM, and this rejects about 95% of those - not all. `random.seed(0)`
    # noise still reads 3691 rpm at contrast 9.49 and passes, which is the very
    # figure this comment once said the gate prevents. See MIN_CONTRAST.
    sharpness = dsp.contrast(bins, hz)
    out["contrast"] = sharpness
    if sharpness < MIN_CONTRAST:
        out["rejected"] = "no clear fundamental - wind, or the engine is off"
        return out
    rpm = bikes.rpm_from_hz(hz, machine)
    if not bikes.plausible(rpm, machine):
        out["rejected"] = "outside this engine's rev range"
        return out
    if previous_rpm:
        ceiling = bikes.redline_rpm(machine) * MAX_RPM_JUMP_FRACTION
        if abs(rpm - previous_rpm) > ceiling:
            # Almost always an octave error: the pick doubled or halved. Nothing
            # mechanical moves this far in a second.
            out["rejected"] = "implausible jump from the last reading"
            return out
    out["hz"] = hz
    out["rpm"] = rpm
    return out


def _quiet():
    """Same reason as `sensors._quiet`, and it applies more often here.

    Flet auto-updates the nearest isolated ancestor after every event handler
    (base_control.py:530 -> messaging/session.py:556-571), and for a service
    that ancestor is `Page`. The recorder delivers PCM chunks continuously, so
    without this a whole-tree diff rode on every chunk that arrived, on top of
    the eleven a second the motion sensors were already causing.

    Read the long note on `sensors._quiet` before describing this as a fix for
    anything in particular: measured, a no-change update is single-digit
    milliseconds and sends nothing. It is waste worth removing, not a proven
    cause. The same trap applies too - never call this outside a Flet event.

    Nothing here touches a control; the numbers reach the screen through
    `ride.paint()` on its own clock.
    """
    try:
        ft.context.disable_auto_update()
    except Exception:              # noqa: BLE001 - older Flet, or called
        pass                       # outside an event.


def on_stream(e, machine=None):
    """A PCM chunk. Loudness every time, the spectrum at most once a second."""
    _quiet()
    samples = pcm16_to_floats(getattr(e, "chunk", b"") or b"")
    if not samples:
        return
    STATE["chunks"] += 1

    dbfs = loudness_dbfs(samples)
    STATE["loudness_db"] = dbfs
    norm = _normalise(dbfs)
    STATE["loudness_norm"] = norm
    if norm is not None and norm > STATE["loudness_peak"]:
        STATE["loudness_peak"] = norm

    buf = STATE["_buf"]
    buf.extend(samples)
    if len(buf) > WINDOW_SAMPLES:
        del buf[:len(buf) - WINDOW_SAMPLES]

    now = time.time()
    if now - STATE["rpm_at"] < RPM_EVERY_S or len(buf) < WINDOW_SAMPLES:
        return
    STATE["rpm_at"] = now
    if dbfs is not None and dbfs < QUIET_DBFS:
        # Silence has no fundamental. Say nothing rather than picking the loudest
        # bin of the noise floor.
        STATE["rpm_est"] = None
        STATE["rpm_hz"] = None
        STATE["rpm_rejected"] = "too quiet"
        return
    if machine is None:
        machine = STATE.get("machine")
    got = analyse(list(buf), SAMPLE_RATE, machine,
                  previous_rpm=STATE.get("rpm_est"))
    STATE["engine_db"] = got["engine_db"]
    STATE["engine_norm"] = _normalise(got["engine_db"])
    if STATE["engine_norm"] is not None:
        STATE["engine_peak"] = max(STATE["engine_peak"], STATE["engine_norm"])
    STATE["bands"] = got["bands"]
    STATE["rpm_rejected"] = got["rejected"]
    # A rejected pick leaves the previous reading standing rather than blanking
    # the gauge: one bad window in a corner should not wipe a figure that was
    # correct a second ago. But only for RPM_HOLD_S - see the constant. Holding
    # it for ever made the stale figure the continuity reference, and it then
    # rejected the real engine speed for as long as it stood.
    if got["rpm"] is not None:
        STATE["rpm_est"] = got["rpm"]
        STATE["rpm_hz"] = got["hz"]
        STATE["rpm_ok_at"] = now
    elif now - STATE["rpm_ok_at"] > RPM_HOLD_S:
        # Blank the figure, KEEP the reason: an empty gauge that says why is
        # honest, and the next window is now judged against nothing rather
        # than against a number that has stopped being true.
        STATE["rpm_est"] = None
        STATE["rpm_hz"] = None


def on_state_change(e):
    _quiet()
    state = str(getattr(e, "state", "")).lower()
    STATE["listening"] = "record" in state and "stop" not in state


def attach(page):
    """Build the recorder service. Does NOT start it - `start()` does.

    Returns the service, or None when the package is not in this build. The web
    bundle deliberately does not carry it: a browser tab asking for the
    microphone to estimate engine speed is not a thing this app does.
    """
    if _M["recorder"] is not None and _M["page"] is page:
        return _M["recorder"]
    try:
        from flet_audio_recorder import AudioRecorder
    except ImportError as exc:
        STATE["error"] = f"no audio recorder in this build ({exc})"
        return None
    recorder = AudioRecorder(on_stream=on_stream,
                             on_state_change=on_state_change)
    if isinstance(getattr(page, "services", None), list):
        page.services.append(recorder)
    _M["recorder"] = recorder
    _M["page"] = page
    return recorder


async def start():
    """Ask for the microphone and begin streaming. Returns (ok, message)."""
    recorder = _M.get("recorder")
    if recorder is None:
        return False, STATE.get("error") or "no audio recorder in this build"
    try:
        from flet_audio_recorder import (AudioEncoder,
                                         AudioRecorderConfiguration)
    except ImportError as exc:
        return False, str(exc)
    try:
        if not await recorder.has_permission():
            return False, "the microphone was refused"
        await recorder.start_recording(
            configuration=AudioRecorderConfiguration(
                encoder=AudioEncoder.PCM16BITS, channels=CHANNELS,
                sample_rate=SAMPLE_RATE, suppress_noise=False,
                auto_gain=False))
    except Exception as exc:                    # noqa: BLE001 - a refused mic
        # must never take the ride down with it
        STATE["error"] = str(exc)
        return False, str(exc)
    STATE["listening"] = True
    STATE["error"] = ""
    return True, "listening"


async def stop():
    """Stop streaming. Safe to call when not started."""
    recorder = _M.get("recorder")
    STATE["listening"] = False
    STATE["rpm_est"] = None
    STATE["loudness_db"] = None
    STATE["loudness_norm"] = None
    if recorder is None:
        return False
    try:
        await recorder.stop_recording()
    except Exception:                           # noqa: BLE001 - already stopped
        return False
    return True


def listening():
    return bool(STATE.get("listening"))


def latest():
    """The audio channels, named for the ride log. Empty when not listening."""
    if not STATE.get("listening"):
        return {}
    out = {}
    if STATE.get("loudness_db") is not None:
        out["loudness_db"] = round(STATE["loudness_db"], 1)
    if STATE.get("engine_db") is not None:
        out["engine_db"] = round(STATE["engine_db"], 1)
    if STATE.get("rpm_est") is not None:
        out["rpm_est"] = round(STATE["rpm_est"])
    if STATE.get("rpm_hz") is not None:
        # The frequency as well as the RPM: the ratio between them depends on the
        # bike profile, and a log that recorded only the RPM could not be
        # recomputed if the profile turned out to be wrong.
        out["exhaust_hz"] = round(STATE["rpm_hz"], 2)
    return out


def set_machine(machine):
    """Which bike the microphone is listening to. Changes the band and the ratio."""
    STATE["machine"] = machine
    # The previous reading was in a different engine's terms, so it must not be
    # used as the continuity reference for the next one.
    STATE["rpm_est"] = None
    STATE["rpm_hz"] = None
    return machine


def reset_peaks():
    STATE["loudness_peak"] = 0.0
    STATE["engine_peak"] = 0.0
