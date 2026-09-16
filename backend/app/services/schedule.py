"""Cron/preset validation for MedallionProject.schedule (Module 3 correctif). Deliberately
minimal — no croniter dependency (not already present, and the spec explicitly rules out a
heavy addition for this): five-field cron syntax plus the three presets the UI exposes.
Validates *shape*, not calendar semantics (e.g. doesn't catch Feb 30) — good enough to keep
an obviously broken string out of a DAG file, which is this module's only job.
"""
import re

PRESETS = ("@hourly", "@daily", "@weekly")

_FIELD_BOUNDS = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),   # day of week (0 and 7 both accepted as Sunday, matches cron convention)
)


class ScheduleError(Exception):
    pass


def _validate_part(part: str, lo: int, hi: int) -> bool:
    # n | n-m | */step | n/step | n-m/step
    m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part)
    if not m:
        return False
    base, step = m.group(1), m.group(2)
    if step is not None and int(step) <= 0:
        return False
    if base == "*":
        return True
    if "-" in base:
        start, end = (int(x) for x in base.split("-"))
        return lo <= start <= hi and lo <= end <= hi and start <= end
    return lo <= int(base) <= hi


def _validate_field(field: str, lo: int, hi: int) -> bool:
    if not field:
        return False
    return all(_validate_part(part, lo, hi) for part in field.split(","))


def validate_schedule(value: str) -> None:
    """Raises ScheduleError with a French, ready-to-display message on anything invalid.
    Accepts the three UI presets as-is; everything else must be a plain 5-field cron
    expression. Called wherever `schedule` is written (project create/update) — an invalid
    value never reaches dag_render.py."""
    stripped = value.strip()
    if stripped in PRESETS:
        return
    if stripped.startswith("@"):
        raise ScheduleError(f"Preset de planification inconnu : « {stripped} » (attendu : {', '.join(PRESETS)}).")

    fields = stripped.split()
    if len(fields) != 5:
        raise ScheduleError("Expression cron invalide : 5 champs attendus (minute heure jour mois jour_semaine).")

    for field, (lo, hi) in zip(fields, _FIELD_BOUNDS):
        if not _validate_field(field, lo, hi):
            raise ScheduleError(f"Expression cron invalide : champ « {field} » hors de la plage attendue ({lo}-{hi}).")
