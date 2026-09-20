"""Serve map tiles to the app from the phone itself, with no internet.

The problem this solves: a map widget knows one way to get a tile - fetch a
URL. It cannot read a file, and it certainly cannot read an MBTiles archive.
So on a motorcycle in a dead zone the map is blank, which is exactly when it
matters most.

The trick is that a Flet Android app ships a real CPython runtime, so the app
can run an HTTP server inside itself, bound to loopback. The map asks
127.0.0.1 for a tile, this hands one over out of a bundled MBTiles file, and
nothing ever leaves the device. The map cannot tell the difference between
that and the internet.

Bound to 127.0.0.1 deliberately: the tiles are for this process, not for
whatever network the phone happens to be on.

MBTiles is a SQLite file with a `tiles(zoom_level, tile_column, tile_row,
tile_data)` table. Its rows are numbered TMS-style, from the bottom, while
slippy-map URLs (/z/x/y.png, what every map widget speaks) number from the
top. Getting that flip wrong yields a map that looks plausible but is
mirrored north-south, so it is done in exactly one place, below.
"""
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# A 1x1 transparent PNG, returned for tiles the archive does not contain.
# Returning 404 for a legitimately empty area (ocean, off the edge of the
# extract) makes map widgets log errors and sometimes draw error tiles; an
# empty tile is the honest representation of "nothing here".
_EMPTY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c63000100000500010d0a2db40000"
    "000049454e44ae426082")

_state = {"server": None, "thread": None, "path": None, "port": None}
_lock = threading.Lock()


class MBTiles:
    """Read-only MBTiles reader, safe to use from several request threads.

    SQLite connections cannot be shared across threads, so each thread gets
    its own via thread-local storage rather than serialising every tile
    fetch behind one lock - panning a map asks for a dozen tiles at once.
    """

    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()
        # Fail loudly here rather than on the first tile request.
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as c:
            c.execute("SELECT 1 FROM tiles LIMIT 1")

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True,
                                   check_same_thread=False)
            self._local.conn = conn
        return conn

    def tile(self, z, x, y):
        """Return PNG bytes for a slippy-map (XYZ) tile, or None."""
        # XYZ counts rows from the top, MBTiles/TMS from the bottom.
        flipped = (1 << z) - 1 - y
        row = self._conn().execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? "
            "AND tile_row=?", (z, x, flipped)).fetchone()
        return row[0] if row else None

    def metadata(self):
        try:
            return dict(self._conn().execute(
                "SELECT name, value FROM metadata").fetchall())
        except sqlite3.Error:
            return {}


def _handler_for(archive):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Headers and body would otherwise go out as two small packets, and
        # Nagle + delayed-ACK adds ~40 ms to every tile on loopback. Measured
        # 45 ms -> 0.3 ms per tile. This is the difference between a map that
        # pans smoothly and one that stutters.
        disable_nagle_algorithm = True
        wbufsize = 64 * 1024

        def log_message(self, *args):
            pass          # a tile server logging every pan is just noise

        def do_GET(self):
            parts = self.path.lstrip("/").split("?")[0].split("/")
            if len(parts) == 1 and parts[0] in ("", "status"):
                return self._send(b'{"ok":true}', "application/json")
            if len(parts) != 3:
                return self.send_error(404)
            try:
                z, x = int(parts[0]), int(parts[1])
                y = int(parts[2].split(".")[0])
            except ValueError:
                return self.send_error(404)
            # Reject nonsense coordinates rather than letting them reach SQL.
            if not (0 <= z <= 22) or not (0 <= x < (1 << z)) or not (0 <= y < (1 << z)):
                return self.send_error(404)
            data = archive.tile(z, x, y)
            self._send(data if data is not None else _EMPTY_PNG, "image/png")

        def _send(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # Tiles for a given z/x/y never change within a build, and the
            # widget re-requests constantly while panning.
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def start(mbtiles_path, port=0):
    """Start serving `mbtiles_path`. Returns a url_template, or None.

    port=0 lets the OS pick a free one, which avoids clashing with whatever
    else the phone is running. Returns the template the map wants, e.g.
    "http://127.0.0.1:41234/{z}/{x}/{y}.png".
    """
    # No archive installed is the normal case before maps are downloaded,
    # not an error: the caller falls back to online tiles.
    if not mbtiles_path:
        return None
    path = Path(mbtiles_path)
    with _lock:
        if _state["server"] is not None and _state["path"] == str(path):
            return url_template()
        stop()
        if not path.is_file():
            return None
        try:
            archive = MBTiles(path)
        except sqlite3.Error:
            return None      # not a usable MBTiles file; caller falls back
        # request_queue_size must be a CLASS attribute: __init__ calls
        # server_activate() -> listen(request_queue_size), so assigning it on
        # the instance afterwards is too late and changes nothing. A map
        # widget opens a screenful of connections at once and the stdlib
        # default backlog of 5 sends the rest into the ~1 s TCP SYN
        # retransmit timer - 36 parallel tiles took 1.3 s instead of 35 ms.
        class _Server(ThreadingHTTPServer):
            daemon_threads = True
            request_queue_size = 128
            allow_reuse_address = True

        server = _Server(("127.0.0.1", port), _handler_for(archive))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _state.update({"server": server, "thread": thread,
                       "path": str(path), "port": server.server_address[1]})
        return url_template()


def url_template():
    return (f"http://127.0.0.1:{_state['port']}/{{z}}/{{x}}/{{y}}.png"
            if _state["port"] else None)


def stop():
    server = _state["server"]
    if server is not None:
        server.shutdown()
        server.server_close()
    _state.update({"server": None, "thread": None, "path": None, "port": None})


def status():
    return {"running": _state["server"] is not None,
            "port": _state["port"], "mbtiles": _state["path"]}
