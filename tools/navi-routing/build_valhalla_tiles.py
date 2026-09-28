#!/usr/bin/env python3
"""Build the Bay Area Valhalla routing tiles for the native Navi app.

The one rule: the tiles must be built by the SAME Valhalla the phone runs.
valhalla-mobile 0.6.3 pins its `src/valhalla` submodule at e2f017b1, which is
the valhalla 3.6.3 release commit, so this builds with pyvalhalla==3.6.3.
Valhalla compares only the MAJOR digit when it loads a tile, and only logs a
warning, so a 3.9 tile read by 3.6.3 loads silently and is misread. The
`check-binding` step re-derives the pin from GitHub; if the app bumps
valhalla-mobile, change BINDING, VALHALLA and VALHALLA_SHA together and rebuild.

usage: build_valhalla_tiles.py [--work DIR] [--source URL|PATH] [--timezones PATH]
                               [--speeds us-ca|global|none] [--jobs N] [STEP ...]
steps, in order (default: all of them):
  check-binding  the binding's submodule pin == the Valhalla tag == pyvalhalla
  fetch          download the Geofabrik extract, checked against its .md5
  extract        cut BBOX out of it with osmium
  config         write valhalla.json
  admins         valhalla_build_admins
  tiles          valhalla_build_tiles
  pack           valhalla_build_extract -> one tar the phone memory-maps
  manifest       sizes, counts, source date -> manifest.json beside the tar

Needs: python >= 3.12 (the wheels are cp312-abi3), osmium-tool, curl, network
for check-binding and fetch. Works on the Pi (aarch64) and on x86_64.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import resource
import subprocess
import sys
import time
import urllib.request

BINDING = "0.6.3"      # io.github.rallista:valhalla-mobile
VALHALLA = "3.6.3"     # what BINDING compiles; pyvalhalla is pinned to it
VALHALLA_SHA = "e2f017b16080f49203de245a211b09efab09cf72"

# The Python graph's coverage (bayarea.rg); west, south, east, north.
BBOX = (-122.65, 37.20, -121.75, 38.10)
SOURCE = "https://download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf"
NAME = f"bayarea-valhalla-{VALHALLA}"

# Typical speeds per road class and density (MIT, OpenStreetMapSpeeds/schema),
# pinned to the commit that is still master. Tables are looked up by the
# node's country/state from the admin db, but a regional extract never holds
# a complete California or United States boundary (the admin build logs
# "California ... is degenerate and will be skipped"), so every node has no
# country and Valhalla falls through to the file's one country-less entry: a
# world average (urban primary 27 km/h). That is what her server has been
# using. `us-ca` writes a one-entry file whose country-less entry IS the
# California row, so the Bay Area gets California speeds on purpose.
SPEEDS_URL = ("https://raw.githubusercontent.com/OpenStreetMapSpeeds/schema/"
              "c9c6872d5ec656f5d290944b44eb1a103ed539fa/default_speeds.json")
SPEEDS_MD5 = "207f3e30773b32282b73a76203b04c72"
HERE = pathlib.Path(__file__).resolve().parent
STEPS = ("check-binding", "fetch", "extract", "config", "admins", "tiles",
         "pack", "manifest")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(*argv, **kw):
    log("$ " + " ".join(map(str, argv)))
    subprocess.run([str(a) for a in argv], check=True, **kw)


def github(path):
    req = urllib.request.Request(f"https://api.github.com/{path}",
                                 headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_url(s):
    return s.startswith(("http://", "https://"))


class Build:
    def __init__(self, args):
        self.work = pathlib.Path(args.work).resolve()
        self.source = args.source
        self.timezones = args.timezones
        self.speeds = args.speeds
        self.jobs = args.jobs
        self.venv = self.work / "venv"
        self.py = self.venv / "bin" / "python"
        self.src = (self.work / "src" / pathlib.Path(self.source).name if is_url(self.source)
                    else pathlib.Path(self.source).resolve())
        self.pbf = self.work / "bayarea.osm.pbf"
        self.config = self.work / "valhalla.json"
        self.tile_dir = self.work / "valhalla_tiles"
        self.admins = self.work / "admins.sqlite"
        self.out = self.work / "out"
        self.tar = self.out / f"{NAME}.tar"

    def ensure_venv(self):
        if not self.py.exists():
            run(sys.executable, "-m", "venv", self.venv)
        have = subprocess.run([self.py, "-c", "import valhalla; print(valhalla.__version__)"],
                              capture_output=True, text=True).stdout.strip()
        if have != VALHALLA:
            run(self.py, "-m", "pip", "install", "-q", f"pyvalhalla=={VALHALLA}")

    def valhalla(self, *argv):
        # pyvalhalla's launcher shutil.which()es the tool name before running
        # it, so it fails unless the venv's bin is on PATH (i.e. activated).
        env = dict(os.environ, PATH=f"{self.venv / 'bin'}{os.pathsep}{os.environ['PATH']}")
        run(self.py, "-m", "valhalla", *argv, env=env)

    # -- steps --------------------------------------------------------------

    def check_binding(self):
        """The binding's own submodule pin, not its README, decides VALHALLA."""
        root = github(f"repos/Rallista/valhalla-mobile/git/trees/{BINDING}")
        src = next(t for t in root["tree"] if t["path"] == "src")
        sub = github(f"repos/Rallista/valhalla-mobile/git/trees/{src['sha']}")
        pin = next(t for t in sub["tree"] if t["path"] == "valhalla")
        ref = github(f"repos/valhalla/valhalla/git/ref/tags/{VALHALLA}")["object"]
        if ref["type"] == "tag":
            ref = github(f"repos/valhalla/valhalla/git/tags/{ref['sha']}")["object"]
        log(f"valhalla-mobile {BINDING} pins valhalla {pin['sha'][:12]}; "
            f"tag {VALHALLA} is {ref['sha'][:12]}")
        if pin["type"] != "commit" or pin["sha"] != ref["sha"] or ref["sha"] != VALHALLA_SHA:
            raise SystemExit(f"MISMATCH: valhalla-mobile {BINDING} does not build valhalla "
                             f"{VALHALLA}. Fix BINDING/VALHALLA/VALHALLA_SHA before building.")
        self.ensure_venv()
        log(f"pyvalhalla in {self.venv} is {VALHALLA}: ok")

    def fetch(self):
        if not is_url(self.source):
            log(f"using local source {self.src}")
            return
        self.src.parent.mkdir(parents=True, exist_ok=True)
        want = urllib.request.urlopen(self.source + ".md5", timeout=30).read().split()[0].decode()
        if self.src.exists() and md5(self.src) == want:
            log(f"{self.src.name} already here, md5 {want}")
            return
        run("curl", "-fL", "--retry", "3", "-o", self.src, self.source)
        got = md5(self.src)
        if got != want:
            raise SystemExit(f"md5 mismatch for {self.src}: {got} != {want}")
        log(f"md5 ok {got}")

    def extract(self):
        # smart + boundary keeps whole admin relations whose members are in the
        # source, so valhalla_build_admins can close their rings.
        run("osmium", "extract", "--overwrite", "-b", ",".join(map(str, BBOX)),
            "-s", "smart", "-S", "types=multipolygon,boundary",
            "-o", self.pbf, self.src)

    def write_config(self):
        self.ensure_venv()
        # get_config resolve(strict=True)s any path it is given, and neither
        # exists yet, so take its defaults blank and fill the paths in here.
        cfg = json.loads(subprocess.run(
            [self.py, "-c", "import json, valhalla; print(json.dumps(valhalla.get_config("
             "tile_extract='', tile_dir='', verbose=True)))"],
            capture_output=True, text=True, check=True).stdout)
        m = cfg["mjolnir"]
        m["tile_dir"] = str(self.tile_dir)
        m["tile_extract"] = str(self.tar)
        m["admin"] = str(self.admins)
        m["timezone"] = str(pathlib.Path(self.timezones).resolve()) if self.timezones else ""
        m["concurrency"] = self.jobs
        # A motorcycle never uses cycle-only or foot-only ways; leaving them
        # out is the largest size saving that changes no motorcycle route.
        m["include_bicycle"] = False
        m["include_pedestrian"] = False
        if self.speeds != "none":
            m["default_speeds_config"] = str(self.speeds_file())
        self.config.write_text(json.dumps(cfg, indent=2) + "\n")
        log(f"wrote {self.config} (timezones: {m['timezone'] or 'NONE'}, "
            f"speeds: {self.speeds})")

    def speeds_file(self):
        raw = urllib.request.urlopen(SPEEDS_URL, timeout=30).read()
        if hashlib.md5(raw).hexdigest() != SPEEDS_MD5:
            raise SystemExit(f"default_speeds.json changed under a pinned commit: {SPEEDS_URL}")
        rows = json.loads(raw)
        if self.speeds == "global":
            row = next(r for r in rows if "iso3166-1" not in r)
        else:
            row = next(r for r in rows if r.get("iso3166-1") == "US"
                       and r.get("iso3166-2") == "CA")
            row = {k: v for k, v in row.items() if not k.startswith("iso3166")}
        path = self.work / f"default_speeds.{self.speeds}.json"
        path.write_text(json.dumps([row], indent=1) + "\n")
        return path

    def build_admins(self):
        self.valhalla("valhalla_build_admins", "-c", self.config, self.pbf)

    def build_tiles(self):
        run("rm", "-rf", self.tile_dir)
        self.valhalla("valhalla_build_tiles", "-c", self.config, self.pbf)

    def pack(self):
        script = self.work / f"valhalla_build_extract-{VALHALLA}"
        if not script.exists():
            url = ("https://raw.githubusercontent.com/valhalla/valhalla/"
                   f"{VALHALLA_SHA}/scripts/valhalla_build_extract")
            script.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        self.out.mkdir(parents=True, exist_ok=True)
        run(self.py, script, "-c", self.config, "-O", "-v")

    def manifest(self):
        levels = {}
        for t in self.tile_dir.rglob("*.gph"):
            lvl = t.relative_to(self.tile_dir).parts[0]
            n, b = levels.get(lvl, (0, 0))
            levels[lvl] = (n + 1, b + t.stat().st_size)
            version = t.read_bytes()[16:32].split(b"\0")[0].decode()
            if not version.startswith(VALHALLA):
                raise SystemExit(f"{t} was built by {version}, not {VALHALLA}")
        osm_ts = subprocess.run(
            ["osmium", "fileinfo", "-g", "header.option.osmosis_replication_timestamp",
             self.src], capture_output=True, text=True).stdout.strip()
        mj = json.loads(self.config.read_text())["mjolnir"]
        man = {
            "valhalla_version": VALHALLA,
            "valhalla_sha": VALHALLA_SHA,
            "binding": f"io.github.rallista:valhalla-mobile:{BINDING}",
            "source": {"url": self.source, "md5": md5(self.src), "osm_timestamp": osm_ts},
            "bbox": BBOX,
            "tar": {"name": self.tar.name, "bytes": self.tar.stat().st_size},
            "levels": {k: {"tiles": n, "bytes": b} for k, (n, b) in sorted(levels.items())},
            "config": {k: mj[k] for k in ("include_bicycle", "include_pedestrian",
                                          "include_driveways", "hierarchy", "shortcuts")},
            "timezones": bool(mj["timezone"]),
            "default_speeds": self.speeds,
            "built_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        path = self.out / f"{NAME}.manifest.json"
        path.write_text(json.dumps(man, indent=2) + "\n")
        log(f"{self.tar} = {man['tar']['bytes']:,} bytes; manifest {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("steps", nargs="*", metavar="STEP")
    ap.add_argument("--work", default=os.environ.get("NAVI_ROUTING_WORK", HERE / "work"))
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--timezones", help="spatialite tz db from valhalla_build_timezones")
    ap.add_argument("--speeds", choices=("us-ca", "global", "none"), default="us-ca",
                    help="typical-speed table for untagged roads (see SPEEDS_URL)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()
    bad = [s for s in args.steps if s not in STEPS]
    if bad:
        ap.error(f"unknown step(s) {bad}; choose from {STEPS}")
    b = Build(args)
    b.work.mkdir(parents=True, exist_ok=True)
    table = {"check-binding": b.check_binding, "fetch": b.fetch, "extract": b.extract,
             "config": b.write_config, "admins": b.build_admins, "tiles": b.build_tiles,
             "pack": b.pack, "manifest": b.manifest}
    for step in args.steps or STEPS:
        t0 = time.monotonic()
        log(f"=== {step}")
        table[step]()
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        log(f"=== {step} done in {time.monotonic() - t0:.0f}s "
            f"(largest child so far: {peak:,.0f} MiB RSS)")


if __name__ == "__main__":
    main()
