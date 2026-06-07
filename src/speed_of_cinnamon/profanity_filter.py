from __future__ import annotations

import re

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


def _safe_profanity_pattern_source(pattern: str) -> str:
    if pattern in _TRUSTED_PROFANITY_PATTERNS:
        return pattern
    return re.escape(pattern)


PROFANITY_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"(?<![\w]){_safe_profanity_pattern_source(pattern)}(?![\w])", re.IGNORECASE), replacement)
    for pattern, replacement in PROFANITY_REPLACEMENT_PAIRS
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
    if len(text) > max_chars or len(text.encode("utf-8")) > max_chars:
        return ""
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
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
            re.compile(rf"(?<![\w]){pattern_source}(?![\w])", re.IGNORECASE)
        except re.error:
            continue
        pairs.append((pattern, replacement))
        if len(pairs) >= MAX_PROFANITY_FILTER_ENTRIES:
            break
    return tuple(pairs) or PROFANITY_REPLACEMENT_PAIRS


def compile_profanity_replacements(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[re.Pattern[str], str], ...]:
    compiled: list[tuple[re.Pattern[str], str]] = []
    for pattern, replacement in pairs[:MAX_PROFANITY_FILTER_ENTRIES]:
        clean_pattern = _clean_editable_value(pattern, max_chars=MAX_PROFANITY_PATTERN_CHARS)
        clean_replacement = _clean_editable_value(replacement, max_chars=MAX_PROFANITY_REPLACEMENT_CHARS)
        if not clean_pattern or not clean_replacement:
            continue
        pattern_source = _safe_profanity_pattern_source(clean_pattern)
        try:
            compiled.append((re.compile(rf"(?<![\w]){pattern_source}(?![\w])", re.IGNORECASE), clean_replacement))
        except re.error:
            continue
    return tuple(compiled) or PROFANITY_REPLACEMENTS
