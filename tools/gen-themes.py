import re, os

SRC = os.path.expanduser("~/www/dashbored/dashbored-v3.py")
OUT = os.path.expanduser("~/Sandbox/tmp/dashbored-flet/flet_app/theme_data.py")

src = open(SRC).read()

# THEME_NAMES dict (key -> display name)
m = re.search(r"THEME_NAMES\s*=\s*\{(.*?)\}", src, re.S)
if not m:
    raise SystemExit("THEME_NAMES not found")
names = dict(re.findall(r"['\"]?([\w-]+)['\"]?\s*:\s*'([^']*?)'", m.group(1)))

# CSS theme blocks: body.theme-X { --var: value; ... }
themes = {}
for bm in re.finditer(r"(?:body,\s*)?body\.theme-([\w-]+)\s*\{([^}]*)\}", src):
    key, body = bm.group(1), bm.group(2)
    if key not in names:
        continue
    vars_ = dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body))
    # keep only color vars, skip -rgb duplicates (we can derive when needed)
    pal = {k: v.strip() for k, v in vars_.items() if not k.endswith("-rgb")}
    themes[key] = pal

missing = [k for k in names if k not in themes]
if missing:
    raise SystemExit(f"missing CSS blocks for: {missing}")

REQ = ["bg", "bg2", "surface", "border", "text", "text2", "text3", "accent",
       "green", "yellow", "red", "orange", "purple", "pink", "cyan"]
for k, pal in themes.items():
    miss = [v for v in REQ if v not in pal]
    if miss:
        raise SystemExit(f"theme {k} missing vars: {miss}")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
lines = ["# AUTO-GENERATED from dashbored-v3.py CSS themes — do not edit by hand",
         f"# {len(themes)} themes", "THEMES = {"]
for k in names:  # preserve THEME_NAMES order
    pal = themes[k]
    lines.append(f"    {k!r}: {{")
    for v in REQ:
        lines.append(f"        {v!r}: {pal[v]!r},")
    lines.append("    },")
lines += ["}", "", "THEME_NAMES = {"]
for k, n in names.items():
    lines.append(f"    {k!r}: {n!r},")
lines += ["}", "", "DEFAULT = 'dark'", ""]
open(OUT, "w").write("\n".join(lines))
print(f"OK: {len(themes)} themes -> {OUT}")
