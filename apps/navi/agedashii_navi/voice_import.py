"""Her recorded voice: getting the clips from her laptop into Navi, and out again.

THE PATH, AND WHY IT IS THIS ONE

No adb and no cable, on the bike's phone, alone, in a few minutes. So:

1. On the laptop she puts the clips on the phone any way she already knows - a
   ZIP in Google Drive, Quick Share or Nearby Share into Download, an email
   attachment saved to Download.
2. In Navi, Settings, she taps "Import voice" and picks them in the phone's
   own file picker: the loose clips, or the one ZIP. Picking is all it takes.

Nothing she can reach with a file manager is readable by this app, and nothing
this app writes is reachable by her: `FLET_APP_STORAGE_DATA` is
Context.getFilesDir() + "/data" (Flet's main.dart asks path_provider for the
application-support directory; path_provider_android 2.3.1 answers filesDir),
which no file manager can list without root. Ride logs live there too
(live.ride_dir). A folder she drops files into for the app to find would need
All-files access - a permission Play reviews and a switch she would have to
find - so there is no such folder. The picker needs no permission at all: it
is Android's Storage Access Framework (ACTION_OPEN_DOCUMENT), and the file she
picks is granted to the app for that one read.

file_picker 11.0.3 (FileUtils.openFileStream) copies each picked document into
the app's cache and hands Python that copy's path, so `import_files` only ever
sees ordinary paths. Those copies are the picker's, in the cache; this module
reads them and never deletes them, because on a desktop the path it is given
is her original file.

A ZIP is accepted beside loose files because 23 clips picked one at a time is
23 long-presses, while a ZIP is one file to move and one tap to pick. Folders
inside it are ignored and Mac debris (__MACOSX, ._files, .DS_Store) is skipped.
A wifi upload page would need the phone and laptop on one network and an
address typed into a browser; the picker is fewer steps and has no server to
leave running, so there is none.

WHAT IMPORT DOES

MERGE: each clip picked replaces that clip, whatever format either one is in,
and every other clip stays. "Remove voice" clears the whole set, and asks for
a second tap first. A new clip is written beside the old one and renamed over
it, so an import that fails partway leaves the old clip playable.

The file name is the prompt: `turn-left.mp3` is the turn-left clip. Case, and
underscores or spaces for the dash, are forgiven; anything that is not one of
NAMES is left out and named in the result, never guessed at.

DISTANCES ARE OPEN-ENDED. Her Arnold set says 400, 800 and 1500 feet, not the
eight distances first listed, so any `dist-<N>ft`, `dist-<N>mile` or
`dist-<N>miles` is a distance clip, N a positive number (a decimal is fine:
`dist-0.5mile`). The player rounds to the nearest one she has. `dist-quarter`
and `dist-half` keep their names. Agreed with the Kotlin app, which reads the
same files.
"""
import asyncio
import os
import re
import time
import traceback
import zipfile
from pathlib import Path

#: The prompts, by the file name that carries each one. Fixed: her recordings
#: are named after these, so a name here never changes.
NAMES = (
    "turn-left", "turn-right", "slight-left", "slight-right", "sharp-left",
    "sharp-right", "uturn", "continue", "merge", "exit", "arrive",
    "dist-100ft", "dist-200ft", "dist-300ft", "dist-500ft", "dist-quarter",
    "dist-half", "dist-1mile", "dist-2miles", "offroute", "rerouting", "start",
    "speeding", "roundabout",
)
_NAMES = frozenset(NAMES)
#: Names she may record and is never told are missing. A roundabout clip was
#: added after the set went to her; without one, the words are spoken.
OPTIONAL = frozenset({"roundabout"})
#: The fixed distance names. Once she has any distance clip, these stop being
#: "missing": the player rounds to what she has.
FIXED_DISTANCES = tuple(n for n in NAMES if n.startswith("dist-"))
#: Every line that is not a distance and not optional - what "N of 15" counts.
LINES = tuple(n for n in NAMES if n not in FIXED_DISTANCES and n not in OPTIONAL)

