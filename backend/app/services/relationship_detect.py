"""Module 14 §4 — deterministic relationship detection: computes real FK-like links between
candidate tables (value-overlap sampling, never a full scan, never raw values sent anywhere)
and hands them to intent_mapping.py / pipeline_plan.py as agreed-upon *facts*, instead of
letting the AI guess joins from column-name resemblance alone. Persists to `source_relationships`
as a recalculable cache (services/source_profile.py's TableProfile is the input shape, same as
intent_mapping.py already consumes) — losing the cache has no consequence, it just gets
recomputed.

Correctif "fiabilisation détection de relations" — match_rate alone used to promote a candidate
straight to "verified fact": a small-integer measure column (a stock level, a threshold, a
narrow surrogate ID) trivially falls within a wide surrogate ID's range, producing
match_rate≈1.0 between two tables with no real business relationship (confirmed on a real
hospital source: drug_inventory.current_stock/reorder_level/inventory_id/drug_id all "matched"
patient_diagnostic.patient_diagnostic_id at match_rate=1.0). match_rate is now a NECESSARY but
not SUFFICIENT condition — four deterministic gates (A: role exclusion, B: parent PK-likeness,
C: name affinity, D: direction) additionally gate promotion to `verified=True`, the only state
that ever reaches the AI prompt. Every candidate is still persisted (verified or not) so the UI
can show a rejected one greyed out with its `rejected_reason`."""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.data_source import DataSource
from app.models.source_relationship import SourceRelationship
from app.services import connections
from app.services.source_profile import ColumnProfile, TableProfile

logger = logging.getLogger(__name__)

# Below this many sampled distinct values, a 100% match rate is still not very informative
# (e.g. 2/2 matched) — confidence is scaled down proportionally to the sample size instead of
# trusting a tiny sample at face value.
MIN_CONFIDENT_SAMPLE = 20

# "NUMBER" matters on its own: Oracle's NUMBER type (used for virtually every integer/id
# column on an Oracle source, confirmed empirically — sh.sales.cust_id, sh.countries.country_id,
# etc. all stringify to exactly "NUMBER") doesn't contain "NUMERIC" — without it, every Oracle
# FK candidate pair was silently excluded and detection found nothing on an Oracle source.
_NUMERIC_MARKERS = ("INT", "NUMERIC", "NUMBER", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "SERIAL")
_STRING_MARKERS = ("CHAR", "TEXT", "STRING", "CLOB", "UUID")

# Bumped whenever the GATE LOGIC itself changes (not the schema) — folded into
# _schema_fingerprint so every row cached under the OLD, ungated logic is transparently treated
# as stale and recomputed exactly once under the new gates, with no manual "Recalculer" needed
# and no risk of a pre-correctif false positive (e.g. the drug_inventory/patient_diagnostic
# pairs above) being trusted as `verified` just because its schema hasn't changed since.
_GATE_VERSION = "gates-a-d-v1"

_ID_SUFFIXES = ("_id", "_key", "_code")
_REASON_LABELS = {
    "low_match_rate": "recouvrement insuffisant",
    "measure_role": "colonne mesure (jamais une clé)",
    "temporal_parent": "colonne parente temporelle",
    "parent_not_unique": "colonne parente non quasi-unique",
    "no_name_affinity": "aucune affinité de nom",
    "wrong_direction": "sens enfant/parent incohérent",
}


def _type_family(sql_type: str) -> str | None:
    """Coarse type compatibility check (§4.2 "familles de types compatibles") — a relationship
    is only worth testing between columns of the same broad family. Date/boolean/binary
    columns are excluded: not a realistic FK convention, and skipping them keeps the candidate
    pair count down."""
    upper = sql_type.upper()
    if any(m in upper for m in _NUMERIC_MARKERS):
        return "numeric"
    if any(m in upper for m in _STRING_MARKERS):
        return "string"
    return None


def _priority(child_col: str, parent_col: str, parent_table: str) -> int:
    """Name-similarity score used only to prioritize which candidate pairs get tested first
    once there are more than `rel_max_pairs` (§4.2's "priorité aux paires suggérées par la
    similarité de noms") — never used to decide correctness, only test order under budget."""
    if child_col == parent_col:
        return 3
    parent_leaf = parent_table.split(".")[-1].lower().rstrip("s")
    if parent_leaf and parent_leaf in child_col.lower():
        return 2
    if child_col.lower() in parent_col.lower() or parent_col.lower() in child_col.lower():
        return 1
    return 0


