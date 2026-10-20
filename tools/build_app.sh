#!/usr/bin/env bash
# Build one Agedashii app, on Tower, from a staging directory.
#
# Why a staging directory and not just `cd apps/moto && flet build`:
# `flet build` bundles the directory it is pointed at, and decides what to
# pip-install into that bundle from the requirements.txt it finds there. The
# two shared packages live at ../../packages/, which is outside the bundle
# and outside the temporary build tree flet copies into - a relative path
# requirement would resolve on this machine and then not exist by the time
# pip runs. Vendoring them into a staging copy sidesteps all of that: what
# gets built is exactly what is in one directory, with no path that has to
# survive being moved.
#
# It also keeps the repo clean. Nothing is copied INTO apps/<id>/, so a
# build leaves no untracked packages behind for the next `git status` to
# show, and two builds cannot interfere with each other.
#
# Usage, on Tower:
#   tools/build_app.sh <tofu|moto|watudo|foodzen|navi> [apk] [linux] [web] [ipa]
# Defaults to apk if no target is given.
#
# `ipa` only runs on macOS - `flet build ipa` shells out to xcodebuild - so
# in practice it runs on a macos-latest CI runner rather than here. Set TC
# to a writable directory there; it defaults to the toolchain path on Tower.
#
# `web` is the PWA - what tofu.alola.lol serves, and the only route onto an
# iPhone that costs nothing. It is also the target that OOM-kills a CI
# runner, which is why the old workflow built it LAST and uploaded
# everything else first. Tower has 62 GB, so it is safe here; build it last
# anyway, so a failure cannot take the other artifacts with it.
#
# IPA is deliberately NOT a target here. Building one needs macOS, because
# `flutter build ipa` shells out to xcodebuild. That is being solved
# separately with a macOS guest under KVM on this same machine - see the
# note at the bottom of this file - but it is a different pipeline with a
# different prerequisite, so it does not belong in this script.
set -uo pipefail

APP="${1:-}"
shift || true
TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=(apk)

case "$APP" in
  tofu|moto|watudo|foodzen|navi) ;;
  *) echo "usage: $0 <tofu|moto|watudo|foodzen|navi> [apk] [linux] [web] [ipa]" >&2; exit 2 ;;
esac

TC="${TC:-/home/nvii/projects/agedashii/flet-toolchain}"
export JAVA_HOME="$TC/jdk17"
export ANDROID_HOME="$TC/android-sdk"
export ANDROID_SDK_ROOT="$TC/android-sdk"
export PATH="$JAVA_HOME/bin:$TC/flutter/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$TC/venv/bin:$PATH"
export PUB_CACHE="$TC/pub-cache"
export GRADLE_USER_HOME="$TC/gradle"
export UV_PYTHON_INSTALL_DIR="$TC/uv-python"

SRC="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$TC/stage/$APP"
OUT="$TC/out/$APP"

# Which apps carry the offline map. assets/maps is 120 MB - a 39 MB tile
# pack and an 86 MB routing graph - and every target that includes it
# embeds its own copy. Moto navigates and records rides, so it needs both.
# Tofu still shows Trips and XNAV, so it does too. Watudo only ever prints
# "routing..." and asks the server for the answer, and Foodzen has no map
# at all: shipping them 120 MB they never open would roughly triple their
# download for nothing.
# Navi is the whole reason the pack exists - an offline navigation app with no
# tiles and no graph is a grey grid and a refusal - so it gets both.
case "$APP" in
  tofu|moto|navi) WANT_MAPS=1 ;;
  *)              WANT_MAPS=0 ;;
esac

echo "=== staging $APP  ($(date -Is))"
rm -rf "$STAGE"
mkdir -p "$STAGE" "$OUT"

# The two shared packages, vendored flat so they import as top-level names
# exactly as they do in development.
cp -r "$SRC/packages/agedashii-qi/agedashii_qi"       "$STAGE/"
cp -r "$SRC/packages/agedashii-washi/agedashii_washi" "$STAGE/"

if [ "$APP" = tofu ]; then
  # Tofu is the repo root itself: its entry point is main.py and its pages
  # live in agedashii_tofu/, which also carries the aliases that let it keep
  # showing the three split apps' surfaces until they are confirmed.
  cp "$SRC/main.py" "$STAGE/"
  cp -r "$SRC/agedashii_tofu" "$STAGE/"
  cp -r "$SRC/apps/moto/agedashii_moto"       "$STAGE/"
  cp -r "$SRC/apps/watudo/agedashii_watudo"   "$STAGE/"
  cp -r "$SRC/apps/foodzen/agedashii_foodzen" "$STAGE/"
  cp "$SRC/pyproject.toml" "$STAGE/"
