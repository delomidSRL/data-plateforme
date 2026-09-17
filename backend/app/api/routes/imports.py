import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.data_source import DataSource
from app.models.file_import import FileImport, FileImportFormat, FileImportStatus, FileImportWriteMode, ImportMode
from app.models.user import User, UserRole
from app.schemas.file_import import ColumnsOut, FileImportOut, FileImportStatusOut, FileImportUpdate, XmlCandidatesOut
from app.services import file_import as file_import_service

router = APIRouter(prefix="/api/imports", tags=["imports"])

SUPPORTED_UPLOAD_FORMATS = {"csv", "excel", "json", "xml"}


def _get_import(db: Session, import_id: int) -> FileImport:
    fi = db.get(FileImport, import_id)
    if fi is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import introuvable.")
    return fi


def _get_source(db: Session, source_id: int, what: str) -> DataSource:
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{what} introuvable.")
    return source


@router.get("/", response_model=list[FileImportOut])
def list_imports(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(FileImport).order_by(FileImport.created_at.desc()).all()


@router.post("/", response_model=FileImportOut, status_code=status.HTTP_201_CREATED)
async def create_import(
    file: UploadFile = File(...),
    format: str = Form(...),
    format_options: str = Form("{}"),
    target_source_id: int = Form(...),
    archive_source_id: int = Form(...),
    name: str | None = Form(None),
    import_mode: str = Form("typed"),
    write_mode: str = Form("create"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if format not in SUPPORTED_UPLOAD_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Format non pris en charge à cette étape : {format}.")
    try:
        options = json.loads(format_options) if format_options else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format_options doit être un JSON valide.")
    try:
        mode = ImportMode(import_mode)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mode d'import invalide : « {import_mode} ».")
    # Module 6 extension §3.1 — payload is a CSV/Excel toggle only. JSON/XML imbriqués already
    # have their own payload path (M6 étape 3, a "__root__" entry in column_mapping) and are
    # untouched by this extension — offering the same toggle for them would be a second,
    # conflicting payload mechanism.
    if mode == ImportMode.payload and format not in file_import_service.PAYLOAD_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le mode payload n'est proposé que pour CSV et Excel — le JSON/XML imbriqué dispose déjà de son propre mode payload.",
        )
    try:
        payload_write_mode = FileImportWriteMode(write_mode)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mode d'écriture invalide : « {write_mode} ».")

    target_source = _get_source(db, target_source_id, "Source cible")
    archive_source = _get_source(db, archive_source_id, "Source d'archive")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fichier vide.")

    fi = FileImport(
        name=name or file.filename,
        imported_by=current_user.id,
        source_file_name=file.filename,
        file_size=len(file_bytes),
        format=FileImportFormat(format),
        format_options=options,
        target_source_id=target_source.id,
        archive_source_id=archive_source.id,
        status=FileImportStatus.draft,
        import_mode=mode,
    )
    db.add(fi)
    db.commit()
    db.refresh(fi)

    try:
        archive_path = file_import_service.archive_raw(archive_source, fi.id, file.filename, file_bytes)
        checksum = file_import_service.compute_checksum(file_bytes)
        fi.archive_path = archive_path
        fi.checksum = checksum
        fi.uploaded_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        fi.status = FileImportStatus.error
        fi.last_error = f"Archivage impossible : {exc}"
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=fi.last_error)

    if mode == ImportMode.payload:
        # §3.5 — no mapping step at all: the target table is derived, the write mode was
        # chosen up front, and the call archives + loads + returns status=imported directly.
        fi.target_table = file_import_service.derive_table_name(fi.name)
        fi.write_mode = payload_write_mode
        db.commit()
        file_import_service.run_import_payload(fi.id)
        db.refresh(fi)
        if fi.status == FileImportStatus.error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=fi.last_error)
        return fi

    try:
        fi.column_mapping = file_import_service.infer_column_mapping(format, file_bytes, options)
        fi.status = FileImportStatus.awaiting_validation
        db.commit()
    except Exception as exc:
        fi.status = FileImportStatus.error
        fi.last_error = f"Analyse du schéma impossible : {exc}"
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=fi.last_error)

    db.refresh(fi)
    return fi


@router.post("/xml-candidates", response_model=XmlCandidatesOut)
async def xml_candidates(file: UploadFile = File(...), _: User = Depends(get_current_user)):
    """Scratch analysis only — no FileImport row, nothing archived. Lets the wizard propose
    record_xpath candidates before the user commits to an upload."""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fichier vide.")
    try:
        candidates = file_import_service.suggest_record_xpaths(file_bytes)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Analyse XML impossible : {exc}")
    return XmlCandidatesOut(candidates=candidates)


