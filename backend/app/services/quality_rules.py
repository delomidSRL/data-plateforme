from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.data_quality import AlertSeverity, AlertStatus, AlertType, DataQualityAlert, DataQualityMetric, DataQualityRule, DataQualitySnapshot, QualityIndicator
from app.models.medallion import MedallionDataset
from app.services import quality_intrinsic

DEFAULT_RULE = {
    "volume_variation_pct": 30,
    "min_rows": 1,
    "freshness_max_hours": 26,
    "alert_on_test_failure": True,
    "alert_on_schema_change": True,
}


def get_or_create_rule(db: Session, project_id: int) -> DataQualityRule:
    rule = db.query(DataQualityRule).filter(DataQualityRule.project_id == project_id, DataQualityRule.dataset_id.is_(None)).first()
    if rule is None:
        rule = DataQualityRule(project_id=project_id, **DEFAULT_RULE)
        db.add(rule)
        db.commit()
        db.refresh(rule)
    return rule


def _has_open_duplicate(db: Session, dataset_id: int, alert_type: AlertType, message: str) -> bool:
    """Anti-bruit : ne pas ré-émettre une alerte identique déjà ouverte pour le même dataset."""
    return (
        db.query(DataQualityAlert)
        .filter(
            DataQualityAlert.dataset_id == dataset_id,
            DataQualityAlert.type == alert_type,
            DataQualityAlert.status == AlertStatus.open,
            DataQualityAlert.message == message,
        )
        .first()
        is not None
    )


def evaluate_snapshot(db: Session, snapshot: DataQualitySnapshot, dataset: MedallionDataset) -> list[DataQualityAlert]:
    """Compares a freshly-collected snapshot against the project's rule + the dataset's previous
    snapshot, materializing threshold breaches as DataQualityAlert rows. Never raises."""
    rule = get_or_create_rule(db, snapshot.project_id)
    alerts: list[DataQualityAlert] = []

    previous = (
        db.query(DataQualitySnapshot)
        .filter(DataQualitySnapshot.dataset_id == snapshot.dataset_id, DataQualitySnapshot.id != snapshot.id)
        .order_by(DataQualitySnapshot.collected_at.desc())
        .first()
    )

    def add(alert_type: AlertType, severity: AlertSeverity, message: str):
        if _has_open_duplicate(db, snapshot.dataset_id, alert_type, message):
            return
        alerts.append(
            DataQualityAlert(
                dataset_id=snapshot.dataset_id, project_id=snapshot.project_id, snapshot_id=snapshot.id,
                type=alert_type, severity=severity, message=message,
            )
        )

    # volume — 0 lignes / sous le plancher, ou variation au-delà du seuil vs run précédent
    if snapshot.row_count is not None:
        if snapshot.row_count < rule.min_rows:
            add(
                AlertType.volume, AlertSeverity.critical,
                f"{dataset.name} : volume sous le seuil minimal ({snapshot.row_count} < {rule.min_rows} lignes).",
            )
        elif previous is not None and previous.row_count:
            variation = (snapshot.row_count - previous.row_count) / previous.row_count * 100
            if abs(variation) >= rule.volume_variation_pct:
                add(
                    AlertType.volume, AlertSeverity.warning,
                    f"{dataset.name} : chute de volume {variation:+.1f}% ({previous.row_count} → {snapshot.row_count}).",
                )

    # fraîcheur — table pas rechargée depuis plus de N heures
    if snapshot.loaded_at is not None:
        loaded_at = snapshot.loaded_at if snapshot.loaded_at.tzinfo else snapshot.loaded_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - loaded_at).total_seconds() / 3600
        if age_hours > rule.freshness_max_hours:
            add(
                AlertType.freshness, AlertSeverity.warning,
                f"{dataset.name} : dernière mise à jour il y a {age_hours:.0f} h (seuil {rule.freshness_max_hours} h).",
            )

    # tests — un test dbt en échec
    if rule.alert_on_test_failure and snapshot.tests_failed > 0:
        add(
            AlertType.tests, AlertSeverity.critical,
            f"{dataset.name} : {snapshot.tests_failed} test(s) dbt en échec.",
        )

    # schéma — colonne ajoutée/supprimée/retypée vs le run précédent
    if rule.alert_on_schema_change and previous is not None and previous.schema_hash and snapshot.schema_hash and previous.schema_hash != snapshot.schema_hash:
        add(
            AlertType.schema, AlertSeverity.warning,
            f"{dataset.name} : schéma modifié depuis le dernier run.",
        )

    for a in alerts:
        db.add(a)
    if alerts:
        db.commit()
        for a in alerts:
            db.refresh(a)
    return alerts


