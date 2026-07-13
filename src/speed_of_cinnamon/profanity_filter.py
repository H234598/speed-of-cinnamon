from __future__ import annotations

import re
import unicodedata

MAX_PROFANITY_FILTER_BYTES = 200_000
MAX_PROFANITY_FILTER_ENTRIES = 500
MAX_PROFANITY_PATTERN_CHARS = 256
MAX_PROFANITY_REPLACEMENT_CHARS = 256

PROFANITY_REPLACEMENT_PAIRS: tuple[tuple[str, str], ...] = (
    (r"abfuck", "Konfetti-Katastrophe"),
    (r"affenarsch", "Bananenbremse"),
    (r"affenkopf", "Bananendenker"),
    (r"arsch", "Sitzkissen"),
    (r"arschbacke", "Sofakissen"),
    (r"arschgeige", "Quatschvioline"),
    (r"arschgesicht", "Knautschgesicht"),
    (r"arschkrampe", "Muffinmuffel"),
    (r"arschloch", "Keksdieb"),
    (r"arschmade", "Krümelraupe"),
    (r"arschmütze", "Puddingmütze"),
    (r"arschpfeife", "Seifenflöte"),
    (r"arschrakete", "Gurkenrakete"),
    (r"arschwasser", "Gurkensaft"),
    (r"bastard", "Wackelpudding"),
    (r"bekloppt", "kringelig"),
    (r"beschissen", "kartoffelig"),
    (r"blödmann", "Nudelritter"),
    (r"blödsack", "Kicherbeutel"),
    (r"blödsinn", "Quatschkonfetti"),
    (r"depp", "Toastnavigator"),
    (r"deppen", "Toastnavigatoren"),
    (r"deppenapostel", "Toastprediger"),
    (r"dreck", "Glitzerstaub"),
    (r"drecksack", "Glitzerbeutel"),
    (r"drecksding", "Keksapparat"),
    (r"drecksfresse", "Keksvisage"),
    (r"dreckskerl", "Glitzeronkel"),
    (r"drecksladen", "Konfettibude"),
    (r"drecksmist", "Glitzerquatsch"),
    (r"dreckstück", "Keksbrocken"),
    (r"dummbeutel", "Waffelbeutel"),
    (r"dummkopf", "Waffelkopf"),
    (r"dummschwätzer", "Quasselwackler"),
    (r"dussel", "Puddingpilot"),
    (r"dödel", "Nudelknopf"),
    (r"flachzange", "Pfannkuchenzange"),
    (r"fotze", "Fluffwolke"),
    (r"fresse", "Plaudertasche"),
    (r"fresssack", "Knabberkönig"),
    (r"fuck", "Frickelfrosch"),
    (r"gefickt", "durchgewackelt"),
    (r"hirni", "Keksdenker"),
    (r"honk", "Hupenwichtel"),
    (r"horst", "Gurkengeneral"),
    (r"idiot", "Quatschpilot"),
    (r"idioten", "Quatschpiloten"),
    (r"kack", "Kakao"),
    (r"kackbratze", "Kakaokeks"),
    (r"kacke", "Kakaokonfetti"),
    (r"kackfass", "Kakaofass"),
    (r"kackfresse", "Kakaogesicht"),
    (r"kackhaufen", "Kakaohügel"),
    (r"kacklappen", "Kakaolappen"),
    (r"kackmist", "Kakaoquatsch"),
    (r"kacksack", "Kakaobeutel"),
    (r"kackvogel", "Kakaospatz"),
    (r"kanaille", "Kicherkanone"),
    (r"knecht", "Keksgehilfe"),
    (r"lappen", "Kuschellappen"),
    (r"lauch", "Suppengrün"),
    (r"mistkerl", "Muffinmensch"),
    (r"miststück", "Muffinstück"),
    (r"muschi", "Miezekissen"),
    (r"pimmel", "Wackelzapfen"),
    (r"pimmelkopf", "Zapfenkopf"),
    (r"pisser", "Sprudelwichtel"),
    (r"rotz", "Wolkenschnupfen"),
    (r"rotzgöre", "Schnupfenfee"),
    (r"rotzlöffel", "Schnupfenlöffel"),
    (r"sackgesicht", "Beutelblick"),
    (r"schei(?:ss|ß)", "Glitzer"),
    (r"schei(?:ss|ß)ding", "Glitzerding"),
    (r"schei(?:ss|ß)e?", "Glitzerkram"),
    (r"schei(?:ss|ß)egal", "pudelwichtig"),
    (r"schei(?:ss|ß)er", "Glitzerwichtel"),
    (r"schei(?:ss|ß)haus", "Glitzerhaus"),
    (r"schei(?:ss|ß)kerl", "Glitzermensch"),
    (r"schei(?:ss|ß)kopf", "Glitzerkopf"),
    (r"schei(?:ss|ß)laden", "Glitzerbude"),
    (r"schei(?:ss|ß)spiel", "Glitzerspiel"),
    (r"schei(?:ss|ß)teil", "Glitzerteil"),
    (r"schei(?:ss|ß)verein", "Glitzerverein"),
    (r"schlampe", "Glitzerdiva"),
    (r"schwachkopf", "Wackelkopf"),
    (r"schwanz", "Wackelstab"),
    (r"schwanzgesicht", "Wackelgesicht"),
    (r"spacko", "Kicherkobold"),
    (r"trottel", "Toasttrommler"),
    (r"verdammt", "verflixt und zugekrümelt"),
    (r"vollidiot", "Vollzeit-Quatschpilot"),
    (r"wichser", "Waffelwackler"),
    (r"wixxer", "Waffelwackler"),
    (r"wixer", "Waffelwackler"),
    (r"abgefuckt", "konfettizerzaust"),
    (r"abkacken", "absprudeln"),
    (r"ankacken", "ankakaonieren"),
    (r"ankotzen", "ankonfettieren"),
    (r"bekackt", "kakaobunt"),
    (r"bescheuert", "bananenkrumm"),
    (r"bitch", "waffle gremlin"),
    (r"bollocks", "bubble noodles"),
    (r"bullshit", "waffle dust"),
    (r"clusterfuck", "confetti rodeo"),
    (r"cock", "wobble stick"),
    (r"cockface", "waffle face"),
    (r"cocksucker", "waffle snorkeler"),
    (r"crap", "sprinkle crumbs"),
    (r"crappy", "sprinkle-powered"),
    (r"cum", "custard sparkle"),
    (r"cunt", "cupcake goblin"),
    (r"dammit", "donut buttons"),
    (r"damn", "darn-a-saurus"),
    (r"damned", "darned with sprinkles"),
    (r"dick", "pickle wand"),
    (r"dickbag", "pickle pouch"),
    (r"dickface", "pickle portrait"),
    (r"dickhead", "pickle captain"),
    (r"dipshit", "waffle snorkel"),
    (r"douche", "bubble wizard"),
    (r"douchebag", "bubble satchel"),
    (r"dumbass", "muffin navigator"),
    (r"dumbfuck", "muffin cyclone"),
    (r"fanny", "pudding pocket"),
    (r"fart", "tuba puff"),
    (r"fartknocker", "tuba goblin"),
    (r"fucker", "frickle frog"),
    (r"fuckery", "frickle circus"),
    (r"fuckface", "frickle face"),
    (r"fuckhead", "frickle captain"),
    (r"fucking", "frickling"),
    (r"fuckwit", "frickle noodle"),
    (r"goddamn", "gosh-darn glitter"),
    (r"jackass", "waffle donkey"),
    (r"jerk", "pickle juggler"),
    (r"jerkoff", "pickle tumbler"),
    (r"motherfucker", "motherfluffin muffin"),
    (r"nutsack", "peanut pouch"),
    (r"piss", "lemon fizz"),
    (r"pissed", "lemon-fizzed"),
    (r"prick", "cactus cupcake"),
    (r"pussy", "kitten pillow"),
    (r"shit", "sprinkle soup"),
    (r"shitbag", "sprinkle satchel"),
    (r"shitbird", "sprinkle pigeon"),
    (r"shitbrain", "sprinkle thinker"),
    (r"shitface", "sprinkle smile"),
    (r"shithead", "sprinkle captain"),
    (r"shitshow", "confetti parade"),
    (r"shitty", "sprinkle-powered"),
    (r"slut", "sparkle llama"),
    (r"sonofabitch", "son of a biscuit"),
    (r"twat", "teacup goblin"),
    (r"wanker", "waffle wobbler"),
    (r"whore", "sparkle pancake"),
    (r"abomination", "confetti mystery"),
    (r"arse", "cushion zone"),
    (r"arsehole", "biscuit burglar"),
    (r"ass", "cushion zone"),
    (r"assclown", "waffle clown"),
    (r"assface", "cushion face"),
    (r"asshat", "pancake hat"),
    (r"asshole", "biscuit burglar"),
    (r"asswipe", "napkin ninja"),
    (r"balls", "jellybeans"),
    (r"ballbag", "jellybean satchel"),
    (r"bastards", "wobble puddings"),
    (r"bellend", "teapot tip"),
    (r"bloody", "tomato-splashed"),
    (r"bonehead", "pretzel brain"),
    (r"bugger", "button badger"),
    (r"buggered", "button-badgered"),
    (r"clownass", "circus cushion"),
    (r"crapbag", "crumb satchel"),
    (r"dipstick", "noodle wand"),
    (r"dumb", "waffle-wise"),
    (r"hell", "heckleberry hill"),
    (r"idiots", "quatsch pilots"),
    (r"moron", "muffin pilot"),
    (r"morons", "muffin pilots"),
    (r"numbnuts", "frozen peanuts"),
    (r"nuts", "jellybeans"),
    (r"pillock", "pickle lantern"),
    (r"plonker", "pudding ranger"),
    (r"tosser", "waffle tosser"),
    (r"verfickt", "frickelig"),
    (r"verkackt", "kakaoverziert"),
    (r"versaut", "konfettiverwuschelt"),
    (r"wank", "waffle wobble"),
    (r"wankstain", "waffle smudge"),
    (r"wix", "waffel"),
    (r"wixkopf", "Waffelkopf"),
    (r"wixstiefel", "Waffelstiefel"),
    (r"zumkotzen", "zum Konfettiwerfen"),
)

