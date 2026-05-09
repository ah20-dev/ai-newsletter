from typing import List

from validator import SECTION_1_HEADER, SECTION_2_HEADER


TELEGRAM_MAX_CHARS = 4096


def _normalize_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""

    if stripped in (SECTION_1_HEADER, SECTION_2_HEADER):
        return stripped

    if stripped.startswith("• "):
        stripped = "- " + stripped[2:].strip()
    elif stripped.startswith("* "):
        stripped = "- " + stripped[2:].strip()
    elif stripped.startswith("-"):
        stripped = "- " + stripped.lstrip("- ").strip()

    # Strip markdown bold **...** to plain text for Telegram digest
    if stripped.startswith("- "):
        rest = stripped[2:].replace("**", "")
        stripped = "- " + rest

    return stripped


def normalize_newsletter(text: str) -> str:
    normalized_lines = []
    for line in text.splitlines():
        fixed = _normalize_line(line)
        if fixed:
            normalized_lines.append(fixed)
    return "\n".join(normalized_lines).strip()


def split_for_telegram(text: str, max_chars: int = TELEGRAM_MAX_CHARS, max_parts: int = 2) -> List[str]:
    if len(text) <= max_chars:
        return [text]

    section2_index = text.find(f"\n{SECTION_2_HEADER}")
    if section2_index != -1:
        first = text[:section2_index].strip()
        second = text[section2_index + 1 :].strip()
        if first and second and len(first) <= max_chars and len(second) <= max_chars:
            return [first, second]

    parts: List[str] = []
    remaining = text
    while remaining and len(parts) < max_parts:
        if len(remaining) <= max_chars:
            parts.append(remaining.strip())
            remaining = ""
            break
        split_idx = remaining.rfind("\n", 0, max_chars)
        if split_idx <= 0:
            split_idx = max_chars
        parts.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()

    if remaining:
        raise ValueError("Formatted output exceeds maximum allowed split parts.")
    if any(len(part) > max_chars for part in parts):
        raise ValueError("A message part exceeds Telegram 4096 character limit.")
    return [part for part in parts if part]