# ---------------------------------------------------------------------------------------------
# Module 16 §8.3 — extension: coherence (⟨intrinsic-quality⟩) alerts from DataQualityMetric,
# same anti-noise/unified-list discipline as evaluate_snapshot above, added to THIS SAME module.
# ---------------------------------------------------------------------------------------------

# §8.2 — which of the 6 new AlertType values each indicator falls under. Several indicators
# share one type (e.g. referential_integrity/join_loss/grain_uniqueness → integrity) — that's
# the point of typed-not-per-indicator alerts (§8.1): the engineer sees "an integrity problem
# on this dataset", the metric/raw behind it gives the specifics.
_INDICATOR_ALERT_TYPE: dict[QualityIndicator, AlertType] = {
    QualityIndicator.referential_integrity: AlertType.integrity,
    QualityIndicator.join_loss: AlertType.integrity,
    QualityIndicator.grain_uniqueness: AlertType.integrity,
    QualityIndicator.type_conformity: AlertType.validity,
    QualityIndicator.format_validity: AlertType.validity,
    QualityIndicator.intra_row_consistency: AlertType.consistency,
    QualityIndicator.aggregate_reconciliation: AlertType.reconciliation,
    QualityIndicator.end_to_end_conservation: AlertType.reconciliation,
    QualityIndicator.plausibility: AlertType.plausibility,
    QualityIndicator.ingestion_completeness: AlertType.completeness,
    QualityIndicator.dimensional_completeness: AlertType.completeness,
    QualityIndicator.conditional_completeness: AlertType.completeness,
    # Module 6 extension (payload & structuration) §7.2 — a high quarantine rate is a
    # structural-validity defect (rows whose values don't conform to the contract's types),
    # same family as type_conformity/format_validity.
    QualityIndicator.parsing_rejection_rate: AlertType.validity,
}


def _coherence_threshold(indicator: QualityIndicator, rule: DataQualityRule) -> float | None:
    """The defect_rate ceiling (0-1) above which this indicator alerts. Uses the project's own
    configured DataQualityRule field when §8.2 defines one; intra_row_consistency and the
    *_completeness indicators have no dedicated field in the spec's 5-field rule model (6 alert
    types, only 5 threshold fields — a disclosed spec gap, not an omission), so they fall back
    to the same fixed value quality_intrinsic._CRITICAL_AT already uses to color the Étape 2
    matrix red — an alert never fires on a cell that wasn't already showing critical there."""
    if indicator == QualityIndicator.referential_integrity:
        return rule.orphan_rate_max
    if indicator == QualityIndicator.join_loss:
        return rule.join_loss_max_pct / 100
    if indicator == QualityIndicator.grain_uniqueness:
        return 0.0
    if indicator in (QualityIndicator.type_conformity, QualityIndicator.format_validity):
        return 1 - rule.validity_min_pct / 100
    if indicator in (QualityIndicator.aggregate_reconciliation, QualityIndicator.end_to_end_conservation):
        return rule.reconciliation_tolerance_pct / 100
    if indicator == QualityIndicator.plausibility:
        return rule.plausibility_max_pct / 100
    return quality_intrinsic._CRITICAL_AT.get(indicator)


