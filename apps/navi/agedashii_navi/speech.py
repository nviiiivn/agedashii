"""Spoken turn prompts, through the phone's own text-to-speech.

Hers: "i gave you tts library shit to put INTO flet because i know it has no
tts". Flet ships no speech control, but the phone has one - the engine every
other navigation app on it already talks through - and pyjnius reaches it from
this app's Python. Measured on the emulator (API 34, airplane mode on): the
engine binds in 0.11 s, the US English voice reports it needs no network, and
speak() returns success - which proved NOTHING: see THE TEST WORD below.

HOW IT IS DRIVEN, AND WHY IT POLLS

pyjnius under Flet can call Java but cannot IMPLEMENT a Java interface: that
needs org.jnius.NativeInvocationHandler, which Flet's APK does not bundle
(probe: ClassNotFoundException). So there is no OnInitListener and no
UtteranceProgressListener here. The engine is created with no listener and
polled - setLanguage() until it answers, isSpeaking() until it stops. It also
means the (String, int, HashMap) overload of speak(): pyjnius will not match a
Python str to the CharSequence one.

WHAT IT PROMISES

- The newest prompt wins. Speaking uses QUEUE_FLUSH, so a new instruction
  interrupts one still being read; an old instruction is worse than none.
- It asks for audio focus as AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK with the
  navigation-guidance attributes, so her music ducks and comes back.
- It never blocks the page. Everything touching the engine runs on one worker
  thread; `say()` only hands over the text.
- If there is no offline voice it says so, in `STATE["problem"]`, in words she
  can act on, and the Ride page shows it. It does not fail silently.
- No voice is used until it has been HEARD: each candidate writes PROBE_WORD
  to a file first, and the first that produces audio is the one used. A voice
  that is listed as installed but writes nothing is skipped; if none writes
  anything, that is the problem shown (NO_WORKING_VOICE).
- Her recorded clips play through MediaPlayer even when no voice works; only
  the phone's words are missing then, and the problem is on screen.
- `voice_active()` stays true while a prompt plays and for TAIL_S after, so the
  microphone's RPM analysis can stand down instead of hearing the voice.

A different voice later - a character voice, a model - replaces the backend and
nothing else: callers only ever see say(), voice_active() and STATE.
"""
import os
import threading
import time

#: Seconds after a prompt ends that the microphone is still told to ignore
#: what it hears. Minion 1's point: the tach otherwise listens to the voice. A
#: blank tach for two seconds beats a wrong RPM.
TAIL_S = 2.0
#: How long the engine gets to bind and accept a language at first use.
INIT_TIMEOUT_S = 15.0
#: How long a prompt gets to start playing before it is given up on.
START_TIMEOUT_S = 3.0
#: Longest a single prompt may play; a turn instruction is a few seconds.
MAX_UTTERANCE_S = 20.0
POLL_S = 0.02

#: What she can do about it, and where. Searching Settings is the one route
#: that works on both her phones whatever the menu is called on each.
NO_VOICE = ("Turns are shown, not spoken: this phone has no offline English "
            "voice. In Settings, search \"text-to-speech\", open the Google "
            "engine's settings, Install voice data, English (United States).")
NO_ENGINE = ("Turns are shown, not spoken: no text-to-speech engine answered. "
             "In Settings, search \"text-to-speech\" and pick Speech Services "
             "by Google.")
NO_WORKING_VOICE = ("Turns are shown, not spoken: every voice this phone lists "
                    "failed a test word. In Settings, search \"text-to-speech\", "
                    "open the Google engine's settings, Install voice data, "
                    "English (United States) - delete it and install it again "
                    "if it is already there.")

#: THE TEST WORD. Minion 1 found Google's engine listing voices as installed
#: that fail every prompt (error -4) while speak() returns 0 and isSpeaking()
#: goes true - "accepted", not "spoken", and on a -no-audio emulator nobody can
#: hear the difference. So no voice is trusted until it has written this word
#: to a file of at least PROBE_MIN_BYTES; a failing voice writes nothing or a
#: bare 44-byte WAV header, and a real one-word WAV is tens of kilobytes.
PROBE_WORD = "Navi"
PROBE_MIN_BYTES = 4096
#: Seconds one voice gets to write it, and all of them together.
PROBE_EACH_S = 4.0
PROBE_BUDGET_S = 30.0

STATE = {}
_M = {}


