import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

SAMPLE_SIZE = 1000
CONFIDENCE_THRESHOLD = 0.95

# Cascade order — first type whose confidence clears the threshold wins. text is the
# always-true fallback and is never tried explicitly (it's the loop's "else" branch).
_CASCADE = ["integer", "bigint", "numeric", "boolean", "date", "timestamp"]

_THOUSANDS_CHARS = "   "  # space, no-break space, narrow no-break space
_LEADING_ZERO_RE = re.compile(r"^[+-]?0\d+$")

_BOOL_TRUE = {"true", "1", "oui", "vrai", "o", "yes", "y"}
_BOOL_FALSE = {"false", "0", "non", "faux", "n", "no"}

_SQL_RESERVED = {
    "select", "insert", "update", "delete", "from", "where", "table", "column", "index",
    "order", "group", "by", "and", "or", "not", "null", "true", "false", "primary", "key",
    "foreign", "references", "unique", "check", "default", "create", "drop", "alter",
    "grant", "revoke", "as", "on", "join", "into", "values", "set", "user", "all", "any",
    "case", "when", "then", "else", "end", "cast", "in", "is", "like", "limit", "offset",
}

_DATE_FORMATS_SLASH_DAY_FIRST = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"]
_DATE_FORMATS_ISO = ["%Y-%m-%d", "%Y/%m/%d"]
_TIMESTAMP_FORMATS = [
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
]


def normalize_column_name(name: str, existing: set[str]) -> str:
    """Lowercase, transliterate accents, non-alnum -> _, collapse repeats, prefix if it
    starts with a digit, reject SQL reserved words, suffix _2/_3... on collision."""
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    ident = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name.strip().lower())
    ident = re.sub(r"_+", "_", ident).strip("_")
    if not ident:
        ident = "col"
    if ident[0].isdigit():
        ident = f"col_{ident}"
    if ident in _SQL_RESERVED:
        ident = f"col_{ident}"

    candidate = ident
    n = 2
    while candidate in existing:
        candidate = f"{ident}_{n}"
        n += 1
    return candidate


def _parse_number(raw: str) -> Decimal | None:
    text = raw.strip()
    if not text:
        return None
    # try as-is first (plain international format: period decimal, no separators)
    try:
        return Decimal(text)
    except InvalidOperation:
        pass
    # French convention: strip thousands separators, comma is the decimal point
    stripped = text
    for ch in _THOUSANDS_CHARS:
        stripped = stripped.replace(ch, "")
    stripped = stripped.replace(",", ".")
    try:
        return Decimal(stripped)
    except InvalidOperation:
        return None


def _is_insignificant_leading_zero(raw: str) -> bool:
    """'00475', '0123' — a leading zero that would be lost by numeric casting. A lone
    '0' or a decimal like '0.5' is not affected."""
    text = raw.strip()
    return bool(_LEADING_ZERO_RE.match(text)) and "." not in text and "," not in text


def _try_date(raw: str) -> tuple[date, int, int, str] | None:
    """Returns (parsed_date, day_component, month_component, matched_format). ISO formats
    are tried first since they're never ambiguous — the year always comes first."""
    text = raw.strip()
    for fmt in _DATE_FORMATS_ISO:
        try:
            d = datetime.strptime(text, fmt).date()
            return d, d.day, d.month, fmt
        except ValueError:
            continue
    for fmt in _DATE_FORMATS_SLASH_DAY_FIRST:
        try:
            d = datetime.strptime(text, fmt).date()
            return d, d.day, d.month, fmt
        except ValueError:
            continue
    return None


def _try_timestamp(raw: str) -> tuple[datetime, str] | None:
    """Returns (parsed_datetime, matched_format)."""
    text = raw.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt), fmt
        except ValueError:
            continue
    return None


def _confidence(values: list[str], predicate) -> float:
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return 0.0
    hits = sum(1 for v in non_null if predicate(str(v)))
    return hits / len(non_null)


