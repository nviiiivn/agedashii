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
#   tools/build_app.sh <tofu|moto|watudo|foodzen> [apk] [linux]
# Defaults to apk if no target is given.
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
  tofu|moto|watudo|foodzen) ;;
  *) echo "usage: $0 <tofu|moto|watudo|foodzen> [apk] [linux]" >&2; exit 2 ;;
esac

TC="${TC:-/home/nvii/flet-toolchain}"
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
case "$APP" in
  tofu|moto) WANT_MAPS=1 ;;
  *)         WANT_MAPS=0 ;;
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
if [ -f "$ICONS/$APP-1024.png" ]; then
  cp "$ICONS/$APP-1024.png" "$STAGE/assets/icon.png"
  cp "$ICONS/$APP-192.png"  "$STAGE/assets/icons/icon-192.png"
  cp "$ICONS/$APP-512.png"  "$STAGE/assets/icons/icon-512.png"
  # Android's adaptive icon crops to a circle; the artwork is already
  # inset for that, so the maskable variant is the same file rather than a
  # second drawing that could drift away from the first.
  cp "$ICONS/$APP-192.png"  "$STAGE/assets/icons/icon-maskable-192.png"
  cp "$ICONS/$APP-512.png"  "$STAGE/assets/icons/icon-maskable-512.png"
  cp "$ICONS/$APP-192.png"  "$STAGE/assets/icons/apple-touch-icon-192.png"
  cp "$ICONS/$APP-192.png"  "$STAGE/assets/favicon.png"
  echo "    icon:  $APP-1024.png"
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
echo "    staged $(du -sh "$STAGE" | cut -f1)"

cd "$STAGE" || exit 1
rc_all=0
for T in "${TARGETS[@]}"; do
  echo "=== $APP $T  ($(date -Is))"
  # --exclude keeps the build's own output and any git history OUT of the
  # bundle. Inspecting an early 402 MB AppImage found the entire .git
  # directory inside it, which hands over the full history of a private
  # repo to anyone who downloads the app.
  time flet build "$T" --yes -o "dist/$T" --exclude .git dist build .venv
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "!!! $APP $T FAILED rc=$rc"
    rc_all=$rc
    continue
  fi
  case "$T" in
    apk)   find "dist/$T" -name '*.apk' -exec cp {} "$OUT/agedashii-$APP.apk" \; ;;
    linux) tar -C "dist/$T" -czf "$OUT/agedashii-$APP-linux-x64.tar.gz" . ;;
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
