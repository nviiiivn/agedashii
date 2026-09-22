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
# IPA is deliberately NOT a target here. Building one needs macOS - Xcode,
# and the iOS SDK - and Tower is Linux. There is no Mac on this network.
# See the note at the bottom of this file for the two honest routes onto an
# iPhone.
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
  # live in flet_app/, which also carries the aliases that let it keep
  # showing the three split apps' surfaces until they are confirmed.
  cp "$SRC/main.py" "$STAGE/"
  cp -r "$SRC/flet_app" "$STAGE/"
  cp -r "$SRC/apps/moto/agedashii_moto"       "$STAGE/"
  cp -r "$SRC/apps/watudo/agedashii_watudo"   "$STAGE/"
  cp -r "$SRC/apps/foodzen/agedashii_foodzen" "$STAGE/"
  cp "$SRC/pyproject.toml" "$STAGE/"
else
  cp "$SRC/apps/$APP/main.py" "$STAGE/"
  cp -r "$SRC/apps/$APP/agedashii_$APP" "$STAGE/"
  cp "$SRC/apps/$APP/pyproject.toml" "$STAGE/"
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

# Getting onto an iPhone, honestly
# ---------------------------------
# `flet build ipa` needs macOS. There is no Mac here and GitHub's macOS
# runners bill at a 10x multiplier against an allowance that is currently
# exhausted. The two routes that actually work today:
#   1. The PWA at https://agedashii.alola.lol - Safari, Share, "Add to Home
#      Screen". No account, no signing, no cost.
#   2. An unsigned .ipa sideloaded with AltStore or Sideloadly, which
#      re-sign it with a free Apple ID. That still requires a Mac (or a
#      macOS runner) to produce the .ipa in the first place.
# A real App Store install needs a paid Apple Developer account. Nothing in
# this script pretends otherwise.
