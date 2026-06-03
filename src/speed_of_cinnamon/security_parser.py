from __future__ import annotations

import functools
import fcntl
import os
import tempfile
import re
from dataclasses import dataclass
from pathlib import Path
from .path_safety import assert_no_symlink_ancestors, read_text_without_following_symlinks


_MAX_BLACKLIST_ENTRY_CHARS = 120
_MAX_BLACKLIST_ENTRIES = 1_000
_MAX_BLACKLIST_FILE_BYTES = 1_000_000
_MAX_BLACKLIST_PATTERN_BYTES = _MAX_BLACKLIST_ENTRY_CHARS * _MAX_BLACKLIST_ENTRIES


_BLACKLIST_ADD_RE = re.compile(
    r"(?im)^\s*(?:blacklisteintrag|blacklist\s*eintrag)\b[\s:,-]*(.+?)\s*$",
)
_BLACKLIST_SHOW_RE = re.compile(
    r"(?im)^\s*(?:(?:bitte\s+)?(?:mir\s+)?(?:die\s+)?"
    r"(?:blacklist|blackliste|sperrliste)\b[^\n]*\b(?:anzeigen|anzeige|öffnen|open|show|zeigen|zeige)\b[^\n]*"
    r"|(?:bitte\s+)?(?:mir\s+)?(?:zeige|zeig|show|open|öffne)\s+[^\n]*"
    r"\b(?:blacklist|blackliste|sperrliste)\b[^\n]*)\s*$",
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IBAN_RE = re.compile(r"\b[A-Za-z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:(?<!\d)\+?\d[\d\s().-]{7,20}\d(?!\d))")
_TOKEN_RE = re.compile(r"(?i)\b(?:token|api[_-]?key|secret|password|apikey|bearer)\b\s*[:=]\s*[^,;\s]+")
_PASSWORD_RE = re.compile(r"(?i)\b(?:password|passwort|kennwort|passcode)\b\s*[:=]\s*[^,;\s]+")
_ACCESS_TOKEN_RE = re.compile(r"(?i)\b(?:sk|sess|ghp|gho|xox[pb]-|hf|pat)[A-Za-z0-9_\-]{12,}\b")
_URL_CRED_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_NAME_RE = re.compile(
    r"(?i)\b(?:mein\s+name\s+ist|ich\s+hei[ßss]e|name\s+is|my\s+name\s+is)\s+"
    r"([A-ZÄÖÜa-zäöüß]+(?:\s+[A-ZÄÖÜa-zäöüß]+){1,3})"
)
_BANK_DATA_RE = re.compile(
    r"(?i)\b(?:iban|kontodaten|kontonummer|kontonr|konto|bank\s+account|account\s+no|account\s+number)\b[^\n]{0,60}(\d{8,30})"
)

_SENSITIVE_PATTERNS = [
    (_TOKEN_RE, "[redacted token]"),
    (_PASSWORD_RE, "[redacted password]"),
    (_ACCESS_TOKEN_RE, "[redacted token]"),
    (_URL_CRED_RE, "[redacted credentials]"),
    (_EMAIL_RE, "[redacted email]"),
    (_IBAN_RE, "[redacted iban]"),
    (_BANK_DATA_RE, "[redacted bank data]"),
    (_PHONE_RE, "[redacted phone]"),
]


@functools.lru_cache(maxsize=16)
def _compile_blacklist_pattern_cached(entries: tuple[str, ...]) -> re.Pattern[str] | None:
    if not entries:
        return None
    escaped = "|".join(re.escape(entry) for entry in entries)
    return re.compile(rf"(?i)(?<!\w)(?:{escaped})(?!\w)")


def _compile_blacklist_pattern(entries: list[str]) -> re.Pattern[str] | None:
    normalized: list[str] = []
    total_bytes = 0
    seen: set[str] = set()
    for raw_entry in entries:
        if isinstance(raw_entry, bool) or not isinstance(raw_entry, str):
            continue
        entry = _normalize_blacklist_entry(raw_entry)
        if not entry or entry in seen:
            continue
        entry_bytes = len(entry.encode("utf-8"))
        if normalized and total_bytes + entry_bytes > _MAX_BLACKLIST_PATTERN_BYTES:
            break
        normalized.append(entry)
        seen.add(entry)
        total_bytes += entry_bytes
        if len(normalized) >= _MAX_BLACKLIST_ENTRIES:
            break
    if not normalized:
        return None
    pattern_key = tuple(sorted(normalized, key=len, reverse=True))
    return _compile_blacklist_pattern_cached(pattern_key)


_DUPLICATE_SPACE_RE = re.compile(r"\s+")


def _contains_escaped_null(value: str) -> bool:
    return "\x00" in value or "\\x00" in value or "\\u0000" in value


@dataclass(frozen=True)
class SecurityParserResult:
    text: str
    added_blacklist: list[str]
    show_blacklist: bool


def _normalize_blacklist_entry(value: str) -> str:
    if _contains_escaped_null(value):
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


def _read_blacklist(path: Path) -> list[str]:
    try:
        path = _safe_blacklist_path(path)
    except RuntimeError:
        return []
    if not path.is_file():
        return []
    try:
        if path.stat().st_size > _MAX_BLACKLIST_FILE_BYTES:
            return []
        text = read_text_without_following_symlinks(path, field_name="blacklist file")
    except (OSError, UnicodeDecodeError):
        return []
    values: list[str] = []
    for line in text.splitlines():
        entry = _normalize_blacklist_entry(line)
        if not entry:
            continue
        if entry not in values:
            values.append(entry)
        if len(values) >= _MAX_BLACKLIST_ENTRIES:
            break
    return values


def _write_blacklist(path: Path, entries: list[str]) -> None:
    try:
        path = _safe_blacklist_path(path)
    except RuntimeError as exc:
        raise ValueError("blacklist file path is not safe") from exc
    if not entries:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}.", suffix=".tmp", dir=str(path.parent))
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    try:
                        os.fchmod(handle.fileno(), 0o600)
                    except OSError:
                        pass
                assert_no_symlink_ancestors(path, field_name="blacklist file")
                os.replace(temp_path, path)
            except (OSError, RuntimeError):
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
            handle.write("\n".join(entries) + "\n")
        assert_no_symlink_ancestors(path, field_name="blacklist file")
        os.replace(temp_path, path)
    except (OSError, RuntimeError) as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError("failed to write blacklist file") from exc
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _acquire_blacklist_lock(path: Path) -> int:
    if not isinstance(path, Path):
        raise ValueError("blacklist file path is not safe")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        assert_no_symlink_ancestors(lock_path, field_name="blacklist lock file")
    except RuntimeError as exc:
        raise ValueError("blacklist lock file path is not safe") from exc
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise ValueError("secure blacklist lock open is not supported on this platform")
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | nofollow_flag, 0o600)
    except OSError as exc:
        raise ValueError("failed to open blacklist lock file") from exc
    try:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as exc:
        os.close(fd)
        raise ValueError("failed to lock blacklist file") from exc
    return fd


