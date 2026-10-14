"""Cleanup of raw listing fields: price, mileage, year, condition, make, model.

The make/model tables below are meant to grow over time. When a popular model
shows up split across several spellings in the dashboard, add an alias here.
"""

from __future__ import annotations

import re
from datetime import date

# --- price / mileage / year -------------------------------------------------

# Asking prices below these are junk ("Rs 1", deposit-only, monthly rent...).
_MIN_PRICE_BY_TYPE: dict[str, int] = {
    "motorcycle": 10_000,
    "three_wheel": 50_000,
}
_DEFAULT_MIN_PRICE = 100_000
_MAX_PRICE = 2_000_000_000  # Rs 2 bn: anything above is a typo
_MAX_MILEAGE_KM = 1_000_000
_MIN_YEAR = 1940

_DIGITS = re.compile(r"\d[\d,]*")


def parse_price(text: str | None, vehicle_type: str = "car") -> int | None:
    """'Rs. 8,450,000' / 'Rs 7,690,000' -> 8450000; 'Negotiable' or junk -> None."""
    if not text:
        return None
    m = _DIGITS.search(text)
    if not m:
        return None
    value = int(m.group().replace(",", ""))
    if value < _MIN_PRICE_BY_TYPE.get(vehicle_type, _DEFAULT_MIN_PRICE) or value > _MAX_PRICE:
        return None
    return value


def parse_mileage(text: str | None) -> int | None:
    """'101,000 km' -> 101000; absurd values -> None."""
    if not text:
        return None
    m = _DIGITS.search(text)
    if not m:
        return None
    value = int(m.group().replace(",", ""))
    return value if value <= _MAX_MILEAGE_KM else None


def parse_year(value: str | int | None, today: date | None = None) -> int | None:
    if value is None:
        return None
    try:
        year = int(str(value).strip())
    except ValueError:
        return None
    max_year = (today or date.today()).year + 1
    return year if _MIN_YEAR <= year <= max_year else None


_YEAR_IN_TITLE = re.compile(r"\b(19[4-9]\d|20\d\d)\b")


def extract_year(title: str, today: date | None = None) -> tuple[int | None, str]:
    """Find a model year in a title; return (year, title with the year removed).

    Uses the last plausible year, since titles look like 'Toyota Axio 2015' or
    'TVS Ntorq 125 BRAND NEW 2026 S'.
    """
    for m in reversed(list(_YEAR_IN_TITLE.finditer(title))):
        year = parse_year(m.group(), today)
        if year is not None:
            rest = (title[: m.start()] + " " + title[m.end():]).strip()
            return year, re.sub(r"\s+", " ", rest)
    return None, title.strip()


_CONDITIONS: dict[str, str] = {
    "used": "used",
    "reconditioned": "reconditioned",
    "recondition": "reconditioned",
    "brand new": "brand_new",
    "new": "brand_new",
    "unregistered": "unregistered",
    "import": "import",
}


def normalize_condition(text: str | None) -> str | None:
    if not text:
        return None
    return _CONDITIONS.get(text.strip().lower())


# --- make ---------------------------------------------------------------------