def _candidate_pairs(profiles: list[TableProfile]) -> list[tuple[TableProfile, ColumnProfile, TableProfile, ColumnProfile]]:
    """Directed (child, parent) column pairs within the same data source only — cross-source
    relationships are explicitly out of scope for v1 (§11). Both directions of every table
    pair are tested since, before querying, there's no way to know which side holds the
    foreign key — the direction with the higher real match_rate is the one that survives the
    threshold. Sorted by name-similarity priority, descending, so a budget cutoff keeps the
    most plausible candidates."""
    pairs = []
    by_source: dict[int, list[TableProfile]] = {}
    for p in profiles:
        by_source.setdefault(p.source_id, []).append(p)

    for source_tables in by_source.values():
        for t1 in source_tables:
            for t2 in source_tables:
                if t1.table == t2.table:
                    continue
                for c1 in t1.columns:
                    fam1 = _type_family(c1.sql_type)
                    if fam1 is None:
                        continue
                    for c2 in t2.columns:
                        if _type_family(c2.sql_type) != fam1:
                            continue
                        pairs.append((t1, c1, t2, c2))

    pairs.sort(key=lambda pr: _priority(pr[1].name, pr[3].name, pr[2].table), reverse=True)
    return pairs


def _schema_fingerprint(child: TableProfile, parent: TableProfile) -> str:
    """Invalidation key (§4.4): changes whenever either table's own column list changes, OR
    whenever the gate logic version changes (_GATE_VERSION) — so a cached row computed against
    a stale schema, or against the pre-correctif ungated logic, is recognizable as stale
    without needing to delete or blanket-expire anything."""
    payload = json.dumps(
        {
            "gate_version": _GATE_VERSION,
            child.table: sorted(c.name for c in child.columns),
            parent.table: sorted(c.name for c in parent.columns),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _tokens(name: str) -> set[str]:
    """Normalizes a column/table name into a set of stems for garde C's affinity check:
    lowercase, strip a trailing _id/_key/_code (surrogate-key noise, not business meaning),
    split on non-alphanumeric boundaries, crude de-pluralization (trailing 's', not 'ss', so
    "address" stays "address")."""
    lowered = name.lower()
    for suf in _ID_SUFFIXES:
        if lowered.endswith(suf):
            lowered = lowered[: -len(suf)]
            break
    tokens = set()
    for tok in re.split(r"[^a-z0-9]+", lowered):
        if not tok:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.add(tok)
    return tokens


def _name_affinity(child_column: str, parent_table: str, parent_column: str) -> float:
    """Garde C (§3.3) — token-overlap affinity in [0, 1] between the child column's stem and
    the parent table/column's stems combined. 0.0 means no common stem at all (e.g.
    "inventory" vs {"patient", "diagnostic"} — the drug_inventory/patient_diagnostic false
    positive) — anything above that is a real, if partial, name match (e.g. "ship_address" vs
    "address" → common stem "address" even though the full names differ, §3.3's multi-FK note)."""
    child_tokens = _tokens(child_column)
    parent_leaf = parent_table.split(".")[-1]
    parent_tokens = _tokens(parent_leaf) | _tokens(parent_column)
    if not child_tokens or not parent_tokens:
        return 0.0
    common = child_tokens & parent_tokens
    if not common:
        return 0.0
    return round(len(common) / len(child_tokens | parent_tokens), 4)


def _has_matching_fk(child_constraints: dict, child_column: str, parent_table: str, parent_column: str) -> bool:
    """A real introspected FK constraint on the child table pointing at this exact
    (parent_table, parent_column) — the hard-proof shortcut gates B/C both accept in place of
    their own stats/name heuristics."""
    parent_leaf = parent_table.split(".")[-1]
    for fk in child_constraints.get("foreign_keys", []):
        if child_column in fk.get("columns", ()) and (fk.get("referred_table") or "").split(".")[-1] == parent_leaf and parent_column in fk.get("referred_columns", ()):
            return True
    return False


def _confidence(match_rate: float, name_affinity: float, parent_unique_ratio: float, from_introspected_fk: bool) -> float:
    """Recalibrated (§3.5) — no longer follows match_rate alone. A real introspected FK
    constraint is the strongest possible evidence (floored at 0.95 regardless of the other
    components); otherwise a weighted blend of the three signals gates A-D already computed,
    so a high match_rate with zero name affinity (the false-positive shape) can no longer
    produce a high confidence."""
    if from_introspected_fk:
        return round(min(1.0, max(0.95, 0.5 * match_rate + 0.3 * name_affinity + 0.2 * parent_unique_ratio)), 4)
    base = 0.5 * match_rate + 0.3 * name_affinity + 0.2 * parent_unique_ratio
    return round(min(1.0, max(0.0, base)), 4)


def _as_dict(row: SourceRelationship) -> dict:
    """Aggregated-facts shape (§4.3) for prompt injection and the API/frontend — only ratios
    and counts, never a raw compared value."""
    return {
        "child_table": row.child_table, "child_column": row.child_column,
        "parent_table": row.parent_table, "parent_column": row.parent_column,
        "child": f"{row.child_table}.{row.child_column}", "parent": f"{row.parent_table}.{row.parent_column}",
        "match_rate": row.match_rate, "distinct_child": row.distinct_child, "distinct_parent": row.distinct_parent,
        "confidence": row.confidence, "basis": row.basis,
        "verified": row.verified, "name_affinity": row.name_affinity, "parent_unique_ratio": row.parent_unique_ratio,
        "from_introspected_fk": row.from_introspected_fk, "rejected_reason": row.rejected_reason,
        "rejected_reason_label": _REASON_LABELS.get(row.rejected_reason) if row.rejected_reason else None,
    }


def compute_relationships(db: Session, profiles: list[TableProfile], force: bool = False) -> list[dict]:
    """Detects and caches relationship candidates among `profiles` (the SAME bounded candidate
    set source_profile.build_profiles() already narrowed down — never the whole schema, per
    §4.2). `force=True` (the explicit "Recalculer" button, §4.5) ignores the schema-fingerprint
    cache and re-queries every candidate pair; a normal call (e.g. during /map-intent) reuses
    any cached row whose fingerprint still matches. Returns only relations that passed EVERY
    gate (`verified=True`, §3) — the ones safe to inject into the AI prompt as facts. Everything
    computed is persisted regardless of verification, so get_cached_relationships() can show
    the engineer why a candidate was rejected without recomputing anything."""
    settings = get_settings()
    if len(profiles) < 2:
        return []

    source_ids = {p.source_id for p in profiles}
    sources = {s.id: s for s in db.query(DataSource).filter(DataSource.id.in_(source_ids)).all()}

    pairs = _candidate_pairs(profiles)[: settings.rel_max_pairs]

    # Garde D's red-flag diagnostic (§3.4) — counted on the RAW candidate pairs, before any
    # gate: a signal that this (child_table, parent_table) pair is dominated by surrogate-range
    # overlap, surfaced even in cases the gates below already filter out correctly.
    pair_counts: dict[tuple[str, str], int] = {}
    for child_t, _c1, parent_t, _c2 in pairs:
        pair_counts[(child_t.table, parent_t.table)] = pair_counts.get((child_t.table, parent_t.table), 0) + 1
    for (child_table, parent_table), count in pair_counts.items():
        if count > settings.rel_max_per_pair:
            logger.warning(
                "Module 14 relations: %d candidats bruts entre %s et %s (seuil %d) — recouvrement de plage surrogate probable.",
                count, child_table, parent_table, settings.rel_max_per_pair,
            )

    constraints_cache: dict[tuple[int, str], dict] = {}

    def _constraints(source: DataSource, table: str) -> dict:
        key = (source.id, table)
        if key not in constraints_cache:
            try:
                secret = decrypt_secret(source.secret_encrypted)
                schema_name, table_name = table.split(".", 1)
                constraints_cache[key] = connections.get_table_constraints(
                    source.type, source.host, source.port, source.database_name, source.username, secret, schema_name, table_name,
                )
            except Exception:
                constraints_cache[key] = {"pk_columns": set(), "unique_columns": set(), "foreign_keys": []}
        return constraints_cache[key]

    results: list[dict] = []
    seen_keys: set[tuple] = set()
    for child_t, child_c, parent_t, parent_c in pairs:
        key = (child_t.source_id, child_t.table, child_c.name, parent_t.table, parent_c.name)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        fingerprint = _schema_fingerprint(child_t, parent_t)
        existing = (
            db.query(SourceRelationship)
            .filter(
                SourceRelationship.data_source_id == child_t.source_id,
                SourceRelationship.child_table == child_t.table,
                SourceRelationship.child_column == child_c.name,
                SourceRelationship.parent_table == parent_t.table,
                SourceRelationship.parent_column == parent_c.name,
            )
            .first()
        )
        if existing is not None and not force and existing.schema_fingerprint == fingerprint:
            if existing.verified:
                results.append(_as_dict(existing))
            continue

        source = sources.get(child_t.source_id)
        if source is None:
            continue
        try:
            secret = decrypt_secret(source.secret_encrypted)
            child_schema, child_table_name = child_t.table.split(".", 1)
            parent_schema, parent_table_name = parent_t.table.split(".", 1)
            overlap = connections.measure_overlap(
                source.type, source.host, source.port, source.database_name, source.username, secret,
                child_schema, child_table_name, child_c.name,
                parent_schema, parent_table_name, parent_c.name,
                settings.rel_sample_size, settings.rel_query_timeout,
            )
        except Exception:
            logger.info(
                "Module 14 relations: échec du calcul %s.%s -> %s.%s",
                child_t.table, child_c.name, parent_t.table, parent_c.name, exc_info=True,
            )
            continue

        sampled, matched, distinct_parent = overlap["sampled"], overlap["matched"], overlap["distinct_parent"]
        if sampled == 0:
            continue
        match_rate = round(matched / sampled, 4)

        # --- Gates A→D (§3) — match_rate is necessary but no longer sufficient. ---
        reason: str | None = None
        name_affinity = 0.0
        parent_unique_ratio = 0.0
        from_fk = False

        if match_rate < settings.rel_match_threshold:
            reason = "low_match_rate"

        if reason is None:
            # Garde A — a measure is never a key, in either position; a temporal column is
            # only admissible as the CHILD (a possible FK into a date dimension), never parent.
            if child_c.role == "measure" or parent_c.role == "measure":
                reason = "measure_role"
            elif parent_c.role == "temporal":
                reason = "temporal_parent"

        if reason is None:
            child_constraints = _constraints(source, child_t.table)
            from_fk = _has_matching_fk(child_constraints, child_c.name, parent_t.table, parent_c.name)

            # Garde B — the parent must be quasi-unique: a real PK/UNIQUE constraint wins
            # outright, otherwise fall back to the sampled distinct/row_count ratio.
            parent_constraints = _constraints(source, parent_t.table)
            parent_is_pk_or_unique = parent_c.name in parent_constraints.get("pk_columns", set()) or parent_c.name in parent_constraints.get("unique_columns", set())
            if from_fk or parent_is_pk_or_unique:
                parent_unique_ratio = 1.0
            elif parent_t.row_count:
                parent_unique_ratio = round(distinct_parent / parent_t.row_count, 4)
            if not (from_fk or parent_is_pk_or_unique or parent_unique_ratio >= settings.rel_parent_unique_ratio):
                reason = "parent_not_unique"

        if reason is None:
            # Garde C — name affinity is the discriminant: a real FK constraint short-circuits
            # it (hard proof beats a heuristic), otherwise the token-overlap score must clear
            # the configured floor (default: any non-null overlap).
            name_affinity = _name_affinity(child_c.name, parent_t.table, parent_c.name)
            if not from_fk and name_affinity <= settings.rel_name_affinity_min:
                reason = "no_name_affinity"

        if reason is None:
            # Garde D — direction: the child's real (unsampled) distinct count must not exceed
            # the parent's, or the two sides are very likely swapped.
            if child_c.distinct_count > distinct_parent:
                reason = "wrong_direction"

        verified = reason is None
        confidence = _confidence(match_rate, name_affinity, parent_unique_ratio, from_fk)

        row = existing
        if row is None:
            row = SourceRelationship(
                data_source_id=child_t.source_id, child_table=child_t.table, child_column=child_c.name,
                parent_table=parent_t.table, parent_column=parent_c.name,
            )
            db.add(row)
        row.match_rate = match_rate
        row.distinct_child = sampled
        row.distinct_parent = distinct_parent
        row.confidence = confidence
        row.basis = "value_overlap_sampled"
        row.schema_fingerprint = fingerprint
        row.computed_at = datetime.now(timezone.utc)
        row.verified = verified
        row.name_affinity = name_affinity
        row.parent_unique_ratio = parent_unique_ratio
        row.from_introspected_fk = from_fk
        row.rejected_reason = reason

        if verified:
            results.append(_as_dict(row))

    db.commit()
    return results


def get_cached_relationships(db: Session, source_ids: list[int]) -> list[dict]:
    """Read-only accessor (GET /relationships, §4.6) — every candidate currently cached for
    these sources, verified or not, no recompute. Correctif §6: returns rejected candidates
    too (greyed out with `rejected_reason` in the UI) — unlike compute_relationships()'s own
    return value, which stays verified-only since that one feeds the AI prompt."""
    if not source_ids:
        return []
    rows = db.query(SourceRelationship).filter(SourceRelationship.data_source_id.in_(source_ids)).all()
    return [_as_dict(r) for r in rows]