#: dist-<N>ft / dist-<N>mile / dist-<N>miles. N is a positive number with no
#: leading zero ("012") and no bare point (".5", "5."), so each distance has
#: one spelling. The dash before the unit is optional on the way in
#: ("dist_5_mile") and dropped from the stored name.
_DISTANCE = re.compile(r"^dist-((?:0|[1-9][0-9]*)(?:\.[0-9]+)?)-?(ft|mile|miles)$")
_FEET_PER = {"ft": 1.0, "mile": 5280.0, "miles": 5280.0}
_NAMED_FEET = {"dist-quarter": 1320.0, "dist-half": 2640.0}


#: Character lines that play ALONGSIDE the instructions, never instead of
#: them: flourish-<mood>-<slug>. The moods are agreed with minion 5 and the
#: Kotlin app, which reads the same files; an unknown mood is left out and
#: named, never guessed. Flourishes are extras - never "missing".
FLOURISH_MOODS = ("start", "arrival", "angry", "urgent", "scold", "sorry")
_FLOURISH = re.compile(r"^flourish-(" + "|".join(FLOURISH_MOODS) + r")-([a-z0-9-]+)$")
#: Sounds that drop INTO a prompt rather than beside it - a cough between the
#: distance and the turn: interject-<slug>. Also extras, never "missing".
_INTERJECT = re.compile(r"^interject-[a-z0-9-]+$")


def _is_flourish(name):
    return bool(_FLOURISH.match(name or ""))


def _is_interjection(name):
    return bool(_INTERJECT.match(name or ""))


def distance_feet(name):
    """How far a distance clip's name says, in feet; None if it is not one."""
    if name in _NAMED_FEET:
        return _NAMED_FEET[name]
    m = _DISTANCE.match(name or "")
    if not m:
        return None
    feet = float(m.group(1)) * _FEET_PER[m.group(2)]
    return feet if feet > 0 else None


def _canonical(name):
    """The stored spelling of a prompt name, or None if it names nothing."""
    if name in _NAMES or _is_flourish(name) or _is_interjection(name):
        return name
    m = _DISTANCE.match(name)
    if m and float(m.group(1)) > 0:
        return f"dist-{m.group(1)}{m.group(2)}"
    return None


def _is_name(name):
    return name in _NAMES or (_canonical(name) == name)

#: What the player can play, in the order one wins when a name has two.
EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac")
_RANK = {ext: i for i, ext in enumerate(EXTS)}

#: A spoken prompt is seconds long. 10 MB is a minute of CD-quality stereo
#: WAV; anything bigger is not a prompt, and a ZIP that claims otherwise does
#: not get to fill the phone.
MAX_CLIP_BYTES = 10 * 1024 * 1024
#: Entries read from one ZIP before it is given up on.
MAX_ZIP_ENTRIES = 1000

_INCOMING = ".incoming-"
#: Seconds the second tap of "Remove voice" has to come within.
CONFIRM_S = 6.0


def voice_dir():
    """Where the clips live: the app's own storage, beside the ride logs."""
    base = (os.environ.get("FLET_APP_STORAGE_DATA")
            or os.path.join(os.path.expanduser("~"), ".agedashii-navi"))
    return os.path.join(base, "voice")


_BUILTIN_DIR = str(Path(__file__).resolve().parent.parent / "assets" / "voice")


def builtin_dir():
    """Where the voice bundled into the APK lives: the app root's assets/voice.

    The same base washi finds assets/maps from on the phone. It exists only in
    the staged navi build (build_app.sh copies her gitignored clips there);
    in the dev tree and the web build it is absent, and nothing is built in.
    """
    return _BUILTIN_DIR


def _find(name, folder):
    """The file for `name` in `folder`, in EXTS order, or None."""
    for ext in EXTS:
        path = os.path.join(folder, name + ext)
        if os.path.isfile(path):
            return path
    return None


def _lookup(name, directory=None, builtin=None):
    """(path, "yours" | "built in") or (None, None). The ONE place the order
    lives - clip_path and clip_source both ask it, so they cannot disagree:
    her imported clip, then the bundled one, then nothing."""
    if not name or not _is_name(name):
        return None, None
    path = _find(name, directory or voice_dir())
    if path:
        return path, "yours"
    path = _find(name, builtin or builtin_dir())
    if path:
        return path, "built in"
    return None, None


def clip_path(name, directory=None, builtin=None):
    """The clip for prompt `name`, or None. Cheap: a few stat calls, no cache.

    Hers wins, then the bundled one. None for a name with no clip in either,
    and for a name that is neither one of NAMES nor a distance.
    `builtin` points the bundled folder elsewhere, for tests.
    """
    return _lookup(name, directory, builtin)[0]


