from dataclasses import dataclass
from typing import List


SECTION_1_HEADER = "SECTION 1: Global News Brief"
SECTION_2_HEADER = "SECTION 2: Markets & Stocks Brief"


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    section1_count: int
    section2_count: int


def _extract_section_lines(text: str, header: str, next_header: str | None) -> List[str] | None:
    start = text.find(header)
    if start == -1:
        return None

    content_start = start + len(header)
    if next_header:
        end = text.find(next_header, content_start)
        if end == -1:
            return None
        section_text = text[content_start:end]
    else:
        section_text = text[content_start:]

    return [line.rstrip() for line in section_text.strip().splitlines() if line.strip()]


def validate_newsletter(text: str) -> ValidationResult:
    errors: List[str] = []
    normalized = text.strip()

    if SECTION_1_HEADER not in normalized:
        errors.append("Missing section 1 header.")
    if SECTION_2_HEADER not in normalized:
        errors.append("Missing section 2 header.")
    if errors:
        return ValidationResult(False, errors, 0, 0)

    if normalized.find(SECTION_1_HEADER) > normalized.find(SECTION_2_HEADER):
        errors.append("Section headers out of order.")

    section1_lines = _extract_section_lines(normalized, SECTION_1_HEADER, SECTION_2_HEADER)
    section2_lines = _extract_section_lines(normalized, SECTION_2_HEADER, None)

    if section1_lines is None:
        errors.append("Unable to parse section 1.")
        section1_lines = []
    if section2_lines is None:
        errors.append("Unable to parse section 2.")
        section2_lines = []

    section1_bullets = [line for line in section1_lines if line.startswith("- ")]
    section2_bullets = [line for line in section2_lines if line.startswith("- ")]

    # Enforce bullet-only content within both sections.
    for line in section1_lines:
        if not line.startswith("- "):
            errors.append("Section 1 contains non-bullet text.")
            break
    for line in section2_lines:
        if not line.startswith("- "):
            errors.append("Section 2 contains non-bullet text.")
            break

    if not (5 <= len(section1_bullets) <= 12):
        errors.append("Section 1 bullet count must be between 5 and 12.")
    if not (5 <= len(section2_bullets) <= 15):
        errors.append("Section 2 bullet count must be between 5 and 15.")

    for bullet in section2_bullets:
        has_ticker = "$" in bullet or "(" in bullet  # accept $TICKER or "Name (TICKER)"
        has_pct = "%" in bullet
        if not has_ticker or not has_pct:
            errors.append(
                "Section 2 bullets must include a ticker ($TICKER or (TICKER)) and % move."
            )
            break

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        section1_count=len(section1_bullets),
        section2_count=len(section2_bullets),
    )


def build_refined_prompt(base_prompt: str, errors: List[str]) -> str:
    guidance = "\n".join(f"- {err}" for err in errors)
    return (
        f"{base_prompt}\n\n"
        "The previous response failed validation. Fix all issues below and return only the corrected newsletter:\n"
        f"{guidance}\n"
        "Hard rules: bullet-only lines using '- ', exact two section headers, no extra text."
    )
