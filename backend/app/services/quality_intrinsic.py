"""Module 16 §4.3 — intrinsic data quality: computes the "auto-portant" indicators (no
baseline, no contract required — Étape 1's socle) for every layer of a project, in the SAME
post-run passage as Module 5's four drift signals (services/quality_collector.py), writing
into DataQualityMetric rows attached to (get-or-create) a DataQualitySnapshot per dataset.

Invariant (§0): the AI never computes an indicator — every formula here is plain SQL against
the tenant warehouse, read-only, best-effort. A failing indicator never raises; it either
logs+skips silently (upstream data not resolvable — nothing to persist) or persists a
`status=skipped` row with a `raw.error` string, and never takes any other indicator down with
it — the exact discipline `quality_collector.collect_quality` already established for M5.

Étape 1 scope (auto-portant only, §4.1): the AI-contract indicators (type_conformity, format_
validity, intra_row_consistency, plausibility, conditional_completeness — Étape 4) are
deliberately NOT computed here. `categorical_normalization` and `completeness_gain` (§3.2) are
also deferred: both need reliable column-identity matching across layers (same logical column,
possibly renamed by an AI-authored silver), which is a real design question on its own —
flagged, not guessed at, rather than shipped half-right.

Étape 3 addition (§6.3): the four ⟨baseline⟩ indicators (ingestion_completeness,
aggregate_reconciliation, end_to_end_conservation, dimensional_completeness) are now computed
too, but ONLY when the project's DataQualityBaseline declares the relevant assertion — a
project with no baseline collects exactly as it did in Étape 1 (§6.6.1: "une baseline non
déclarée ne casse rien").
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_quality import CheckStatus, CheckType, DataQualityBaseline, DataQualityCheck, DataQualityMetric, DataQualitySnapshot, QualityIndicator, QualityLayer, QualityMetricStatus
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, MedallionRun
from app.models.payload_structuration import PayloadStructuration
from app.models.pipeline_plan import PipelinePlan
from app.models.source_relationship import SourceRelationship
from app.services.medallion_stats import SCHEMA_BY_LAYER, list_columns, table_name

logger = logging.getLogger("app.quality_intrinsic")

# Étape 1 has no configurable rule yet (that's Étape 5's DataQualityRule extension) — these
# are safe, documented internal defaults classifying a computed defect_rate into a status the
# Étape 2 matrix can color. Purely advisory (never alerts on their own — no DataQualityAlert
# is raised from this module; §8 wires that up later); superseded wholesale once thresholds
# become configurable.
_WARNING_AT = {
    QualityIndicator.referential_integrity: 0.01,
    QualityIndicator.join_loss: 0.05,
    QualityIndicator.ingestion_completeness: 0.01,
    QualityIndicator.aggregate_reconciliation: 0.005,
    QualityIndicator.end_to_end_conservation: 0.01,
    QualityIndicator.dimensional_completeness: 0.05,
    # §7 — ⟨contrat⟩ indicators (Étape 4): a check only exists once an engineer validated it, so
    # any violation at all is already meaningful — thresholds stay tight, same order of
    # magnitude as the other business-rule indicators above (aggregate_reconciliation,
    # referential_integrity) rather than the more tolerant drift-style ones.
    QualityIndicator.type_conformity: 0.01,
    QualityIndicator.format_validity: 0.02,
    QualityIndicator.intra_row_consistency: 0.005,
    QualityIndicator.plausibility: 0.01,
    QualityIndicator.conditional_completeness: 0.01,
    # Module 6 extension (payload & structuration) §7.2 — same order of magnitude as
    # ingestion_completeness/type_conformity: a bronze fidelity signal, not a business rule.
    QualityIndicator.parsing_rejection_rate: 0.01,
}
_CRITICAL_AT = {
    QualityIndicator.referential_integrity: 0.05,
    QualityIndicator.join_loss: 0.20,
    QualityIndicator.ingestion_completeness: 0.05,
    QualityIndicator.aggregate_reconciliation: 0.02,
    QualityIndicator.end_to_end_conservation: 0.05,
    QualityIndicator.dimensional_completeness: 0.20,
    QualityIndicator.type_conformity: 0.05,
    QualityIndicator.format_validity: 0.10,
    QualityIndicator.intra_row_consistency: 0.02,
    QualityIndicator.plausibility: 0.05,
    QualityIndicator.conditional_completeness: 0.05,
    QualityIndicator.parsing_rejection_rate: 0.05,
}
# grain_uniqueness has no meaningful "small tolerated defect" — any duplicate at the declared
# grain is a real correctness bug, not a drifting metric — critical the instant it's non-zero.
_ZERO_TOLERANCE = {QualityIndicator.grain_uniqueness}
# Purely descriptive per §3.1 ("En bronze, null rate et doublons sont descriptifs, pas des
# alertes") — never anything but "ok", regardless of the measured rate.
_DESCRIPTIVE = {QualityIndicator.null_rate, QualityIndicator.raw_duplicates}


def _status_for(indicator: QualityIndicator, defect_rate: float) -> QualityMetricStatus:
    if indicator in _DESCRIPTIVE:
        return QualityMetricStatus.ok
    if indicator in _ZERO_TOLERANCE:
        return QualityMetricStatus.ok if defect_rate <= 0 else QualityMetricStatus.critical
    critical_at = _CRITICAL_AT.get(indicator)
    warning_at = _WARNING_AT.get(indicator)
    if critical_at is not None and defect_rate >= critical_at:
        return QualityMetricStatus.critical
    if warning_at is not None and defect_rate >= warning_at:
        return QualityMetricStatus.warning
    return QualityMetricStatus.ok


def _sanitize_numeric(value):
    """Postgres SUM()/count() over a NUMERIC column comes back through psycopg as a Python
    Decimal, not a float — and Decimal refuses direct arithmetic with a float literal (raises
    TypeError, confirmed live: `1.0 - Decimal(...)` blew up `_upsert_metric` for every SUM
    computed over a NUMERIC-typed measure, e.g. end_to_end_conservation/aggregate_reconciliation
    on a real financial column). Recursively coerced to float here — the one boundary every
    indicator's result crosses before being scored or persisted — rather than patched at each
    of the (growing) call sites that do SUM/AVG arithmetic."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _sanitize_numeric(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_numeric(v) for v in value]
    return value