def clip_source(name, directory=None, builtin=None):
    """"yours", "built in", or None - where clip_path(name) would come from."""
    return _lookup(name, directory, builtin)[1]


def _debris(parts):
    """True for what an archiver or a Mac leaves behind, never a clip."""
    return (any(p == "__MACOSX" for p in parts)
            or parts[-1].startswith("."))


def _clip_name(filename):
    """(prompt name or None, lower-case extension) for a file name."""
    stem, ext = os.path.splitext(filename)
    ext = ext.lower()
    if ext not in _RANK:
        return None, ext
    name = re.sub(r"[\s_]+", "-", stem.strip().lower())
    return _canonical(name), ext


class _TooBig(Exception):
    pass


def _copy(src, dst):
    """Copy an open binary stream to `dst`, refusing past MAX_CLIP_BYTES."""
    total = 0
    with open(dst, "wb") as out:
        while True:
            chunk = src.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CLIP_BYTES:
                raise _TooBig()
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    return total


def _new_result():
    return {"imported": [], "replaced": [], "ignored": [], "refused": [],
            "failed": [], "duplicates": {}}


def _candidates(paths, result, zips):
    """Every clip on offer as (name, ext, label, open-it, size)."""
    found = []

    def offer(label, size, opener):
        name, ext = _clip_name(label)
        if name is None:
            result["ignored"].append(label)
        elif size == 0:
            result["refused"].append((label, "empty"))
        elif size > MAX_CLIP_BYTES:
            result["refused"].append((label, "too big for a prompt"))
        else:
            found.append((name, ext, label, opener, size))

    for path in paths:
        label = os.path.basename(path)
        if label.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(path)
                zips.append(zf)
                entries = zf.infolist()
            except (OSError, zipfile.BadZipFile) as exc:
                result["failed"].append((label, _why(exc)))
                continue
            if len(entries) > MAX_ZIP_ENTRIES:
                result["failed"].append((label, f"more than {MAX_ZIP_ENTRIES} files"))
                continue
            for info in entries:
                if info.is_dir():
                    continue
                # Flattened: only the last part of the path is ever used, so
                # "../../x.mp3" and "/abs/x.mp3" land in the voice folder.
                parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
                if not parts or _debris(parts):
                    continue
                offer(parts[-1], info.file_size,
                      lambda zf=zf, info=info: zf.open(info))
            continue
        if _debris([label]):
            continue
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            result["failed"].append((label, _why(exc)))
            continue
        offer(label, size, lambda path=path: open(path, "rb"))
    return found


def _why(exc):
    if isinstance(exc, zipfile.BadZipFile):
        return "not a readable ZIP"
    if isinstance(exc, FileNotFoundError):
        return "the file was not there"
    if isinstance(exc, PermissionError):
        return "the phone would not let it be read"
    return f"{type(exc).__name__}: {exc}"


def import_files(paths, directory=None):
    """Import clips from `paths`: audio files, ZIPs of them, or both.

    Returns what happened, for `summary`: names imported and replaced, files
    ignored (not one of NAMES), refused (empty, too big) and failed (could not
    be read), and names that came with two files, mapped to those files with
    the one used first.
    """
    folder = directory or voice_dir()
    result = _new_result()
    zips = []
    try:
        chosen = {}
        offered = {}
        for cand in _candidates(paths, result, zips):
            name, ext = cand[0], cand[1]
            offered.setdefault(name, []).append(cand)
            best = chosen.get(name)
            if best is None or _RANK[ext] < _RANK[best[1]]:
                chosen[name] = cand
        for name, cands in offered.items():
            if len(cands) > 1:
                ordered = sorted(cands, key=lambda c: (c is not chosen[name], _RANK[c[1]]))
                result["duplicates"][name] = [c[2] for c in ordered]
        if chosen:
            os.makedirs(folder, exist_ok=True)
        for name in _in_order(chosen):
            _install(folder, chosen[name], result)
    finally:
        for zf in zips:
            zf.close()
    return result