else
  cp "$SRC/apps/$APP/main.py" "$STAGE/"
  cp -r "$SRC/apps/$APP/agedashii_$APP" "$STAGE/"
  cp "$SRC/apps/$APP/pyproject.toml" "$STAGE/"
  # Strip the shared packages out of the STAGED pyproject - they are
  # vendored above as plain directories, so asking pip to fetch them is
  # asking for something that does not exist:
  #
  #   ERROR: No matching distribution found for agedashii-washi==1.0.0
  #
  # which is exactly how moto, watudo and foodzen all failed in six
  # seconds while tofu built fine. Tofu's pyproject deliberately declares
  # NO dependencies at all, so flet fell through to requirements.txt and
  # never tried. The apps DO declare them, correctly - that is what makes
  # `pip install -e apps/moto` work for development - so the declaration
  # stays in the repo and is removed here, at staging time, where it is
  # wrong. [tool.flet.dev_packages] goes for the same reason: it points at
  # ../../packages/, a path that stops existing the moment flet copies the
  # tree somewhere else.
  python3 - "$STAGE/pyproject.toml" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p).read()

def strip_comments(inner):
    """Drop TOML # comments, so a quoted phrase inside one is not read as a
    dependency. Watudo's list explains flet-map by saying the app's
    `"route to here"` would have nowhere to draw - and the naive
    findall below turned that sentence fragment into a requirement:

        ERROR: Invalid requirement: 'route to here'

    which failed the whole build in two seconds. A # only starts a comment
    when it is outside a string, so the scan tracks quoting rather than
    cutting at the first # it sees."""
    out = []
    for line in inner.splitlines():
        q = None
        for i, ch in enumerate(line):
            if q:
                if ch == q:
                    q = None
            elif ch in '"\'':
                q = ch
            elif ch == "#":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def drop_shared(m):
    inner = strip_comments(m.group(1))
    kept = [d for d in re.findall(r'"[^"]*"', inner)
            if not d.strip('"').lower().startswith("agedashii-")]
    return "dependencies = [" + ", ".join(kept) + "]"

s = re.sub(r"dependencies\s*=\s*\[([^\]]*)\]", drop_shared, s, count=1)
# Remove the whole [tool.flet.dev_packages] table, up to the next table.
#
# The cut ends at the next line that STARTS with `[`, not at the next `[`
# anywhere. `[^\[]*` was the latter, and a `[` inside a prose comment ends
# it just as well as a real table header does. Watudo's comment explaining
# why it declares no permissions mentions "an `[tool.flet.android.feature]`
# table saying GPS hardware is not required" - so the cut stopped in the
# middle of that sentence and left
#
#     [tool.flet.android.feature]` table saying
#
# as a line of its own, which is not TOML:
#
#     TOMLDecodeError: Expected newline or end of document after a
#     statement (at line 60, column 28)
#
# and watudo failed both targets in half a second while the other three
# built fine. A comment is prose and is allowed to name a table; the regex
# is the thing that has to be precise about what a table header looks like.
s = re.sub(r"\n\[tool\.flet\.dev_packages\].*?(?=\n\[|\Z)", "", s, flags=re.S)
open(p, "w").write(s)
print("    deps:  shared packages stripped from the staged pyproject")
PYEOF
fi

# flet reads requirements.txt to decide what to install INTO the bundle.
# flet-cli is deliberately absent: it is the build tool, not a runtime
# dependency, and listing it bundles the whole build system into the app.
# The two shared packages are absent too - they are already vendored above
# as plain directories, so pip has nothing to fetch.
grep -v -E '^\s*(flet-cli|agedashii-)' "$SRC/requirements.txt" > "$STAGE/requirements.txt"

mkdir -p "$STAGE/assets/icons"
# Flet picks an app's icon up by FILENAME, not from any setting: assets/
# icon.png for the launcher on every platform, and assets/icons/icon-192
# and icon-512 for the PWA and its "Add to Home Screen" tile. The masters
# in assets/icons/ are named per app (tofu-1024.png, moto-1024.png, ...)
# because one repo holds four apps, so the rename happens here, at staging
# time. Keeping four copies of icon.png in the repo under four directories
# would mean four places to forget when an icon changes.
ICONS="$SRC/assets/icons"