_TRUSTED_PROFANITY_PATTERNS = frozenset(pattern for pattern, _replacement in PROFANITY_REPLACEMENT_PAIRS)
_MATCH_IGNORE_CATEGORIES = frozenset({"Mn", "Mc", "Me", "Cf"})
_REGEX_META_CHARS = frozenset(r".^$*+?{}[]\|()")


def _regex_escape_codepoint(codepoint: int) -> str:
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04X}"
    return f"\\U{codepoint:08X}"


def _unicode_category_char_class_ranges(categories: frozenset[str]) -> str:
    parts: list[str] = []
    range_start: int | None = None
    previous: int | None = None
    for codepoint in range(0x110000):
        if unicodedata.category(chr(codepoint)) in categories:
            if range_start is None:
                range_start = codepoint
            previous = codepoint
            continue
        if range_start is not None and previous is not None:
            if range_start == previous:
                parts.append(_regex_escape_codepoint(range_start))
            else:
                parts.append(f"{_regex_escape_codepoint(range_start)}-{_regex_escape_codepoint(previous)}")
            range_start = None
            previous = None
    if range_start is not None and previous is not None:
        if range_start == previous:
            parts.append(_regex_escape_codepoint(range_start))
        else:
            parts.append(f"{_regex_escape_codepoint(range_start)}-{_regex_escape_codepoint(previous)}")
    return "".join(parts)