def _install(folder, cand, result):
    """Write one clip beside the old one, then rename it over."""
    name, ext, label, opener, _size = cand
    temp = os.path.join(folder, _INCOMING + name + ext)
    try:
        with opener() as src:
            written = _copy(src, temp)
        if written == 0:
            os.remove(temp)
            result["refused"].append((label, "empty"))
            return
        had = _find(name, folder) is not None          # hers only, not a bundled one
        final = os.path.join(folder, name + ext)
        os.replace(temp, final)
        for other in EXTS:
            if other != ext:
                old = os.path.join(folder, name + other)
                if os.path.isfile(old):
                    os.remove(old)
    except _TooBig:
        _discard(temp)
        result["refused"].append((label, "too big for a prompt"))
        return
    except (OSError, zipfile.BadZipFile, EOFError) as exc:
        _discard(temp)
        result["failed"].append((label, _why(exc)))
        return
    result["imported"].append(name)
    if had:
        result["replaced"].append(name)


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def clear_voice(directory=None):
    """Remove every clip. Returns how many files went. The folder stays."""
    folder = directory or voice_dir()
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        return 0
    removed = 0
    for entry in entries:
        path = os.path.join(folder, entry)
        if os.path.isfile(path):
            os.remove(path)
            removed += 1
    return removed


def _in_order(names):
    """NAMES order first, then open-ended distances nearest first."""
    fixed = [n for n in NAMES if n in names]
    extra = sorted((n for n in names if n not in _NAMES),
                   key=lambda n: (distance_feet(n) or 0.0, n))
    return fixed + extra


def _found(folder):
    """({name: [its files]}, [files that are no prompt's]) in `folder`."""
    try:
        entries = sorted(os.listdir(folder))
    except FileNotFoundError:
        entries = []
    found, unknown = {}, []
    for entry in entries:
        if entry.startswith(_INCOMING) or not os.path.isfile(os.path.join(folder, entry)):
            continue
        stem, ext = os.path.splitext(entry)
        if ext in _RANK and _is_name(stem):
            found.setdefault(stem, []).append(entry)
        else:
            unknown.append(entry)
    return found, unknown


def distance_clips(directory=None, builtin=None):
    """{name: path} for every distance clip there is, fixed or open-ended -
    hers and the bundled ones together, hers winning per name. The union, so
    Arnold's 400/800/1500 ft still round her distances after she imports one
    of her own."""
    hers, _unknown = _found(directory or voice_dir())
    bundled, _ = _found(builtin or builtin_dir())
    names = set(hers) | set(bundled)
    return {n: clip_path(n, directory, builtin) for n in _in_order(names) if distance_feet(n)}


def flourishes(mood, directory=None, builtin=None):
    """{name: path} of every flourish of `mood` - hers and the bundled ones,
    hers winning per name, through the same lookup as clip_path."""
    hers, _unknown = _found(directory or voice_dir())
    bundled, _ = _found(builtin or builtin_dir())
    prefix = f"flourish-{mood}-"
    names = sorted(n for n in set(hers) | set(bundled) if n.startswith(prefix))
    return {n: clip_path(n, directory, builtin) for n in names}


def interjections(prefix="interject-cough", directory=None, builtin=None):
    """{name: path} of every interjection whose name starts with `prefix` -
    hers and the bundled ones, hers winning per name (clip_path's lookup)."""
    hers, _unknown = _found(directory or voice_dir())
    bundled, _ = _found(builtin or builtin_dir())
    names = sorted(n for n in set(hers) | set(bundled)
                   if _is_interjection(n) and n.startswith(prefix))
    return {n: clip_path(n, directory, builtin) for n in names}


def status(directory=None, builtin=None):
    """What there is to play, against NAMES - hers and the bundled voice.

    present: names with a clip from either - NAMES order, then open-ended
    distances. builtin: the present names that come from the bundle (she has
    no clip of her own for them). missing: NAMES with neither, never OPTIONAL
    ones, and not the fixed distances once any distance clip exists (the
    player rounds to those). flourishes: the character lines there are, sorted
    - apart from `present`, since they are extras and never missing.
    duplicates and unknown: in HER folder.
    """
    found, unknown = _found(directory or voice_dir())
    bundled, _ = _found(builtin or builtin_dir())
    have = set(found) | set(bundled)
    extras = sorted(n for n in have if _is_flourish(n))
    sounds = sorted(n for n in have if _is_interjection(n))
    have -= set(extras) | set(sounds)
    any_distance = any(distance_feet(n) for n in have)
    present = _in_order(have)
    return {
        "present": present,
        "flourishes": extras,
        "interjections": sounds,
        "builtin": [n for n in present if n not in found],
        "missing": [n for n in NAMES if n not in have and n not in OPTIONAL
                    and not (any_distance and n in FIXED_DISTANCES)],
        "duplicates": {n: sorted(files, key=lambda f: _RANK[os.path.splitext(f)[1]])
                       for n, files in found.items() if len(files) > 1},
        "unknown": unknown,
    }