def _upsert_metric(
    db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, layer: QualityLayer,
    indicator: QualityIndicator, target_column: str, defect_rate: float | None, raw: dict, status: QualityMetricStatus,
) -> None:
    """Idempotent per (snapshot, dataset, indicator, target_column) — recollecting the same
    run updates the existing row in place instead of duplicating (§4.5.2)."""
    defect_rate = _sanitize_numeric(defect_rate)
    raw = _sanitize_numeric(raw)
    existing = (
        db.query(DataQualityMetric)
        .filter(
            DataQualityMetric.snapshot_id == snapshot.id, DataQualityMetric.dataset_id == dataset.id,
            DataQualityMetric.indicator == indicator, DataQualityMetric.target_column == target_column,
        )
        .first()
    )
    score = None if defect_rate is None else round(max(0.0, 1.0 - defect_rate), 4)
    if existing is not None:
        existing.defect_rate = defect_rate
        existing.score = score
        existing.raw = raw
        existing.status = status
        existing.computed_at = datetime.now(timezone.utc)
        return
    db.add(
        DataQualityMetric(
            snapshot_id=snapshot.id, dataset_id=dataset.id, project_id=dataset.project_id,
            layer=layer, indicator=indicator, target_column=target_column,
            defect_rate=defect_rate, score=score, raw=raw, status=status,
        )
    )


def _get_or_create_snapshot(db: Session, dataset: MedallionDataset, project: MedallionProject, run: MedallionRun) -> DataQualitySnapshot:
    """DataQualitySnapshot becomes the parent of every intrinsic-quality metric (§4.2), for
    EVERY layer — but Module 5's own collection (quality_collector.collect_quality) only ever
    snapshots GOLD datasets (its 4 drift signals only make sense there today). Reused
    unchanged when it already exists (the normal case for gold, since M5 runs first in the
    same passage, §4.3); created bare (M5 signal fields left null) for bronze/silver, which
    M5 never touches — this never modifies M5's own snapshot semantics or query surface."""
    snap = (
        db.query(DataQualitySnapshot)
        .filter(DataQualitySnapshot.dataset_id == dataset.id, DataQualitySnapshot.run_id == run.id)
        .first()
    )
    if snap is not None:
        return snap
    snap = DataQualitySnapshot(dataset_id=dataset.id, project_id=project.id, run_id=run.id, loaded_at=run.finished_at)
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _q(col: str) -> str:
    """Double-quoted identifier — every column name used here comes from real introspection
    (list_columns) or from source_relationships (itself computed from real introspection,
    Module 14), never from free text, so this is quoting for correctness (mixed-case/
    reserved-word columns), not an injection boundary being asked to do double duty."""
    return f'"{col}"'


def _bronze_null_rates(conn: Connection, schema: str, table: str, columns: list[str]) -> tuple[int, dict[str, int]]:
    if not columns:
        return 0, {}
    null_exprs = ", ".join(f"count(*) FILTER (WHERE {_q(c)} IS NULL) AS {_q('null_' + c)}" for c in columns)
    row = conn.execute(text(f'SELECT count(*) AS total, {null_exprs} FROM {schema}."{table}"')).mappings().one()
    return row["total"], {c: row[f"null_{c}"] for c in columns}


