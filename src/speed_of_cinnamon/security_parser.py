from __future__ import annotations

import functools
import fcntl
import os
import re
import stat
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from .path_safety import (
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)


_MAX_BLACKLIST_ENTRY_CHARS = 120
_MAX_BLACKLIST_ENTRIES = 1_000
_MAX_BLACKLIST_FILE_BYTES = 1_000_000
_MAX_BLACKLIST_PATTERN_BYTES = _MAX_BLACKLIST_ENTRY_CHARS * _MAX_BLACKLIST_ENTRIES
_MAX_SECURITY_TEXT_CHARS = 65_535
_MATCH_IGNORE_CATEGORIES = frozenset({"Mn", "Mc", "Me", "Cf"})
_NORMALIZED_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[\d\s-]{11,40}\d)(?!\d)")


def _note_lock_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"blacklist lock cleanup failed: {cleanup_error}")


_BLACKLIST_ADD_RE = re.compile(
    r"(?im)^\s*(?:blacklisteintrag|blacklist\s*eintrag)\b[\s:,-]*(.+?)\s*$",
)
_BLACKLIST_SHOW_RE = re.compile(
    r"(?im)^\s*(?:(?:bitte\s+)?(?:mir\s+)?(?:die\s+)?"
    r"(?:blacklist|blackliste|sperrliste)\s+(?:anzeigen|anzeige|öffnen|open|show|zeigen|zeige)"
    r"|(?:bitte\s+)?(?:mir\s+)?(?:zeige|zeig|show|open|öffne)\s+(?:die\s+)?"
    r"(?:blacklist|blackliste|sperrliste)(?:\s+(?:anzeigen|anzeige|öffnen|open|show|zeigen|zeige))?)\s*$",
)

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}\b"
)
_IBAN_RE = re.compile(r"\b[A-Za-z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d[\d\s().-]{7,20}\d|(?:\(?\d{2,5}\)?[\s.-]){1,3}\d{3,}|\d{10,15})(?!\d)"
)
_SPOKEN_SENSITIVE_LABEL_PATTERN = (
    r"(?:token|api[_-]?key|api\s+key|secret|apikey|bearer|password|passwort|kennwort|"
    r"passcode|iban|name|adresse|anschrift|address|kundennummer|kundennr|kunden-nr|ssn|tax\s+id)"
)
_SECRET_VALUE_PATTERN = (
    r"(?:\"[^\n\"]{1,20000}\"|'[^\n']{1,20000}'|"
    rf"(?:(?!\s+\[redacted\b)(?!\s+(?:und|and)\s+\[redacted\b)"
    rf"(?!\b(?:und|and)\s+(?:meine|my\s+)?{_SPOKEN_SENSITIVE_LABEL_PATTERN}\b)"
    rf"(?!\s+(?:(?:und|and)\s+)?(?:meine|my\s+)?{_SPOKEN_SENSITIVE_LABEL_PATTERN}\b)[^\n]){{1,20000}})"
)
_TOKEN_RE = re.compile(
    r"(?i)\b(?:token|api[_-]?key|api\s+key|secret|apikey|bearer)\b\s*(?::|=)\s*"
    + _SECRET_VALUE_PATTERN
)
_SPOKEN_SENSITIVE_VALUE_PATTERN = (
    _SECRET_VALUE_PATTERN
)
_SPOKEN_NAME_VALUE_PATTERN = (
    rf"(?:(?!\s+(?:und|and)\b)(?!\s+(?:(?:und|and)\s+)?(?:meine|my\s+)?{_SPOKEN_SENSITIVE_LABEL_PATTERN}\b)[^,;\n.?!]){{1,10000}}"
)
_BARE_SENSITIVE_STATUS_WORD_PATTERN = (
    r"(?:ist|is|war|was|are|were|missing|invalid|required|too|contains?|fehlt|leer|ung[üu]ltig|"
    r"muss|darf|soll|active|aktiv|gesetzt|set|needed|erforderlich|not|nicht|kein|keine|keinen|und|and)"
)
_BARE_SENSITIVE_WORD_VALUE_PATTERN = (
    rf"(?!(?:{_BARE_SENSITIVE_STATUS_WORD_PATTERN})\b)"
    r"[A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß'-]{1,20000}"
    rf"(?:\s+(?!(?:(?:und|and)\s+)?(?:meine|my\s+)?{_SPOKEN_SENSITIVE_LABEL_PATTERN}\b)"
    r"[A-ZÄÖÜa-zäöüß][A-ZÄÖÜa-zäöüß'-]{0,20000})*\b"
)
_VERBAL_TOKEN_RE = re.compile(
    r"(?i)\b(?:token|api[_-]?key|api\s+key|secret|apikey|bearer)\b\s+"
    r"(?:ist|is|lautet|heißt|heisst|heist|heisse|heise)\s*[:=]?\s+"
    rf"(?!(?:{_BARE_SENSITIVE_STATUS_WORD_PATTERN})\b)"
    rf"(?!(?:{_SPOKEN_SENSITIVE_LABEL_PATTERN})\s*[:=])"
    + _SPOKEN_SENSITIVE_VALUE_PATTERN
)
_BARE_TOKEN_RE = re.compile(
    r"(?i)\b(?:token|api[_-]?key|api\s+key|secret|apikey|bearer)\b\s+"
    r"(?!(?:ist|is|war|was|are|were|missing|invalid|required|too|contains?|muss|darf|soll)\b)"
    r"(?=[A-Za-z0-9_./+=-]*\d|[A-Za-z0-9_./+=-]*[+/=_-][A-Za-z0-9_./+=-]*)"
    r"[A-Za-z0-9_./+=-]{4,}"
)
_BARE_TOKEN_WORD_RE = re.compile(
    r"(?i)\b(?:token|api[_-]?key|api\s+key|secret|apikey|bearer)\b\s+"
    + _BARE_SENSITIVE_WORD_VALUE_PATTERN
)
_PASSWORD_RE = re.compile(
    r"(?i)\b(?:password|passwort|kennwort|passcode)\b\s*[:=]\s*"
    + _SECRET_VALUE_PATTERN
)
_VERBAL_PASSWORD_RE = re.compile(
    r"(?i)\b(?:password|passwort|kennwort|passcode)\b\s+"
    r"(?:ist|is|lautet|heißt|heisst|heist|heisse|heise)\s*[:=]?\s+"
    rf"(?!(?:{_BARE_SENSITIVE_STATUS_WORD_PATTERN})\b)"
    rf"(?!(?:{_SPOKEN_SENSITIVE_LABEL_PATTERN})\s*[:=])"
    + _SPOKEN_SENSITIVE_VALUE_PATTERN
)
_BARE_PASSWORD_WORD_RE = re.compile(
    r"(?i)\b(?:password|passwort|kennwort|passcode)\b\s+"
    + _BARE_SENSITIVE_WORD_VALUE_PATTERN
)
_ACCESS_TOKEN_RE = re.compile(r"(?i)\b(?:sk|sess|ghp|gho|xox[pb]-|hf|pat)[A-Za-z0-9_\-]{12,}\b")
# Kept as the ordering marker in _SENSITIVE_PATTERNS; matching uses the linear
# _apply_url_credential_redaction scanner below instead of regex finditer().
_URL_CRED_RE = re.compile(r"[a-z][a-z0-9+.-]{0,255}+://[^\s/@]+@")
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_LABELED_NAME_RE = re.compile(
    r"(?i)\b(?:name|voller\s+name|full\s+name)\b\s*[:=]\s*"
    + _SPOKEN_NAME_VALUE_PATTERN
)
_NAME_RE = re.compile(
    r"(?i)\b(?:mein\s+name\s+ist|ich\s+(?:heiße|heisse|heise|heisst|bin)|name\s+is|my\s+name\s+is)\s+"
    + _SPOKEN_NAME_VALUE_PATTERN
)
_BANK_DATA_RE = re.compile(
    r"(?i)\b(?:iban|kontodaten|kontonummer|kontonr|konto|bank\s+account|account\s+no|account\s+number)\b[^\n]{0,60}(\d{8,30})"
)
_ID_NUMBER_RE = re.compile(
    r"(?i)\b(?:ssn|social\s+security(?:\s+number)?|sozialversicherungsnummer|"
    r"steuer(?:identifikations)?nummer|tax\s+id)\b\s*[:=]?\s*[A-Z0-9][A-Z0-9 ._-]{5,24}"
)
_CUSTOMER_ID_RE = re.compile(
    r"(?i)\b(?:kundennummer|kundennr|kunden-nr|customer(?:\s+(?:id|number))?|"
    r"client\s+id|account\s+id)\b\s*[:=]?\s*[A-Z0-9][A-Z0-9._-]{3,}"
)
_ADDRESS_RE = re.compile(
    r"(?i)\b(?:adresse|anschrift|address)\b\s*[:=]?\s*[^\n,;]{0,80}?"
    r"(?:straße|strasse|street|st\.|road|rd\.|avenue|ave\.|weg|allee|platz)\s+"
    r"\d+[A-ZÄÖÜa-zäöüß0-9 ._-]*?"
    r"(?=$|[,;]|\s+(?:kundennummer|kundennr|kunden-nr|customer|client\s+id|account\s+id|ssn|"
    r"social\s+security|steuer|tax\s+id)\b)"
)