def _few(items, most=4):
    items = list(items)
    if len(items) <= most:
        return ", ".join(items)
    return ", ".join(items[:most]) + f" and {len(items) - most} more"


def status_text(directory=None, builtin=None):
    """One line: how much of her voice is in, and what is not."""
    st = status(directory, builtin)
    extras = len(st["flourishes"])
    if not st["present"] and not extras:
        line = "No recorded voice yet."
    else:
        lines = sum(1 for n in st["present"] if n in LINES)
        built = sum(1 for n in st["builtin"] if n in LINES)
        dists = sum(1 for n in st["present"] if distance_feet(n))
        line = f"Recorded voice: {lines} of {len(LINES)} lines"
        if built:
            line += f" ({built} built in)"
        if dists:
            line += f", {dists} distance{'' if dists == 1 else 's'}"
        if extras:
            line += f", {extras} character line{'' if extras == 1 else 's'}"
        line += "."
        if st["missing"]:
            line += f" Missing: {_few(st['missing'])}."
    for name, files in st["duplicates"].items():
        line += f" Two files for {name}; using {files[0]}."
    return line


def summary(result):
    """One line for what an import just did."""
    parts = []
    n = len(result["imported"])
    if n:
        text = f"Imported {n} clip{'' if n == 1 else 's'}"
        if result["replaced"]:
            text += f" ({len(result['replaced'])} replaced)"
        parts.append(text + ".")
    else:
        parts.append("Nothing imported.")
    for name, files in result["duplicates"].items():
        parts.append(f"Two files for {name}; used {files[0]}.")
    if result["refused"]:
        parts.append("Not imported: " + _few(f"{label} ({why})"
                                             for label, why in result["refused"]) + ".")
    if result["failed"]:
        parts.append("Could not read: " + _few(f"{label} ({why})"
                                               for label, why in result["failed"]) + ".")
    if result["ignored"]:
        parts.append("Left out, not a prompt, distance or flourish name: "
                     + _few(result["ignored"]) + ".")
    return " ".join(parts)


# ------------------------------------------------------------------ the panel

def _page_picker(page):
    """The page's FilePicker, made and registered once.

    In Flet 0.86 a service is dropped once nothing holds it; `page.services`
    is the root view's own list, so a picker in it lives as long as the page.
    """
    import flet as ft
    services = getattr(page, "services", None)
    if isinstance(services, list):
        for service in services:
            if isinstance(service, ft.FilePicker):
                return service
        picker = ft.FilePicker()
        services.append(picker)
        return picker
    return ft.FilePicker()


