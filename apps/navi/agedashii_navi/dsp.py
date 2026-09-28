"""Finding an engine in a microphone, in pure Python, on a phone.

Her suggestion, which is the shape of this module: "idk if you have already
considered a peak EQ to really hone in on the frequency that engine noise is most
predominant. e.g., cutting out the lows entirely and most of the highs would give
you a much stronger / broader view of JUST the range we're looking for. you could
probably even do a multi-peak meter".

That is right, and it splits into two wins worth separating:

**For the pitch, band-limiting the SEARCH was already happening** - only bins
inside the engine's band are ever examined - so a filter in front of the transform
would not move the peak much.

**For the METER it changes everything.** Loudness was RMS of the whole signal, so
at 60 mph the needle showed airflow, not engine. `band_energy` measures only the
engine's band, which tracks the throttle instead of the wind. And `band_levels`
gives the multi-band meter she asked for, free, because the spectrum is already
computed.

WHY IT IS FAST ENOUGH

A naive transform over a four-cylinder's band - up to 400 Hz - on a 4096-sample
window is about a million trig operations, which is a second of CPython on a
phone. So the signal is DECIMATED first: the band tops out below 500 Hz, so
averaging every `factor` samples is both an anti-alias low-pass and an eightfold
reduction in work. 4096 samples at 8 kHz becomes 512 at 1 kHz - bin width just
under 2 Hz, about 200 bins in band, ~100k operations. A tenth of a second, once
per second.

The box-car average used for decimation is a crude low-pass with a sinc response,
and that is fine here: its first null sits at the new sample rate and everything
it fails to reject is far above the band being searched.

WHY HARMONIC PRODUCT AND NOT THE LOUDEST BIN

An exhaust note is a fundamental plus a stack of harmonics, and often the second
harmonic is the loudest single component - so picking the largest bin returns
exactly double the true engine speed. This is the classic octave error, and it is
why a naive tachometer reads 6000 when the engine is at 3000. The harmonic product
spectrum multiplies the spectrum by its own decimations, so a true fundamental is
reinforced by every harmonic above it while an isolated loud harmonic is not - and
broadband wind noise, having no harmonic structure at all, multiplies down to
nothing.
"""
import math

#: What the signal is decimated to before the transform, in Hz. Twice the top of
#: any engine band here, with room to spare.
DECIMATE_TO_HZ = 1000.0
#: How many harmonics the product folds in. Three is enough to kill the octave
#: error; more starts multiplying in bins above the band, where there is nothing
#: but wind.
HARMONICS = 3

#: Windows and trig tables are reused across calls - they depend only on a length.
_HANN = {}
_TRIG = {}


def hann(n):
    """A Hann window of length n, cached.

    Without a window the transform sees the buffer's hard edges as a step, which
    smears every peak across neighbouring bins and costs exactly the resolution
    the long window was taken for.
    """
    got = _HANN.get(n)
    if got is None:
        got = ([0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))
                for i in range(n)] if n > 1 else [1.0])
        _HANN[n] = got
    return got


def rms(samples):
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def highpass(samples, rate, cutoff_hz=18.0):
    """A one-pole high-pass. Removes road rumble and mount thump.

    Below about 18 Hz there is nothing an engine can produce - under 1000 rpm even
    on a single - and there is a great deal of the phone moving in its mount,
    which otherwise dominates the low bins and drags the band energy up with it.

    One pole rather than a designed filter because the job is to remove a
    nuisance, not to meet a specification, and this costs one multiply per sample.
    """
    if not samples or rate <= 0:
        return list(samples)
    dt = 1.0 / rate
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = tau / (tau + dt)
    out = [0.0] * len(samples)
    prev_in = samples[0]
    prev_out = 0.0
    for i, sample in enumerate(samples):
        prev_out = alpha * (prev_out + sample - prev_in)
        prev_in = sample
        out[i] = prev_out
    return out


