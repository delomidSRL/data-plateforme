from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import decrypt_secret, encrypt_secret
from app.db.session import get_db
from app.models.data_source import DataSource, DataSourceOrigin, DataSourceStatus
from app.models.file_import import FileImport, FileImportStatus
from app.models.semantic_annotation import SemanticAnnotation
from app.models.user import User
from app.schemas.semantic_annotation import AnnotationOut, AnnotationUpsert, ColumnInfoOut
from app.schemas.source import (
    ProvenanceOut,
    SchemaInfoOut,
    SourceCreate,
    SourceIntrospectResult,
    SourceOut,
    SourceTestRequest,
    SourceTestResult,
    SourceUpdate,
    TableInfoOut,
)
from app.services import connections

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _get_external_source(db: Session, source_id: int) -> DataSource:
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")
    if source.origin != DataSourceOrigin.external:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette source est gérée par la plateforme et n'est pas modifiable ici.")
    return source


@router.get("/", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(DataSource).order_by(DataSource.created_at.asc()).all()


@router.post("/", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = DataSource(
        name=payload.name,
        type=payload.type,
        origin=DataSourceOrigin.external,
        host=payload.host,
        port=payload.port,
        database_name=payload.database_name,
        username=payload.username,
        secret_encrypted=encrypt_secret(payload.secret),
        options=payload.options,
        status=DataSourceStatus.unknown,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")
    return source


@router.put("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = _get_external_source(db, source_id)

    data = payload.model_dump(exclude_unset=True, exclude={"secret"})
    for field, value in data.items():
        setattr(source, field, value)
    if payload.secret:
        source.secret_encrypted = encrypt_secret(payload.secret)

    db.commit()
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = _get_external_source(db, source_id)
    db.delete(source)
    db.commit()


@router.post("/test", response_model=SourceTestResult)
def test_source_adhoc(payload: SourceTestRequest, _: User = Depends(get_current_user)):
    result = connections.test_connection(payload.type, payload.host, payload.port, payload.database_name, payload.username, payload.secret, payload.options)
    return SourceTestResult(reachable=result.reachable, message=result.message, latency_ms=result.latency_ms, version=result.version)


@router.post("/{source_id}/test", response_model=SourceTestResult)
def test_source(source_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")

    secret = decrypt_secret(source.secret_encrypted)
    result = connections.test_connection(source.type, source.host, source.port, source.database_name, source.username, secret, source.options)

    source.status = DataSourceStatus.reachable if result.reachable else DataSourceStatus.unreachable
    source.last_tested_at = datetime.now(timezone.utc)
    db.commit()

    return SourceTestResult(reachable=result.reachable, message=result.message, latency_ms=result.latency_ms, version=result.version)


@router.get("/{source_id}/introspect", response_model=SourceIntrospectResult)
def introspect_source(source_id: int, bucket: str | None = Query(default=None), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")

    secret = decrypt_secret(source.secret_encrypted)
    try:
        result = connections.introspect(source.type, source.host, source.port, source.database_name, source.username, secret, source.options, bucket_override=bucket)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Introspection impossible : {exc}")

    # a table is "provenance-tagged" if some completed file import wrote it — the join
    # key is exactly (target_source_id, target_schema, target_table), nothing new to store
    imports_by_key = {
        (fi.target_schema, fi.target_table): fi
        for fi in db.query(FileImport).filter(FileImport.target_source_id == source_id, FileImport.status == FileImportStatus.imported).all()
    }

    def _table_info(schema_name: str, table_name: str) -> TableInfoOut:
        fi = imports_by_key.get((schema_name, table_name))
        provenance = None
        if fi is not None:
            provenance = ProvenanceOut(import_id=fi.id, file=fi.source_file_name, imported_at=fi.imported_at, row_count=fi.row_count)
        return TableInfoOut(name=table_name, provenance=provenance)

    return SourceIntrospectResult(
        schemas=[SchemaInfoOut(name=s.name, tables=[_table_info(s.name, t) for t in s.tables]) for s in result.schemas],
        buckets=result.buckets,
        objects=result.objects,
    )


@router.get("/{source_id}/tables/{schema}/{table}/columns", response_model=list[ColumnInfoOut])
def list_table_columns(source_id: int, schema: str, table: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")

    secret = decrypt_secret(source.secret_encrypted)
    try:
        return connections.list_columns(source.type, source.host, source.port, source.database_name, source.username, secret, schema, table)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Lecture des colonnes impossible : {exc}")


@router.get("/{source_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(source_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")
    return (
        db.query(SemanticAnnotation)
        .filter(SemanticAnnotation.data_source_id == source_id)
        .order_by(SemanticAnnotation.table_name.asc(), SemanticAnnotation.column_name.asc().nullsfirst())
        .all()
    )


@router.put("/{source_id}/annotations", response_model=list[AnnotationOut])
def upsert_annotations(source_id: int, payload: list[AnnotationUpsert], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")

    existing_by_key = {
        (a.table_name, a.column_name): a
        for a in db.query(SemanticAnnotation).filter(SemanticAnnotation.data_source_id == source_id).all()
    }
    touched = []
    for item in payload:
        annotation = existing_by_key.get((item.table_name, item.column_name))
        if annotation is None:
            annotation = SemanticAnnotation(data_source_id=source_id, table_name=item.table_name, column_name=item.column_name, created_by=current_user.id)
            db.add(annotation)
        annotation.description = item.description
        touched.append(annotation)
    db.commit()
    for a in touched:
        db.refresh(a)
    return touched


@router.delete("/{source_id}/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(source_id: int, annotation_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    annotation = db.query(SemanticAnnotation).filter(SemanticAnnotation.id == annotation_id, SemanticAnnotation.data_source_id == source_id).first()
    if annotation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Annotation introuvable.")
    db.delete(annotation)
    db.commit()
