"""Module 6 extension — File Watcher. A FileWatch never redeclares schema/target: it points at
an already-validated FileImport (FK, referenced never altered — §0 "non régressif") and answers
one question on a schedule: has a new, complete file matching this pattern appeared? If so it
replays the existing M6 import contract (services/file_watch.py + file_import.py) — no file ever
reaches Airflow (§0/§2 invariant, inherited from Module 6)."""
import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class WatchTransport(str, enum.Enum):
    minio = "minio"
    local = "local"


class WatchPatternType(str, enum.Enum):
    glob = "glob"
    regex = "regex"


class CompletenessStrategy(str, enum.Enum):
    stable_size = "stable_size"
    control_file = "control_file"
    none = "none"


class PostProcessMode(str, enum.Enum):
    record_only = "record_only"
    move = "move"


class FileWatchWriteMode(str, enum.Enum):
    """Deliberately NOT the same enum as FileImportWriteMode (Decision G, §3): a recurring
    watch can never use 'create' — the table already exists after the first file — so this
    restricted vocabulary makes an unsupported value impossible at the type level, not just
    rejected by a validator."""
    append = "append"
    replace = "replace"


class FileWatchStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    error = "error"


class WatchOutcome(str, enum.Enum):
    imported = "imported"
    skipped_duplicate = "skipped_duplicate"
    drift_rejected = "drift_rejected"
    fetch_error = "fetch_error"
    absent_sla = "absent_sla"


class WatchSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class FileWatch(Base):
    __tablename__ = "file_watches"
    __table_args__ = (Index("ix_file_watches_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # cible — le contrat rejoué (Décision C). FK jamais nullable : une surveillance sans
    # import cible n'a pas de sens (contrairement à archive_source_id qui peut disparaître).
    file_import_id: Mapped[int] = mapped_column(ForeignKey("file_imports.id", ondelete="CASCADE"), nullable=False)

    # transport (§4.2)
    transport: Mapped[WatchTransport] = mapped_column(Enum(WatchTransport, name="file_watch_transport"), nullable=False)
    location: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # motif
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    pattern_type: Mapped[WatchPatternType] = mapped_column(Enum(WatchPatternType, name="file_watch_pattern_type"), default=WatchPatternType.glob, nullable=False)

    # cadence
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    # complétude
    completeness_strategy: Mapped[CompletenessStrategy] = mapped_column(Enum(CompletenessStrategy, name="file_watch_completeness_strategy"), default=CompletenessStrategy.stable_size, nullable=False)
    stable_size_delay_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    control_file_suffix: Mapped[str] = mapped_column(String(50), default=".done", nullable=False)

    # SLA d'arrivée (Étape 3 — champs présents dès l'Étape 1 pour éviter une migration
    # ultérieure sur une table déjà en charge de production ; simplement non exploités avant §6)
    arrival_cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    arrival_grace_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # post-traitement
    post_process: Mapped[PostProcessMode] = mapped_column(Enum(PostProcessMode, name="file_watch_post_process"), default=PostProcessMode.record_only, nullable=False)
    done_target: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_target: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # écriture (Décision G)
    write_mode: Mapped[FileWatchWriteMode] = mapped_column(Enum(FileWatchWriteMode, name="file_watch_write_mode"), nullable=False)

    # état
    status: Mapped[FileWatchStatus] = mapped_column(Enum(FileWatchStatus, name="file_watch_status"), default=FileWatchStatus.active, nullable=False)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class FileWatchEvent(Base):
    """One row per file at its TERMINAL verdict (§4.2 note) — a file seen but still awaiting
    completeness never gets a row; it's simply re-evaluated next tick. Keeps the journal
    readable and deduplication a single unique constraint, not a state machine."""
    __tablename__ = "file_watch_events"
    __table_args__ = (
        UniqueConstraint("watch_id", "file_checksum", name="uq_file_watch_event_checksum"),
        Index("ix_file_watch_events_watch_id", "watch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("file_watches.id", ondelete="CASCADE"), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    outcome: Mapped[WatchOutcome] = mapped_column(Enum(WatchOutcome, name="file_watch_outcome"), nullable=False)

    rows_imported: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cast_errors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Pas de table d'historique de réimport dédiée côté Module 6 — chaque réimport écrase les
    # mêmes champs sur LA MÊME ligne FileImport. Cette référence est donc l'horodatage
    # (fi.imported_at) au moment où CET événement a fait aboutir l'import, pour corréler après
    # coup avec l'état du FileImport si besoin, pas une clé étrangère vers une ligne dédiée.
    file_import_reimport_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    severity: Mapped[WatchSeverity] = mapped_column(Enum(WatchSeverity, name="file_watch_severity"), default=WatchSeverity.info, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Étape 3 — acquittement des incidents (aligné sur les alertes Module 5)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
