import json
import logging
from dataclasses import replace

from pydantic import BaseModel, Field, ValidationError

from app.services import ai_client
from app.services.ai_config import AIConfig
from app.services.ai_json import extract_json_object
from app.services.source_profile import TableProfile

logger = logging.getLogger(__name__)

_VALID_ROLES = {"fact", "reference"}
_COLUMN_ROLES = ("measure", "temporal", "dimension", "join_keys")

# Was 600s on the original CPU-bound self-hosted Mistral (a 1-table prompt alone took ~170s
# there). The deployment moved to a GPU-backed instance — the equivalent plan-generation call
# (a harder task than mapping) now completes in ~20-30s on a small prompt. Bumped from 180s:
# source_profile.build_profiles() no longer truncates a project's own bronze layer to
# max_tables (a real ~19-table mapping call was confirmed needing up to ~400s on this same
# GPU-backed model) — still headroom below that observed ceiling, far below the old CPU-era one.
MAPPING_TIMEOUT_FLOOR = 420.0


class AgentAIError(Exception):
    pass


class _RawColumns(BaseModel):
    measure: list[str] = Field(default_factory=list)
    temporal: list[str] = Field(default_factory=list)
    dimension: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)


class _RawEvidence(BaseModel):
    """Module 14 §5.2 — the AI's own argumentation for a table's role/confidence, structurally
    validated (below) against the real profile before it ever reaches the engineer."""
    from_model: list[str] = Field(default_factory=list)


class _RawTable(BaseModel):
    table: str
    role: str = ""
    confidence: float = 0.0
    columns: _RawColumns = Field(default_factory=_RawColumns)
    evidence: _RawEvidence = Field(default_factory=_RawEvidence)


class _RawUnresolved(BaseModel):
    need: str
    candidates: list[str] = Field(default_factory=list)


class _RawMapping(BaseModel):
    status: str = "unresolved"
    tables: list[_RawTable] = Field(default_factory=list)
    unresolved: list[_RawUnresolved] = Field(default_factory=list)


def _profile_sheet(profiles: list[TableProfile]) -> list[dict]:
    """Deliberately lean: table/column relevance only needs name, type, inferred role and any
    human description/example values — not the full stats profile (distinct_count, null_rate)
    that Module 12's gold indicator suggestion needs. Keys are omitted rather than sent empty/
    null, since most columns have neither a description nor low-cardinality examples — with
    ~10+ tables profiled at once this keeps the prompt from ballooning."""
    sheet = []
    for p in profiles:
        entry: dict = {"table": p.table}
        if p.description:
            entry["description"] = p.description
        cols = []
        for c in p.columns:
            col: dict = {"name": c.name, "type": c.sql_type, "role": c.role}
            if c.description:
                col["description"] = c.description
            if c.sample_values:
                col["examples"] = c.sample_values
            cols.append(col)
        entry["columns"] = cols
        sheet.append(entry)
    return sheet


def _relationship_sheet(relationships: list[dict]) -> list[dict]:
    """Module 14 §4.3 — aggregated facts only (child/parent column pair + measured ratios),
    never a compared value: these are real, backend-computed relationships, offered as
    join_keys hints the model should prefer over guessing from column-name resemblance alone."""
    return [{"child": r["child"], "parent": r["parent"], "match_rate": r["match_rate"], "confidence": r["confidence"]} for r in relationships]