def decimate(samples, rate, target_hz=DECIMATE_TO_HZ):
    """(samples, rate) reduced towards `target_hz` by box-car averaging.

    The average IS the anti-alias filter - see the module docstring. Returns the
    input untouched when it is already at or below the target, so a caller need
    not check.
    """
    if not samples or rate <= target_hz:
        return list(samples), rate
    factor = max(1, int(rate // target_hz))
    if factor == 1:
        return list(samples), rate
    out = []
    for start in range(0, len(samples) - factor + 1, factor):
        total = 0.0
        for i in range(start, start + factor):
            total += samples[i]
        out.append(total / factor)
    return out, rate / factor


def spectrum(samples, rate, lo_hz, hi_hz, window=True):
    """[(hz, power)] for the bins inside a band. The one expensive call.

    Power, not amplitude: the harmonic product multiplies these together, and the
    square is both cheaper and the right quantity for energy.

    Trig tables are cached per window length, which is what makes a repeated call
    at the same size cheap - the cosines are identical every time and only the
    samples change.
    """
    n = len(samples)
    if n < 32 or rate <= 0 or hi_hz <= lo_hz:
        return []
    weights = hann(n) if window else None
    step = rate / n
    lo = max(1, int(lo_hz / step))
    hi = min(n // 2, int(hi_hz / step) + 1)
    if hi <= lo:
        return []
    table = _TRIG.setdefault(n, {})
    data = ([s * w for s, w in zip(samples, weights)] if weights
            else list(samples))
    out = []
    for k in range(lo, hi):
        pair = table.get(k)
        if pair is None:
            angle = 2.0 * math.pi * k / n
            pair = ([math.cos(angle * i) for i in range(n)],
                    [math.sin(angle * i) for i in range(n)])
            table[k] = pair
        cos_row, sin_row = pair
        re = im = 0.0
        for i, value in enumerate(data):
            re += value * cos_row[i]
            im -= value * sin_row[i]
        out.append((k * step, re * re + im * im))
    return out


#: A candidate must hold at least this fraction of the band's strongest bin to be
#: considered a fundamental at all. See `harmonic_pick` for why.
MIN_CANDIDATE_FRACTION = 0.02


def harmonic_pick(bins, harmonics=HARMONICS,
                  floor_fraction=MIN_CANDIDATE_FRACTION):
    """The fundamental frequency in a spectrum, by harmonic product, or None.

    For each candidate bin, its power is multiplied by the power at 2x, 3x ... its
    frequency. A real fundamental has energy at all of them; a lone loud harmonic
    does not, and neither does wind.

    A harmonic that falls outside the searched band contributes the band's own
    median rather than zero, so a fundamental near the top of the range is not
    disqualified merely for having its harmonics beyond the window.

    THE SUB-HARMONIC TRAP, AND THE FLOOR THAT CLOSES IT

    That median substitution opens a hole, and it was a real failure: a CB750 at
    9000 rpm has a 300 Hz fundamental whose harmonics are all above the band, so
    candidate 100 Hz scored `median x median x (real energy at 300)` and won -
    reporting 2988 rpm for an engine at 9000. The candidate had almost no energy
    of its own; it was picked entirely on somebody else's.

    So a candidate must first hold `floor_fraction` of the strongest bin in the
    band. 2% of peak POWER is about 14% of peak amplitude - low enough to keep a
    genuine fundamental that is quieter than its own second harmonic, which is the
    ordinary case for an exhaust, and high enough to exclude a bin that is
    indistinguishable from the noise floor.
    """
    if not bins:
        return None
    powers = [power for _hz, power in bins]
    strongest = max(powers)
    if strongest <= 0:
        return None
    median = sorted(powers)[len(powers) // 2] or 1e-12
    lowest = bins[0][0]
    span = bins[-1][0] - lowest
    if span <= 0 or len(bins) < 2:
        return bins[0][0]
    stride = span / (len(bins) - 1)
    floor = strongest * floor_fraction

    def power_at(hz):
        index = int(round((hz - lowest) / stride))
        return bins[index][1] if 0 <= index < len(bins) else median

    best_hz, best_score = None, 0.0
    for hz, power in bins:
        if power < floor:
            continue
        score = power
        for harmonic in range(2, harmonics + 1):
            score *= power_at(hz * harmonic)
        if score > best_score:
            best_score, best_hz = score, hz
    return best_hz


def contrast(bins, hz):
    """How far the power at `hz` stands above the band's own background.

    A ratio against the median bin. This is the test that separates an engine from
    wind, and it is needed because the harmonic product alone does not provide it:
    given nothing but broadband noise, the product still has a largest value, so
    `harmonic_pick` still returns a frequency and it looks exactly like an answer.
    On uniform white noise that ratio is NOT near 1, as this once said: its median
    is about 4 and one window in twenty passes 8 (see `audio.MIN_CONTRAST` for the
    measurement). A bike with no baffles is orders of magnitude above it.

    Returns 0.0 when there is nothing to measure, which no threshold will pass.
    """
    if not bins or hz is None:
        return 0.0
    powers = [power for _hz, power in bins]
    median = sorted(powers)[len(powers) // 2]
    if median <= 0:
        return 0.0
    lowest = bins[0][0]
    stride = ((bins[-1][0] - lowest) / (len(bins) - 1)) if len(bins) > 1 else 1.0
    index = int(round((hz - lowest) / stride)) if stride else 0
    if not 0 <= index < len(bins):
        return 0.0
    # The neighbouring bins count too: a Hann window spreads a pure tone across
    # three bins, so testing only the centre one understates a real peak by
    # roughly a third.
    near = bins[max(0, index - 1):index + 2]
    return (max(power for _hz, power in near) / median) if near else 0.0


def band_energy(bins, lo_hz=None, hi_hz=None):
    """Total power in a band of an already-computed spectrum.

    This is the number the loudness meter should have shown all along: it excludes
    everything outside the engine's range, so it tracks the throttle rather than
    the airflow over the microphone.
    """
    total = 0.0
    for hz, power in bins:
        if lo_hz is not None and hz < lo_hz:
            continue
        if hi_hz is not None and hz > hi_hz:
            continue
        total += power
    return total


def band_levels(bins, edges):
    """[(label, power)] for consecutive bands. Her multi-peak meter.

    Computed from one spectrum, so three bands cost the same as one.
    """
    return [(label, band_energy(bins, lo, hi)) for label, lo, hi in edges]


def to_db(power, reference=1.0):
    """Power to decibels, floored at -90 rather than running to -inf."""
    if power <= 0 or reference <= 0:
        return -90.0
    return max(-90.0, 10.0 * math.log10(power / reference))
