"""Is there a newer build, and how to get it.

Her "can we set up OTA updates at some point?", then "mmmmmm okay yeah do it".

WHAT THIS DOES AND WHAT IT DELIBERATELY DOES NOT

It asks the server for a small `version.json`, compares the build number against
this build's, and if there is a newer one it says so and opens the download in
one tap. It does **not** silently install.

That boundary is a real technical limit, not caution. Handing an APK to Android's
package installer from inside an app needs a `content://` URI from a
FileProvider, and a FileProvider needs an entry in AndroidManifest.xml and a Dart
side to pass the intent - neither of which Flet exposes. The honest version is:
the download and the install are the browser's job, and the app's job is knowing
there is something to install. When a FileProvider exists, `install()` below is
the only function that changes.

WHY IT IS SAFE TO RUN AT LAUNCH

It is one request for a few hundred bytes, it never blocks, and every failure is
silent: no signal is the normal state for this app, and an update check that
complained about it would be complaining constantly. `check()` returns a dict
rather than raising, and the error only ever appears on the Settings screen where
she went looking for it.
"""
import json
import time
import urllib.request

from . import version

#: Where the published build is described. Alongside the APK on purpose, so there
#: is one place to publish to and the two cannot drift apart.
MANIFEST_URL = "https://xnav.alola.lol/version.json"
APK_URL = "https://xnav.alola.lol/apk"

#: Seconds before an unanswered check gives up. Short: this runs at launch and
#: must never be something the app waits on.
TIMEOUT_S = 6.0

STATE = {}


def reset():
    STATE.clear()
    STATE.update({
        "checked_at": 0.0,
        "latest_code": None,
        "latest_name": "",
        "size": None,
        "sha256": "",
        "notes": "",
        "url": APK_URL,
        "error": "",
    })


reset()


def fetch_manifest(url=MANIFEST_URL, timeout=TIMEOUT_S):
    """The published build description, or None. Never raises.

    Plain urllib rather than Washi's store, because this must NOT be cached: a
    cached answer to "is there something newer" is the one answer that is
    worthless. It must not land in the offline outbox either - there is nothing
    to retry, and a missed check is simply a check that happens next launch.
    """
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "agedashii-navi"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception:                       # noqa: BLE001 - see the docstring
        return None


def check(url=MANIFEST_URL, timeout=TIMEOUT_S):
    """Ask whether there is a newer build. Returns a copy of STATE.

    Blocking - call it from a worker thread.
    """
    STATE["checked_at"] = time.time()
    STATE["error"] = ""
    manifest = fetch_manifest(url, timeout)
    if manifest is None:
        STATE["error"] = "could not reach the server"
        return dict(STATE)
    try:
        STATE["latest_code"] = int(manifest.get("code"))
    except (TypeError, ValueError):
        STATE["latest_code"] = None
        STATE["error"] = "the server's version file is malformed"
        return dict(STATE)
    STATE["latest_name"] = str(manifest.get("name") or "")
    STATE["size"] = manifest.get("size")
    STATE["sha256"] = str(manifest.get("sha256") or "")
    STATE["notes"] = str(manifest.get("notes") or "")
    STATE["url"] = str(manifest.get("url") or APK_URL)
    return dict(STATE)


def available(current=None):
    """Whether the last check found something newer than this build.

    Strictly greater. Equal is current, and LOWER is also "no" - a server that
    has been rolled back must never talk a phone into downgrading, because on
    Android a downgrade is not an install at all. It is an uninstall, and it
    takes every ride that had not synced with it.
    """
    latest = STATE.get("latest_code")
    if latest is None:
        return False
    return latest > (version.VERSION_CODE if current is None else current)


def summary(current=None):
    """One line for the Settings row, in her terms."""
    if STATE.get("error"):
        return STATE["error"]
    if STATE.get("latest_code") is None:
        return "not checked yet"
    if available(current):
        size = STATE.get("size")
        tail = f" · {size / 1_000_000:.0f} MB" if size else ""
        return f"v{STATE['latest_name']} is available{tail}"
    # Say WHEN, not only WHAT.
    #
    # Hers: "check for updates doesnt give any indications im on the current
    # version or if theres a new vesion ... idk if it works". It did work and
    # it did say "up to date" - but the line read identically before and after
    # the tap, so the button looked dead. The clock is the proof it ran.
    when = STATE.get("checked_at")
    stamp = (f" · checked {time.strftime('%H:%M', time.localtime(when))}"
             if when else "")
    return f"up to date ({version.label()}){stamp}"


def install(page):
    """Get the new build onto the phone. One tap, then Android takes over.

    Opens the APK URL, which hands the download to the browser and the install to
    Android's package installer - the same flow as tapping the link on the site,
    minus having to remember the link.

    The silent path replaces exactly this function; see the module docstring.
    """
    url = STATE.get("url") or APK_URL
    launcher = getattr(page, "launch_url", None)
    if not callable(launcher):
        return False
    # `page.launch_url` is ASYNC in Flet 0.86.
    #
    # Calling it plainly built a coroutine, never awaited it, and returned
    # True - so "Get it" reported success and nothing whatsoever happened.
    # Hers: "i click get it and it never seems to download or anythingg".
    # Same mistake as `Map.center_on`; both were hidden by a bare except.
    runner = getattr(page, "run_task", None)
    try:
        if callable(runner):
            runner(launcher, url)
        else:
            launcher(url)                   # a stub page in the tests
        return True
    except Exception as exc:                # noqa: BLE001 - no browser, or a
        # platform that refuses. The URL is on screen either way.
        print(f"[navi] update download failed: {type(exc).__name__}: {exc}",
              flush=True)
        return False


def manifest_for(apk_path, url=APK_URL, notes=""):
    """Build the `version.json` this module reads. Used by the publish step.

    Here rather than in the build script so the thing that WRITES the manifest
    and the thing that READS it are the same file - a format living in two places
    is a format that eventually disagrees with itself.
    """
    import hashlib
    import os
    digest = hashlib.sha256()
    with open(apk_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "code": version.VERSION_CODE,
        "name": version.VERSION_NAME,
        "size": os.path.getsize(apk_path),
        "sha256": digest.hexdigest(),
        "url": url,
        "notes": notes,
        "published": int(time.time()),
    }
