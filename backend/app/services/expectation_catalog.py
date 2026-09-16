"""Module 16 extension §3 — the versioned catalogue mapping each M16 `check_type` to its dbt
form (twin of chart_catalog.py / chart_catalog.json for Superset). The knowledge of "which
macro, which tier" lives HERE, in code, never in an LLM output.

Only 3 of the 5 `CheckType` values are Tier A (dbt-expectations macro): type_conformity,
format_validity, plausibility. The other 2 (intra_row_consistency, conditional_completeness)
are Tier C (compiled singular test). Tier B (referential_integrity/grain_uniqueness/
null_rate) and the ⟨baseline⟩ indicators have NO `DataQualityCheck` row to opt in from — see
`_meta.not_materializable` in expectation_catalog.json for why each is out of scope for the
per-check materialization toggle (§7 of the extension spec: `data_quality_baselines`
explicitly stays unchanged; Tier B is already unconditionally rendered since M8/M14)."""
import json
from pathlib import Path

from app.models.data_quality import CheckType

_CATALOG_PATH = Path(__file__).parent / "expectation_catalog.json"


def _load() -> tuple[dict[str, dict], dict[str, str]]:
    raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    entries = {e["check_type"]: e for e in raw["entries"]}
    not_materializable = raw["_meta"]["not_materializable"]
    return entries, not_materializable


CATALOG, NOT_MATERIALIZABLE = _load()


def is_materializable(check_type: CheckType) -> bool:
    """An entry absent from the catalogue ⇒ the check stays observation-only (§3 "repli
    propre") — this covers every M16 CheckType not in the catalogue by construction, never a
    hard error."""
    return check_type.value in CATALOG


def tier(check_type: CheckType) -> str | None:
    entry = CATALOG.get(check_type.value)
    return entry["tier"] if entry else None