# Which artwork this build wears on the home screen.
#
# Android will not let an app change its own launcher icon at runtime - that
# needs activity-alias entries and a native PackageManager call, and Flet
# exposes neither. So the choice is made HERE, at build time, and the answer to
# "I want the Pixel to show the cb and the Moto to show the ex" is two builds of
# the same app: same package id, same signing key, different icon. Both install
# and update normally; they are the same app wearing different clothes.
#
#   AGEDASHII_ICON=ex tools/build_app.sh navi apk
ICON_ID="$APP"
if [ -n "${AGEDASHII_ICON:-}" ] && [ "${AGEDASHII_ICON}" != "default" ]; then
  if [ -f "$ICONS/$APP-${AGEDASHII_ICON}-1024.png" ]; then
    ICON_ID="$APP-${AGEDASHII_ICON}"
    echo "    icon:  variant ${AGEDASHII_ICON}"
  else
    echo "    icon:  no '${AGEDASHII_ICON}' variant for $APP, using the default"
  fi
fi

# The adaptive icon's background is the artwork's OWN ground, so no ring of
# another colour can show round it on a launcher whose mask is a touch wider
# than the foreground. The default artwork's is in its pyproject
# ([tool.flet.android] adaptive_icon_background); an alternate with a
# different ground overrides it here. Sampled from each master's corners.
ICON_BG=""
case "$ICON_ID" in
  navi-ex) ICON_BG="#FDFDFE" ;;
esac

if [ -f "$ICONS/$ICON_ID-1024.png" ]; then
  cp "$ICONS/$ICON_ID-1024.png" "$STAGE/assets/icon.png"
  cp "$ICONS/$ICON_ID-192.png"  "$STAGE/assets/icons/icon-192.png"
  cp "$ICONS/$ICON_ID-512.png"  "$STAGE/assets/icons/icon-512.png"
  # Android's adaptive icon crops to a circle and the launcher crops again, so
  # only the middle ~72% is guaranteed to survive. The generated SVG icons are
  # drawn inset for that; her own PNG artwork is a full-bleed circle and was
  # being cut twice - "it gets shoved into a circle and it doesn't look riht".
  # A dedicated maskable file, inset into the safe zone, is used when one
  # exists, and the plain icon otherwise.
  if [ -f "$ICONS/$ICON_ID-maskable-192.png" ]; then
    cp "$ICONS/$ICON_ID-maskable-192.png" "$STAGE/assets/icons/icon-maskable-192.png"
    cp "$ICONS/$ICON_ID-maskable-512.png" "$STAGE/assets/icons/icon-maskable-512.png"
  else
    cp "$ICONS/$ICON_ID-192.png"  "$STAGE/assets/icons/icon-maskable-192.png"
    cp "$ICONS/$ICON_ID-512.png"  "$STAGE/assets/icons/icon-maskable-512.png"
  fi
  cp "$ICONS/$ICON_ID-192.png"  "$STAGE/assets/icons/apple-touch-icon-192.png"
  cp "$ICONS/$ICON_ID-192.png"  "$STAGE/assets/favicon.png"
  # The alternate artwork, when the app has one. Shipped in every build rather
  # than downloaded, so switching it in Settings is instant and works offline.
  # It is the IN-APP icon only - Android will not let an app change its own
  # launcher icon without activity-alias entries and a native PackageManager
  # call, neither of which Flet exposes.
  if [ -f "$ICONS/$APP-ex-192.png" ]; then
    cp "$ICONS/$APP-ex-192.png" "$STAGE/assets/icons/icon-ex-192.png"
    cp "$ICONS/$APP-ex-512.png" "$STAGE/assets/icons/icon-ex-512.png"
    echo "    icon:  + alternate ($APP-ex)"
  fi
  echo "    icon:  $ICON_ID-1024.png"
else
  echo "    icon:  NONE - $ICONS/$APP-1024.png is missing, the build will"
  echo "           carry Flet's default and look like every other Flet app"
fi
if [ "$WANT_MAPS" = 1 ] && [ -d "$SRC/assets/maps" ]; then
  cp -r "$SRC/assets/maps" "$STAGE/assets/"
  echo "    maps: included ($(du -sh "$STAGE/assets/maps" | cut -f1))"
else
  echo "    maps: omitted - this app never opens one"
fi
# Her built-in voice lines, navi only: they play with no import, and an
# imported clip of the same name still wins (voice_import.clip_path).
# UNTRACKED ON PURPOSE (.gitignore): this repo also pushes to GitHub, and
# they are hers alone. Staged at the same app-root assets/ path the map is
# found at on the phone. Never in the web build - see EXTRA below.
if [ "$APP" = navi ]; then
  if ls "$SRC/assets/voice/"*.* >/dev/null 2>&1; then
    mkdir -p "$STAGE/assets/voice"
    cp "$SRC/assets/voice/"*.* "$STAGE/assets/voice/"
    echo "    voice: $(ls "$STAGE/assets/voice" | wc -l) built-in clips"
  else
    echo "    voice: NONE - $SRC/assets/voice is empty, so the phone's voice"
    echo "           says every line until she imports her own"
  fi
