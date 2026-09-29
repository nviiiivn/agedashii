"""What build this is.

Two numbers, because Android wants two and only reads one of them:

    VERSION_CODE   an integer. This is what Android compares to decide whether
                   an APK is an update or a downgrade. It must only ever go up.
    VERSION_NAME   a string, for people. Android never reads it.

Her question: "how do we format the versioning? v1.13c or v.1.13.05 orrrr what"

`YYMMDDnn` for the code - two digits of year, month, day, then a counter for
builds within that day. 26092601 is the first build on 2026-09-26. It always
increases, it can never collide, and it says when a build was made without
having to look anything up. Eight digits leaves enormous room under Android's
2,100,000,000 ceiling, where the tempting `YYYYMMDDnn` form would be within a
few years of hitting it.

`major.minor.patch` for the name, bumped by hand: last digit for a fix, middle
for a feature, first when something changes that she would have to relearn.

BOTH ARE READ BY THE UPDATE CHECK

`updates.py` compares `VERSION_CODE` against the one in the published
`version.json`. Forgetting to raise it means the phone believes it is already
current and never offers the new build - so it is raised in the same commit as
whatever it describes, and the publish step reads it straight from here rather
than being told separately.
"""

VERSION_NAME = "1.21.0"
VERSION_CODE = 26092902


def label():
    """What this build is called in the UI and in the update prompt."""
    return f"v{VERSION_NAME} ({VERSION_CODE})"
