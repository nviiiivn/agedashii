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
"""
import asyncio
import os
import re
import time
import traceback
import zipfile

#: The prompts, by the file name that carries each one. Fixed: her recordings
#: are named after these, so a name here never changes.
NAMES = (
    "turn-left", "turn-right", "slight-left", "slight-right", "sharp-left",
    "sharp-right", "uturn", "continue", "merge", "exit", "arrive",
    "dist-100ft", "dist-200ft", "dist-300ft", "dist-500ft", "dist-quarter",
    "dist-half", "dist-1mile", "dist-2miles", "offroute", "rerouting", "start",
    "speeding",
)
_NAMES = frozenset(NAMES)

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


def clip_path(name, directory=None):
    """The clip for prompt `name`, or None. Cheap: a few stat calls, no cache.

    None for a name with no clip and for a name that is not one of NAMES.
    """
    if name not in _NAMES:
        return None
    folder = directory or voice_dir()
    for ext in EXTS:
        path = os.path.join(folder, name + ext)
        if os.path.isfile(path):
            return path
    return None


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
    return (name if name in _NAMES else None), ext


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
        for name in NAMES:
            if name in chosen:
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
        had = clip_path(name, folder) is not None
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


def status(directory=None):
    """What is in the voice folder, against NAMES.

    present / missing: names, in NAMES order. duplicates: name -> its files,
    the one clip_path plays first. unknown: files that are no prompt's.
    """
    folder = directory or voice_dir()
    try:
        entries = sorted(os.listdir(folder))
    except FileNotFoundError:
        entries = []
    found, unknown = {}, []
    for entry in entries:
        if entry.startswith(_INCOMING) or not os.path.isfile(os.path.join(folder, entry)):
            continue
        stem, ext = os.path.splitext(entry)
        if stem in _NAMES and ext in _RANK:
            found.setdefault(stem, []).append(entry)
        else:
            unknown.append(entry)
    return {
        "present": [n for n in NAMES if n in found],
        "missing": [n for n in NAMES if n not in found],
        "duplicates": {n: sorted(files, key=lambda f: _RANK[os.path.splitext(f)[1]])
                       for n, files in found.items() if len(files) > 1},
        "unknown": unknown,
    }


def _few(items, most=4):
    items = list(items)
    if len(items) <= most:
        return ", ".join(items)
    return ", ".join(items[:most]) + f" and {len(items) - most} more"


def status_text(directory=None):
    """One line: how much of her voice is in, and what is not."""
    st = status(directory)
    have = len(st["present"])
    if not have:
        line = "No recorded voice yet."
    else:
        line = f"Recorded voice: {have} of {len(NAMES)} clips."
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
        parts.append(f"Left out, not one of the {len(NAMES)} names: "
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
        trouble = result["failed"] or result["refused"] or result["duplicates"]
        show(summary(result), p["warn"] if trouble else p["good"])

    def remove_voice(_e):
        now = time.time()
        if now - armed["at"] > CONFIRM_S:
            armed["at"] = now
            remove_text.value = "Tap again to remove every clip"
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
        show(f"Removed {gone} file{'' if gone == 1 else 's'}.", p["text2"])

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