def panel(page, picker=None, on_change=None):
    """The Settings card: what is in, Import voice, Remove voice.

    `picker` is for tests; on a phone it is the page's own FilePicker.
    `on_change()` is called after an import or a removal, before the page
    updates, so anything else showing the clips can redraw in the same frame.
    If it raises, the traceback is printed and this card still says what
    happened.
    """
    import flet as ft

    from . import ui
    from .theme import PAL

    p = PAL()
    picker = picker or _page_picker(page)
    line = ft.Text(status_text(), size=ui.SMALL, color=p["text"])
    said = ft.Text("", size=ui.SMALL, color=p["text2"])
    armed = {"at": 0.0}

    def show(text, colour):
        line.value = status_text()
        said.value = text
        said.color = colour
        if on_change is not None:
            try:
                on_change()
            except Exception:                                 # noqa: BLE001
                traceback.print_exc()
        page.update()

    async def import_voice(_e):
        armed["at"] = 0.0
        remove_text.value = "Remove voice"
        try:
            files = await picker.pick_files(
                dialog_title="Voice clips, or a ZIP of them",
                file_type=ft.FilePickerFileType.ANY, allow_multiple=True)
        except Exception as exc:                              # noqa: BLE001
            traceback.print_exc()
            show(f"Could not open the file picker: {exc}", p["bad"])
            return
        if not files:
            return
        paths = [f.path for f in files if getattr(f, "path", None)]
        unread = [f.name for f in files if not getattr(f, "path", None)]
        try:
            result = await asyncio.to_thread(import_files, paths)
        except Exception as exc:                              # noqa: BLE001
            traceback.print_exc()
            show(f"Import failed: {type(exc).__name__}: {exc}", p["bad"])
            return
        result["failed"] += [(name, "the phone did not hand it over")
                             for name in unread]
        # Anything that did not arrive is trouble, however much else did.
        # v1.14.0 showed "Imported 16 clips. Left out, ...: dist-400ft.mp3" in
        # green, and green is read as "that worked" before the words are.
        trouble = (result["failed"] or result["refused"] or result["duplicates"]
                   or result["ignored"])
        show(summary(result), p["warn"] if trouble else p["good"])

    def remove_voice(_e):
        now = time.time()
        if now - armed["at"] > CONFIRM_S:
            armed["at"] = now
            remove_text.value = "Tap again to remove your clips"
            page.update()
            return
        armed["at"] = 0.0
        remove_text.value = "Remove voice"
        try:
            gone = clear_voice()
        except OSError as exc:
            traceback.print_exc()
            show(f"Could not remove the voice: {exc}", p["bad"])
            return
        # Only her files go; the voice bundled into the app stays, so say what
        # speaks now rather than leaving "removed" to sound like silence.
        after = (" The built-in voice speaks again." if status()["builtin"]
                 else " The phone's own voice speaks.")
        show(f"Removed {gone} file{'' if gone == 1 else 's'}.{after}", p["text2"])

    remove_btn = ui.button("Remove voice", remove_voice, icon=ft.Icons.DELETE_OUTLINE)
    remove_text = remove_btn.content.controls[-1]

    return ui.card(ft.Column(spacing=8, tight=True, controls=[
        ui.label("RECORDED VOICE"),
        line,
        ft.Text("Pick the clips, or one ZIP of them, from Downloads, Drive or "
                "anywhere the phone's picker reaches. A clip picked again "
                "replaces that one.", size=ui.SMALL, color=p["text2"]),
        ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
            ui.button("Import voice", import_voice, icon=ft.Icons.FILE_OPEN,
                      primary=True),
            remove_btn,
        ]),
        said,
    ]))


# ------------------------------------------------------------ the theatre card

#: The moods in the order a ride meets them, each with when it plays. Words,
#: not code: these are hers to read while she decides which clips to keep.
#: How OFTEN comes from voicelines' own rates, so it cannot drift from them.
THEATRE = (
    ("start", "Setting off", "a route to a new destination"),
    ("urgent", "Ordinary turns", "after a turn prompt - never in the last "
                                 "seconds before the turn"),
    ("angry", "Off route", "off route, or re-routing"),
    ("scold", "Speeding", "going over the limit"),
    ("sorry", "Sorry", "after saying a re-route could not be made"),
    ("arrival", "Arriving", "the last prompt, at the destination"),
)
COUGH = ("Cough", "between the distance and the turn - \"in a quarter mile "
                  "*cough* turn left\"")


def how_often(rate):
    """A 0-1 chance as words: 1.0 "every time", 0.25 "1 time in 4"."""
    if rate >= 1.0:
        return "every time"
    if rate <= 0:
        return "never"
    return f"1 time in {round(1 / rate)}"


def words_of(name):
    """What a theatre clip says, from its file name.

    flourish-angry-you-idiot -> "you idiot"; interject-cough-2 -> "cough 2".
    """
    match = _FLOURISH.match(name)
    rest = match.group(2) if match else name.removeprefix("interject-")
    return rest.replace("-", " ")


def theatre(directory=None, builtin=None):
    """[(mood, title, when, often, {name: path})] - every theatre group, in
    ride order, then the cough, empty groups included (the empty ones say
    what to name a clip so it lands there)."""
    from . import voicelines
    groups = [(mood, title, when, how_often(voicelines.FLOURISH_RATE[mood]),
               flourishes(mood, directory, builtin))
              for mood, title, when in THEATRE]
    groups.append(("cough", COUGH[0], COUGH[1],
                   how_often(voicelines.COUGH_RATE),
                   interjections("interject-cough", directory, builtin)))
    return groups


