# Agedashii

Companion app for [dashbored](https://git.alola.lol) — nvii's homelab dashboard.
Built with [Flet](https://flet.dev) (Python → Flutter). One codebase builds an
Android APK, a Linux x64 desktop app, a macOS app, an unsigned iOS simulator
build, and a web/PWA bundle.

## Pointing it at your server

The app is a client; it needs a dashbored server to talk to. Set the address in
**Settings → Server** — it is saved on the device (Flet `SharedPreferences`) and
reused on next launch. No rebuild, no editing source.

On startup the app tries each known address in order and adopts the first that
answers, so a saved address that has gone stale falls back to the defaults in
`agedashii_tofu/config.py` rather than hanging.

### Reaching it from outside the house

| Where you are | Works? | How |
|---|---|---|
| On the LAN | yes | `http://192.0.2.21:6900` direct |
| On WireGuard (`vpn.alola.lol`) | yes | same LAN address, through the tunnel |
| Cellular / anywhere else | **no** | see below |

`iamprettyfamous.online` will not work as the app's server address. Two separate
walls, both measured on 2026-09-19:

1. **Cloudflare** 403s the default `Python-urllib/3.x` user agent at the edge,
   before the request reaches the origin. The app therefore identifies itself as
   `agedashii/1.0`, which passes.
2. **VoidAuth** forward-auth then 302s *every* path to
   `auth.iamprettyfamous.online` — `/api/dashboard` and `/api/config` included.
   There is no token or header the app can send to get past this today.

So off-LAN use means the VPN. Settings reports the difference explicitly
("blocked by auth" vs "unreachable") rather than a generic offline state,
because the app silently showing sample data is the failure this is designed to
make visible.

Giving the app real off-LAN access would mean adding a token-auth path on the
dashbored server and a Cloudflare rule to let it through — a deliberate decision
about exposing the API publicly, not something to switch on casually.

## What's live vs sample

If the server is unreachable, pages fall back to bundled mock data so the UI
never blanks. **Settings → Server** shows which one you are looking at.

| Page | Live from server | Sample / placeholder |
|---|---|---|
| Home | weather, Wat2Do checklist + progress, machine dots | now-playing (mock), quick actions (not wired) |
| Lab → Machines | machines, uptime, CPU/RAM/temp, online dots | Things (soon), Services (demo), Qbit (sample) |
| Tools | feeds · **Journal — create, edit, delete** · **FoodZen — grocery list, needs, pantry** · **Vault — write notes over SSH** · **EatZen — sodium tracking** · **BPM — blood pressure** | fixation radar (mock) |
| Trips | trip list, ride files · **create a trip** | stops/route are website-only; ride stats columns (pending GPX parse) |
| Weather | current + 4-day forecast | Explore map (soon) |
| Garage | bikes, specs, stats · **Mechanic — log mileage / services / expenses** | Flipper deals (sample) |
| Wat2Do | full checklist — **toggles and sliders save to the server** | date chevrons (not wired yet) |
| Extras | projects, **AI chat (real replies via OpenRouter)** | XNAV (soon), obligations (mock) |
| Settings | server address + status, all 21 themes | theme applies live (per-session) |

## Tests

```bash
python3 -m unittest discover -s tests -v    # 21 tests, no network needed
```

They cover server discovery and persistence: URL normalising, failover across
candidates, storage round-trips, and — most importantly — that each failure mode
is reported distinctly, since "unreachable", "behind a login" and "blocked at
the edge" need completely different fixes.

## Running it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # flet==0.86.5
.venv/bin/python main.py                    # serves on :8550
```

**This does not currently work on an aarch64 Raspberry Pi**, and the cause is
not what a previous version of this README claimed. It is not a piwheels
problem, and force-reinstalling from pypi.org does not fix it:
`flet_web-0.86.5-py3-none-any.whl` fetched straight from pypi.org with
`--no-cache-dir` is 24 kB and contains **zero** files under `flet_web/web/`
(verified 2026-09-19). The Flutter web client is simply not shipped in the
0.86.5 wheel, so the server starts and then 500s with
`RuntimeError: Web root path not found`.

`flet run --web` is no better here — it imports `flet_desktop`, which is not
installed and is a 6 kB stub that fetches a platform binary at runtime.

Neither affects the actual deliverables: `flet build` in CI downloads its own
Flutter SDK and produces real apps. To preview on the Pi, use the CI **web/PWA**
artifact and serve the folder statically.

## Building

CI (`.github/workflows/build.yml`) builds everything on GitHub runners —
`workflow_dispatch` to run it by hand:

| Artifact | Runner | Notes |
|---|---|---|
| `agedashii-android-apk` | ubuntu | release APK |
| `agedashii-linux-x64` | ubuntu | folder build (binary + libs) |
| `agedashii-linux-appimage` | ubuntu | **single double-clickable executable** |
| `agedashii-web-pwa` | ubuntu | browser build — also the only way to preview on the Pi |
| `agedashii-macos` | macos | unsigned; right-click → Open on first launch |
| `agedashii-ios-unsigned-ipa` | macos | unsigned .ipa for sideloading |

**iOS:** the .ipa is built unsigned — with no provisioning profile configured,
flet passes `--no-codesign` through to flutter. Sideload it with AltStore or
Sideloadly, which re-sign with a free Apple ID; no paid Apple Developer Program
account needed. (Free certs expire every 7 days, so the app needs periodic
re-signing — AltStore does that automatically over wifi.)

`flutter build ipa --no-codesign` frequently stops at the `.app` and never
emits a `.ipa`, since packaging is normally part of signing. The workflow
detects that and zips the app into `Payload/` itself, so the artifact is always
a sideload-ready `.ipa`.

Note on an earlier green build: run `34007945982` reported success for both jobs
and uploaded **zero artifacts**, because the upload globs did not match flet's
output paths and `if-no-files-found` defaulted to a warning. Every build now
writes to an explicit `-o dist/<target>` and every upload uses
`if-no-files-found: error`, so an empty artifact fails the run instead of
quietly shipping nothing.

**Building on the Pi directly (arm64):** Google ships no arm64-host
`gen_snapshot` or `aapt2` for Linux, so a release AOT build needs a debug build
(`flet build apk --flutter-build-args=--debug`, skipping AOT) plus
`android.aapt2FromMavenOverride=<arm64 aapt2>` in
`build/flutter/android/gradle.properties`. Use CI for a real release APK.

## Code map

- `main.py` — async entry point: loads the saved server address before first
  paint, then the nav shell (topbar, drawer, bottom bar, page router)
- `agedashii_tofu/config.py` — which server to talk to: candidates, probing, failure
  diagnosis, `SharedPreferences` persistence
- `agedashii_tofu/api.py` — the client: per-endpoint adapters reshaping server JSON,
  30s TTL cache, mock fallback, AI chat
- `agedashii_tofu/mock.py` — mock data, same shapes as the adapters
- `agedashii_tofu/theme_data.py` — 21 themes extracted 1:1 from the dashboard's CSS by
  `tools/gen-themes.py`
- `agedashii_tofu/ui.py` — theme + app state, card/chip/dot helpers, `apply_theme`
- `agedashii_tofu/pages/` — one module per page; tabbed pages share `lab._tabs`
- `tests/` — stdlib unittest

Architecture: pages read only the `M` interface — `from .. import api as M`.
Swap `api` for `mock` and the app renders fully offline (writes become no-ops).

## Versions

Flet `0.86.5` and Flutter `3.44.8` are pinned together in `requirements.txt` and
the workflow; bump both at once. The app uses the 0.86 API (`ft.run`,
`SharedPreferences` as a registered service), so an unpinned `pip install flet`
will eventually break it.