def _build_prompt(instruction: str, profiles: list[TableProfile], relationships: list[dict] | None = None) -> list[dict]:
    system = (
        "You are an assistant who identifies, among really introspected database tables, "
        "those relevant to a business objective expressed in natural language. "
        "Respond ONLY with a valid JSON object, no text or markdown around it, in exactly this "
        'format: {"status": "resolved|partial|unresolved", "tables": [{"table": "schema.table", '
        '"role": "fact|reference", "confidence": 0.0-1.0, "columns": {"measure": [...], '
        '"temporal": [...], "dimension": [...], "join_keys": [...]}, "evidence": {"from_model": '
        '[...]}}], "unresolved": [{"need": "description of the uncovered need", "candidates": '
        '["schema.table", ...]}]}. '
        "Strict rules: NEVER invent a table or column absent from the list provided; "
        "role='fact' for a fact/event table carrying numeric measures, 'reference' for a "
        "reference/dimension table; every column classified into "
        "measure/temporal/dimension/join_keys must belong to the table concerned; if no table "
        "matches with sufficient confidence, return status='unresolved' or 'partial' and "
        "describe the uncovered need in 'unresolved' with the best candidates. "
        "'measure': ONLY summable/cumulative numeric quantities (amounts, quantities, "
        "durations). NEVER classify as 'measure': an identifier or a key (*_id, *_key columns), "
        "a number/rank (floor number, room number), a year or a tenure duration expressed in "
        "years (e.g. experience_years, age), a numeric code or status. When in doubt, classify "
        "as 'dimension'. "
        "'evidence.from_model' is a short list (2 to 4 items) of factual reasons justifying the "
        "confidence granted to this table — cite an existing description/annotation and the "
        "real columns that support your choice, in free format \"<role> column: <column_name>\" "
        "or a quote from an annotation; NEVER cite a column absent from the table."
    )
    payload: dict = {"instruction": instruction, "tables": _profile_sheet(profiles)}
    if relationships:
        payload["detected_relationships"] = _relationship_sheet(relationships)
        # Correctif "fiabilisation détection de relations" §4 — every entry here has already
        # passed match_rate + 4 deterministic gates (role, parent-uniqueness, name affinity,
        # direction) before ever reaching this prompt: they're facts, not candidates. Explicit
        # about the "verified listed" scope and forbids inventing anything beyond it, closing
        # the loop the old wording left open (a table/column pair NOT in this list could
        # previously still get guessed into join_keys from name resemblance alone).
        system += (
            " The \"detected_relationships\" field lists foreign-key relationships REALLY "
            "VERIFIED on the data (child = child column, parent = parent column, match_rate = "
            "measured overlap ratio): classify into 'join_keys' the columns of the verified "
            "relationships listed in \"detected_relationships\"; do not invent any join outside "
            "this list."
        )
    user = json.dumps(payload, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def map_intent_ai(config: AIConfig, instruction: str, profiles: list[TableProfile], relationships: list[dict] | None = None) -> _RawMapping:
    """Raises AgentAIError on any failure. Unlike Module 12's indicator suggestion, mapping
    has no meaningful deterministic fallback — it needs Mistral's semantic reading of the
    instruction — so a failure here must surface clearly rather than degrade silently.
    `relationships` (Module 14 §4.3): backend-detected FK candidates, offered as facts."""
    messages = _build_prompt(instruction, profiles, relationships)
    call_config = config if config.timeout >= MAPPING_TIMEOUT_FLOOR else replace(config, timeout=MAPPING_TIMEOUT_FLOOR)
    try:
        raw = await ai_client.chat_completion(call_config, messages, temperature=0.1)
        return _RawMapping.model_validate(extract_json_object(raw))
    except (ai_client.AIClientError, ValueError, ValidationError) as exc:
        raise AgentAIError(str(exc)) from exc


def _validate_from_model(entries: list[str], real_columns: set[str]) -> list[str]:
    """Module 14 §5.2 — keeps the AI's free-form rationale strings, but drops any that
    explicitly cite a column (the text after a trailing ": ") that doesn't actually exist on
    this table: the model can argue freely, but can't invent evidence for a column that was
    never there. Bounded to 5 entries — this is supporting evidence, not a report."""
    valid = []
    for e in entries:
        e = e.strip()
        if not e:
            continue
        if ":" in e:
            cited = e.rsplit(":", 1)[1].strip().strip("'\"")
            if cited and not cited.startswith("'") and cited not in real_columns and len(cited.split()) == 1:
                logger.info("Module 14 evidence: from_model écarté (colonne citée inexistante) : %s", e)
                continue
        valid.append(e)
    return valid[:5]


def _from_profile(profile: TableProfile, join_keys: list[str]) -> dict:
    """Module 14 §5.2 — the deterministic, non-falsifiable anchor: row_count (from
    source_profile.py's own COUNT(*)), the real min/max span across the table's temporal
    columns (already computed by profile_table(), no extra query), and the null rate of
    whichever columns this mapping tagged as join_keys (ditto). Only non-empty facts are
    included — the caller (resolve_mapping) treats a table with none of these as having no
    deterministic anchor at all."""
    out: dict = {}
    if profile.row_count is not None:
        out["row_count"] = profile.row_count
    temporal_cols = [c for c in profile.columns if c.role == "temporal" and c.min_value and c.max_value]
    if temporal_cols:
        out["temporal_range"] = [min(c.min_value for c in temporal_cols), max(c.max_value for c in temporal_cols)]
    key_cols = [c for c in profile.columns if c.name in join_keys]
    if key_cols:
        out["null_rate_key"] = round(sum(c.null_rate for c in key_cols) / len(key_cols), 4)
    return out


def resolve_mapping(raw: _RawMapping, profiles: list[TableProfile], min_confidence: float) -> dict:
    """Whitelists the AI's raw output against the real profiled tables/columns — anything
    hallucinated is dropped (logged, never surfaced), and low-confidence tables are demoted
    to unresolved rather than trusted as-is. Module 14 §5.2: a table is additionally demoted
    to unresolved if it has no deterministic `from_profile` anchor at all — the anti-loop rule
    ("la confiance affichée est toujours accompagnée d'au moins une preuve from_profile")."""
    profile_by_table = {p.table: p for p in profiles}

    def _candidates(names: list[str]) -> list[dict]:
        return [{"table": c, "source_id": profile_by_table[c].source_id} for c in names if c in profile_by_table]

    unresolved_out = [{"need": u.need, "candidates": _candidates(u.candidates)} for u in raw.unresolved]

    tables_out = []
    for t in raw.tables:
        profile = profile_by_table.get(t.table)
        if profile is None:
            logger.info("Module 13 mapping: table hallucinée écartée: %s", t.table)
            continue
        if t.role not in _VALID_ROLES:
            logger.info("Module 13 mapping: rôle invalide écarté pour %s: %s", t.table, t.role)
            continue
        real_columns = {c.name for c in profile.columns}
        columns = {role: [c for c in getattr(t.columns, role) if c in real_columns] for role in _COLUMN_ROLES}
        # Module 14 extension "primitives gold" §3.1 — carried forward so pipeline_plan.py can
        # validate a gold filter's literal value against the column's real distinct values
        # without ever re-querying the source; keyed by real column name, only columns with a
        # captured enumeration included (mirrors _profile_sheet's "omit rather than send empty").
        filter_enum_values = {c.name: c.filter_enum_values for c in profile.columns if c.filter_enum_values}

        if t.confidence < min_confidence:
            unresolved_out.append({"need": t.table, "candidates": _candidates([t.table])})
            continue

        from_profile = _from_profile(profile, columns["join_keys"])
        if not from_profile:
            logger.info("Module 14 evidence: %s écartée — aucune ancre from_profile disponible", t.table)
            unresolved_out.append({"need": t.table, "candidates": _candidates([t.table])})
            continue
        evidence = {"from_model": _validate_from_model(t.evidence.from_model, real_columns), "from_profile": from_profile}

        tables_out.append({
            "table": t.table, "source_id": profile.source_id, "role": t.role, "confidence": t.confidence,
            "columns": columns, "evidence": evidence, "filter_enum_values": filter_enum_values,
        })

    if not tables_out:
        status = "unresolved"
    elif unresolved_out:
        status = "partial"
    else:
        status = "resolved"

    return {"status": status, "tables": tables_out, "unresolved": unresolved_out}