# alias (lowercase, spaces) -> canonical make
_MAKE_ALIASES: dict[str, str] = {
    "mercedes benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "mercedes": "Mercedes-Benz",
    "benz": "Mercedes-Benz",
    "land rover": "Land Rover",
    "landrover": "Land Rover",
    "range rover": "Land Rover",
    "hero honda": "Hero-Honda",
    "hero-honda": "Hero-Honda",
    "alfa romeo": "Alfa Romeo",
    "aston martin": "Aston Martin",
    "rolls royce": "Rolls-Royce",
    "rolls-royce": "Rolls-Royce",
    "great wall": "Great Wall",
    "royal enfield": "Royal Enfield",
    "ashok leyland": "Ashok Leyland",
    "tata": "Tata",
    "mahindra": "Mahindra",
    "maruti": "Maruti Suzuki",
    "maruti suzuki": "Maruti Suzuki",
    "vw": "Volkswagen",
    "volkswagen": "Volkswagen",
    "bmw": "BMW",
    "byd": "BYD",
    "mg": "MG",
    "tvs": "TVS",
    "ktm": "KTM",
    "dfsk": "DFSK",
    "dfm": "DFSK",
    "jac": "JAC",
    "jmc": "JMC",
    "faw": "FAW",
    "baic": "BAIC",
    "gac": "GAC",
    "chery": "Chery",
    "isuzu": "Isuzu",
    "hino": "Hino",
    "mitsubishi": "Mitsubishi",
    "fuso": "Mitsubishi",
    "toyota": "Toyota",
    "nissan": "Nissan",
    "honda": "Honda",
    "suzuki": "Suzuki",
    "daihatsu": "Daihatsu",
    "mazda": "Mazda",
    "subaru": "Subaru",
    "lexus": "Lexus",
    "hyundai": "Hyundai",
    "kia": "Kia",
    "perodua": "Perodua",
    "proton": "Proton",
    "micro": "Micro",
    "audi": "Audi",
    "peugeot": "Peugeot",
    "renault": "Renault",
    "ford": "Ford",
    "chevrolet": "Chevrolet",
    "bajaj": "Bajaj",
    "hero": "Hero",
    "yamaha": "Yamaha",
    "kawasaki": "Kawasaki",
    "piaggio": "Piaggio",
    "vespa": "Vespa",
    "demak": "Demak",
    "zotye": "Zotye",
    "tesla": "Tesla",
    "volvo": "Volvo",
    "jeep": "Jeep",
    "porsche": "Porsche",
    "jaguar": "Jaguar",
    "mini": "Mini",
    "fiat": "Fiat",
    "citroen": "Citroen",
    "skoda": "Skoda",
    "ssangyong": "SsangYong",
    "foton": "Foton",
    "dongfeng": "Dongfeng",
    "eicher": "Eicher",
    "massey ferguson": "Massey Ferguson",
    "john deere": "John Deere",
    "kubota": "Kubota",
    "yanmar": "Yanmar",
}
_MAX_MAKE_WORDS = max(len(a.split()) for a in _MAKE_ALIASES)


def _tokens(text: str) -> list[str]:
    return text.split()


def split_make(text: str) -> tuple[str | None, str]:
    """'Mercedes Benz C220' -> ('Mercedes-Benz', 'C220'). Unknown makes keep the first word."""
    words = _tokens(text)
    if not words:
        return None, ""
    for n in range(min(_MAX_MAKE_WORDS, len(words)), 0, -1):
        alias = " ".join(words[:n]).lower()
        if alias in _MAKE_ALIASES:
            return _MAKE_ALIASES[alias], " ".join(words[n:])
    first = words[0]
    make = first.title() if first.islower() or first.isupper() and len(first) > 3 else first
    return make, " ".join(words[1:])


# --- model --------------------------------------------------------------------

