# NVII-DASH

The mobile companion app for [dashbored](../dashbored) — a Flet (Flutter) client for nvii's personal homelab dashboard, built to run on Android, Linux desktop, and the web from one Python codebase.

## What it is

A native-feeling app that talks to a running **dashbored** server on your LAN and mirrors its pages:

| Tab | What you get |
|---|---|
| **Home** | Greeting, live weather (Open-Meteo via server), now-playing, machine status dots, quick actions, Wat2Do mini-checklist |
| **Lab** | Machines (CPU / RAM / disk / temps / uptime / docker counts, live from SSH probes), Things, Services (up/down dots), Qbit |
| **Tools** | Moto feeds (4 sources + RSS), Vault, Journal, FoodZen (grocery list, needs, frequency-radar fixations) |
| **Trips** | Saved rides + GPX ride stats |
| **Weather** | Current conditions + 4-day forecast |
| **Garage** | Bikes with mileage-since-service, Flipper deal scanner |
| **Wat2Do** | Full daily checklist with sub-tasks, pain/spoons vitals, live save to the server |
| **Extras** | Projects (Gitea), AI chat (real replies via the server's OpenRouter/Zen/Ollama routes), XNAV, Obligations |
| **Settings** | All 21 dashboard themes, extracted 1:1 from the web app's CSS, switchable live |

Everything interactive: check items off, chat with the AI, flip themes — state persists through the server.

## Quick start (web preview)

```bash
python3 -m venv ~/Sandbox/tmp/flet-venv
~/Sandbox/tmp/flet-venv/bin/pip install flet        # see note below on Raspberry Pi
~/Sandbox/tmp/flet-venv/bin/python main.py          # serves http://<host>:8550
```

> **Raspberry Pi note:** the piwheels mirror ships a broken `flet-web` wheel (code only, no browser runtime). If the web preview 500s, install the real wheel from PyPI:
> `pip install --force-reinstall --no-deps --index-url https://pypi.org/simple flet_web==0.86.5`

## Configuration

The app expects a dashbored server at `http://192.0.2.21:6900` (edit `BASE` in `flet_app/api.py`). If the server is unreachable, every page falls back to bundled mock data so the UI never blanks — machines/weather/checklists keep rendering, clearly fake.

## Building

- **Android APK** — `flet build apk` (needs Flutter + Android SDK; on Raspberry Pi use an arm64 Flutter source checkout + Debian's arm64 `aapt2` via `android.aapt2FromMavenOverride`)
- **Linux** — `flet build linux`
- **iOS / macOS** — requires macOS hardware (or a macOS CI runner) for Xcode; iOS install additionally needs an Apple Developer account

## Code map

```
main.py                  nav shell: topbar, drawer, bottom bar, page router
flet_app/
  api.py                 server client: adapters, TTL cache, mock fallback, AI chat
  mock.py                offline fallback data (same shapes as api adapters)
  theme_data.py          21 themes, generated from the dashboard's CSS (gen-themes.py)
  ui.py                  palette accessor, card/chip/dot helpers, apply_theme
  pages/                 one module per page; tabbed pages share lab._tabs()
```

Architecture in one line: **pages read only the `M` interface** (`from .. import api as M`) — swap `api` for `mock` and the entire app runs offline unchanged.
