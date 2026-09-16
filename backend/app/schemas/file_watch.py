from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TransportType = Literal["minio", "local"]
PatternTypeLit = Literal["glob", "regex"]
CompletenessLit = Literal["stable_size", "control_file", "none"]
PostProcessLit = Literal["record_only", "move"]
WriteModeLit = Literal["append", "replace"]


class WatchBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    transport: TransportType
    location: dict
    pattern: str = Field(min_length=1, max_length=500)
    pattern_type: PatternTypeLit = "glob"
    poll_interval_seconds: int = Field(default=300, ge=60)
    completeness_strategy: CompletenessLit = "stable_size"
    stable_size_delay_seconds: int = Field(default=30, ge=1, le=3600)
    control_file_suffix: str = Field(default=".done", max_length=50)
    post_process: PostProcessLit = "record_only"
    done_target: str | None = Field(default=None, max_length=1000)
    error_target: str | None = Field(default=None, max_length=1000)
    # §6.3/§6.6 — arrival SLA, both optional and independent: a watch with only one of the two
    # set behaves as if neither were (check_sla requires both, services/file_watch.py).
    arrival_cron: str | None = Field(default=None, max_length=120)
    arrival_grace_minutes: int | None = Field(default=None, ge=0, le=1440)


class WatchCreate(WatchBase):
    file_import_id: int
    write_mode: WriteModeLit

    @field_validator("done_target", "error_target")
    @classmethod
    def _no_path_traversal(cls, v: str | None) -> str | None:
        # §4.3 — done_target/error_target are always a bare subfolder NAME under the watch's
        # own already-validated location, never a free-form path (watch_transport.py's own
        # move() relies on exactly this to inherit containment without a separate check).
        if v is not None and ("/" in v or "\\" in v or v in (".", "..")):
            raise ValueError("doit être un simple nom de sous-dossier (pas de chemin, pas de « .. »).")
        return v


class WatchUpdate(WatchBase):
    write_mode: WriteModeLit

    @field_validator("done_target", "error_target")
    @classmethod
    def _no_path_traversal(cls, v: str | None) -> str | None:
        if v is not None and ("/" in v or "\\" in v or v in (".", "..")):
            raise ValueError("doit être un simple nom de sous-dossier (pas de chemin, pas de « .. »).")
        return v


class WatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    created_by: int | None = None

    file_import_id: int
    transport: str
    location: dict
    pattern: str
    pattern_type: str
    poll_interval_seconds: int
    completeness_strategy: str
    stable_size_delay_seconds: int
    control_file_suffix: str
    post_process: str
    done_target: str | None = None
    error_target: str | None = None
    write_mode: str
    arrival_cron: str | None = None
    arrival_grace_minutes: int | None = None

    status: str
    last_poll_at: datetime | None = None
    last_triggered_at: datetime | None = None
    last_file: str | None = None
    consecutive_failures: int
    last_error: str | None = None

    # §6.6 — computed at read time (never stored), populated by the route handler after
    # loading the ORM row: not a plain from_attributes field, needs a DB lookup + arrival_cron
    # arithmetic (services/file_watch.sla_view).
    next_expected_arrival: str | None = None
    sla_state: Literal["on_time", "late"] | None = None


class WatchEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    watch_id: int
    detected_at: datetime
    file_name: str
    file_checksum: str
    file_size: int
    outcome: str
    rows_imported: int | None = None
    cast_errors: dict | None = None
    severity: str
    error: str | None = None
    acknowledged_at: datetime | None = None


class WatchTestCandidateOut(BaseModel):
    name: str
    size: int
    complete: bool
    reason: str | None = None  # e.g. "taille instable" — populated when complete=False


class WatchTestOut(BaseModel):
    candidates: list[WatchTestCandidateOut] = Field(default_factory=list)


class WatchTestDraftIn(BaseModel):
    """§4.5 — the creation drawer's « Tester » button runs before the watch is ever saved, so
    it needs the transport/pattern/completeness shape alone, no file_import_id/write_mode/name."""
    transport: TransportType
    location: dict
    pattern: str = Field(min_length=1, max_length=500)
    pattern_type: PatternTypeLit = "glob"
    completeness_strategy: CompletenessLit = "stable_size"
    stable_size_delay_seconds: int = Field(default=30, ge=1, le=3600)
    control_file_suffix: str = Field(default=".done", max_length=50)
