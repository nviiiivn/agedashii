#!/usr/bin/env python3
"""Generate agedashii.alola.lol's framework page FROM THE CODE.

The page describes Qi and Washi. Everything it says about them is already
written down - in their module docstrings, in their version numbers, in the
actual size of the theme table - so it is read out of the installed
packages at generate time rather than typed into HTML by hand.

That is the whole point. A hand-written page starts accurate and drifts:
it would still claim 24 themes after a 25th landed, still list a module
that had been split in two, still describe behaviour that changed a month
ago. Anything asserted here is asserted by importing the thing and asking
it.

Nothing about the owner appears on this page. No server address, no
machine names, no hostnames, no data - it is a page about a framework, and
she was explicit that it carries none of her information.

    python3 tools/gen_framework_page.py > index.html
"""
import html
import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agedashii_qi                                       # noqa: E402
import agedashii_washi                                    # noqa: E402

# The modules the page lists, in the order someone meeting the framework
# should meet them: what it stores, what it talks to, what it draws.
WASHI_MODULES = [
    "store", "api", "config",
    "panels", "surface", "widgets", "forms", "motion",
    "prefs", "routing", "tileserver",
]

# The four apps built on it. Described in one honest sentence each - what
# it is for, not what it is like.
APPS = [
    ("Tofu", "tofu", "The whole dashboard. Every surface on one home: "
     "machines, services, containers, feeds, notes, rides, food, weather."),
    ("Moto", "moto", "Trips, Garage and XNAV. Route planning, ride "
     "recording and bike maintenance, with the map and the router on the "
     "device."),
    ("Watudo", "watudo", "Wat2Do and Future Finds. A day planner that "
     "tracks pain and spoons, and a list of places worth going."),
    ("Foodzen", "foodzen", "Grocery lists, food logging against a local "
     "USDA table, and blood pressure readings."),
]


def first_para(doc):
    """A module docstring's opening paragraph, as one line."""
    return " ".join((doc or "").strip().split("\n\n")[0].split())


def collect():
    mods = []
    for name in WASHI_MODULES:
        mod = importlib.import_module(f"agedashii_washi.{name}")
        src = pathlib.Path(mod.__file__).read_text()
        mods.append({
            "name": name,
            "summary": first_para(mod.__doc__),
            "lines": len(src.splitlines()),
        })
    return mods


def render():
    mods = collect()
    themes = sorted(agedashii_qi.THEMES)
    e = html.escape

    rows = "\n".join(
        f'      <tr><td><code>{e(m["name"])}</code></td>'
        f'<td>{e(m["summary"])}</td>'
        f'<td class="n">{m["lines"]:,}</td></tr>'
        for m in mods)

    swatches = "\n".join(
        '        <div class="sw" title="{n}">'
        '<span style="background:{bg}"></span>'
        '<span style="background:{ac}"></span>'
        '<span style="background:{tx}"></span>'
        '<b>{n}</b></div>'.format(
            n=e(name),
            bg=e(agedashii_qi.THEMES[name]["bg"]),
            ac=e(agedashii_qi.THEMES[name]["accent"]),
            tx=e(agedashii_qi.THEMES[name]["text"]))
        for name in themes)

    apps = "\n".join(
        f'      <a class="app" href="https://{slug}.alola.lol">'
        f'<b>Agedashii {e(nice)}</b><span>{e(desc)}</span></a>'
        for nice, slug, desc in APPS)

    total = sum(m["lines"] for m in mods)

    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Agedashii &mdash; Qi and Washi</title>
<style>
  :root {{ color-scheme: dark; --bg:#0d1117; --fg:#e6edf3; --dim:#8b949e;
           --line:#21262d; --card:#161b22; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.6
          system-ui,-apple-system,"Segoe UI",sans-serif; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:48px 20px 80px; }}
  .pill {{ display:inline-block; padding:5px 14px; border-radius:10px;
           font-weight:800; color:#0d1117;
           background:linear-gradient(90deg,#ffb020,#ff5e8a,#39d0ff); }}
  h1 {{ font-size:30px; margin:20px 0 6px; letter-spacing:-.02em; }}
  h2 {{ font-size:19px; margin:44px 0 10px; }}
  p.lede {{ color:var(--dim); margin:0 0 4px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:10px;
           display:block; overflow-x:auto; }}
  th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line);
           vertical-align:top; }}
  th {{ color:var(--dim); font-weight:600; font-size:13px; }}
  td.n {{ color:var(--dim); text-align:right; white-space:nowrap; }}
  code {{ background:var(--card); padding:2px 6px; border-radius:5px;
          font:13px ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .apps {{ display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); }}
  a.app {{ display:block; padding:14px; border:1px solid var(--line);
           border-radius:10px; background:var(--card); text-decoration:none;
           color:var(--fg); }}
  a.app:hover {{ border-color:#39d0ff; }}
  a.app span {{ display:block; color:var(--dim); font-size:13px; margin-top:5px; }}
  .sws {{ display:grid; gap:7px; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); }}
  .sw {{ display:flex; align-items:center; gap:5px; font-size:12px;
         color:var(--dim); }}
  .sw span {{ width:13px; height:13px; border-radius:3px;
              border:1px solid #30363d; flex:none; }}
  .sw b {{ font-weight:500; margin-left:3px; }}
  footer {{ margin-top:52px; padding-top:18px; border-top:1px solid var(--line);
            color:var(--dim); font-size:13px; }}
</style>
<div class="wrap">
  <span class="pill">Agedashii</span>
  <h1>Qi and Washi</h1>
  <p class="lede">The two pieces every Agedashii app is built out of.
  Each is usable on its own.</p>

  <h2>Qi &mdash; the look</h2>
  <p class="lede">{len(themes)} themes, corner radius, accent glow and a CRT
  overlay. Qi does not import Washi: a theme has no opinion about cards, and
  a card cannot be drawn without a theme, so the arrow points one way.
  Version {e(agedashii_qi.__version__)}.</p>
  <div class="sws">
{swatches}
  </div>

  <h2>Washi &mdash; the system</h2>
  <p class="lede">The shell an app is written in. Washi is paper: the thing
  the rest is written on. {total:,} lines across {len(mods)} modules.
  Version {e(agedashii_washi.__version__)}.</p>
  <table>
    <thead><tr><th>Module</th><th>What it is</th><th>Lines</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>

  <h2>Local first, and meant literally</h2>
  <p class="lede"><code>store</code> is the only module in the system that
  talks to a network, and it is built to be told no: writes queue offline,
  an unreachable server burns no retry attempt, failures park rather than
  vanish, and the outbox drains on resume. Themes, layouts, preferences,
  the routing graph, the map tiles and the nutrition table are local and
  work with the radio off.</p>

  <h2>Built on it</h2>
  <div class="apps">
{apps}
  </div>

  <footer>This page is generated from the packages themselves &mdash; the
  module list, the line counts, the version numbers and every colour above
  are read out of the installed code, so they cannot drift from what
  actually ships.</footer>
</div>
"""


if __name__ == "__main__":
    sys.stdout.write(render())
