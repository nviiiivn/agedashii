# Mock data for the NVII Flet app (pre-API-wiring).

MACHINES = [
    {"id": "blavksaba", "display": "Tower", "color": "#58a6ff", "online": True,
     "os": "Windows 11", "uptime": "3d 14h", "cpu": 34, "ram": 62, "temp": 45,
     "docker": 7, "host": "192.0.2.14"},
    {"id": "ai-tp", "display": "Pi 5", "color": "#3fb950", "online": True,
     "os": "Pi OS 64", "uptime": "12d 2h", "cpu": 22, "ram": 48, "temp": 51,
     "docker": 5, "host": "192.0.2.21"},
    {"id": "void", "display": "Yoga", "color": "#d29922", "online": False,
     "os": "Ubuntu", "uptime": "—", "cpu": 0, "ram": 0, "temp": 0,
     "docker": 0, "host": "192.0.2.35"},
    {"id": "lil-faegola", "display": "Media Pi", "color": "#bc8cff", "online": False,
     "os": "LibreELEC", "uptime": "—", "cpu": 0, "ram": 0, "temp": 0,
     "docker": 0, "host": "192.0.2.23"},
    {"id": "sp3", "display": "SP3", "color": "#f85149", "online": False,
     "os": "Win 11", "uptime": "—", "cpu": 0, "ram": 0, "temp": 0,
     "docker": 0, "host": "192.0.2.42"},
]

WEATHER = {
    "now": {"t": 66, "desc": "Partly cloudy", "icon": "partly_cloudy_day",
            "h": 72, "l": 58, "wind": 8, "rain": 0, "city": "Oakland"},
    "days": [
        {"d": "Fri", "t": 66, "desc": "Partly cloudy"},
        {"d": "Sat", "t": 71, "desc": "Sunny"},
        {"d": "Sun", "t": 64, "desc": "Fog AM"},
        {"d": "Mon", "t": 61, "desc": "Rain"},
    ],
}

# Deliberately not a song. api.nowplaying() never returns None, so this is
# only reachable if someone unwires it again - and a dashboard that invents a
# track you are not playing is worse than one that admits it does not know.
NOWPLAYING = {"title": "Nothing playing", "artist": "—", "playing": False,
              "art_color": "#ff5e8a"}

WAT2DO = {
    "date": "Fri, Sep 4",
    "pain": 4, "spoons": 3,
    "items": [
        {"t": "Shave / shower", "sub": False, "done": False},
        {"t": "Cleaning home", "sub": False, "done": True},
        {"t": "vacuum", "sub": True, "done": True},
        {"t": "dishes", "sub": True, "done": False},
        {"t": "Laundry", "sub": False, "done": False},
        {"t": "Groceries", "sub": False, "done": False},
    ],
    "wx_call": "meh — windy later",
}

BIKES = [
    {"id": "cb250-nighthawk-1992", "name": "CB250 Nighthawk", "year": 1992,
     "make": "Honda", "mi_since_svc": 412, "total_mi": 8240, "color": "#d29922",
     "status": "running"},
    {"id": "cmx250-rebel-2001", "name": "Rebel 250", "year": 2001,
     "make": "Honda", "mi_since_svc": 1180, "total_mi": 14310, "color": "#f85149",
     "status": "project"},
]

FLIPPER = [
    {"title": "1996 Honda Nighthawk 750", "price": 950, "score": 82,
     "net": 1180, "flag": "clean"},
    {"title": "2003 Kawasaki Vulcan 800", "price": 1200, "score": 67,
     "net": 640, "flag": "needs carb clean"},
    {"title": "1988 Yamaha Virago 535", "price": 700, "score": 74,
     "net": 905, "flag": "no start"},
]

FEEDS = [
    {"src": "riders-share", "tag": "route", "title": "Grizzly Peak twisties — full loop",
     "meta": "Mar 15 · 34 mi · Oakland"},
    {"src": "cyclefish", "tag": "event", "title": "Bay Area Bike Night @ Oakland",
     "meta": "Mar 21 · 7pm · free"},
    {"src": "sf-mc", "tag": "event", "title": "Sunday KSU — SF clubhouse",
     "meta": "Mar 22 · 9am · 2194 Folsom"},
    {"src": "lightning", "tag": "event", "title": "Santa Cruz toy run",
     "meta": "Apr 05 · Santa Cruz, CA"},
]

JOURNAL = [
    {"cat": "Mental-Health", "title": "Fixup retro", "date": "2026-09-03"},
    {"cat": "Trip-Reports", "title": "Alameda loop", "date": "2026-08-28"},
]

FOODZEN = {
    "list": ["Milk 1 gal", "Bananas 2 ct", "Bread", "Eggs", "Cold brew"],
    "need": ["Dish soap", "Rice vinegar"],
    "pantry": ["Pasta", "Crushed tomatoes", "Garlic", "Rice"],
    "fixation": "fettuccine alfredo (since Aug 22)",
}

TRIPS = [
    {"id": "alameda-loop", "name": "Alameda loop", "mi": 22, "date": "2026-08-28"},
    {"id": "grizzly-full", "name": "Grizzly Peak full", "mi": 34, "date": "2026-08-15"},
]

RIDES = [
    {"name": "grizzly-peak.gpx", "pts": 1204, "mi": 34.5, "top": 52},
    {"name": "alameda-loop.gpx", "pts": 488, "mi": 21.9, "top": 38},
]

PROJECTS = [
    {"name": "dashbored", "lang": "Python", "desc": "this very dashboard"},
    {"name": "nvii-boilerplate", "lang": "JS", "desc": "first-run configurator"},
]

# The chat starts empty. It used to open with a fabricated exchange that
# looked like something you had actually asked.
AI_CHAT = []

# No server store for these yet. Empty on purpose: the pages render an
# honest "nothing tracked yet" instead of invented insurance renewals and
# DMV appointments, which read as real deadlines.
OBLIGATIONS = []

# stubs so a mock-only swap never AttributeErrors (review finding)
def ai_model():
    return "openrouter/auto"

def ai_chat(provider, model, messages):
    return None

def wat2do_save(state):
    return None