def reset():
    """Back to not started. Tests, and a fresh process."""
    STATE.clear()
    STATE.update({"started": False, "ready": False, "problem": "",
                  "engine": "", "voice": "", "offline": None,
                  "speaking": False, "quiet_until": 0.0, "last": "",
                  "spoken": 0, "error": ""})
    _M.clear()
    # `gen` retires any worker from before this reset: it checks the token on
    # every turn and leaves once it is stale. Without it a test's old worker,
    # holding an old engine, could take the next test's prompt.
    _M.update({"backend": None, "worker": None, "pending": None,
               "wake": threading.Event(), "lock": threading.Lock(),
               "factory": _android_backend, "gen": object()})


class AndroidBackend:
    """android.speech.tts.TextToSpeech and AudioManager, through pyjnius."""

    def __init__(self):
        from jnius import autoclass, cast
        self._autoclass = autoclass
        ctx = autoclass("android.app.ActivityThread").currentApplication()
        self._ctx = ctx
        self._tts_cls = autoclass("android.speech.tts.TextToSpeech")
        self.tts = self._tts_cls(ctx, None)
        Builder = autoclass("android.media.AudioAttributes$Builder")
        Attrs = autoclass("android.media.AudioAttributes")
        self.attrs = (Builder().setUsage(Attrs.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                      .setContentType(Attrs.CONTENT_TYPE_SPEECH).build())
        Context = autoclass("android.content.Context")
        self.audio = cast("android.media.AudioManager",
                          ctx.getSystemService(Context.AUDIO_SERVICE))
        AudioManager = autoclass("android.media.AudioManager")
        FocusBuilder = autoclass("android.media.AudioFocusRequest$Builder")
        self.focus = (FocusBuilder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                      .setAudioAttributes(self.attrs).build())

    def init(self, deadline):
        """(ok, problem, engine, voice name, offline). Polls; no callbacks."""
        Locale = self._autoclass("java.util.Locale")
        lang = None
        while time.time() < deadline:
            lang = self.tts.setLanguage(Locale.US)
            if lang is not None and lang >= 0:
                break
            time.sleep(0.05)
        engine = self.tts.getDefaultEngine() or ""
        if lang is None or lang < -1 or not engine:
            return False, NO_ENGINE, engine, "", False
        # Candidates: INSTALLED, on-device English voices - US first, then best
        # quality, then quickest. Google lists network voices and
        # not-yet-downloaded ones alongside them.
        candidates = []
        voices = self.tts.getVoices()
        if voices is not None:
            it = voices.iterator()
            while it.hasNext():
                v = it.next()
                loc = v.getLocale()
                if (loc.getLanguage() == "en"
                        and not v.isNetworkConnectionRequired()
                        and not v.getFeatures().contains("notInstalled")):
                    candidates.append(((loc.getCountry() != "US", -v.getQuality(),
                                        v.getLatency(), v.getName()), v))
        if not candidates:
            return False, NO_VOICE, engine, "", False
        # The voice that passed last time goes first. Each silent voice costs
        # PROBE_EACH_S; the emulator has five before a working one (24 s), and
        # that should be paid once, not at every start.
        cache = self._ctx.getCacheDir().getAbsolutePath()
        remembered = os.path.join(cache, "navi-voice-ok.txt")
        try:
            with open(remembered, encoding="utf-8") as f:
                good = f.read().strip()
        except OSError:
            good = ""
        candidates.sort(key=lambda c: (c[0][3] != good, c[0]))
        self.tts.setAudioAttributes(self.attrs)
        # Then the one that is HEARD: the first that writes the test word.
        path = os.path.join(cache, "navi-voice-test.wav")
        budget = time.time() + PROBE_BUDGET_S
        try:
            for (_key, voice) in candidates:
                left = budget - time.time()
                if left <= 0:
                    break
                try:
                    size = self._test_word(voice, path, min(PROBE_EACH_S, left))
                except Exception as exc:                      # noqa: BLE001
                    # One voice failing is that voice, not the engine.
                    print(f"[navi] speech: test word in {voice.getName()} raised "
                          f"{type(exc).__name__}: {exc}", flush=True)
                    size = 0
                heard = size >= PROBE_MIN_BYTES
                print(f"[navi] speech: test word in {voice.getName()}: {size} bytes"
                      f" -> {'speaks' if heard else 'SILENT'}", flush=True)
                if heard:
                    try:
                        with open(remembered, "w", encoding="utf-8") as f:
                            f.write(voice.getName())
                    except OSError as exc:
                        print(f"[navi] speech: could not remember the voice: {exc}",
                              flush=True)
                    return True, "", engine, voice.getName(), True
        finally:
            _discard(path)
        return False, NO_WORKING_VOICE, engine, "", False

    def _test_word(self, voice, path, seconds):
        """Bytes `voice` wrote for PROBE_WORD within `seconds`. Polled - see
        the module docstring for why there is no listener to wait on."""
        self.tts.stop()
        self.tts.setVoice(voice)
        _discard(path)
        # An empty map, not None: this (String, HashMap, String) overload reads
        # it unchecked - NullPointerException on the emulator, API 34.
        params = self._autoclass("java.util.HashMap")()
        rc = self.tts.synthesizeToFile(PROBE_WORD, params, path)
        if rc != 0:
            print(f"[navi] speech: synthesizeToFile() returned {rc}", flush=True)
            return 0
        size, end = 0, time.time() + seconds
        while time.time() < end:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size >= PROBE_MIN_BYTES:
                break
            time.sleep(0.05)
        return size

    def speak(self, text):
        return self.tts.speak(text, self._tts_cls.QUEUE_FLUSH, None)

    def is_speaking(self):
        return bool(self.tts.isSpeaking())

    def request_focus(self):
        return self.audio.requestAudioFocus(self.focus)

    def abandon_focus(self):
        return self.audio.abandonAudioFocusRequest(self.focus)

    # Her recorded lines, through android.media.MediaPlayer with the same
    # navigation-guidance attributes, so they duck the music the same way.
    # Any format the platform decodes: mp3, wav, m4a/aac, ogg/opus, flac.
    _player = None

    def play(self, path):
        MediaPlayer = self._autoclass("android.media.MediaPlayer")
        player = MediaPlayer()
        try:
            player.setAudioAttributes(self.attrs)
            player.setDataSource(path)
            player.prepare()
            player.start()
        except Exception:
            # A file that will not decode still made a native player; without
            # this it sat idle in `dumpsys audio`, one more per bad prompt.
            player.release()
            raise
        self._player = player

    def is_playing(self):
        return bool(self._player is not None and self._player.isPlaying())

    def stop_clip(self):
        player, self._player = self._player, None
        if player is not None:
            try:
                if player.isPlaying():
                    player.stop()
            finally:
                player.release()


def _discard(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"[navi] speech: could not remove {path}: {exc}", flush=True)


def _android_backend():
    return AndroidBackend()


def start():
    """Bring the engine up in the background. Safe to call on every build."""
    with _M["lock"]:
        if STATE["started"]:
            return
        STATE["started"] = True
        worker = threading.Thread(target=_run, args=(_M["gen"],),
                                  name="navi-speech", daemon=True)
        _M["worker"] = worker
    worker.start()


def for_the_ear(text):
    """The on-screen line, with the screen's abbreviations spelled out.

    guidance writes "400 ft" and "0.3 mi" because that is what fits a band at a
    glance. An engine may read "mi" as the letters. Only the spoken copy
    changes; what is shown stays short.
    """
    import re
    text = re.sub(r"(\d)\s*ft\b", r"\1 feet", text)
    # "2.0 mi" is "two point zero miles" out loud.
    text = re.sub(r"(\d)\.0\s*(mi|km)\b", r"\1 \2", text)
    # Singular only for a whole 1: "0.1 mi" is not "0.1 mile".
    text = re.sub(r"(?<![\d.])1\s*mi\b", "1 mile", text)
    return re.sub(r"(\d)\s*mi\b", r"\1 miles", text)


def play(items):
    """Play a playlist, replacing anything not yet said. Never blocks.

    `items` are voicelines' (kind, value, fallback): ("tts", words, None) or
    ("clip", path, words to say if the file will not play).
    """
    clean = []
    for kind, value, fallback in items or ():
        if kind == "tts" and value and value.strip():
            clean.append(("tts", for_the_ear(value.strip()), None))
        elif kind == "clip" and value:
            clean.append(("clip", value, for_the_ear(fallback) if fallback else None))
    if not clean:
        return False
    start()
    with _M["lock"]:
        _M["pending"] = clean
    _M["wake"].set()
    return True


def say(text):
    """Speak `text` in the phone's voice, replacing anything not yet said."""
    text = (text or "").strip()
    return play([("tts", text, None)]) if text else False


def voice_active(now=None):
    """True while a prompt plays, and for TAIL_S after it."""
    now = time.time() if now is None else now
    return bool(STATE.get("speaking")) or now < STATE.get("quiet_until", 0.0)


def status_text():
    """One line for Settings: what speaks, or why nothing does."""
    if STATE.get("problem"):
        return STATE["problem"]
    if STATE.get("ready"):
        return f"spoken offline by {STATE['engine']} ({STATE['voice']})"
    if STATE.get("started"):
        return "starting the phone's voice…"
    return "the phone's own voice, offline"


def _take():
    with _M["lock"]:
        items, _M["pending"] = _M["pending"], None
        _M["wake"].clear()
    return items


def _bring_up(gen):
    try:
        backend = _M["factory"]()
        ok, problem, engine, voice, offline = backend.init(time.time() + INIT_TIMEOUT_S)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[navi] speech: no text-to-speech: {type(exc).__name__}: {exc}",
              flush=True)
        if _M.get("gen") is gen:
            STATE.update(ready=False, problem=NO_ENGINE, error=f"{type(exc).__name__}: {exc}")
        return None
    if _M.get("gen") is not gen:
        return None
    STATE.update(ready=ok, problem=problem, engine=engine, voice=voice, offline=offline)
    print(f"[navi] speech: ready={ok} engine={engine} voice={voice} "
          f"offline={offline} {problem}", flush=True)
    # Kept even with no working voice: her clips go through MediaPlayer, which
    # needs no voice at all. Only the words are dropped (_speak_one).
    return backend