_SENSITIVE_PATTERNS = [
    (_TOKEN_RE, "[redacted token]"),
    (_VERBAL_TOKEN_RE, "[redacted token]"),
    (_BARE_TOKEN_RE, "[redacted token]"),
    (_BARE_TOKEN_WORD_RE, "[redacted token]"),
    (_PASSWORD_RE, "[redacted password]"),
    (_VERBAL_PASSWORD_RE, "[redacted password]"),
    (_BARE_PASSWORD_WORD_RE, "[redacted password]"),
    (_ACCESS_TOKEN_RE, "[redacted token]"),
    (_URL_CRED_RE, "[redacted credentials]"),
    (_LABELED_NAME_RE, "[redacted name]"),
    (_EMAIL_RE, "[redacted email]"),
    (_IBAN_RE, "[redacted iban]"),
    (_BANK_DATA_RE, "[redacted bank data]"),
    (_ADDRESS_RE, "[redacted address]"),
    (_ID_NUMBER_RE, "[redacted id]"),
    (_CUSTOMER_ID_RE, "[redacted customer id]"),
    (_PHONE_RE, "[redacted phone]"),
]
_REDACTION_PLACEHOLDER_RE = re.compile(
    "|".join(
        re.escape(placeholder)
        for placeholder in (
            "[redacted token]",
            "[redacted password]",
            "[redacted credentials]",
            "[redacted name]",
            "[redacted email]",
            "[redacted iban]",
            "[redacted bank data]",
            "[redacted address]",
            "[redacted id]",
            "[redacted customer id]",
            "[redacted phone]",
            "[redacted card]",
            "[redacted blacklist item]",
        )
    )
)