#: A theatre tile: two to a row at 412 dp, more when the phone is sideways.
THEATRE_TILE_W = 164


def theatre_card(page, directory=None, builtin=None, play=None):
    """The character lines, grouped by when they play, each a tap to hear.

    Hers to curate: she asked for them grouped by mood with a tap to hear
    each one. Every group says when it plays and how often, from the same
    rates the ride uses; a group with nothing in it says what to call a clip
    so it lands there. The tile that is playing shows a speaker, so she can
    see which one she is hearing without looking for a line at the bottom.

    Returns (card, refresh) like the lines grid: `refresh` re-reads the
    folders after an import or a removal. `play` is speech.play (tests pass
    their own).
    """
    import flet as ft

    from . import prefs, speech, ui
    from .theme import BRD, PAL, RAD

    p = PAL()
    play = play or speech.play
    groups = ft.Column(spacing=12, tight=True)
    heard = ft.Text("", size=ui.SMALL, color=p["text2"])
    off = ft.Text("", size=ui.SMALL, color=p["warn"])
    lit = {"icon": None}

    def tap(name, path, icon):
        def handler(_e):
            if lit["icon"] is not None:
                lit["icon"].icon = ft.Icons.PLAY_ARROW
            icon.icon = ft.Icons.VOLUME_UP
            lit["icon"] = icon
            play([("flourish", path, None)])
            whose = clip_source(name, directory, builtin) or "yours"
            problem = speech.STATE.get("problem")
            heard.value = (f"Playing \"{words_of(name)}\", {whose}."
                           + (f" {problem}" if problem else ""))
            page.update()
        return handler

    def tile(name, path):
        source = clip_source(name, directory, builtin)
        colour = {"yours": p["good"], "built in": p["accent"]}.get(source,
                                                                    p["border"])
        icon = ft.Icon(ft.Icons.PLAY_ARROW, size=18, color=colour)
        return ft.Container(
            width=THEATRE_TILE_W, height=ui.TOUCH, ink=True,
            on_click=tap(name, path, icon),
            padding=ft.Padding(left=10, top=0, right=8, bottom=0),
            border_radius=RAD(12), bgcolor=p["surface"],
            border=BRD(colour, 2), alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(spacing=8, controls=[
                icon,
                # A fixed width, not expand: the tiles sit in a wrapping Row,
                # and an expanding child there paints grey in a release build.
                ft.Column(spacing=0, tight=True, width=THEATRE_TILE_W - 44,
                          alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Text(words_of(name), size=ui.SMALL,
                            weight=ft.FontWeight.W_700, color=p["text"],
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(source or "", size=ui.SMALL, color=colour)])]))

    def group(mood, title, when, often, clips):
        head = ft.Row(spacing=8, controls=[
            ft.Text(title, size=ui.MID, weight=ft.FontWeight.W_800,
                    color=p["text"]),
            ft.Text(f"{len(clips)}" if clips else "none", size=ui.SMALL,
                    weight=ft.FontWeight.W_700,
                    color=p["text2"] if clips else p["warn"])])
        prefix = ("interject-cough" if mood == "cough"
                  else f"flourish-{mood}")
        body = (ft.Row(wrap=True, spacing=8, run_spacing=8,
                       controls=[tile(n, path) for n, path in clips.items()])
                if clips else
                ft.Text(f"Name a clip {prefix}-<words>.mp3 and import it to "
                        "put it here.", size=ui.SMALL, color=p["text2"]))
        return ft.Column(spacing=6, tight=True, controls=[
            head,
            ft.Text(f"{when} · {often}", size=ui.SMALL, color=p["text2"]),
            body])

    def refresh():
        lit["icon"] = None
        off.value = ("" if prefs.flourishes() else
                     "Character lines is off, so none of these play on a "
                     "ride - tap to hear them anyway.")
        groups.controls = [group(*g) for g in theatre(directory, builtin)]

    refresh()
    card = ui.card(ft.Column(spacing=8, tight=True, controls=[
        ui.label("CHARACTER LINES"),
        ft.Text("Tap one to hear it. Each group plays at one moment of a "
                "ride; one clip is picked at random, never the same one twice "
                "running. Each tile says whose it is: yours, or built in.",
                size=ui.SMALL, color=p["text2"]),
        off,
        heard,
        groups,
    ]))
    return card, refresh
