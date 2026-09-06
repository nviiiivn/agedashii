# NVII-DASH

Mobile companion app for [dashbored](https://git.alola.lol) — nvii's single-file homelab dashboard. Built with [Flet](https://flet.dev) (Python → Flutter). Same codebase compiles to Android APK, Linux desktop, and runs as a web preview.

## What's live vs sample

The app talks to a running dashbored server on your LAN (`http://192.0.2.21:6900` by default — edit `BASE` in `flet_app/api.py`). If the server is unreachable, pages fall back to bundled mock data so the UI never blanks.

| Page | Live from server | Sample / placeholder |
|---|---|---|
| Home | weather, Wat2Do checklist + progress, machine dots | now-playing (mock), quick actions (not wired) |
| Lab → Machines | machines, uptime, CPU/RAM/temp, online dots | Things (soon), Services (demo), Qbit (sample) |
| Tools | feeds, journal, FoodZen lists/needs/pantry | Vault (static preview), fixation radar (mock) |
| Trips | trip list, ride files | ride stats columns (pending GPX parse) |
| Weather | current + 4-day forecast | Explore map (soon) |
| Garage | bikes, specs, mileage/service stats | Flipper deals (sample), Mechanic (soon) |
| Wat2Do | full checklist — **toggles and sliders save to the server** | date chevrons (not wired yet) |
| Extras | projects, **AI chat (real replies via OpenRouter)** | XNAV (soon), obligations (mock) |
| Settings | all 21 themes from the dashboard, exact CSS colors | theme applies live (per-session) |

## Quick start (web preview)

Python 3.10+:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # flet==0.86.5
.venv/bin/python main.py                    # serves on :8550
```

**Raspberry Pi note:** the piwheels mirror ships a broken `flet-web` wheel (code only, no browser runtime). Fix by force-installing the real wheel:

```bash
.venv/bin/pip install --force-reinstall --no-deps --index-url https://pypi.org/simple flet-web==0.86.5
```

## Building

**Android (on the Pi / arm64):** Google ships no arm64-host `gen_snapshot` or `aapt2` for Linux, so a release-AOT build needs two workarounds this repo has already applied on the build machine:

- debug build via `flet build apk --flutter-build-args=--debug` (skips AOT), and
- `android.aapt2FromMavenOverride=<arm64 aapt2>` in `build/flutter/android/gradle.properties` (Debian's arm64 aapt).

The committed CI (`.github/workflows/build.yml`) builds a **release APK on GitHub's x86 runners** plus macOS `.app` and unsigned iOS `.ipa` on macos runners — use that for the optimized APK. Installing the iOS build additionally requires an Apple Developer account ($99/yr).

**Linux desktop:** `flet build linux` (needs clang/cmake/GTK dev packages on the build machine; CI covers it).

## Code map

- `main.py` — nav shell: topbar (drawer, brand, dots), bottom bar, page router
- `flet_app/api.py` — the client: per-endpoint adapters reshaping server JSON, 30s TTL cache, mock fallback, AI chat
- `flet_app/mock.py` — mock data, same shapes as the adapters (+ no-op stubs so a full mock-swap runs)
- `flet_app/theme_data.py` — 21 themes extracted 1:1 from the dashboard's CSS by `tools/gen-themes.py`
- `flet_app/ui.py` — theme state, card/chip/dot helpers, `apply_theme`
- `flet_app/pages/` — one module per page; tabbed pages share `lab._tabs`

Architecture: pages read only the `M` interface — `from .. import api as M`. Swap `api` for `mock` and the app renders fully offline (writes degrade to no-ops).

## Review

Full 8-axis code-ultrareview on this repo: verdict **Needs work** (8 red / 24 orange). The reds (drawer crash, stale-data pinning, progress-bar crashes, shape drift) were fixed in commits `d7b6ad9` and the second round; remaining oranges are tracked (threading, lazy tabs, honest-demo labels landed). Report: see review artifacts in the build history.
