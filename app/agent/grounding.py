"""Numeric grounding checks: every figure in a final answer must be traceable
to a tool result (or to the user's own question)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER_RE = re.compile(
    r"(?P<sign>-)?(?P<currency>₹|\$)?(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?P<gap>\s*)"
    r"(?P<suffix>%|k|m|b|million|billion|thousand|crore|cr|lakh|lakhs|l)?"
    r"(?![a-z])",
    re.IGNORECASE,
)
DATE_PATTERNS = [
    re.compile(r"\b(?:19|20)\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?\b"),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\bQ[1-4]\s+\d{4}\b", re.IGNORECASE),
]
SUFFIX = {
    "k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
    "million": 1_000_000, "billion": 1_000_000_000, "thousand": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "l": 100_000,
    "crore": 10_000_000, "cr": 10_000_000,
}


@dataclass
class GroundingResult:
    ok: bool
    checked: int
    ungrounded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "verified" if self.ok else "failed",
            "checked_numbers": self.checked,
            "ungrounded_numbers": self.ungrounded[:10],
        }


def parse_token(number: str, suffix: str | None, gap: str = "") -> float | None:
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        key = suffix.lower()
        # "12 months" is a count, not 12 million. Only treat k/m/b/l as a
        # multiplier when the suffix is attached to the number ("12m").
        if key in {"k", "m", "b", "l"} and gap:
            return value
        value *= SUFFIX.get(key, 1)
    return value


def add_variants(target: set[float], value: float) -> None:
    if value != value or abs(value) == float("inf"):
        return
    target.add(round(float(value), 6))
    target.add(round(float(value), 2))
    target.add(round(float(value), 1))
    target.add(round(float(value)))
    target.add(abs(round(float(value), 2)))
    target.add(round(float(value) * 100, 4))
    target.add(round(float(value) / 100, 6))


def collect_numbers(value: Any, target: set[float] | None = None) -> set[float]:
    target = target if target is not None else set()
    if isinstance(value, bool) or value is None:
        return target
    if isinstance(value, (int, float)):
        add_variants(target, float(value))
        return target
    if isinstance(value, str):
        for match in NUMBER_RE.finditer(value):
            parsed = parse_token(match.group("number"), match.group("suffix"), match.group("gap") or "")
            if parsed is not None:
                if match.group("sign") == "-":
                    parsed = -parsed
                add_variants(target, parsed)
        return target
    if isinstance(value, dict):
        for item in value.values():
            collect_numbers(item, target)
        return target
    if isinstance(value, (list, tuple, set)):
        for item in value:
            collect_numbers(item, target)
        return target
    return target


def _strip_dates(text: str) -> str:
    for pattern in DATE_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def extract_answer_numbers(text: str) -> list[tuple[str, float, str]]:
    cleaned = _strip_dates(text)
    found: list[tuple[str, float, str]] = []
    for match in NUMBER_RE.finditer(cleaned):
        raw = match.group(0).strip()
        number = match.group("number")
        suffix = match.group("suffix")
        currency = match.group("currency")
        value = parse_token(number, suffix, match.group("gap") or "")
        if value is None:
            continue
        if match.group("sign") == "-":
            value = -value
        kind = "percent" if (suffix or "").lower() == "%" else ("currency" if currency else "plain")
        tail = cleaned[match.end():match.end() + 14].lstrip().lower()
        tail = re.sub(r"^[^a-z]+", "", tail)
        # Years are labels, not data claims.
        if kind == "plain" and not suffix and 2000 <= value <= 2100 and float(value).is_integer():
            continue
        # Period / list labels such as "last 12 months" or "90+ days" are not data claims.
        if kind == "plain" and not suffix and tail.startswith(("month", "day")):
            continue
        # Small integers are usually list counts ("3 drivers", "5 products") rather than data.
        if kind == "plain" and not suffix and abs(value) <= 24 and float(value).is_integer():
            continue
        found.append((raw, value, kind))
    return found


def validate_answer(answer: str, evidence: Any, question: str = "") -> GroundingResult:
    allowed = collect_numbers(evidence)
    collect_numbers(question, allowed)
    checked = 0
    ungrounded: list[str] = []
    for raw, value, kind in extract_answer_numbers(answer or ""):
        checked += 1
        match = False
        for candidate in allowed:
            if abs(candidate) < 1e-9:
                continue
            if kind == "percent":
                tolerance = max(0.2, abs(candidate) * 0.01)
            elif kind == "currency":
                tolerance = max(1.0, abs(candidate) * 0.005)
            else:
                tolerance = max(0.51, abs(candidate) * 0.005)
            if abs(abs(value) - abs(candidate)) <= tolerance:
                match = True
                break
        if not match:
            ungrounded.append(raw)
    return GroundingResult(ok=not ungrounded, checked=checked, ungrounded=ungrounded)