@router.post("/columns", response_model=ColumnsOut)
async def csv_excel_columns(
    file: UploadFile = File(...),
    format: str = Form(...),
    format_options: str = Form("{}"),
    _: User = Depends(get_current_user),
):
    """Scratch analysis only — no FileImport row, nothing archived. Schema-on-Read (payload)
    mode skips inference entirely, so this is the wizard's only way to propose source_pk
    candidates before commit — mirrors xml_candidates above."""
    if format not in ("csv", "excel"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aperçu des colonnes disponible seulement pour CSV et Excel.")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fichier vide.")
    try:
        options = json.loads(format_options) if format_options else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format_options doit être un JSON valide.")
    try:
        columns, _rows = file_import_service.read_columns_and_sample(format, file_bytes, options)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Analyse des colonnes impossible : {exc}")
    return ColumnsOut(columns=columns)


@router.get("/{import_id}", response_model=FileImportOut)
def get_import(import_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _get_import(db, import_id)


@router.put("/{import_id}", response_model=FileImportOut)
def update_import(import_id: int, payload: FileImportUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    fi = _get_import(db, import_id)
    column_mapping = [c.model_dump() for c in payload.column_mapping]

    included = [c for c in column_mapping if c.get("include", True)]
    if not included:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Au moins une colonne doit être incluse.")
    seen_names = set()
    for c in included:
        try:
            file_import_service._validate_identifier(c["target_name"], "Nom de colonne")
        except file_import_service.FileImportError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        if c["target_name"] in seen_names:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Colonne cible en double : « {c['target_name']} ».")
        seen_names.add(c["target_name"])
    try:
        file_import_service._validate_identifier(payload.target_table, "Table cible")
    except file_import_service.FileImportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    fi.column_mapping = column_mapping
    fi.target_table = payload.target_table
    fi.write_mode = FileImportWriteMode(payload.write_mode)
    fi.contract_hash = file_import_service.canonical_contract(column_mapping)
    db.commit()
    db.refresh(fi)
    return fi


@router.post("/{import_id}/run", response_model=FileImportOut)
def trigger_run(import_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    fi = _get_import(db, import_id)
    if fi.status not in (FileImportStatus.awaiting_validation, FileImportStatus.error):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="L'import doit être en attente de validation (contrat déjà écrit) pour être lancé.")
    if not fi.target_table:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le contrat de mapping n'a pas encore été validé (PUT /{id}).")

    fi.status = FileImportStatus.importing
    db.commit()
    background_tasks.add_task(file_import_service.run_import, fi.id)
    db.refresh(fi)
    return fi


@router.get("/{import_id}/status", response_model=FileImportStatusOut)
def get_status(import_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    fi = _get_import(db, import_id)
    return FileImportStatusOut(status=fi.status.value, row_count=fi.row_count, cast_errors=fi.cast_errors, last_error=fi.last_error)


@router.post("/{import_id}/reimport", response_model=FileImportOut)
async def reimport(
    import_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    fi = _get_import(db, import_id)
    archive_source = _get_source(db, fi.archive_source_id, "Source d'archive")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fichier vide.")

    try:
        archive_path = file_import_service.archive_raw(archive_source, fi.id, file.filename, file_bytes)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Archivage impossible : {exc}")

    fi.source_file_name = file.filename
    fi.file_size = len(file_bytes)
    fi.checksum = file_import_service.compute_checksum(file_bytes)
    fi.archive_path = archive_path
    fi.uploaded_at = datetime.now(timezone.utc)

    if fi.import_mode == ImportMode.payload:
        # §3.5 — payload is shape-agnostic (an added/removed key just lands in the JSONB):
        # no drift check, no modal, straight re-load under the same write_mode/target_table.
        db.commit()
        file_import_service.run_import_payload(fi.id)
        db.refresh(fi)
        if fi.status == FileImportStatus.error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=fi.last_error)
        return fi

    try:
        new_mapping = file_import_service.infer_column_mapping(fi.format.value, file_bytes, fi.format_options or {})
    except Exception as exc:
        fi.status = FileImportStatus.error
        fi.last_error = f"Lecture du fichier impossible : {exc}"
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=fi.last_error)

    existing_source_names = {c["source_name"] for c in fi.column_mapping}
    new_source_names = {c["source_name"] for c in new_mapping}
    same_shape = new_source_names == existing_source_names

    if same_shape:
        fi.status = FileImportStatus.importing
        db.commit()
        background_tasks.add_task(file_import_service.run_import, fi.id)
    else:
        fi.column_mapping = new_mapping
        fi.status = FileImportStatus.awaiting_validation
        db.commit()

    db.refresh(fi)
    return fi


@router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(
    import_id: int,
    drop_table: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    fi = _get_import(db, import_id)

    if drop_table:
        if current_user.role != UserRole.admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Réservé aux administrateurs.")
        if fi.target_source_id and fi.target_table:
            target = db.get(DataSource, fi.target_source_id)
            if target is not None:
                try:
                    conn = file_import_service._pg_connect(target)
                    try:
                        with conn.cursor() as cur:
                            from psycopg import sql

                            cur.execute(
                                sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                                    sql.Identifier(fi.target_schema), sql.Identifier(fi.target_table)
                                )
                            )
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as exc:
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Suppression de la table impossible : {exc}")

    db.delete(fi)
    db.commit()
