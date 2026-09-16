"""Module 6 extension §4.3 — transport abstraction for the file watcher. Two v1
implementations (minio, local); services/file_watch.py never branches on transport type
itself, it just calls Transport.list_candidates/stat/exists/fetch/move.

§2.1 security invariants enforced here, not trusted from the caller:
- a local `location.path` must resolve strictly under WATCH_LOCAL_BASE (symlink escapes
  included — Path.resolve() follows real symlinks, so an outward-pointing link is caught too).
- a regex pattern is probe-matched against a worst-case string, bounded by a wall-clock
  timeout, ONCE at validation time (creation/edit) — "rejeté à la création, pas à l'exécution"
  (§2.1): a pattern that clears the probe is trusted at scan time, matched directly with no
  further guard. The probe runs in a genuinely killable subprocess, not a thread: Python's `re`
  engine holds the GIL for the full duration of a single match call (confirmed live — a
  catastrophic (a+)+$ probe froze the entire interpreter, including the "timeout" thread meant
  to be watching it, for the whole backtrack), so a thread-based timeout cannot actually bound
  a catastrophic match; only killing the OS process running it can.
- MinIO access only ever goes through an existing `data_sources` row (Fernet-decrypted secret,
  never a raw credential in `location`).
"""
import fnmatch
import multiprocessing
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.data_source import DataSource, DataSourceType
from app.models.file_watch import WatchPatternType
from app.services import connections

MAX_FETCH_BYTES = 50 * 1024 * 1024  # same ceiling as Module 6's own Excel/JSON hard limits


class WatchTransportError(Exception):
    pass


@dataclass
class FileRef:
    name: str        # basename — what pattern matching and FileWatchEvent.file_name use
    key: str          # transport-specific full identifier (S3 object_name, or absolute local path)
    size: int
    mtime: float | None  # epoch seconds; None when the transport can't report one


# ---------------------------------------------------------------------------
# Pattern validation & matching (§2.1)
# ---------------------------------------------------------------------------

# Forces full backtracking across every "a" before failing on a vulnerable pattern
# (e.g. (a+)+$, (a|aa)+$) — a classic nested-quantifier ReDoS blows up exponentially with
# this input length (2^40), so a genuinely vulnerable pattern times out reliably while any
# sane pattern matches in microseconds, with no perceptible delay at watch creation.
_REDOS_PROBE = "a" * 40 + "!"


def _redos_probe_worker(pattern: str, text: str, queue) -> None:
    """Runs in a spawned subprocess (see validate_pattern) — never imports/shares state with
    the live server process."""
    try:
        queue.put(re.compile(pattern).search(text) is not None)
    except Exception:
        queue.put(None)