fi
echo "    staged $(du -sh "$STAGE" | cut -f1)"

cd "$STAGE" || exit 1
rc_all=0
for T in "${TARGETS[@]}"; do
  echo "=== $APP $T  ($(date -Is))"
  # --exclude keeps the build's own output and any git history OUT of the
  # bundle. Inspecting an early 402 MB AppImage found the entire .git
  # directory inside it, which hands over the full history of a private
  # repo to anyone who downloads the app.
  #
  # The web target additionally drops assets/maps. A browser build must not
  # be a 120 MB download - on the web the app talks to the server for
  # routing and tiles anyway, so the offline pack is dead weight there, and
  # baking it in is what used to OOM the CI runner.
  EXTRA=""
  # assets/voice as well: her clips are hers alone and the web app cannot play
  # them anyway (no MediaPlayer in a browser - speech.NO_BRIDGE).
  [ "$T" = web ] && EXTRA="assets/maps assets/voice"

  # Android-only packages, removed from the STAGED pyproject for web.
  #
  # pyjnius is how the app speaks: it calls android.speech.tts. There is no
  # Pyodide wheel for it, so `flet build web` dies with
  #
  #   ERROR: No matching distribution found for pyjnius
  #
  # and the web target fails in eight seconds while the APK is fine.
  #
  # The obvious fix - `pyjnius; sys_platform == 'android'` - DOES NOT WORK,
  # and failed in the more dangerous direction first. flet evaluates markers
  # on the BUILD HOST, which is Linux for both targets, so the marker meant
  # to include it on Android EXCLUDED IT EVERYWHERE: the APK built clean,
  # every test passed, and the app was silently mute. One host builds both
  # targets, so no marker can tell them apart. The split has to be made
  # here, where the target is actually known.
  ANDROID_ONLY="pyjnius"
  [ -f "$STAGE/pyproject.toml.all" ] || cp "$STAGE/pyproject.toml" "$STAGE/pyproject.toml.all"
  cp "$STAGE/pyproject.toml.all" "$STAGE/pyproject.toml"
  # The same goes for ipa: pypi.flet.dev has no iOS wheel for pyjnius either,
  # and the iPhone speaks through speech.py's iOS backend instead.
  if [ "$T" = web ] || [ "$T" = ipa ]; then
    python3 - "$STAGE/pyproject.toml" "$ANDROID_ONLY" <<'PYEOF'
import re, sys
path, names = sys.argv[1], sys.argv[2].split()
s = open(path).read()

def drop(m):
    kept = [d for d in re.findall(r'"[^"]*"', m.group(1))
            if re.split(r"[<>=!~;\[ ]", d.strip('"').lower())[0] not in names]
    return "dependencies = [" + ", ".join(kept) + "]"