_INDICATOR_ALERT_LABEL = {
    QualityIndicator.referential_integrity: "intégrité référentielle",
    QualityIndicator.join_loss: "perte à la jointure",
    QualityIndicator.grain_uniqueness: "unicité du grain",
    QualityIndicator.type_conformity: "conformité de type",
    QualityIndicator.format_validity: "validité de format",
    QualityIndicator.intra_row_consistency: "cohérence intra-ligne",
    QualityIndicator.aggregate_reconciliation: "réconciliation d'agrégat",
    QualityIndicator.end_to_end_conservation: "conservation bout-en-bout",
    QualityIndicator.plausibility: "plausibilité",
    QualityIndicator.ingestion_completeness: "complétude d'ingestion",
    QualityIndicator.dimensional_completeness: "complétude dimensionnelle",
    QualityIndicator.conditional_completeness: "complétude conditionnelle",
    QualityIndicator.parsing_rejection_rate: "taux de quarantaine (structuration payload)",
}

# §6.5/§8.4 — the "double chemin" the frontend offers on a baseline-backed alert: the engineer
# either fixes the data or admits the baseline itself was wrong and updates it. Only the 4
# ⟨baseline⟩ indicators carry this choice — a ⟨contrat⟩ or auto-portant defect only has "fix".
_BASELINE_INDICATORS = {
    QualityIndicator.ingestion_completeness, QualityIndicator.aggregate_reconciliation,
    QualityIndicator.end_to_end_conservation, QualityIndicator.dimensional_completeness,
}


def evaluate_coherence(db: Session, project_id: int, metrics: list[DataQualityMetric], dataset_names: dict[int, str]) -> list[DataQualityAlert]:
    """Module 16 §8.3 — one pass over a run's freshly-collected DataQualityMetric rows,
    comparing each to the project's (possibly per-dataset-overridden) DataQualityRule. Never
    raises; same anti-duplicate (_has_open_duplicate) and best-effort discipline as
    evaluate_snapshot. Skipped/None-rate metrics never alert — a measurement that couldn't run
    is not evidence of a defect."""
    if not metrics:
        return []
    project_rule = get_or_create_rule(db, project_id)
    dataset_rules: dict[int, DataQualityRule] = {}
    alerts: list[DataQualityAlert] = []

    def add(dataset_id: int, snapshot_id: int, alert_type: AlertType, severity: AlertSeverity, message: str):
        if _has_open_duplicate(db, dataset_id, alert_type, message):
            return
        alerts.append(DataQualityAlert(dataset_id=dataset_id, project_id=project_id, snapshot_id=snapshot_id, type=alert_type, severity=severity, message=message))

    for m in metrics:
        if m.defect_rate is None or m.indicator not in _INDICATOR_ALERT_TYPE:
            continue
        rule = dataset_rules.setdefault(
            m.dataset_id,
            db.query(DataQualityRule).filter(DataQualityRule.project_id == project_id, DataQualityRule.dataset_id == m.dataset_id).first() or project_rule,
        )
        threshold = _coherence_threshold(m.indicator, rule)
        if threshold is None or m.defect_rate <= threshold:
            continue
        name = dataset_names.get(m.dataset_id, "?")
        label = _INDICATOR_ALERT_LABEL.get(m.indicator, m.indicator.value)
        column = f" ({m.target_column})" if m.target_column else ""
        message = f"{name} : {label}{column} — taux de défaut {m.defect_rate * 100:.2f}% (seuil {threshold * 100:.2f}%)."
        add(m.dataset_id, m.snapshot_id, _INDICATOR_ALERT_TYPE[m.indicator], AlertSeverity.critical, message)

    for a in alerts:
        db.add(a)
    if alerts:
        db.commit()
        for a in alerts:
            db.refresh(a)
    return alerts