def _collect_bronze(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, warehouse: DataSource, conn: Connection) -> None:
    table = table_name(dataset)
    if not table:
        return
    columns = [c["column"] for c in list_columns(warehouse, dataset)]
    if not columns:
        return

    # null_rate — per column, descriptive (§3.1)
    try:
        total, null_counts = _bronze_null_rates(conn, "bronze", table, columns)
        for col, nulls in null_counts.items():
            rate = round(nulls / total, 4) if total else 0.0
            _upsert_metric(
                db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.null_rate, col,
                rate, {"nulls": nulls, "total": total}, _status_for(QualityIndicator.null_rate, rate),
            )
    except Exception as exc:
        logger.info("quality_intrinsic: null_rate failed for bronze.%s: %s", table, exc)
        _upsert_metric(db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.null_rate, "", None, {"error": str(exc)}, QualityMetricStatus.skipped)

    # raw_duplicates — full-row distinctness, descriptive (§3.1)
    try:
        row = conn.execute(text(
            f'SELECT count(*) AS total, (SELECT count(*) FROM (SELECT DISTINCT * FROM bronze."{table}") s) AS distinct_rows '
            f'FROM bronze."{table}"'
        )).mappings().one()
        total, distinct_rows = row["total"], row["distinct_rows"]
        rate = round(1 - (distinct_rows / total), 4) if total else 0.0
        _upsert_metric(
            db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.raw_duplicates, "",
            rate, {"duplicate_rows": total - distinct_rows, "total": total}, _status_for(QualityIndicator.raw_duplicates, rate),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: raw_duplicates failed for bronze.%s: %s", table, exc)
        _upsert_metric(db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.raw_duplicates, "", None, {"error": str(exc)}, QualityMetricStatus.skipped)

    _collect_parsing_rejection_rate(db, snapshot, dataset, table, conn)


def _collect_parsing_rejection_rate(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, table: str, conn: Connection) -> None:
    """Module 6 extension (payload & structuration) §7.2 — `taux_de_quarantaine` fills in the
    catalogue's pre-existing `parsing_rejection_rate` indicator (declared in Étape 1's scope
    but never computed there — nothing produced a rejection rate to measure before this
    extension's `__parsed`/`__quarantine` split existed). Only applies to a bronze dataset
    that actually has a validated structuration contract; every other project collects
    exactly as before (§0 "zéro régression")."""
    has_contract = db.query(PayloadStructuration.id).filter(PayloadStructuration.dataset_id == dataset.id).first() is not None
    if not has_contract:
        return
    try:
        row = conn.execute(text(
            f'SELECT '
            f'(SELECT count(*) FROM bronze."{table}__parsed") AS clean, '
            f'(SELECT count(*) FROM bronze."{table}__quarantine") AS rejected'
        )).mappings().one()
        clean, rejected = row["clean"], row["rejected"]
        total = clean + rejected
        rate = round(rejected / total, 4) if total else 0.0
        _upsert_metric(
            db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.parsing_rejection_rate, "",
            rate, {"rejected": rejected, "clean": clean, "total": total},
            _status_for(QualityIndicator.parsing_rejection_rate, rate),
        )
    except Exception as exc:
        # Most common cause: no build/run yet since the contract was saved — __parsed/
        # __quarantine don't exist. Not an error worth alarming over, just nothing to measure.
        logger.info("quality_intrinsic: parsing_rejection_rate failed for bronze.%s: %s", table, exc)
        _upsert_metric(db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.parsing_rejection_rate, "", None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_referential_integrity(db: Session, project: MedallionProject, snapshots_by_dataset: dict[int, DataQualitySnapshot], bronze_by_source_object: dict[str, MedallionDataset], conn: Connection) -> None:
    """§3.2 — orphans, computed directly on the BRONZE tables: source_relationships (M14) was
    itself computed against these exact table/column names, and a prebuilt-join silver's INNER
    JOIN can never show an orphan by construction (a row only exists post-join if it matched) —
    the bronze pair is the only place these column names are guaranteed valid AND the check is
    actually meaningful. Recorded as layer="silver" per the catalogue (§3.2): what it answers
    is "is the join this silver performs built on sound data", even though the query itself
    reads bronze."""
    source_ids = {ds.source_id for ds in bronze_by_source_object.values() if ds.source_id}
    if not source_ids:
        return
    rels = (
        db.query(SourceRelationship)
        .filter(SourceRelationship.data_source_id.in_(source_ids), SourceRelationship.verified.is_(True))
        .all()
    )
    for rel in rels:
        child_ds = bronze_by_source_object.get(rel.child_table)
        parent_ds = bronze_by_source_object.get(rel.parent_table)
        if child_ds is None or parent_ds is None:
            continue  # relationship involves a table not registered as bronze in THIS project
        child_snap = snapshots_by_dataset.get(child_ds.id)
        if child_snap is None:
            continue
        child_table, parent_table = table_name(child_ds), table_name(parent_ds)
        if not child_table or not parent_table:
            continue
        target = f"{rel.child_table}.{rel.child_column}→{rel.parent_table}.{rel.parent_column}"
        try:
            # LEFT JOIN + IS NULL rather than a correlated NOT EXISTS: neither bronze table has
            # an index (bronze is a raw, unindexed landing zone), and a correlated subquery
            # degrades to a nested-loop sequential scan per child row — confirmed on real data
            # (45k-row child, unindexed parent) to hang well past any reasonable budget. A hash
            # anti-join needs no index on either side and stays fast at this scale.
            sql = text(
                f'SELECT count(*) AS total, count(*) FILTER (WHERE p.{_q(rel.parent_column)} IS NULL) AS orphans '
                f'FROM bronze."{child_table}" c LEFT JOIN bronze."{parent_table}" p '
                f'ON p.{_q(rel.parent_column)} = c.{_q(rel.child_column)} '
                f'WHERE c.{_q(rel.child_column)} IS NOT NULL'
            )
            row = conn.execute(sql).mappings().one()
            total, orphans = row["total"], row["orphans"]
            rate = round(orphans / total, 4) if total else 0.0
            _upsert_metric(
                db, child_snap, child_ds, QualityLayer.silver, QualityIndicator.referential_integrity, target,
                rate, {"orphans": orphans, "total": total}, _status_for(QualityIndicator.referential_integrity, rate),
            )
        except Exception as exc:
            logger.info("quality_intrinsic: referential_integrity failed for %s: %s", target, exc)
            _upsert_metric(db, child_snap, child_ds, QualityLayer.silver, QualityIndicator.referential_integrity, target, None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_join_loss(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, datasets_by_id: dict[int, MedallionDataset], conn: Connection) -> None:
    """§3.2 — 1 − rows_silver / rows_bronze_éligibles. Only computed when this silver's
    upstreams resolve to at least one bronze dataset; the largest bronze upstream is taken as
    the "eligible" fact-table row count (a heuristic — documented, not hidden) when several are
    joined in."""
    table = table_name(dataset)
    if not table:
        return
    bronze_upstreams = [datasets_by_id[i] for i in dataset.upstream_dataset_ids if i in datasets_by_id and datasets_by_id[i].layer == MedallionLayer.bronze]
    if not bronze_upstreams:
        return
    try:
        silver_count = conn.execute(text(f'SELECT count(*) FROM silver."{table}"')).scalar()
        bronze_counts = {}
        for b in bronze_upstreams:
            btable = table_name(b)
            if not btable:
                continue
            bronze_counts[b.name] = conn.execute(text(f'SELECT count(*) FROM bronze."{btable}"')).scalar()
        if not bronze_counts:
            return
        eligible = max(bronze_counts.values())
        rate = round(1 - (silver_count / eligible), 4) if eligible else 0.0
        _upsert_metric(
            db, snapshot, dataset, QualityLayer.silver, QualityIndicator.join_loss, "",
            max(0.0, rate), {"rows_silver": silver_count, "rows_bronze_eligible": eligible, "bronze_counts": bronze_counts},
            _status_for(QualityIndicator.join_loss, max(0.0, rate)),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: join_loss failed for silver.%s: %s", table, exc)
        _upsert_metric(db, snapshot, dataset, QualityLayer.silver, QualityIndicator.join_loss, "", None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_grain_uniqueness(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, grain: list[str], conn: Connection) -> None:
    """§3.2 catalogue lists this under silver; computed here against GOLD instead, where the
    plan (Module 14's gold_grain()) actually persists a declared grain today — silver carries
    no equivalent declared-grain field yet. Skipped (not guessed) when a gold has no grain at
    all (a single-row aggregate, e.g. no dimension_columns/time_column — uniqueness is
    trivially true there)."""
    if not grain:
        return
    table = table_name(dataset)
    if not table:
        return
    try:
        cols = ", ".join(_q(c) for c in grain)
        row = conn.execute(text(f'SELECT count(*) AS total, count(DISTINCT ({cols})) AS distinct_rows FROM gold."{table}"')).mappings().one()
        total, distinct_rows = row["total"], row["distinct_rows"]
        rate = round(1 - (distinct_rows / total), 4) if total else 0.0
        _upsert_metric(
            db, snapshot, dataset, QualityLayer.gold, QualityIndicator.grain_uniqueness, ", ".join(grain),
            rate, {"duplicate_rows": total - distinct_rows, "total": total, "grain": grain}, _status_for(QualityIndicator.grain_uniqueness, rate),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: grain_uniqueness failed for gold.%s: %s", table, exc)
        _upsert_metric(db, snapshot, dataset, QualityLayer.gold, QualityIndicator.grain_uniqueness, ", ".join(grain), None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _bronze_source_for_column(dataset: MedallionDataset, column: str, datasets_by_id: dict[int, MedallionDataset], warehouse: DataSource, columns_cache: dict[int, set[str]]) -> MedallionDataset | None:
    """Walks a gold/silver dataset's own upstream_dataset_ids looking for a BRONZE ancestor
    that actually has `column` — the "original source" a conservative_measure/
    required_dimension needs for end_to_end_conservation / dimensional_completeness. Only one
    hop of indirection is needed in practice (gold -> its silver's own upstream bronze), since
    every silver in this pipeline is either a prebuilt join (preserves bronze column names
    unchanged) or an AI-authored silver whose AST-validated output already had to reference
    real bronze columns (Module 14 §6.3) — so the column name survives to bronze either way."""
    seen: set[int] = set()
    frontier = [dataset]
    while frontier:
        current = frontier.pop()
        if current.id in seen:
            continue
        seen.add(current.id)
        if current.layer == MedallionLayer.bronze:
            if current.id not in columns_cache:
                columns_cache[current.id] = {c["column"] for c in list_columns(warehouse, current)}
            if column in columns_cache[current.id]:
                return current
            continue
        for uid in current.upstream_dataset_ids:
            up = datasets_by_id.get(uid)
            if up is not None:
                frontier.append(up)
    return None


def _collect_ingestion_completeness(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, expected_volume: int, conn: Connection) -> None:
    """§6.3/§3.1 — 1 − rows_bronze / rows_source_attendues, |·| so both under- AND
    over-ingestion (a genuinely wrong extraction, not just a shortfall) register as a real
    defect rather than an over-ingestion silently reading as "better than perfect"."""
    table = table_name(dataset)
    if not table:
        return
    try:
        actual = conn.execute(text(f'SELECT count(*) FROM bronze."{table}"')).scalar()
        rate = round(abs(1 - (actual / expected_volume)), 4) if expected_volume else 0.0
        _upsert_metric(
            db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.ingestion_completeness, "",
            rate, {"actual": actual, "expected": expected_volume}, _status_for(QualityIndicator.ingestion_completeness, rate),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: ingestion_completeness failed for bronze.%s: %s", table, exc)
        _upsert_metric(db, snapshot, dataset, QualityLayer.bronze, QualityIndicator.ingestion_completeness, "", None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_conservative_measure(
    db: Session, snapshot: DataQualitySnapshot, gold: MedallionDataset, silver: MedallionDataset, column: str,
    datasets_by_id: dict[int, MedallionDataset], warehouse: DataSource, columns_cache: dict[int, set[str]], conn: Connection,
) -> None:
    """§6.3/§3.3 — aggregate_reconciliation (gold vs its own silver) AND end_to_end_conservation
    (gold vs the original bronze), for ONE conservative measure on ONE gold that actually uses
    it as a SUM metric_column. Two independent try/except: a bronze ancestor not resolvable
    (end_to_end) never prevents the silver-level reconciliation check from still running."""
    gold_table, silver_table = table_name(gold), table_name(silver)
    if not gold_table or not silver_table:
        return

    gold_sum = None
    try:
        gold_alias = f"total_{column.lower()}"
        gold_sum = conn.execute(text(f'SELECT COALESCE(SUM({_q(gold_alias)}), 0) FROM gold."{gold_table}"')).scalar()
    except Exception as exc:
        logger.info("quality_intrinsic: could not read gold.%s.%s for reconciliation: %s", gold_table, gold_alias, exc)

    try:
        if gold_sum is None:
            raise ValueError("gold sum unavailable")
        silver_sum = conn.execute(text(f'SELECT COALESCE(SUM({_q(column)}), 0) FROM silver."{silver_table}"')).scalar()
        rate = round(abs(gold_sum - silver_sum) / silver_sum, 4) if silver_sum else 0.0
        _upsert_metric(
            db, snapshot, gold, QualityLayer.gold, QualityIndicator.aggregate_reconciliation, column,
            rate, {"sum_gold": gold_sum, "sum_silver": silver_sum}, _status_for(QualityIndicator.aggregate_reconciliation, rate),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: aggregate_reconciliation failed for %s.%s: %s", gold_table, column, exc)
        _upsert_metric(db, snapshot, gold, QualityLayer.gold, QualityIndicator.aggregate_reconciliation, column, None, {"error": str(exc)}, QualityMetricStatus.skipped)

    bronze = _bronze_source_for_column(silver, column, datasets_by_id, warehouse, columns_cache)
    if bronze is None:
        return
    bronze_table = table_name(bronze)
    if not bronze_table:
        return
    try:
        if gold_sum is None:
            raise ValueError("gold sum unavailable")
        bronze_sum = conn.execute(text(f'SELECT COALESCE(SUM({_q(column)}), 0) FROM bronze."{bronze_table}"')).scalar()
        rate = round(abs(1 - (gold_sum / bronze_sum)), 4) if bronze_sum else 0.0
        _upsert_metric(
            db, snapshot, gold, QualityLayer.gold, QualityIndicator.end_to_end_conservation, column,
            rate, {"sum_gold": gold_sum, "sum_source": bronze_sum, "source_table": bronze.name}, _status_for(QualityIndicator.end_to_end_conservation, rate),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: end_to_end_conservation failed for %s.%s: %s", gold_table, column, exc)
        _upsert_metric(db, snapshot, gold, QualityLayer.gold, QualityIndicator.end_to_end_conservation, column, None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_dimensional_completeness(
    db: Session, snapshot: DataQualitySnapshot, gold: MedallionDataset, silver: MedallionDataset, column: str,
    datasets_by_id: dict[int, MedallionDataset], warehouse: DataSource, columns_cache: dict[int, set[str]], conn: Connection,
) -> None:
    """§6.3/§3.3 — 1 − count(distinct dim présentes en gold) / count(distinct dim attendues en
    bronze). The bronze ancestor is the "universe" of possible values (e.g. every department
    that exists, even one with zero admissions this run) — the gold is what actually made it
    through."""
    gold_table = table_name(gold)
    if not gold_table:
        return
    bronze = _bronze_source_for_column(silver, column, datasets_by_id, warehouse, columns_cache)
    if bronze is None:
        return
    bronze_table = table_name(bronze)
    if not bronze_table:
        return
    try:
        gold_distinct = conn.execute(text(f'SELECT count(DISTINCT {_q(column)}) FROM gold."{gold_table}"')).scalar()
        bronze_distinct = conn.execute(text(f'SELECT count(DISTINCT {_q(column)}) FROM bronze."{bronze_table}"')).scalar()
        rate = round(max(0.0, 1 - (gold_distinct / bronze_distinct)), 4) if bronze_distinct else 0.0
        _upsert_metric(
            db, snapshot, gold, QualityLayer.gold, QualityIndicator.dimensional_completeness, column,
            rate, {"distinct_gold": gold_distinct, "distinct_expected": bronze_distinct, "source_table": bronze.name}, _status_for(QualityIndicator.dimensional_completeness, rate),
        )
    except Exception as exc:
        logger.info("quality_intrinsic: dimensional_completeness failed for %s.%s: %s", gold_table, column, exc)
        _upsert_metric(db, snapshot, gold, QualityLayer.gold, QualityIndicator.dimensional_completeness, column, None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_baseline_indicators(
    db: Session, project: MedallionProject, plan: dict, datasets: list[MedallionDataset],
    datasets_by_id: dict[int, MedallionDataset], snapshots_by_dataset: dict[int, DataQualitySnapshot],
    warehouse: DataSource, conn: Connection,
) -> None:
    baseline = db.query(DataQualityBaseline).filter(DataQualityBaseline.project_id == project.id).first()
    if baseline is None or not baseline.assertions:
        return
    assertions = baseline.assertions
    names_to_dataset = {d.name: d for d in datasets}
    columns_cache: dict[int, set[str]] = {}

    vol = assertions.get("expected_source_volume")
    if vol and vol.get("value") is not None:
        source_ds = names_to_dataset.get(vol["source_ref"])
        if source_ds is not None and source_ds.layer == MedallionLayer.bronze:
            snap = snapshots_by_dataset.get(source_ds.id)
            if snap is not None:
                _collect_ingestion_completeness(db, snap, source_ds, vol["value"], conn)
        elif source_ds is None:
            logger.info("quality_intrinsic: baseline expected_source_volume.source_ref '%s' has no matching materialized bronze dataset (project %s)", vol["source_ref"], project.id)

    conservative_columns = [m["column"] for m in (assertions.get("conservative_measures") or []) if m.get("column")]
    required_columns = [d["column"] for d in (assertions.get("required_dimensions") or []) if d.get("column")]
    if not conservative_columns and not required_columns:
        return

    for g in plan.get("gold", []):
        gold_ds = names_to_dataset.get(g["name"])
        if gold_ds is None:
            # Plan and materialized datasets diverge — a /replan since the last /execute
            # (PipelinePlan is a single upserted row per project, not versioned: its "gold"
            # names can outrun what's actually built). Not an error, just nothing to compute
            # against yet; logged (not silently skipped) so this is diagnosable without a
            # direct DB comparison.
            logger.info("quality_intrinsic: baseline gold '%s' from the current plan has no matching materialized dataset (project %s) — plan likely replanned since the last execute", g["name"], project.id)
            continue
        snap = snapshots_by_dataset.get(gold_ds.id)
        if snap is None:
            continue
        silver_name = (g.get("upstreams") or [None])[0]
        silver_ds = names_to_dataset.get(silver_name) if silver_name else None
        if silver_ds is None:
            logger.info("quality_intrinsic: baseline gold '%s' upstream '%s' has no matching materialized dataset (project %s)", g["name"], silver_name, project.id)
            continue

        if g.get("aggregation") == "SUM" and g.get("metric_column") in conservative_columns:
            _collect_conservative_measure(db, snap, gold_ds, silver_ds, g["metric_column"], datasets_by_id, warehouse, columns_cache, conn)

        for col in g.get("dimension_columns") or []:
            if col in required_columns:
                _collect_dimensional_completeness(db, snap, gold_ds, silver_ds, col, datasets_by_id, warehouse, columns_cache, conn)


# Module 16 §7.2/§7.3 — type_conformity's closed vocabulary (unlike format_validity, whose
# pattern is free/AI-proposed per §3.1 "propose le type_cible depuis le nom/sémantique" but the
# EVALUATION stays a fixed, deterministic regex per named type — never a pattern the AI writes
# itself for this indicator). Applied to `lower(col::text)` so the patterns themselves can stay
# lowercase. A real Postgres CAST-and-catch would need a PL/pgSQL wrapper function; a regex
# heuristic is the same "closed, deterministic, disclosed" trade-off Module 14's primitives
# already made for date_diff/RATIO rather than introducing a stored procedure.
TYPE_CONFORMITY_PATTERNS: dict[str, str] = {
    "integer": r"^-?[0-9]+$",
    "numeric": r"^-?[0-9]+(\.[0-9]+)?$",
    "date": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    "email": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "boolean": r"^(true|false|t|f|0|1|yes|no)$",
    "phone": r"^\+?[0-9()\-\s]{6,20}$",
}


def _sql_string_literal(s: str) -> str:
    """Single-quote a string for direct SQL embedding (doubled '' escaping) — used for
    format_validity's regex pattern, the one contract-check parameter that's a string value
    rather than a pre-validated SQL fragment (predicate/condition are embedded as-is, already
    AST-checked by sql_validator.validate_predicate_sql at write time, same trust boundary
    _q() documents for identifiers)."""
    return "'" + s.replace("'", "''") + "'"


def _collect_check(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, layer: QualityLayer, check: DataQualityCheck, conn: Connection) -> None:
    """Module 16 §7.3 — evaluates ONE active, already-validated DataQualityCheck (validated once
    at write time — POST /quality/checks — never re-validated here, same "trust once past the
    boundary" discipline as every other resolved/whitelisted structure in this codebase). Each
    check_type's SQL mirrors §3's formula table exactly."""
    table = table_name(dataset)
    if not table:
        return
    schema = SCHEMA_BY_LAYER[dataset.layer]
    params = check.parameters or {}
    indicator = QualityIndicator(check.check_type.value)  # CheckType/QualityIndicator share the same 5 string values

    try:
        if check.check_type == CheckType.type_conformity:
            col = _q(check.target_column)
            pattern = TYPE_CONFORMITY_PATTERNS.get(params.get("target_type"))
            if pattern is None:
                raise ValueError(f"type_cible inconnu : {params.get('target_type')!r}")
            row = conn.execute(text(
                f'SELECT count(*) FILTER (WHERE {col} IS NOT NULL) AS total, '
                f'count(*) FILTER (WHERE {col} IS NOT NULL AND lower({col}::text) ~ {_sql_string_literal(pattern)}) AS conforming '
                f'FROM {schema}."{table}"'
            )).mappings().first()
            total, conforming = row["total"], row["conforming"]
            rate = round(1 - (conforming / total), 4) if total else 0.0
            raw = {"conforming": conforming, "total": total, "target_type": params.get("target_type")}

        elif check.check_type == CheckType.format_validity:
            col = _q(check.target_column)
            pattern = params.get("pattern") or ""
            row = conn.execute(text(
                f'SELECT count(*) FILTER (WHERE {col} IS NOT NULL) AS total, '
                f'count(*) FILTER (WHERE {col} IS NOT NULL AND {col}::text ~ {_sql_string_literal(pattern)}) AS conforming '
                f'FROM {schema}."{table}"'
            )).mappings().first()
            total, conforming = row["total"], row["conforming"]
            rate = round(1 - (conforming / total), 4) if total else 0.0
            raw = {"conforming": conforming, "total": total, "pattern": pattern}

        elif check.check_type == CheckType.intra_row_consistency:
            predicate = params.get("predicate") or "TRUE"
            row = conn.execute(text(
                f'SELECT count(*) AS total, count(*) FILTER (WHERE NOT ({predicate})) AS violations FROM {schema}."{table}"'
            )).mappings().first()
            total, violations = row["total"], row["violations"]
            rate = round(violations / total, 4) if total else 0.0
            raw = {"violations": violations, "total": total, "predicate": predicate}

        elif check.check_type == CheckType.plausibility:
            col = _q(check.target_column)
            bounds = []
            if params.get("min") is not None:
                bounds.append(f"{col} < {float(params['min'])}")
            if params.get("max") is not None:
                bounds.append(f"{col} > {float(params['max'])}")
            if not bounds:
                raise ValueError("aucune borne min/max déclarée")
            condition = " OR ".join(bounds)
            row = conn.execute(text(
                f'SELECT count(*) AS total, count(*) FILTER (WHERE {col} IS NOT NULL AND ({condition})) AS violations FROM {schema}."{table}"'
            )).mappings().first()
            total, violations = row["total"], row["violations"]
            rate = round(violations / total, 4) if total else 0.0
            raw = {"violations": violations, "total": total, "min": params.get("min"), "max": params.get("max")}

        elif check.check_type == CheckType.conditional_completeness:
            condition = params.get("condition") or "FALSE"
            target = _q(params.get("target") or "")
            row = conn.execute(text(
                f'SELECT count(*) FILTER (WHERE {condition}) AS total, '
                f'count(*) FILTER (WHERE ({condition}) AND {target} IS NULL) AS violations FROM {schema}."{table}"'
            )).mappings().first()
            total, violations = row["total"], row["violations"]
            rate = round(violations / total, 4) if total else 0.0
            raw = {"violations": violations, "total": total, "condition": condition, "target": params.get("target")}

        else:
            return

        _upsert_metric(db, snapshot, dataset, layer, indicator, check.target_column, rate, raw, _status_for(indicator, rate))
    except Exception as exc:
        logger.info("quality_intrinsic: check #%s (%s) failed for %s.%s: %s", check.id, check.check_type.value, table, check.target_column, exc)
        _upsert_metric(db, snapshot, dataset, layer, indicator, check.target_column, None, {"error": str(exc)}, QualityMetricStatus.skipped)


def _collect_contract_checks(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset, checks: list[DataQualityCheck], conn: Connection) -> None:
    for check in checks:
        _collect_check(db, snapshot, dataset, check.layer, check, conn)


def collect(db: Session, project: MedallionProject, datasets: list[MedallionDataset], warehouse: DataSource, run: MedallionRun) -> list[DataQualityMetric]:
    """Best-effort, called right after quality_collector.collect_quality() in the SAME
    post-run passage (§4.3) — never raises, never affects the triggering run. Computes every
    auto-portant indicator (§4.1) applicable to each dataset's layer."""
    if not datasets:
        return []

    datasets_by_id = {d.id: d for d in datasets}
    bronze_by_source_object = {d.source_object: d for d in datasets if d.layer == MedallionLayer.bronze and d.source_object}

    plan_row = db.query(PipelinePlan).filter(PipelinePlan.project_id == project.id).order_by(PipelinePlan.id.desc()).first()
    grain_by_gold_name: dict[str, list[str]] = {}
    if plan_row and plan_row.plan:
        for g in plan_row.plan.get("gold", []):
            grain_by_gold_name[g["name"]] = g.get("grain") or []

    # §7.3 — active contract checks, grouped by dataset (project-wide checks, dataset_id=None,
    # are Étape 4's declared-but-not-yet-scoped case — §7.2 allows the field but nothing in this
    # codebase resolves a project-wide check to a table yet, so they're loaded but simply never
    # matched below; not a regression, just not wired until a real use case asks for it).
    active_checks = db.query(DataQualityCheck).filter(DataQualityCheck.project_id == project.id, DataQualityCheck.status == CheckStatus.active).all()
    checks_by_dataset: dict[int, list[DataQualityCheck]] = {}
    for c in active_checks:
        if c.dataset_id is not None:
            checks_by_dataset.setdefault(c.dataset_id, []).append(c)

    try:
        warehouse_secret = decrypt_secret(warehouse.secret_encrypted)
        engine = create_engine(
            f"postgresql+psycopg://{warehouse.username}:{warehouse_secret}@{warehouse.host}:{warehouse.port}/{warehouse.database_name}",
            connect_args={"connect_timeout": 5},
        )
    except Exception as exc:
        logger.warning("quality_intrinsic: could not build warehouse engine for project %s: %s", project.id, exc)
        return []

    written: list[DataQualityMetric] = []
    snapshots_by_dataset: dict[int, DataQualitySnapshot] = {}
    try:
        with engine.connect() as conn:
            # AUTOCOMMIT: each statement below is its own independent unit. Without this, a
            # SQLAlchemy Core connection keeps ONE implicit transaction open across every
            # execute() call on it — so the moment statement_timeout cancels one query, that
            # transaction is aborted and EVERY subsequent query on this same connection (i.e.
            # every remaining indicator) would fail too ("current transaction is aborted"),
            # exactly the kind of one-bad-indicator-takes-down-the-rest failure §2's
            # "indépendant par indicateur" rule forbids.
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            # "timeout court" (§2) enforced server-side, not via SQLAlchemy's own
            # execution_options (which has no generic "timeout" — that was a silent no-op,
            # confirmed on real data: an unindexed 45k-row bronze table hung well past 60s
            # before this was added).
            conn.execute(text("SET statement_timeout = 8000"))

            for ds in datasets:
                snap = _get_or_create_snapshot(db, ds, project, run)
                snapshots_by_dataset[ds.id] = snap
                if ds.layer == MedallionLayer.bronze:
                    _collect_bronze(db, snap, ds, warehouse, conn)
                elif ds.layer == MedallionLayer.silver:
                    _collect_join_loss(db, snap, ds, datasets_by_id, conn)
                elif ds.layer == MedallionLayer.gold:
                    _collect_grain_uniqueness(db, snap, ds, grain_by_gold_name.get(ds.name, []), conn)
                if ds.id in checks_by_dataset:
                    _collect_contract_checks(db, snap, ds, checks_by_dataset[ds.id], conn)

            _collect_referential_integrity(db, project, snapshots_by_dataset, bronze_by_source_object, conn)
            # §6.3 — no-op when the project has declared no baseline (get-query returns None,
            # every assertion block is then skipped): a project with no baseline collects
            # exactly as it did before this étape existed.
            _collect_baseline_indicators(db, project, plan_row.plan if plan_row else {}, datasets, datasets_by_id, snapshots_by_dataset, warehouse, conn)
    except Exception as exc:
        logger.warning("quality_intrinsic: warehouse unreachable for project %s: %s", project.id, exc)
        db.commit()  # keep whatever snapshots/metrics were already written before the failure
        return []
    finally:
        engine.dispose()

    db.commit()
    written = (
        db.query(DataQualityMetric)
        .filter(DataQualityMetric.snapshot_id.in_([s.id for s in snapshots_by_dataset.values()]))
        .all()
    )
    return written