_MULTILINE_SENSITIVE_RE = re.compile(
    rf"(?i)\b(?P<label>{_SPOKEN_SENSITIVE_LABEL_PATTERN})\b"
    r"(?:\s*(?::|=)|\s+(?:ist|is|lautet|heißt|heisst|heist|heisse|heise)\s*[:=]?)"
    r"[^\S\n]*\n+[^\S\n]*"
    r"(?P<value>(?:(?!\n[^\S\n]*\n)"
    rf"(?!\n[^\S\n]*(?:(?:und|and)[^\S\n]+)?(?:meine|my[^\S\n]+)?{_SPOKEN_SENSITIVE_LABEL_PATTERN}\b)"
    r"(?!\n[^\S\n]*(?:und|and)\b)[\s\S]){1,20000})"
)

_SENSITIVE_BLACKLIST_LABELS = frozenset(
    {
        "token",
        "api_key",
        "api key",
        "secret",
        "apikey",
        "bearer",
        "password",
        "passwort",
        "kennwort",
        "passcode",
        "iban",
        "name",
        "adresse",
        "anschrift",
        "address",
        "kundennummer",
        "kundennr",
        "kunden-nr",
        "customer id",
        "customer number",
        "client id",
        "account id",
        "ssn",
        "tax id",
    }
)
_SENSITIVE_BLACKLIST_LABEL_GUARD = r"(?!\s*(?::|=)|\s+)"