# canonical make -> {alias: base model}. Aliases are matched against the model
# text with spaces and hyphens ignored, so 'wagon r' also matches 'WagonR' and
# 'Wagon-R', and 'ct100' matches 'CT-100'.
_MODEL_ALIASES: dict[str, dict[str, str]] = {
    "Toyota": {
        "axio": "axio", "allion": "allion", "premio": "premio", "aqua": "aqua",
        "prius": "prius", "vitz": "vitz", "yaris": "yaris", "corolla": "corolla",
        "elephant back": "corolla", "fielder": "fielder", "camry": "camry",
        "land cruiser prado": "land cruiser prado", "prado": "land cruiser prado",
        "land cruiser": "land cruiser", "hilux": "hilux", "hiace": "hiace",
        "kdh": "hiace", "noah": "noah", "voxy": "voxy", "roomy": "roomy",
        "raize": "raize", "rush": "rush", "passo": "passo", "starlet": "starlet",
        "c-hr": "c-hr", "chr": "c-hr", "harrier": "harrier", "rav4": "rav4",
        "townace": "townace", "liteace": "liteace", "dyna": "dyna",
        "belta": "belta", "vios": "vios", "tank": "tank", "crown": "crown",
        "mark x": "mark x", "markx": "mark x", "esquire": "esquire", "sienta": "sienta",
        "pixis": "pixis", "carina": "carina", "corona": "corona",
        "wigo": "wigo", "fortuner": "fortuner", "probox": "probox", "succeed": "succeed",
    },
    "Suzuki": {
        "wagon r": "wagon r", "alto": "alto", "swift": "swift", "celerio": "celerio",
        "spacia": "spacia", "hustler": "hustler", "every": "every", "estilo": "estilo",
        "maruti": "maruti", "a-star": "a-star", "zen": "zen", "baleno": "baleno",
        "jimny": "jimny", "vitara": "vitara", "ertiga": "ertiga", "xbee": "xbee",
        "s-presso": "s-presso", "dzire": "dzire", "burgman": "burgman", "gixxer": "gixxer",
        "gn125": "gn125", "access": "access", "solio": "solio",
    },
    "Honda": {
        "vezel": "vezel", "fit": "fit", "grace": "grace", "civic": "civic",
        "insight": "insight", "freed": "freed", "city": "city", "accord": "accord",
        "cr-v": "cr-v", "crv": "cr-v", "hr-v": "hr-v", "n-box": "n-box", "nbox": "n-box",
        "n-wgn": "n-wgn", "shuttle": "shuttle", "wr-v": "wr-v", "zr-v": "zr-v",
        "dio": "dio", "cd 90": "cd90", "cd90": "cd90", "cd125": "cd125", "adv": "adv",
        "hornet": "hornet", "shine": "shine", "click": "click", "pcx": "pcx",
        "cb shine": "shine", "unicorn": "unicorn", "activa": "activa", "xr": "xr",
    },
    "Nissan": {
        "sunny": "sunny", "leaf": "leaf", "march": "march", "dayz": "dayz",
        "x-trail": "x-trail", "xtrail": "x-trail", "note": "note", "tiida": "tiida",
        "bluebird": "bluebird", "sylphy": "sylphy", "pulsar": "pulsar",
        "caravan": "caravan", "navara": "navara", "kicks": "kicks", "juke": "juke",
        "clipper": "clipper", "vanette": "vanette", "ad wagon": "ad", "ad": "ad",
        "fb14": "sunny", "fb15": "sunny", "n16": "sunny", "b12": "sunny", "hb12": "sunny",
        "sb14": "sunny", "b14": "sunny", "fb12": "sunny", "roox": "roox", "serena": "serena",
    },
    "Daihatsu": {
        "mira": "mira", "move": "move", "hijet": "hijet", "copen": "copen",
        "tanto": "tanto", "rocky": "rocky", "terios": "terios", "boon": "boon",
        "charade": "charade", "cast": "cast", "taft": "taft",
    },
    "Mitsubishi": {
        "lancer": "lancer", "montero": "montero", "pajero": "pajero",
        "outlander": "outlander", "attrage": "attrage", "mirage": "mirage",
        "l200": "l200", "triton": "triton", "ek wagon": "ek wagon", "canter": "canter",
        "rosa": "rosa", "xpander": "xpander", "delica": "delica", "eclipse cross": "eclipse cross",
    },
    "Mazda": {
        "axela": "axela", "demio": "demio", "familia": "familia", "flair": "flair",
        "cx-3": "cx-3", "cx-5": "cx-5", "cx-30": "cx-30", "bongo": "bongo", "carol": "carol",
        "mazda 3": "mazda3", "mazda3": "mazda3", "atenza": "atenza",
    },
    "Micro": {"panda": "panda", "trend": "trend", "mx7": "mx7", "geely": "geely", "kyron": "kyron"},
    "Perodua": {"viva": "viva", "axia": "axia", "bezza": "bezza", "myvi": "myvi", "kelisa": "kelisa"},
    "Hyundai": {
        "eon": "eon", "grand i10": "grand i10", "i10": "i10", "i20": "i20", "tucson": "tucson",
        "santa fe": "santa fe", "elantra": "elantra", "accent": "accent", "creta": "creta",
        "kona": "kona", "venue": "venue", "sonata": "sonata", "h100": "h100",
    },
    "Kia": {
        "picanto": "picanto", "sorento": "sorento", "sportage": "sportage",
        "sonet": "sonet", "seltos": "seltos", "rio": "rio", "cerato": "cerato",
    },
    "Mercedes-Benz": {
        "c class": "c-class", "c180": "c-class", "c200": "c-class", "c220": "c-class",
        "c250": "c-class", "c300": "c-class", "c350": "c-class",
        "e class": "e-class", "e200": "e-class", "e220": "e-class", "e250": "e-class",
        "e300": "e-class", "e350": "e-class",
        "s class": "s-class", "cla": "cla", "gla": "gla", "glc": "glc", "gle": "gle",
        "a class": "a-class", "a180": "a-class", "a200": "a-class", "b class": "b-class",
        "b180": "b-class", "b200": "b-class", "ml": "m-class", "vito": "vito",
    },
    "BMW": {
        "318i": "3 series", "320i": "3 series", "320d": "3 series", "318": "3 series",
        "320": "3 series", "330e": "3 series", "3 series": "3 series",
        "520d": "5 series", "520i": "5 series", "530e": "5 series", "525": "5 series",
        "5 series": "5 series", "x1": "x1", "x3": "x3", "x5": "x5", "i3": "i3",
    },
    "Audi": {"a1": "a1", "a3": "a3", "a4": "a4", "a5": "a5", "a6": "a6", "q2": "q2",
             "q3": "q3", "q5": "q5", "q7": "q7", "e-tron": "e-tron"},
    "Bajaj": {
        "pulsar": "pulsar", "ns200": "pulsar", "ns 200": "pulsar", "ns": "pulsar",
        "discover": "discover", "platina": "platina", "ct100": "ct100", "ct 100": "ct100",
        "boxer": "boxer", "avenger": "avenger", "dominar": "dominar", "re": "re",
        "qute": "qute", "chetak": "chetak", "v15": "v15",
    },
    "TVS": {
        "ntorq": "ntorq", "metro": "metro", "scooty pep": "scooty", "scooty": "scooty",
        "wego": "wego", "star city": "star city", "apache": "apache", "jupiter": "jupiter",
        "raider": "raider", "rockz": "rockz", "king": "king",
    },
    "Hero": {
        "dawn": "dawn", "pleasure": "pleasure", "stunner": "stunner", "ismart": "ismart",
        "maestro": "maestro", "splendor": "splendor", "hunk": "hunk", "passion": "passion",
        "xpulse": "xpulse", "dash": "dash",
    },
    "Yamaha": {
        "fz": "fz", "fzs": "fz", "fz-s": "fz", "fz16": "fz", "fzs v2": "fz", "fz v3": "fz",
        "ray zr": "ray zr", "ray": "ray zr", "zr": "ray zr", "fazer": "fazer",
        "r15": "r15", "mt-15": "mt-15", "mt15": "mt-15", "fascino": "fascino", "nmax": "nmax",
        "aerox": "aerox", "rx100": "rx100", "saluto": "saluto",
    },
    "Volkswagen": {"polo": "polo", "golf": "golf", "passat": "passat", "beetle": "beetle",
                   "id.4": "id.4", "t-cross": "t-cross", "taigun": "taigun"},
    "Peugeot": {"3008": "3008", "2008": "2008", "308": "308", "208": "208", "406": "406"},
    "Land Rover": {"range rover sport": "range rover sport", "range rover evoque": "range rover evoque",
                   "evoque": "range rover evoque", "range rover": "range rover",
                   "defender": "defender", "discovery": "discovery"},
    "Isuzu": {"elf": "elf", "d-max": "d-max", "dmax": "d-max", "mu-x": "mu-x"},
    "Mahindra": {"bolero": "bolero", "scorpio": "scorpio", "kuv100": "kuv100", "maxximo": "maxximo",
                 "xuv300": "xuv300", "thar": "thar"},
    "Tata": {"nano": "nano", "indica": "indica", "ace": "ace", "dimo batta": "dimo batta",
             "batta": "dimo batta", "super ace": "super ace", "xenon": "xenon"},
}