def validate_pattern(pattern: str, pattern_type: WatchPatternType) -> None:
    if not pattern or not pattern.strip():
        raise WatchTransportError("Motif vide.")
    if pattern_type == WatchPatternType.glob:
        return  # fnmatch.translate never raises — any string is a valid glob

    try:
        re.compile(pattern)  # cheap, synchronous syntax check — no need for a subprocess just for this
    except re.error as exc:
        raise WatchTransportError(f"Motif regex invalide : {exc}")

    timeout_s = get_settings().watch_regex_timeout_ms / 1000
    # "spawn" explicitly (not the platform default, which is "fork" on Linux): this runs
    # inside a live FastAPI process holding open DB/HTTP connections — forking would duplicate
    # those file descriptors into the child, spawn starts a genuinely clean interpreter instead.
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(target=_redos_probe_worker, args=(pattern, _REDOS_PROBE, result_queue), daemon=True)
    proc.start()
    proc.join(timeout=timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        raise WatchTransportError("Motif regex rejeté : temps de correspondance excessif sur une chaîne de test (risque de blocage catastrophique).")
    result = result_queue.get() if not result_queue.empty() else None
    if result is None:
        raise WatchTransportError("Motif regex rejeté : échec de la validation.")


def _matches(name: str, pattern: str, pattern_type: WatchPatternType) -> bool:
    """Scan-time matching — no timeout guard here by design (§2.1): only a pattern that
    already cleared validate_pattern's subprocess probe can ever reach a stored FileWatch, so
    it's trusted here exactly as any other already-validated stored value in this codebase."""
    if pattern_type == WatchPatternType.glob:
        return fnmatch.fnmatchcase(name, pattern)
    try:
        return re.compile(pattern).search(name) is not None
    except re.error:
        return False


# ---------------------------------------------------------------------------
# Local directory transport
# ---------------------------------------------------------------------------

def validate_local_path(path: str) -> Path:
    settings = get_settings()
    base = settings.watch_local_base
    if not base:
        raise WatchTransportError("Transport local non configuré sur ce déploiement (WATCH_LOCAL_BASE vide).")
    if not path:
        raise WatchTransportError("Chemin local vide.")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise WatchTransportError(f"Le chemin local doit être absolu : « {path} ».")
    base_resolved = Path(base).resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError:
        raise WatchTransportError(f"Chemin hors de la base autorisée ({base}) : « {path} ».")
    return candidate_resolved


class LocalTransport:
    def __init__(self, location: dict):
        self.base_path = validate_local_path(location.get("path", ""))

    def list_candidates(self, pattern: str, pattern_type: WatchPatternType) -> list[FileRef]:
        if not self.base_path.is_dir():
            return []
        refs = []
        for entry in self.base_path.iterdir():
            if not entry.is_file():
                continue
            if not _matches(entry.name, pattern, pattern_type):
                continue
            st = entry.stat()
            refs.append(FileRef(name=entry.name, key=str(entry), size=st.st_size, mtime=st.st_mtime))
        return refs

    def stat(self, ref: FileRef) -> tuple[int, float | None]:
        st = Path(ref.key).stat()
        return st.st_size, st.st_mtime

    def exists(self, name: str) -> bool:
        return (self.base_path / name).exists()

    def fetch(self, ref: FileRef) -> bytes:
        if ref.size > MAX_FETCH_BYTES:
            raise WatchTransportError(f"Fichier trop volumineux ({ref.size} octets > {MAX_FETCH_BYTES}).")
        return Path(ref.key).read_bytes()

    def move(self, ref: FileRef, target_subfolder: str) -> None:
        # target_subfolder is always a bare folder NAME (done/error), never a free-form path —
        # it lives under the same already-validated base, so it inherits that containment
        # without needing its own traversal check.
        dest_dir = validate_local_path(str(self.base_path / target_subfolder))
        dest_dir.mkdir(parents=True, exist_ok=True)
        Path(ref.key).rename(dest_dir / ref.name)


# ---------------------------------------------------------------------------
# MinIO transport — reuses the same data_sources-backed client as connections.py/file_import.py
# ---------------------------------------------------------------------------

class MinioTransport:
    def __init__(self, db: Session, location: dict):
        source_id = location.get("source_id")
        self.bucket = location.get("bucket") or ""
        self.prefix = (location.get("prefix") or "").lstrip("/")
        if self.prefix and not self.prefix.endswith("/"):
            self.prefix += "/"
        if not source_id or not self.bucket:
            raise WatchTransportError("source_id et bucket sont requis pour un transport MinIO.")
        source = db.get(DataSource, int(source_id))
        if source is None or source.type != DataSourceType.minio:
            raise WatchTransportError("Source MinIO introuvable ou invalide.")
        secret = decrypt_secret(source.secret_encrypted)
        self.client = connections._minio_client(source.host, source.port, source.username, secret, source.options)

    def list_candidates(self, pattern: str, pattern_type: WatchPatternType) -> list[FileRef]:
        refs = []
        for obj in self.client.list_objects(self.bucket, prefix=self.prefix, recursive=False):
            if obj.is_dir:
                continue
            name = obj.object_name[len(self.prefix):]
            if not name or not _matches(name, pattern, pattern_type):
                continue
            mtime = obj.last_modified.timestamp() if obj.last_modified else None
            refs.append(FileRef(name=name, key=obj.object_name, size=obj.size or 0, mtime=mtime))
        return refs

    def stat(self, ref: FileRef) -> tuple[int, float | None]:
        st = self.client.stat_object(self.bucket, ref.key)
        mtime = st.last_modified.timestamp() if st.last_modified else None
        return st.size or 0, mtime

    def exists(self, name: str) -> bool:
        from minio.error import S3Error
        try:
            self.client.stat_object(self.bucket, self.prefix + name)
            return True
        except S3Error:
            return False

    def fetch(self, ref: FileRef) -> bytes:
        if ref.size > MAX_FETCH_BYTES:
            raise WatchTransportError(f"Fichier trop volumineux ({ref.size} octets > {MAX_FETCH_BYTES}).")
        response = self.client.get_object(self.bucket, ref.key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def move(self, ref: FileRef, target_subfolder: str) -> None:
        from minio.commonconfig import CopySource
        dest_key = f"{self.prefix}{target_subfolder}/{ref.name}"
        self.client.copy_object(self.bucket, dest_key, CopySource(self.bucket, ref.key))
        self.client.remove_object(self.bucket, ref.key)


def build_transport(db: Session, transport_type, location: dict) -> "LocalTransport | MinioTransport":
    """`transport_type` is either a WatchTransport enum member or its plain string value —
    both come through here (a real FileWatch's own column vs. a still-unsaved draft payload,
    §4.5's pre-creation « Tester »), so this accepts either rather than forcing callers to
    fake an enum-shaped object just to share this one function."""
    value = transport_type.value if hasattr(transport_type, "value") else transport_type
    if value == "local":
        return LocalTransport(location)
    if value == "minio":
        return MinioTransport(db, location)
    raise WatchTransportError(f"Transport non pris en charge : {transport_type}.")