@functools.lru_cache(maxsize=16)
def _compile_blacklist_pattern_cached(
    entries: tuple[str, ...], preserve_sensitive_labels: bool = False
) -> re.Pattern[str] | None:
    if not entries:
        return None
    escaped_entries = []
    for entry in entries:
        escaped_entry = re.escape(entry)
        if preserve_sensitive_labels and entry in _SENSITIVE_BLACKLIST_LABELS:
            escaped_entry += _SENSITIVE_BLACKLIST_LABEL_GUARD
        escaped_entries.append(escaped_entry)
    escaped = "|".join(escaped_entries)
    return re.compile(rf"(?i)(?<!\w)(?:{escaped})(?!\w)")


def _compile_blacklist_pattern(
    entries: list[str], *, preserve_sensitive_labels: bool = False
) -> re.Pattern[str] | None:
    normalized: list[str] = []
    total_bytes = 0
    seen: set[str] = set()
    for raw_entry in entries:
        if isinstance(raw_entry, bool) or not isinstance(raw_entry, str):
            continue
        entry = _normalize_blacklist_entry(raw_entry)
        entry = _normalize_blacklist_entry_for_match(entry)
        entry_key = entry.casefold()
        if not entry or entry_key in seen:
            continue
        try:
            entry_bytes = _safe_utf8_length(entry, field_name="blacklist entry")
        except ValueError:
            continue
        if normalized and total_bytes + entry_bytes > _MAX_BLACKLIST_PATTERN_BYTES:
            break
        normalized.append(entry)
        seen.add(entry_key)
        total_bytes += entry_bytes
        if len(normalized) >= _MAX_BLACKLIST_ENTRIES:
            break
    if not normalized:
        return None
    pattern_key = tuple(sorted(normalized, key=len, reverse=True))
    return _compile_blacklist_pattern_cached(pattern_key, preserve_sensitive_labels)


_DUPLICATE_SPACE_RE = re.compile(r"\s+")
_FORBIDDEN_CONTROL_CHAR_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")
_ESCAPED_FORBIDDEN_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)


def _contains_escaped_null(value: str) -> bool:
    return "\x00" in value or "\\x00" in value or "\\u0000" in value


def _contains_forbidden_control(value: str) -> bool:
    return bool(_FORBIDDEN_CONTROL_CHAR_RE.search(value) or _ESCAPED_FORBIDDEN_CONTROL_RE.search(value))


def _safe_utf8_length(value: str, *, field_name: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} contains invalid unicode") from exc


