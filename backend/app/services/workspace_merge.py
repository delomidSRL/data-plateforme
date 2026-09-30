"""Module 19 étape 3 §5.2 — the merge-aware replacement for workspace.materialize() at every
real generator call site (today: the build; see medallion_deploy.build_project). A human-
modified file is never silently overwritten (that invariant already held from étape 2's
materialize() hardening) — now it's also never silently left to drift forever: a genuine
regeneration produces either a clean three-way-merge *proposal* (nothing written until
accepted) or an *open conflict* (markers, must be resolved), using `git merge-file`, exactly
the primitive §1 calls for ("aucune librairie de merge Python")."""
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.medallion import MedallionDataset, MedallionProject
from app.models.project_file import ProjectFile
from app.models.project_file_conflict import ConflictStatus, ConflictTrigger, ProjectFileConflict
from app.models.user import User
from app.services import workspace


class WorkspaceConflictsPending(Exception):
    """§5.6 — raised by a build when regenerating just created (or left standing) an active
    proposal/conflict: nothing is deployed until every one of these is arbitrated."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__(f"{len(paths)} fichier(s) à arbitrer avant de déployer : {', '.join(paths)}")


@dataclass
class MergeOutcome:
    proposed: list[str] = field(default_factory=list)
    conflicted: list[str] = field(default_factory=list)

    @property
    def has_pending(self) -> bool:
        return bool(self.proposed or self.conflicted)


def _three_way_merge(ours: str, base: str, theirs: str) -> tuple[str, bool]:
    """Returns (merged_text, has_conflicts). No repository needed — `git merge-file` operates
    on three plain files. Exit 0 = clean; a positive count = that many conflicting hunks."""
    with tempfile.TemporaryDirectory(prefix="wsmerge_") as tmp_str:
        tmp = Path(tmp_str)
        ours_f, base_f, theirs_f = tmp / "ours", tmp / "base", tmp / "theirs"
        ours_f.write_text(ours, encoding="utf-8")
        base_f.write_text(base, encoding="utf-8")
        theirs_f.write_text(theirs, encoding="utf-8")
        result = subprocess.run(
            [
                "git", "merge-file", "-p", "--diff3",
                "-L", "votre version", "-L", "base", "-L", "régénéré",
                str(ours_f), str(base_f), str(theirs_f),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout, result.returncode != 0


def _active_conflicts(db: Session, project_id: int) -> dict[str, ProjectFileConflict]:
    rows = db.query(ProjectFileConflict).filter(
        ProjectFileConflict.project_id == project_id,
        ProjectFileConflict.status.in_([ConflictStatus.proposed, ConflictStatus.open]),
    ).all()
    return {c.path: c for c in rows}


def list_active_conflicts(db: Session, project_id: int) -> list[ProjectFileConflict]:
    return db.query(ProjectFileConflict).filter(
        ProjectFileConflict.project_id == project_id,
        ProjectFileConflict.status.in_([ConflictStatus.proposed, ConflictStatus.open]),
    ).order_by(ProjectFileConflict.path).all()


def has_active_conflicts(db: Session, project_id: int) -> bool:
    return db.query(ProjectFileConflict.id).filter(
        ProjectFileConflict.project_id == project_id,
        ProjectFileConflict.status.in_([ConflictStatus.proposed, ConflictStatus.open]),
    ).first() is not None


def apply_generated(
    db: Session, project: MedallionProject, files: dict[str, str],
    generator: str, trigger: ConflictTrigger, datasets: list[MedallionDataset] | None = None,
) -> MergeOutcome:
    """§5.2's per-file cases, in order: new -> create; intact -> update in place (materialize's
    own behavior, unchanged); modified & generation unchanged -> nothing; modified & changed ->
    three-way merge, proposal or conflict. A path that already has an active (proposed/open)
    conflict gets that SAME row updated with the new `theirs` (§5.6 — never a second one)."""
    if "profiles.yml" in files:
        files = {path: content for path, content in files.items() if path != "profiles.yml"}
    path_to_dataset = workspace.dataset_path_map(datasets or [])

    existing = {pf.path: pf for pf in db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()}
    active_conflicts = _active_conflicts(db, project.id)
    seen: set[str] = set()
    outcome = MergeOutcome()

    for path, content in files.items():
        seen.add(path)
        h = workspace.hash_content(content)
        dataset_id = path_to_dataset.get(path)
        row = existing.get(path)

        if row is None:
            db.add(ProjectFile(
                project_id=project.id, path=path, content=content, content_hash=h,
                base_content=content, base_hash=h, generator=generator, dataset_id=dataset_id, version=1,
            ))
            continue

        if row.dataset_id != dataset_id:
            row.dataset_id = dataset_id

        if not workspace.is_modified(row):
            if row.content_hash != h:
                row.content = content
                row.content_hash = h
                row.base_content = content
                row.base_hash = h
                row.generator = generator
                row.version += 1
            continue

        if content == row.base_content:
            continue  # the generator's output hasn't actually changed — nothing to propose

        if content == row.content:
            # The regeneration now matches exactly what the human already has — typically
            # because their Code-tab edit already updated the underlying MedallionDataset via
            # workspace_sync, so regenerating from the canvas reproduces the same bytes. There
            # is nothing to arbitrate: just advance the base silently (same "no version bump on
            # a base-only move" rule as workspace.materialize()'s own), so the NEXT genuinely
            # different regeneration diffs against this, not the stale pre-edit base.
            if row.base_hash != h:
                row.base_content = content
                row.base_hash = h
                row.generator = generator
            continue

        merged_text, has_conflicts = _three_way_merge(row.content, row.base_content, content)
        status = ConflictStatus.open if has_conflicts else ConflictStatus.proposed
        conflict = active_conflicts.get(path)
        if conflict is not None:
            conflict.theirs_content = content
            conflict.merged_content = merged_text
            conflict.status = status
            conflict.generator = generator
            conflict.trigger = trigger
        else:
            db.add(ProjectFileConflict(
                project_id=project.id, path=path, base_content=row.base_content, ours_content=row.content,
                theirs_content=content, merged_content=merged_text, status=status, generator=generator, trigger=trigger,
            ))
        (outcome.conflicted if has_conflicts else outcome.proposed).append(path)

    for path, row in existing.items():
        if path not in seen and not workspace.is_modified(row):
            db.delete(row)

    db.flush()
    return outcome


def _settle(db: Session, project: MedallionProject, conflict: ProjectFileConflict, new_content: str, user: User | None, status: ConflictStatus) -> ProjectFile:
    """Shared tail for accept/resolve: `new_content` becomes the file's content, and
    `theirs_content` (the regeneration that triggered this conflict) becomes its new base —
    the next regeneration diffs against what JUST got resolved, not the stale pre-conflict
    base, so an unrelated later change doesn't re-surface this same merge."""
    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == conflict.path).first()
    if row is None:
        raise ValueError(f"workspace file for conflict {conflict.id} no longer exists")
    row.content = new_content
    row.content_hash = workspace.hash_content(new_content)
    row.base_content = conflict.theirs_content
    row.base_hash = workspace.hash_content(conflict.theirs_content)
    row.version += 1
    row.updated_by = user.id if user else None
    conflict.status = status
    conflict.resolved_by = user.id if user else None
    conflict.resolved_at = datetime.now(timezone.utc)
    project.has_pending_changes = True
    return row


def accept_conflict(db: Session, project: MedallionProject, conflict: ProjectFileConflict, user: User | None) -> ProjectFile:
    return _settle(db, project, conflict, conflict.merged_content, user, ConflictStatus.resolved)


def resolve_conflict(db: Session, project: MedallionProject, conflict: ProjectFileConflict, content: str, user: User | None) -> ProjectFile:
    return _settle(db, project, conflict, content, user, ConflictStatus.resolved)


def discard_conflict(db: Session, project: MedallionProject, conflict: ProjectFileConflict, user: User | None) -> ProjectFile:
    """§5.5 — "garder la version humaine, la nouvelle base est adoptée pour ne pas reproposer
    la même fusion" : `content` is untouched (it already IS `conflict.ours_content`), only the
    base moves — same "no version bump on a base-only move" rule as materialize()'s own."""
    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == conflict.path).first()
    if row is None:
        raise ValueError(f"workspace file for conflict {conflict.id} no longer exists")
    row.base_content = conflict.theirs_content
    row.base_hash = workspace.hash_content(conflict.theirs_content)
    conflict.status = ConflictStatus.discarded
    conflict.resolved_by = user.id if user else None
    conflict.resolved_at = datetime.now(timezone.utc)
    return row