def _release_blacklist_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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


def parse_security_directives(text: str) -> SecurityParserResult:
    if not isinstance(text, str) or isinstance(text, bool):
        raise ValueError("transcript must be text")
    lines = text.splitlines()
    kept: list[str] = []
    added: list[str] = []
    show_blacklist = False
    saw_directive = False

    for line in lines:
        match_add = _BLACKLIST_ADD_RE.match(line)
        if match_add:
            saw_directive = True
            entry = _normalize_blacklist_entry(match_add.group(1))
            if entry and entry not in added:
                added.append(entry)
            continue
        if _BLACKLIST_SHOW_RE.match(line):
            saw_directive = True
            show_blacklist = True
            continue
        kept.append(line)

    if saw_directive and any(line.strip() for line in kept):
        return SecurityParserResult(text="\n".join(kept).strip(), added_blacklist=[], show_blacklist=False)
    return SecurityParserResult(text="\n".join(kept).strip(), added_blacklist=added, show_blacklist=show_blacklist)


def _apply_name_redaction(text: str) -> tuple[str, int]:
    count = 0

    def _mask(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return match.group(0)[:1] + "[redacted name]"

    return _NAME_RE.sub(_mask, text), count


def apply_security_mode(text: str, blacklist: list[str]) -> tuple[str, int]:
    if _contains_escaped_null(text):
        raise ValueError("transcript contains invalid null byte")

    clean = text
    redactions = 0
    clean, count = _mask_cards(clean)
    redactions += count
    for pattern, placeholder in _SENSITIVE_PATTERNS:
        clean, count = pattern.subn(placeholder, clean)
        if count:
            redactions += count
    clean, count = _apply_name_redaction(clean)
    redactions += count

    blacklist_pattern = _compile_blacklist_pattern(blacklist)
    if blacklist_pattern is not None:
        clean, count = blacklist_pattern.subn("[redacted blacklist item]", clean)
        redactions += count

    return clean.strip(), redactions


def apply_blacklist_mode(text: str, blacklist: list[str]) -> tuple[str, int]:
    if _contains_escaped_null(text):
        raise ValueError("transcript contains invalid null byte")
    clean = text
    pattern = _compile_blacklist_pattern(blacklist)
    if pattern is None:
        return clean.strip(), 0
    clean, count = pattern.subn("[redacted blacklist item]", clean)
    return clean.strip(), count


def load_blacklist_file(path: Path) -> list[str]:
    return _read_blacklist(path)


def update_blacklist_file(path: Path, added: list[str]) -> list[str]:
    try:
        path = _safe_blacklist_path(path)
    except RuntimeError as exc:
        raise ValueError("blacklist file path is not safe") from exc
    lock_fd = _acquire_blacklist_lock(path)
    try:
        existing = _read_blacklist(path)
        changed = False
        for entry in added:
            if entry not in existing:
                existing.append(entry)
                changed = True
        if len(existing) > _MAX_BLACKLIST_ENTRIES:
            existing = existing[:_MAX_BLACKLIST_ENTRIES]
            changed = True
        if changed:
            _write_blacklist(path, existing)
        return existing
    finally:
        _release_blacklist_lock(lock_fd)