def _assert_security_text(value: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise ValueError("transcript must be text")
    if _contains_escaped_null(value):
        raise ValueError("transcript contains invalid null byte")
    if _contains_forbidden_control(value):
        raise ValueError("transcript contains invalid control character")
    if len(value) > _MAX_SECURITY_TEXT_CHARS or _safe_utf8_length(
        value, field_name="transcript"
    ) > _MAX_SECURITY_TEXT_CHARS:
        raise ValueError(f"transcript is too large (max {_MAX_SECURITY_TEXT_CHARS} bytes)")
    return value


def _is_match_ignorable_char(value: str) -> bool:
    return unicodedata.category(value) in _MATCH_IGNORE_CATEGORIES


def _normalize_for_matching(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    index_map: list[int] = []
    for source_index, char in enumerate(value):
        normalized_block = unicodedata.normalize("NFKD", char)
        for normalized_char in normalized_block.casefold():
            if _is_match_ignorable_char(normalized_char):
                continue
            normalized.append(normalized_char)
            index_map.append(source_index)
    return "".join(normalized), index_map


def _normalize_blacklist_entry_for_match(value: str) -> str:
    normalized, _ = _normalize_for_matching(value)
    return normalized


def _sub_with_normalized_projection(
    text: str,
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str | None],
) -> tuple[str, int]:
    normalized_text, index_map = _normalize_for_matching(text)
    if not normalized_text:
        return text, 0

    redactions = 0
    pieces: list[str] = []
    cursor = 0

    for match in pattern.finditer(normalized_text):
        if match.start() >= match.end():
            continue
        if match.start() >= len(index_map) or match.end() - 1 >= len(index_map):
            continue

        original_start = index_map[match.start()]
        original_end = index_map[match.end() - 1] + 1

        if original_end <= cursor:
            continue
        if original_start < cursor:
            original_start = cursor
        while original_start > cursor and _is_match_ignorable_char(text[original_start - 1]):
            original_start -= 1
        while original_end < len(text) and _is_match_ignorable_char(text[original_end]):
            original_end += 1

        replacement_value = replacement(match) if callable(replacement) else replacement
        if replacement_value is None:
            continue
        pieces.append(text[cursor:original_start])
        pieces.append(replacement_value)
        cursor = original_end
        redactions += 1

    if redactions == 0:
        return text, 0

    pieces.append(text[cursor:])
    return "".join(pieces), redactions


_URL_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789+.-")
_URL_SCHEME_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyz")


def _url_credential_ranges(normalized_text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    search_start = 0
    while True:
        delimiter_start = normalized_text.find("://", search_start)
        if delimiter_start < 0:
            return ranges

        scheme_boundary = normalized_text.rfind(":", 0, delimiter_start) + 1
        scheme_start = delimiter_start - 1
        while scheme_start >= scheme_boundary and normalized_text[scheme_start] in _URL_SCHEME_CHARS:
            scheme_start -= 1
        scheme_start += 1
        while scheme_start < delimiter_start and normalized_text[scheme_start] not in _URL_SCHEME_LETTERS:
            scheme_start += 1
        if scheme_start >= delimiter_start:
            search_start = delimiter_start + 3
            continue

        userinfo_start = delimiter_start + 3
        at_sign = normalized_text.find("@", userinfo_start)
        if at_sign <= userinfo_start:
            search_start = delimiter_start + 3
            continue
        userinfo = normalized_text[userinfo_start:at_sign]
        if any(char.isspace() or char == "/" for char in userinfo):
            search_start = delimiter_start + 3
            continue

        ranges.append((scheme_start, at_sign + 1))
        search_start = at_sign + 1


def _apply_url_credential_redaction(text: str) -> tuple[str, int]:
    normalized_text, index_map = _normalize_for_matching(text)
    ranges = _url_credential_ranges(normalized_text)
    if not ranges:
        return text, 0

    pieces: list[str] = []
    cursor = 0
    redactions = 0
    for normalized_start, normalized_end in ranges:
        if normalized_start >= normalized_end:
            continue
        if normalized_start >= len(index_map) or normalized_end - 1 >= len(index_map):
            continue

        original_start = index_map[normalized_start]
        original_end = index_map[normalized_end - 1] + 1
        if original_end <= cursor:
            continue
        if original_start < cursor:
            original_start = cursor
        while original_start > cursor and _is_match_ignorable_char(text[original_start - 1]):
            original_start -= 1
        while original_end < len(text) and _is_match_ignorable_char(text[original_end]):
            original_end += 1

        pieces.append(text[cursor:original_start])
        pieces.append("[redacted credentials]")
        cursor = original_end
        redactions += 1

    if redactions == 0:
        return text, 0
    pieces.append(text[cursor:])
    return "".join(pieces), redactions


@dataclass(frozen=True)
class SecurityParserResult:
    text: str
    added_blacklist: list[str]
    show_blacklist: bool


def _normalize_blacklist_entry(value: str) -> str:
    if _contains_escaped_null(value) or _contains_forbidden_control(value):
        return ""
    entry = value.strip().strip("\"'")
    if not entry:
        return ""
    entry = entry.strip(" .,:;!?()[]{}")
    entry = _DUPLICATE_SPACE_RE.sub(" ", entry)
    if len(entry) > _MAX_BLACKLIST_ENTRY_CHARS:
        entry = entry[:_MAX_BLACKLIST_ENTRY_CHARS].strip()
    return entry


def _safe_blacklist_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise RuntimeError("blacklist path must be a path")
    path = path.expanduser()
    assert_no_symlink_ancestors(path, field_name="blacklist file")
    return path


def _read_blacklist(path: Path, *, strict: bool = False) -> list[str]:
    try:
        path = _safe_blacklist_path(path)
    except (MemoryError, RecursionError, RuntimeError) as exc:
        raise ValueError("blacklist file path is not safe") from exc
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ValueError("failed to inspect blacklist file") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("blacklist file is not a regular file")
    try:
        if file_stat.st_size > _MAX_BLACKLIST_FILE_BYTES:
            raise ValueError("blacklist file is too large")
        text = read_text_without_following_symlinks(
            path,
            field_name="blacklist file",
            max_bytes=_MAX_BLACKLIST_FILE_BYTES,
            require_private_mode=True,
            expected_stat=file_stat,
        )
    except (OSError, UnicodeDecodeError) as exc:
        if "too large" in str(exc):
            raise ValueError("blacklist file is too large") from exc
        raise ValueError("failed to read blacklist file") from exc
    values: list[str] = []
    value_keys: set[str] = set()
    for line in text.splitlines():
        entry = _normalize_blacklist_entry(line)
        if not entry:
            continue
        entry_key = entry.casefold()
        if len(values) >= _MAX_BLACKLIST_ENTRIES:
            if strict and entry_key not in value_keys:
                raise ValueError("blacklist file exceeds maximum entries")
            break
        if entry_key not in value_keys:
            values.append(entry)
            value_keys.add(entry_key)
    return values


def _write_blacklist(path: Path, entries: list[str]) -> None:
    try:
        path = _safe_blacklist_path(path)
    except RuntimeError as exc:
        raise ValueError("blacklist file path is not safe") from exc
    rendered = "\n".join(entries) + "\n" if entries else ""
    try:
        write_text_atomically_without_following_symlinks(path, rendered, field_name="blacklist file")
    except (OSError, RuntimeError) as exc:
        raise ValueError("failed to write blacklist file") from exc


def _acquire_blacklist_lock(path: Path) -> int:
    if not isinstance(path, Path):
        raise ValueError("blacklist file path is not safe")
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        assert_no_symlink_ancestors(lock_path, field_name="blacklist lock file")
    except (MemoryError, RecursionError, RuntimeError) as exc:
        raise ValueError("blacklist lock file path is not safe") from exc
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise ValueError("secure blacklist lock open is not supported on this platform")
    try:
        parent_fd = ensure_directory_without_following_symlinks(lock_path.parent, field_name="blacklist lock directory")
    except (MemoryError, RecursionError, OSError, RuntimeError) as exc:
        raise ValueError("blacklist lock file path is not safe") from exc
    try:
        fd = os.open(lock_path.name, os.O_RDWR | os.O_CREAT | nofollow_flag, 0o600, dir_fd=parent_fd)
    except (MemoryError, RecursionError) as exc:
        error = ValueError("failed to open blacklist lock file")
        try:
            os.close(parent_fd)
        except BaseException as cleanup_error:
            _note_lock_cleanup_failure(error, cleanup_error)
        raise error from exc
    except OSError as exc:
        error = ValueError("failed to open blacklist lock file")
        try:
            os.close(parent_fd)
        except BaseException as cleanup_error:
            _note_lock_cleanup_failure(error, cleanup_error)
        raise error from exc
    except BaseException as exc:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            _note_lock_cleanup_failure(exc, cleanup_error)
        raise
    primary_error: BaseException | None = None
    try:
        assert_fd_is_regular_private_file(fd, field_name="blacklist lock file", require_private_mode=True)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert_fd_is_regular_private_file(fd, field_name="blacklist lock file", require_private_mode=True)
    except (MemoryError, RecursionError) as exc:
        error = ValueError("failed to lock blacklist file")
        primary_error = error
        try:
            os.close(fd)
        except BaseException as cleanup_error:
            _note_lock_cleanup_failure(error, cleanup_error)
        raise error from exc
    except (OSError, RuntimeError) as exc:
        error = ValueError("failed to lock blacklist file")
        primary_error = error
        try:
            os.close(fd)
        except BaseException as cleanup_error:
            _note_lock_cleanup_failure(error, cleanup_error)
        raise error from exc
    except BaseException as exc:
        primary_error = exc
        try:
            os.close(fd)
        except BaseException as cleanup_error:
            _note_lock_cleanup_failure(exc, cleanup_error)
        raise
    finally:
        try:
            os.close(parent_fd)
        except (MemoryError, RecursionError) as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                try:
                    os.close(fd)
                except BaseException as fd_cleanup_error:
                    _note_lock_cleanup_failure(cleanup_error, fd_cleanup_error)
                raise OSError("blacklist lock directory could not be closed") from cleanup_error
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                pass
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                try:
                    os.close(fd)
                except BaseException as fd_cleanup_error:
                    _note_lock_cleanup_failure(cleanup_error, fd_cleanup_error)
                raise
    return fd


def _release_blacklist_lock(fd: int) -> None:
    primary_error: BaseException | None = None
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (MemoryError, RecursionError) as exc:
        primary_error = OSError("blacklist lock could not be released")
        raise primary_error from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
        except (MemoryError, RecursionError) as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                raise OSError("blacklist lock could not be closed") from cleanup_error
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def _luhn_valid(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    return total % 10 == 0


def _mask_cards(text: str) -> tuple[str, int]:
    redactions = 0

    def _mask(pattern_match: re.Match[str]) -> str:
        nonlocal redactions
        candidate = pattern_match.group(0)
        digits = "".join(char for char in candidate if char.isdigit())
        if _luhn_valid(digits):
            redactions += 1
            return "[redacted card]"
        return candidate

    clean = _CREDIT_CARD_RE.sub(_mask, text)
    return clean, redactions


def _placeholder_for_sensitive_label(label: str) -> str:
    normalized = re.sub(r"[\s_-]+", " ", label.casefold()).strip()
    if normalized in {"password", "passwort", "kennwort", "passcode"}:
        return "[redacted password]"
    if normalized == "iban":
        return "[redacted iban]"
    if normalized in {"name", "adresse", "anschrift", "address"}:
        if normalized == "name":
            return "[redacted name]"
        return "[redacted address]"
    if normalized in {"kundennummer", "kundennr", "kunden nr"}:
        return "[redacted customer id]"
    if normalized in {"ssn", "tax id"}:
        return "[redacted id]"
    return "[redacted token]"


def _apply_multiline_sensitive_redaction(text: str) -> tuple[str, int]:
    return _sub_with_normalized_projection(
        text,
        _MULTILINE_SENSITIVE_RE,
        lambda match: _placeholder_for_sensitive_label(match.group("label")),
    )


def _is_valid_card_digits(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _apply_normalized_card_redaction(text: str) -> tuple[str, int]:
    def _mask_if_valid_card(match: re.Match[str]) -> str | None:
        if _is_valid_card_digits(match.group(0)):
            return "[redacted card]"
        return None

    return _sub_with_normalized_projection(text, _NORMALIZED_CARD_CANDIDATE_RE, _mask_if_valid_card)


def _apply_name_redaction(text: str) -> tuple[str, int]:
    return _sub_with_normalized_projection(text, _NAME_RE, "[redacted name]")


def _apply_blacklist_around_redaction_placeholders(
    text: str, pattern: re.Pattern[str]
) -> tuple[str, int]:
    pieces: list[str] = []
    cursor = 0
    redactions = 0
    for placeholder_match in _REDACTION_PLACEHOLDER_RE.finditer(text):
        segment, count = _sub_with_normalized_projection(
            text[cursor:placeholder_match.start()],
            pattern,
            "[redacted blacklist item]",
        )
        pieces.append(segment)
        pieces.append(placeholder_match.group(0))
        redactions += count
        cursor = placeholder_match.end()
    segment, count = _sub_with_normalized_projection(
        text[cursor:],
        pattern,
        "[redacted blacklist item]",
    )
    pieces.append(segment)
    redactions += count
    return "".join(pieces), redactions


def parse_security_directives(text: str) -> SecurityParserResult:
    text = _assert_security_text(text)
    lines = text.splitlines()
    kept: list[str] = []
    added: list[str] = []
    added_keys: set[str] = set()
    show_blacklist = False

    for line in lines:
        match_add = _BLACKLIST_ADD_RE.match(line)
        if match_add:
            entry = _normalize_blacklist_entry(match_add.group(1))
            entry_key = entry.casefold()
            if entry and entry_key not in added_keys:
                added.append(entry)
                added_keys.add(entry_key)
            continue
        if _BLACKLIST_SHOW_RE.match(line):
            show_blacklist = True
            continue
        kept.append(line)

    return SecurityParserResult(text="\n".join(kept).strip(), added_blacklist=added, show_blacklist=show_blacklist)


def apply_security_mode(text: str, blacklist: list[str]) -> tuple[str, int]:
    text = _assert_security_text(text)

    clean = text
    redactions = 0
    blacklist_pattern = _compile_blacklist_pattern(blacklist, preserve_sensitive_labels=True)
    if blacklist_pattern is not None:
        clean, count = _apply_blacklist_around_redaction_placeholders(clean, blacklist_pattern)
        redactions += count
    clean, count = _apply_multiline_sensitive_redaction(clean)
    redactions += count
    clean, count = _mask_cards(clean)
    redactions += count
    clean, count = _apply_normalized_card_redaction(clean)
    redactions += count
    for pattern, placeholder in _SENSITIVE_PATTERNS:
        if pattern is _URL_CRED_RE:
            clean, count = _apply_url_credential_redaction(clean)
        else:
            clean, count = _sub_with_normalized_projection(clean, pattern, placeholder)
        if count:
            redactions += count
    clean, count = _apply_name_redaction(clean)
    redactions += count
    final_blacklist_pattern = _compile_blacklist_pattern(blacklist)
    if final_blacklist_pattern is not None:
        clean, count = _apply_blacklist_around_redaction_placeholders(clean, final_blacklist_pattern)
        redactions += count

    return clean.strip(), redactions


def apply_blacklist_mode(text: str, blacklist: list[str]) -> tuple[str, int]:
    text = _assert_security_text(text)
    clean = text
    pattern = _compile_blacklist_pattern(blacklist)
    if pattern is None:
        return clean.strip(), 0
    clean, count = _apply_blacklist_around_redaction_placeholders(clean, pattern)
    return clean.strip(), count


def load_blacklist_file(path: Path, *, strict: bool = False) -> list[str]:
    return _read_blacklist(path, strict=strict)


def update_blacklist_file(path: Path, added: list[str]) -> list[str]:
    try:
        path = _safe_blacklist_path(path)
    except RuntimeError as exc:
        raise ValueError("blacklist file path is not safe") from exc
    lock_fd = _acquire_blacklist_lock(path)
    primary_error: BaseException | None = None
    try:
        existing = _read_blacklist(path, strict=True)
        existing_keys = {entry.casefold() for entry in existing}
        changed = False
        for raw_entry in added:
            if isinstance(raw_entry, bool) or not isinstance(raw_entry, str):
                continue
            entry = _normalize_blacklist_entry(raw_entry)
            if not entry:
                continue
            entry_key = entry.casefold()
            if entry_key in existing_keys:
                continue
            if len(existing) >= _MAX_BLACKLIST_ENTRIES:
                raise ValueError("blacklist file exceeds maximum entries")
            existing.append(entry)
            existing_keys.add(entry_key)
            changed = True
        if len(existing) > _MAX_BLACKLIST_ENTRIES:
            existing = existing[:_MAX_BLACKLIST_ENTRIES]
            changed = True
        if changed:
            _write_blacklist(path, existing)
        return existing
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            _release_blacklist_lock(lock_fd)
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_lock_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