open(path, "w").write(re.sub(r"dependencies\s*=\s*\[([^\]]*)\]", drop, s, count=1))
print(f"    deps:  {', '.join(names)} stripped (Android-only)")
PYEOF
  fi

  # Release signing, for apk only.
  #
  # Without a key, `flet build apk` signs with Flutter's throwaway debug key -
  # and Android ties an app's identity to its signing key, so the NEXT build
  # would be refused as an update and would have to be uninstalled first. On a
  # phone that is recording rides, uninstalling deletes every ride that has not
  # yet synced. One permanent key means every future build installs straight
  # over the top.
  #
  # The keystore and its password come from the environment and are never in
  # this repo. They live in vault on the Pi and are copied to the builder;
  # `set -u` is on, so the defaults below are what let an unsigned build still
  # work rather than dying on an unset variable.
  SIGN=""
  KS="${AGEDASHII_KEYSTORE:-}"
  if [ "$T" = apk ] && [ -n "$KS" ] && [ -f "$KS" ]; then
    SIGN="--android-signing-key-store $KS
          --android-signing-key-store-password ${AGEDASHII_KEYSTORE_PASSWORD:-}
          --android-signing-key-password ${AGEDASHII_KEY_PASSWORD:-${AGEDASHII_KEYSTORE_PASSWORD:-}}
          --android-signing-key-alias ${AGEDASHII_KEY_ALIAS:-nvii}"
    echo "    sign:  $KS (alias ${AGEDASHII_KEY_ALIAS:-nvii})"
  elif [ "$T" = apk ]; then
    echo "    sign:  NONE - debug key, this build cannot update a signed one"
  fi

  # The version Android sees.
  #
  # `--build-number` becomes versionCode, which is the ONLY thing Android
  # compares to decide whether an APK is an update. `--build-version` becomes
  # versionName, which is for people. Both are read out of the app's own
  # version.py when it has one, so the number in the APK, the number the update
  # check compares and the number in the published version.json are one number
  # with one source. Setting them here and in version.py separately is how they
  # would eventually disagree - and a versionCode that did not rise is an update
  # that silently never offers itself.
  VER=""
  VERPY="$STAGE/agedashii_$APP/version.py"
  if [ -f "$VERPY" ]; then
    V_NAME=$(python3 -c "import re,sys;print(re.search(r'VERSION_NAME\s*=\s*\"([^\"]+)\"',open(sys.argv[1]).read()).group(1))" "$VERPY" 2>/dev/null || true)
    V_CODE=$(python3 -c "import re,sys;print(re.search(r'VERSION_CODE\s*=\s*(\d+)',open(sys.argv[1]).read()).group(1))" "$VERPY" 2>/dev/null || true)
    if [ -n "$V_NAME" ] && [ -n "$V_CODE" ]; then
      VER="--build-version $V_NAME --build-number $V_CODE"
      echo "    ver:   $V_NAME ($V_CODE)"
    fi
  fi

  # $SIGN and $VER are deliberately unquoted: they are lists of arguments, not
  # single strings.
  BG=""
  [ -n "$ICON_BG" ] && [ "$T" = apk ] && BG="--android-adaptive-icon-background $ICON_BG"
  time flet build "$T" --yes -o "dist/$T" --exclude .git dist build .venv $EXTRA $SIGN $VER $BG
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "!!! $APP $T FAILED rc=$rc"
    rc_all=$rc
    continue
  fi
  case "$T" in
    apk)   find "dist/$T" -name '*.apk' -exec cp {} "$OUT/agedashii-$APP.apk" \; ;;
    # An unsigned iOS build frequently stops at the .app or .xcarchive and
    # never emits a .ipa, because packaging is normally the signing step's
    # job. A .ipa is only a zip with the bundle under Payload/, and that is
    # what Sideloadly and AltStore want, so build one when flet has not.
    ipa)
      found="$(find "dist/$T" -name '*.ipa' | head -1)"
      if [ -z "$found" ]; then
        app="$(find "dist/$T" -maxdepth 5 -name '*.app' -print -quit)"
        if [ -z "$app" ]; then
          echo "!!! $APP ipa: no .ipa and no .app under dist/$T"
          find "dist/$T" -maxdepth 4 | head -30
          rc_all=1
          continue
        fi
        echo "    packaging $app into a Payload/ zip"
        pay="$(mktemp -d)/Payload"
        mkdir -p "$pay"
        cp -R "$app" "$pay/"
        ( cd "$(dirname "$pay")" && zip -qry "$OUT/agedashii-$APP.ipa" Payload )
      else
        cp "$found" "$OUT/agedashii-$APP.ipa"
      fi ;;
    linux) tar -C "dist/$T" -czf "$OUT/agedashii-$APP-linux-x64.tar.gz" . ;;
    # The web build is a directory of files to serve, not one artifact, so
    # it is tarred whole and unpacked at the far end.
    web)   tar -C "dist/$T" -czf "$OUT/agedashii-$APP-web.tar.gz" . ;;
  esac
done

echo "=== $APP done ($(date -Is)) rc=$rc_all"
ls -la "$OUT"
exit $rc_all

# Getting onto an iPhone
# ----------------------
# `flet build ipa` needs macOS, and macOS runs here: a Sonoma guest under
# KVM on this machine, via sickcodes/docker-osx. That is proven working -
# KVM passes into the container, the image carries its own QEMU 10.1.2, and
# Apple's installer catalogue downloads without an account.
#
# The one thing that is NOT free to fetch is Xcode itself: every route to it
# (the developer site, the App Store, the `xcodes` tool) redirects to
# idmsa.apple.com to sign in. A FREE Apple Developer account is enough - the
# $99 programme is not needed - but somebody has to sign in once and put the
# .xip on this machine. After that the iOS build runs unattended.
#
# The build must be unsigned (`--no-codesign`), which is what AltStore and
# Sideloadly want: they re-sign with a free Apple ID on the way in. Only a
# real App Store install needs the paid programme.
#
# Until then, the PWA at https://agedashii.alola.lol is the no-account route
# onto an iPhone: Safari, Share, Add to Home Screen.