_IGNORABLE_MATCH_RANGES = _unicode_category_char_class_ranges(_MATCH_IGNORE_CATEGORIES)
_IGNORABLE_CHAR_CLASS = f"[{_IGNORABLE_MATCH_RANGES}]"
_IGNORABLE_BOUNDARY_CLASS = f"[\\w{_IGNORABLE_MATCH_RANGES}]"
_IGNORABLE_GAP_PATTERN = rf"{_IGNORABLE_CHAR_CLASS}*"
_CONFUSABLE_FOLD = str.maketrans({
    "а": "a",
    "А": "a",
    "е": "e",
    "Е": "e",
    "о": "o",
    "О": "o",
    "р": "p",
    "Р": "p",
    "с": "c",
    "С": "c",
    "у": "y",
    "У": "y",
    "х": "x",
    "Х": "x",
    "і": "i",
    "І": "i",
    "ј": "j",
    "Ј": "j",
    "к": "k",
    "К": "k",
    "ѕ": "s",
    "Ѕ": "s",
})
_CONFUSABLE_REGEX_EQUIVALENTS: dict[str, str] = {
    "a": "aаА",
    "c": "cсС",
    "e": "eеЕ",
    "i": "iіІ",
    "j": "jјЈ",
    "k": "kкК",
    "o": "oоО",
    "p": "pрР",
    "s": "sѕЅ",
    "x": "xхХ",
    "y": "yуУ",
}


def _confusable_regex_source(char: str) -> str:
    equivalents = _CONFUSABLE_REGEX_EQUIVALENTS.get(char)
    if not equivalents:
        return re.escape(char)
    return "[" + "".join(re.escape(item) for item in equivalents) + "]"


def _normalize_profanity_pattern(pattern: str) -> str:
    normalized: list[str] = []
    for char in unicodedata.normalize("NFKD", pattern).casefold():
        if unicodedata.category(char) in _MATCH_IGNORE_CATEGORIES:
            continue
        normalized.append(char.translate(_CONFUSABLE_FOLD))
    return "".join(normalized)