def _run(gen):
    backend = _bring_up(gen)
    if _M.get("gen") is not gen:
        return
    _M["backend"] = backend
    while _M.get("gen") is gen:
        if not _M["wake"].wait(0.5) or _M.get("gen") is not gen:
            continue
        items = _take()
        if not items:
            continue
        if backend is None:
            # Nothing can speak. The words are still on screen, and the
            # problem is in STATE for the Ride page to show.
            continue
        if not STATE["ready"]:
            # No voice passed the test word: her clips still play, the
            # phone's words (and a broken clip's fallback) cannot.
            items = [(k, v, None) for k, v, _f in items if k == "clip"]
            if not items:
                continue
        _speak_one(backend, items)


def _wait(busy, started):
    """Poll `busy()` to start and then to finish; stop early for a newer prompt."""
    while not busy() and time.time() - started < START_TIMEOUT_S:
        if _M["pending"]:
            return
        time.sleep(POLL_S)
    while busy() and time.time() - started < MAX_UTTERANCE_S:
        if _M["pending"]:
            return
        time.sleep(POLL_S)


def _say_words(backend, words):
    rc = backend.speak(words)
    if rc != 0:
        print(f"[navi] speech: speak() returned {rc}", flush=True)
    _wait(backend.is_speaking, time.time())