def infer_column(values: list[str]) -> dict:
    """values: raw string cell values for one column, already-truncated to SAMPLE_SIZE.
    Returns the column_mapping fields produced by inference (not source_name/target_name,
    which the caller attaches): inferred_type, confidence, ambiguous, sample."""
    non_null = [str(v) for v in values if v is not None and str(v).strip() != ""]
    sample = non_null[:5]

    if not non_null:
        return {"inferred_type": "text", "confidence": 0.0, "ambiguous": False, "format": None, "sample": sample}

    # forced text: leading zeros that carry meaning (postal codes, account numbers)
    if any(_is_insignificant_leading_zero(v) for v in non_null):
        return {"inferred_type": "text", "confidence": 1.0, "ambiguous": False, "format": None, "sample": sample}

    for candidate in _CASCADE:
        if candidate == "integer":
            def pred(v):
                n = _parse_number(v)
                return n is not None and n == n.to_integral_value() and -2147483648 <= n <= 2147483647
        elif candidate == "bigint":
            def pred(v):
                n = _parse_number(v)
                return n is not None and n == n.to_integral_value()
        elif candidate == "numeric":
            def pred(v):
                return _parse_number(v) is not None
        elif candidate == "boolean":
            def pred(v):
                return v.strip().lower() in _BOOL_TRUE or v.strip().lower() in _BOOL_FALSE
        elif candidate == "date":
            def pred(v):
                return _try_date(v) is not None
        else:  # timestamp
            def pred(v):
                return _try_timestamp(v) is not None

        confidence = _confidence(non_null, pred)
        if confidence >= CONFIDENCE_THRESHOLD:
            ambiguous = False
            matched_format = None
            if candidate == "date":
                parsed = [p for p in (_try_date(v) for v in non_null) if p is not None]
                # the format that actually parses the sample, not a hardcoded guess —
                # using the wrong one here silently NULLs every row at import time
                matched_format = Counter(p[3] for p in parsed).most_common(1)[0][0]
                if matched_format not in _DATE_FORMATS_ISO:
                    day_first = [p for p in parsed if p[3] == matched_format]
                    if day_first and all(day <= 12 and month <= 12 for _, day, month, _ in day_first):
                        ambiguous = True
            elif candidate == "timestamp":
                parsed = [p for p in (_try_timestamp(v) for v in non_null) if p is not None]
                matched_format = Counter(p[1] for p in parsed).most_common(1)[0][0]
            return {
                "inferred_type": candidate,
                "confidence": round(confidence, 4),
                "ambiguous": ambiguous,
                "format": matched_format,
                "sample": sample,
            }

    return {"inferred_type": "text", "confidence": 1.0, "ambiguous": False, "format": None, "sample": sample}


def infer_schema(rows: list[dict], source_columns: list[str], reserved_names: set[str] | None = None) -> list[dict]:
    """rows: list of dicts keyed by source_columns (raw string/None cell values).
    Returns one column_mapping entry per source column, in source order."""
    existing_names: set[str] = set(reserved_names or ())
    mapping = []
    for col in source_columns:
        values = [row.get(col) for row in rows[:SAMPLE_SIZE]]
        inferred = infer_column(values)
        target_name = normalize_column_name(col, existing_names)
        existing_names.add(target_name)
        entry = {
            "source_name": col,
            "target_name": target_name,
            "target_type": inferred["inferred_type"],
            "format": inferred["format"],
            "include": True,
            "nullable": True,
            "inferred_type": inferred["inferred_type"],
            "confidence": inferred["confidence"],
            "ambiguous": inferred["ambiguous"],
            "sample": inferred["sample"],
        }
        mapping.append(entry)
    return mapping


def is_flat_records(rows: list[dict]) -> bool:
    """A record is 'flat' if none of its values are themselves a dict/list. JSON/XML only —
    CSV/Excel cells are always scalar strings and never reach this check."""
    for row in rows[:SAMPLE_SIZE]:
        for v in row.values():
            if isinstance(v, (dict, list)):
                return False
    return True


def infer_nested_schema(rows: list[dict]) -> list[dict]:
    """Nested JSON/XML records don't get flattened here (no aplatissement engine, by design —
    see Module 6 spec §5.2) — the whole record lands in one `payload JSONB` column. Top-level
    *scalar* keys are additionally offered as opt-in typed columns (unchecked by default) so a
    simple case doesn't force a detour through dbt just to pull out one obvious id."""
    payload_entry = {
        "source_name": "__root__",
        "target_name": "payload",
        "target_type": "jsonb",
        "format": None,
        "include": True,
        "nullable": True,
        "inferred_type": "jsonb",
        "confidence": 1.0,
        "ambiguous": False,
        "sample": [json.dumps(r, default=str)[:200] for r in rows[:5]],
    }

    scalar_keys: list[str] = []
    seen = set()
    for row in rows[:SAMPLE_SIZE]:
        for k, v in row.items():
            if k not in seen and not isinstance(v, (dict, list)):
                seen.add(k)
                scalar_keys.append(k)

    scalar_mapping = infer_schema(rows, scalar_keys, reserved_names={"payload"})
    for entry in scalar_mapping:
        entry["include"] = False

    return [payload_entry] + scalar_mapping
