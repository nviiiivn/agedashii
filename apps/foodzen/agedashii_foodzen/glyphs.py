"""Two kinds of little picture, and the rule about which goes where.

She asked for "emoticons/small icons glyphs especially for foodzen", and then
settled the ambiguity herself: **emoji for the food, themed Material icons for
the UI**. Those are different jobs and they do not mix:

* An **emoji** stands for a *thing she ate or has to buy*. It is content. It
  is the same picture on every theme, because a banana is not accent-coloured.
  ``food_emoji`` is the only way one gets on screen.
* An **icon** is chrome - a tab heading, a button, a meter, a status mark. It
  is drawn in a palette colour through ``mark()``/``head()`` so it follows all
  24 themes. An emoji cannot do that: it carries its own colours and would sit
  on a matrix-green screen looking like it came from another app.

The food table is deliberately *incomplete*. An unmapped food falls back to a
neutral plate, or to nothing at all where even that would be noise - never to
an approximate glyph. A wrong emoji on a sodium row is worse than no emoji: it
reads as a claim about what the row is, and this app's whole discipline is not
claiming things it does not know. That is why there is no "pepper" (the
vegetable or the spice?), no yoghurt (no glyph exists for it) and no
grapefruit (an orange is not one).

Matching is by whole word, never by substring: "grapefruit" does not contain
"grape" as far as this module is concerned, and the longest phrase wins, so
"peanut butter" is a nut and "sweet potato" is not a potato.
"""
import re

import flet as ft

from agedashii_qi import PAL
from agedashii_washi.widgets import title

