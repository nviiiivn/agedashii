"""Agedashii Washi - the system, without any of the owner's data.

The shell every Agedashii app is built in. Washi is paper: the thing the
rest is written on.

What is in here
---------------
``store``        offline-first SQLite cache + outbox. Writes queue when
                 there is nobody to talk to and drain on resume; an
                 unreachable server burns no retry and failures park
                 rather than being dropped.
``api``          the dashbored server client, on top of that store.
``panels``       the layout engine - reorder, hide and resize cards per
                 surface, in a 60-column grid.
``prefs``        preferences, held locally in SharedPreferences.
``surface``      tabs, dialogs, error cards - what a page is assembled from.
``widgets``      card, chip, title, sub, dot. The vocabulary, not a library.
``forms``        writing back to the server without losing the chrome.
``config``       which server, and whether it is answering.
``routing``      offline A* over a packed OSM graph.
``tileserver``   loopback HTTP so the map reads local tiles.

Local first, and meant literally
--------------------------------
Only server data ever syncs. Themes, layouts, preferences, the routing
graph, the map tiles and the nutrition table are local and stay local, so
every one of them works with the radio off. ``store`` is the single place
that talks to a network, and it is built to be told no.
"""

__version__ = "1.0.0"