# Raw model text tokens that carry no model information.
_NOISE_WORDS = {"brand", "new", "used", "reconditioned", "for", "sale", "car"}

_SPLIT = re.compile(r"[\s/]+")


def _compact(text: str) -> str:
    return re.sub(r"[\s\-_.]", "", text.lower())


def normalize_model(make: str | None, model_raw: str) -> str | None:
    """Reduce a raw model string to a base model, e.g. Suzuki 'Wagon R FX Safety' -> 'wagon r'.

    Picks the alias that starts earliest in the text, preferring the longest
    alias at that position. Unknown models fall back to their first word.
    """
    words = [w for w in _SPLIT.split(model_raw.strip()) if w]
    if not words:
        return None
    aliases = _MODEL_ALIASES.get(make or "", {})
    if aliases:
        compact_aliases = {_compact(a): m for a, m in aliases.items()}
        max_len = max(len(a.split()) for a in aliases) + 1
        for start in range(len(words)):
            best: str | None = None
            for end in range(min(len(words), start + max_len), start, -1):
                hit = compact_aliases.get(_compact("".join(words[start:end])))
                if hit:
                    best = hit
                    break
            if best:
                return best
    for word in words:
        if word.lower() not in _NOISE_WORDS:
            return _compact(word) or None
    return None


def split_title(title_without_year: str) -> tuple[str | None, str | None, str | None]:
    """'Suzuki Wagon R FZ Premium' -> ('Suzuki', 'wagon r', 'Wagon R FZ Premium')."""
    make, rest = split_make(title_without_year.strip())
    model_raw = rest.strip() or None
    model = normalize_model(make, model_raw) if model_raw else None
    return make, model, model_raw