# ---------------------------------------------------------------- the food table
# Keys are lower case and are normalised the same way a looked-up name is (see
# `_norm`), so "Eggs" and "egg" are the same key. Multi-word keys are phrases
# and beat single words.
FOODS = {
    # fruit
    "apple": "🍎", "green apple": "🍏", "pear": "🍐", "orange": "🍊",
    "mandarin": "🍊", "tangerine": "🍊", "clementine": "🍊", "satsuma": "🍊",
    "lemon": "🍋", "lime": "🍋", "banana": "🍌", "plantain": "🍌",
    "watermelon": "🍉", "grape": "🍇", "raisin": "🍇", "strawberry": "🍓",
    "blueberry": "🫐", "melon": "🍈", "cantaloupe": "🍈", "honeydew": "🍈",
    "cherry": "🍒", "peach": "🍑", "nectarine": "🍑", "mango": "🥭",
    "pineapple": "🍍", "coconut": "🥥", "kiwi": "🥝", "avocado": "🥑",
    "guacamole": "🥑", "olive": "🫒", "date": "🌴",
    # vegetables
    "tomato": "🍅", "cherry tomato": "🍅", "potato": "🥔",
    "mashed potato": "🥔", "sweet potato": "🍠", "yam": "🍠", "carrot": "🥕",
    "corn": "🌽", "sweetcorn": "🌽", "broccoli": "🥦", "lettuce": "🥬",
    "romaine": "🥬", "cabbage": "🥬", "kale": "🥬", "spinach": "🥬",
    "bok choy": "🥬", "chard": "🥬", "cucumber": "🥒", "pickle": "🥒",
    "bell pepper": "🫑", "chilli": "🌶️", "chili": "🌶️", "jalapeno": "🌶️",
    "onion": "🧅", "shallot": "🧅", "garlic": "🧄", "mushroom": "🍄",
    "portobello": "🍄", "eggplant": "🍆", "aubergine": "🍆", "pea": "🫛",
    "green bean": "🫛", "bean": "🫘", "black bean": "🫘", "kidney bean": "🫘",
    "lentil": "🫘", "chickpea": "🫘", "ginger": "🫚", "beet": "🥬",
    # eggs, meat, fish
    "egg": "🥚", "hardboiled egg": "🥚", "boiled egg": "🥚",
    "fried egg": "🍳", "scrambled egg": "🍳", "omelette": "🍳",
    "omelet": "🍳", "chicken": "🍗", "chicken breast": "🍗",
    "chicken thigh": "🍗", "turkey": "🍗", "drumstick": "🍗", "wing": "🍗",
    "steak": "🥩", "beef": "🥩", "mince": "🥩", "ground beef": "🥩",
    "lamb": "🍖", "pork": "🥩", "pork chop": "🥩", "rib": "🍖", "ham": "🍖",
    "bacon": "🥓", "fish": "🐟", "salmon": "🐟", "tuna": "🐟", "cod": "🐟",
    "tilapia": "🐟", "sardine": "🐟", "shrimp": "🍤", "prawn": "🍤",
    "crab": "🦀", "lobster": "🦞", "squid": "🦑", "calamari": "🦑",
    "oyster": "🦪", "clam": "🦪", "mussel": "🦪", "scallop": "🦪",
    # dairy and the nut aisle
    "milk": "🥛", "whole milk": "🥛", "skim milk": "🥛", "oat milk": "🥛",
    "soy milk": "🥛", "almond milk": "🥛", "chocolate milk": "🥛",
    "cheese": "🧀", "cheddar": "🧀", "parmesan": "🧀", "mozzarella": "🧀",
    "feta": "🧀", "butter": "🧈", "margarine": "🧈", "cream cheese": "🧀",
    "peanut": "🥜", "peanut butter": "🥜", "almond": "🥜", "cashew": "🥜",
    "pistachio": "🥜", "nut": "🥜", "chestnut": "🌰",
    # bread, grain, breakfast
    "bread": "🍞", "toast": "🍞", "sourdough": "🍞", "roll": "🍞",
    "bagel": "🥯", "croissant": "🥐", "baguette": "🥖", "pretzel": "🥨",
    "pancake": "🥞", "waffle": "🧇", "rice": "🍚", "brown rice": "🍚",
    "fried rice": "🍚", "pasta": "🍝", "spaghetti": "🍝", "macaroni": "🍝",
    "noodle": "🍜", "ramen": "🍜", "pho": "🍜", "oat": "🥣",
    "oatmeal": "🥣", "porridge": "🥣", "cereal": "🥣", "granola": "🥣",
    "muesli": "🥣", "tortilla": "🫓", "pita": "🫓", "flatbread": "🫓",
    # made dishes
    "pizza": "🍕", "burger": "🍔", "hamburger": "🍔", "cheeseburger": "🍔",
    "hot dog": "🌭", "sandwich": "🥪", "blt": "🥪", "taco": "🌮",
    "burrito": "🌯", "wrap": "🌯", "quesadilla": "🌯", "salad": "🥗",
    "chicken salad": "🥗", "coleslaw": "🥗", "soup": "🍲", "stew": "🍲",
    "chilli con carne": "🍲", "curry": "🍛", "sushi": "🍣", "sashimi": "🍣",
    "dumpling": "🥟", "gyoza": "🥟", "potsticker": "🥟", "fries": "🍟",
    "french fries": "🍟", "popcorn": "🍿", "bento": "🍱",
    # sweet
    "ice cream": "🍦", "gelato": "🍦", "cake": "🍰", "cheesecake": "🍰",
    "birthday cake": "🎂", "cookie": "🍪", "biscuit": "🍪",
    "chocolate": "🍫", "milk chocolate": "🍫", "candy": "🍬", "sweet": "🍬",
    "lollipop": "🍭", "donut": "🍩", "doughnut": "🍩", "pie": "🥧",
    "custard": "🍮", "pudding": "🍮", "honey": "🍯", "syrup": "🍯",
    # drinks
    "coffee": "☕", "espresso": "☕", "latte": "☕", "cappuccino": "☕",
    "americano": "☕", "tea": "🍵", "green tea": "🍵", "matcha": "🍵",
    "water": "💧", "sparkling water": "💧", "juice": "🧃",
    "orange juice": "🧃", "apple juice": "🧃", "soda": "🥤", "cola": "🥤",
    "coke": "🥤", "smoothie": "🥤", "milkshake": "🥤", "beer": "🍺",
    "wine": "🍷", "whiskey": "🥃", "whisky": "🥃", "cocktail": "🍸",
    "sake": "🍶",
    # the seasoning that matters here
    "salt": "🧂", "olive oil": "🫒",
}

# What an unrecognised food gets when a row wants *something* in the slot: a
# place setting, which says "this is food" and nothing else. Never a guess at
# which food.
FALLBACK = "🍽️"

_WORDS = re.compile(r"[^a-z0-9]+")


def _tokens(text):
    return [t for t in _WORDS.split(str(text or "").lower()) if t]