def _play_clip(backend, path):
    """Her clip. False if it would not play, so the caller says the words."""
    try:
        backend.play(path)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[navi] speech: clip {path} will not play: {type(exc).__name__}: {exc}",
              flush=True)
        return False
    try:
        _wait(backend.is_playing, time.time())
    finally:
        backend.stop_clip()
    return True


def _speak_one(backend, items):
    """Play one prompt's items; hand over to a newer prompt the moment it arrives."""
    focused = False
    try:
        focused = backend.request_focus() == 1          # AUDIOFOCUS_REQUEST_GRANTED
        while items:
            STATE.update(speaking=True,
                         last=" + ".join(v if k == "tts" else f"[{v.rsplit('/', 1)[-1]}]"
                                         for k, v, _f in items),
                         quiet_until=time.time() + MAX_UTTERANCE_S)
            for kind, value, fallback in items:
                if kind == "clip":
                    if not _play_clip(backend, value) and fallback:
                        _say_words(backend, fallback)
                else:
                    _say_words(backend, value)
                if _M["pending"]:
                    break
            STATE["spoken"] += 1
            items = _take()                              # a newer prompt, if one came
    except Exception as exc:                                  # noqa: BLE001
        print(f"[navi] speech: failed: {type(exc).__name__}: {exc}", flush=True)
        STATE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        STATE.update(speaking=False, quiet_until=time.time() + TAIL_S)
        if focused:
            try:
                backend.abandon_focus()
            except Exception as exc:                          # noqa: BLE001
                print(f"[navi] speech: abandon focus failed: {exc}", flush=True)


reset()