def _build_tolerant_profanity_pattern(pattern: str) -> str:
    normalized = _normalize_profanity_pattern(pattern)
    if not normalized:
        return ""
    source = _IGNORABLE_GAP_PATTERN.join(_confusable_regex_source(char) for char in normalized)
    return rf"(?<!{_IGNORABLE_BOUNDARY_CLASS}){_IGNORABLE_GAP_PATTERN}{source}{_IGNORABLE_GAP_PATTERN}(?!{_IGNORABLE_BOUNDARY_CLASS})"


def _safe_profanity_pattern_source(pattern: str) -> str:
    if pattern in _TRUSTED_PROFANITY_PATTERNS and any(char in _REGEX_META_CHARS for char in pattern):
        return rf"(?<!{_IGNORABLE_BOUNDARY_CLASS}){_IGNORABLE_GAP_PATTERN}(?:{pattern}){_IGNORABLE_GAP_PATTERN}(?!{_IGNORABLE_BOUNDARY_CLASS})"
    return _build_tolerant_profanity_pattern(pattern)


def _safe_ascii_profanity_pattern_source(pattern: str) -> str:
    if pattern in _TRUSTED_PROFANITY_PATTERNS and any(char in _REGEX_META_CHARS for char in pattern):
        return rf"(?<!\w)(?:{pattern})(?!\w)"
    normalized = _normalize_profanity_pattern(pattern)
    if not normalized:
        return ""
    return rf"(?<!\w){re.escape(normalized)}(?!\w)"


PROFANITY_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(_safe_profanity_pattern_source(pattern), re.IGNORECASE), replacement)
    for pattern, replacement in PROFANITY_REPLACEMENT_PAIRS
    if _safe_profanity_pattern_source(pattern)
)


def render_profanity_replacement_list() -> str:
    lines = [
        "Speed of Cinnamon profanity replacement list",
        "",
        "This local list is used only when 'Replace profanity with harmless words' is enabled.",
        "# Format: text -> replacement.",
        "# Bundled patterns may use trusted regex syntax; custom patterns are treated as literal text for safety.",
        "# Replacements try to stay silly and harmless.",
        "",
    ]
    for index, (pattern, replacement) in enumerate(PROFANITY_REPLACEMENT_PAIRS, start=1):
        lines.append(f"{pattern} -> {replacement}")
    return "\n".join(lines) + "\n"


def _clean_editable_value(value: str, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        text_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return ""
    if len(text) > max_chars or text_bytes > max_chars:
        return ""
    if any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in text):
        return ""
    return text


def parse_profanity_replacement_list(text: str) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" not in line:
            continue
        pattern_text, replacement_text = line.split("->", 1)
        pattern = _clean_editable_value(pattern_text, max_chars=MAX_PROFANITY_PATTERN_CHARS)
        replacement = _clean_editable_value(replacement_text, max_chars=MAX_PROFANITY_REPLACEMENT_CHARS)
        if not pattern or not replacement:
            continue
        pattern_source = _safe_profanity_pattern_source(pattern)
        try:
            re.compile(pattern_source, re.IGNORECASE)
        except re.error:
            continue
        pairs.append((pattern, replacement))
        if len(pairs) >= MAX_PROFANITY_FILTER_ENTRIES:
            break
    return tuple(pairs) or PROFANITY_REPLACEMENT_PAIRS


def compile_profanity_replacements(
    pairs: tuple[tuple[str, str], ...],
    *,
    text: str | None = None,
) -> tuple[tuple[re.Pattern[str], str], ...]:
    if text is not None and (not isinstance(text, str) or isinstance(text, bool)):
        raise ValueError("text must be text")
    use_ascii_patterns = text is not None and text.isascii()
    compiled: list[tuple[re.Pattern[str], str]] = []
    for pattern, replacement in pairs[:MAX_PROFANITY_FILTER_ENTRIES]:
        clean_pattern = _clean_editable_value(pattern, max_chars=MAX_PROFANITY_PATTERN_CHARS)
        clean_replacement = _clean_editable_value(replacement, max_chars=MAX_PROFANITY_REPLACEMENT_CHARS)
        if not clean_pattern or not clean_replacement:
            continue
        pattern_source = (
            _safe_ascii_profanity_pattern_source(clean_pattern)
            if use_ascii_patterns
            else _safe_profanity_pattern_source(clean_pattern)
        )
        try:
            compiled.append((re.compile(pattern_source, re.IGNORECASE), clean_replacement))
        except re.error:
            continue
    return tuple(compiled) or PROFANITY_REPLACEMENTS