def _singular(token):
    """A crude English de-pluraliser - enough for "eggs" and "cherries".

    Deliberately not a stemmer. Over-stemming would make "grass" match
    "gras" and start producing exactly the wrong-emoji answers this module
    exists to avoid, so it only strips the three endings that actually turn
    up on a shopping list.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and token[-3] in "sxzho":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _norm(text):
    return [_singular(t) for t in _tokens(text)]


# The table, keyed the same way a query is normalised, so "Eggs" finds "egg".
_TABLE = {" ".join(_norm(k)): v for k, v in FOODS.items()}
# Phrases longest first: "peanut butter" must be tried before "butter", and
# "sweet potato" before "potato", or the answer is the wrong food.
_PHRASES = sorted(((k.split(), v) for k, v in _TABLE.items() if " " in k),
                  key=lambda kv: -len(kv[0]))


def _holds(tokens, phrase):
    n = len(phrase)
    return any(tokens[i:i + n] == phrase for i in range(len(tokens) - n + 1))


def food_emoji_or_none(name):
    """The emoji for `name`, or None when nothing in the table fits.

    None rather than a near-miss, every time. Callers that need a slot
    filled use `food_emoji`, which substitutes the neutral plate; callers
    that would rather draw nothing (a row that is still being typed) use
    this and skip.
    """
    tokens = _norm(name)
    if not tokens:
        return None
    whole = " ".join(tokens)
    if whole in _TABLE:                      # the whole name is a food
        return _TABLE[whole]
    for phrase, glyph in _PHRASES:           # longest phrase inside it
        if _holds(tokens, phrase):
            return glyph
    for token in tokens:                     # any one word of it
        if token in _TABLE:
            return _TABLE[token]
    return None


def food_emoji(name, default=FALLBACK):
    """The emoji for `name`, falling back to a neutral place setting."""
    return food_emoji_or_none(name) or default


def food_label(name, default=FALLBACK):
    """`name` with its emoji in front - for a control that only takes a string.

    Used by the Browse wall, where 231 items are TextButtons and the label is
    all there is. Returns the name unchanged rather than a lonely plate when
    nothing matches, so the wall does not become 231 identical plates.
    """
    glyph = food_emoji_or_none(name)
    return f"{glyph} {name}" if glyph else str(name)


# ---------------------------------------------------------------- the chrome
# Material icons, drawn in a palette colour. Every one of these is UI, not
# food: a heading, a button, a meter, a status. They are named here rather
# than written at the call site so that "what is the sodium mark" has one
# answer across the app.
ICON_GROCERY = ft.Icons.SHOPPING_CART
ICON_MEALS = ft.Icons.RESTAURANT_MENU
ICON_SCRAPE = ft.Icons.LINK
ICON_EXPENSES = ft.Icons.RECEIPT_LONG
ICON_EATZEN = ft.Icons.RESTAURANT
ICON_SETTINGS = ft.Icons.TUNE
ICON_HELP = ft.Icons.MENU_BOOK
ICON_SERVER = ft.Icons.DNS
ICON_APPEARANCE = ft.Icons.PALETTE
ICON_NAV = ft.Icons.DASHBOARD_CUSTOMIZE
ICON_UNITS = ft.Icons.STRAIGHTEN
ICON_DATA = ft.Icons.CLOUD_SYNC
ICON_DIAGNOSTICS = ft.Icons.FACT_CHECK
ICON_CAMERA = ft.Icons.CAMERA_ALT_OUTLINED
ICON_PANTRY = ft.Icons.KITCHEN
ICON_RECIPES = ft.Icons.MENU_BOOK
ICON_PLAN = ft.Icons.EVENT_NOTE
ICON_TEMPLATES = ft.Icons.BOOKMARKS
ICON_NEED = ft.Icons.ADD_SHOPPING_CART
ICON_BROWSE = ft.Icons.GRID_VIEW
ICON_LOG = ft.Icons.HISTORY

# The two marks that had to be recognisable at a glance and their own.
# Sodium is a grain - the shaker, not a generic warning triangle, because it
# labels an amount she is tracking rather than an alarm. Blood pressure is
# the heart monitor trace, which is also the app's BPM tab icon, so the mark
# on a reading and the tab it lives on are the same picture.
ICON_SODIUM = ft.Icons.GRAIN
ICON_SUGAR = ft.Icons.ICECREAM
ICON_BP = ft.Icons.MONITOR_HEART
ICON_PULSE = ft.Icons.FAVORITE


def mark(icon, *, size=16, tone="accent"):
    """One themed icon. `tone` is a palette key, so it follows all 24 themes."""
    p = PAL()
    return ft.Icon(icon, size=size, color=p.get(tone, p["accent"]))


def head(icon, text, size=13, *, tone="accent", trailing=None):
    """A card heading: themed icon, the title, and whatever goes on the right.

    `trailing` is appended after a spacer, which is the shape every heading
    on these pages already had; passing None leaves the spacer so headings
    line up whether or not they carry a chip.
    """
    return ft.Row(spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[mark(icon, size=size + 3, tone=tone), title(text, size),
                            ft.Container(expand=True),
                            *([trailing] if trailing else [])])
