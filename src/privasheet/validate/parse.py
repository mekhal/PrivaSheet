"""Parse source values and validate canonical review values."""

import re
from datetime import date
from decimal import Decimal

_CURRENCY = r"(?:[$€£¥฿]|[A-Z]{3})"
_CURRENCY_START = re.compile(rf"^{_CURRENCY}\s*")
_CURRENCY_END = re.compile(rf"\s*{_CURRENCY}$")
_NUMBER = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,4})?")
_PLAIN = re.compile(r"-?[0-9]{1,15}(?:\.[0-9]{1,4})?")
_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TOKENS = re.compile(r"YYYY|MMM|DD|MM|YY")
_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
        1,
    )
}
_PARTS = {
    "DD": ("day", r"[0-9]{2}"),
    "MM": ("month", r"[0-9]{2}"),
    "MMM": ("month", r"(?i:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"),
    "YYYY": ("year", r"[0-9]{4}"),
    "YY": ("year", r"[0-9]{2}"),
}


def parse_decimal(text: str) -> str | None:
    """Apply section 6's currency, grouping, sign and precision grammar."""
    text = text.strip()
    marker = _CURRENCY_START.match(text)
    if marker:
        text = text[marker.end() :]
    else:
        marker = _CURRENCY_END.search(text)
        if marker:
            text = text[: marker.start()]

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if not _NUMBER.fullmatch(text):
        return None
    plain = text.replace(",", "")
    if len(plain.lstrip("-").split(".", 1)[0]) > 15:
        return None
    value = Decimal(plain)
    if negative:
        value = value.copy_abs().copy_negate()
    return format(value, "f")


def parse_date(text: str, fmt: str) -> str | None:
    """Match date tokens exactly after collapsing and trimming whitespace."""
    text = " ".join(text.split())
    fmt = " ".join(fmt.split())
    pattern = []
    fields = {}
    position = 0
    for token in _TOKENS.finditer(fmt):
        pattern.append(re.escape(fmt[position : token.start()]))
        name, expression = _PARTS[token.group()]
        if name in fields:
            return None
        fields[name] = token.group()
        pattern.append(f"(?P<{name}>{expression})")
        position = token.end()
    pattern.append(re.escape(fmt[position:]))
    if fields.keys() != {"day", "month", "year"}:
        return None
    match = re.fullmatch("".join(pattern), text)
    if match is None:
        return None
    month = (
        _MONTHS[match["month"].lower()]
        if fields["month"] == "MMM"
        else int(match["month"])
    )
    year = int(match["year"]) + (2000 if fields["year"] == "YY" else 0)
    try:
        return date(year, month, int(match["day"])).isoformat()
    except ValueError:
        return None


def is_canonical_decimal(text: str) -> bool:
    """Whether a review value is a bounded plain decimal string."""
    return isinstance(text, str) and _PLAIN.fullmatch(text) is not None


def is_canonical_date(text: str) -> bool:
    """Whether a review value is an exact ISO date that exists."""
    if not isinstance(text, str) or not _ISO.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True
