"""Documents attached to an FDR: upload, list, metadata, download and delete.

Permissions (all enforced server-side; session, Origin and CSRF are checked by `require_roles`):
  * Upload and delete: INSTITUTION_USER only, for an FDR of their own institution, and only while the
    institution may still change the record (DRAFT or CORRECTION_REQUESTED, the same rule as editing
    the FDR itself). Once submitted, accepted or rejected, the document set is frozen.
  * List, metadata, download: INSTITUTION_USER (own institution, any status), AICTE_REVIEWER and
    AICTE_APPROVER (any institution, but only records that have left DRAFT: the same visibility as the
    FDR itself). ADMIN and AUDITOR are NOT granted document access. Documents are more sensitive than
    registry metadata and no policy has given those roles a need for them; widening this is a
    one-line change to `readers`.
A record the caller cannot see is a 404 identical to a missing one (audited for institution users).

An upload is a raw request body, not multipart (no extra dependency, and the size cap can be enforced
while streaming): the file's name travels percent-encoded in `X-Filename`, the category in the query.

Consistency. The body is streamed into a private temporary file with a running size limit and
checksum, then validated against its real signature (app.documents.validation). Only then are the row
locked and the metadata row and audit event written; the file is moved into place just before the
single commit. If anything fails, the moved file is removed and the transaction rolled back, so
metadata never points at a file that was never stored. The one gap is a process crash between the
move and the commit, which leaves an unreferenced file that nothing can reach.

Downloads are always attachments, with `nosniff` and a CSP that disables everything, so a stored file
can never run as a page. They are integrity-checked (size and SHA-256) before being sent, audited,
and never cached.
"""

import hashlib
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_roles
from app.api.fdrs import EDITABLE_STATUSES, locked_visible_fdr
from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.db import get_db
from app.documents.storage import DocumentStore, StorageError, sha256_hex
from app.documents.validation import SIGNATURE_BYTES, UploadRejected, validate_upload
from app.models import FDR, DocumentCategory, DocumentStatus, FDRDocument, Role, User
from app.schemas.documents import DocumentList, DocumentOut
from app.security.scope import fdr_visibility
from app.timeutil import utcnow

router = APIRouter(tags=["documents"])

writers = require_roles(Role.INSTITUTION_USER)
readers = require_roles(Role.INSTITUTION_USER, Role.AICTE_REVIEWER, Role.AICTE_APPROVER)

EDITABLE = {s.value for s in EDITABLE_STATUSES}
CHUNK = 64 * 1024
FILENAME_HEADER = "X-Filename"


def get_document_store(settings: Settings = Depends(get_settings)) -> DocumentStore:
    return DocumentStore(settings.storage_dir)


def _to_out(doc: FDRDocument, uploader_name: str) -> DocumentOut:
    return DocumentOut(
        id=doc.id, fdr_id=doc.fdr_id, category=DocumentCategory(doc.category),
        original_filename=doc.original_filename, content_type=doc.content_type, size_bytes=doc.size_bytes,
        sha256=doc.sha256, uploaded_by_name=uploader_name, uploaded_at=doc.uploaded_at,
    )


def _audit(db: Session, ctx: RequestContext, auth: AuthContext, action: str, doc: FDRDocument, **details):
    # Metadata only: the document's category, type and size, and the FDR it belongs to. Never the
    # filename (it may contain personal data), the checksum, the storage key or any content.
    return write_audit_event(
        db,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        actor_user_id=auth.user.id,
        actor_role=auth.user.role,
        entity_type="document",
        entity_id=doc.id,
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
        details={"target_fdr_id": str(doc.fdr_id), **details},
    )


def _visible_fdr(db: Session, request: Request, auth: AuthContext, ctx: RequestContext, fdr_id: uuid.UUID) -> FDR:
    fdr = db.scalars(select(FDR).where(FDR.id == fdr_id, fdr_visibility(auth.user))).first()
    if fdr is None:
        if auth.user.role == Role.INSTITUTION_USER.value:
            raise deny(
                db, request, auth, ctx,
                action="authz.scope_denied", status_code=404, message="FDR not found",
                target_fdr_id=str(fdr_id),
            )
        raise HTTPException(status_code=404, detail="FDR not found")
    return fdr


def _visible_document(
    db: Session, request: Request, auth: AuthContext, ctx: RequestContext, fdr_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[FDRDocument, str]:
    _visible_fdr(db, request, auth, ctx, fdr_id)
    row = db.execute(
        select(FDRDocument, User.full_name)
        .join(User, User.id == FDRDocument.uploaded_by)
        .where(
            FDRDocument.id == document_id,
            FDRDocument.fdr_id == fdr_id,
            FDRDocument.status == DocumentStatus.ACTIVE.value,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return row[0], row[1]


def _require_editable(db, request, auth, ctx, fdr: FDR, action: str) -> None:
    if fdr.status not in EDITABLE:
        raise deny(
            db, request, auth, ctx,
            action=action, status_code=409,
            message="Documents can only be changed while the FDR is a draft or has been returned for correction",
            reason="invalid_status", target_fdr_id=str(fdr.id),
        )


# ---- reads -----------------------------------------------------------------------------------

@router.get("/fdrs/{fdr_id}/documents", response_model=DocumentList)
def list_documents(
    fdr_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(readers),
) -> DocumentList:
    response.headers["Cache-Control"] = "no-store"
    _visible_fdr(db, request, auth, ctx, fdr_id)
    rows = db.execute(
        select(FDRDocument, User.full_name)
        .join(User, User.id == FDRDocument.uploaded_by)
        .where(FDRDocument.fdr_id == fdr_id, FDRDocument.status == DocumentStatus.ACTIVE.value)
        .order_by(FDRDocument.uploaded_at, FDRDocument.id)
    ).all()
    return DocumentList(items=[_to_out(d, name) for d, name in rows])


@router.get("/fdrs/{fdr_id}/documents/{document_id}", response_model=DocumentOut)
def get_document(
    fdr_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(readers),
) -> DocumentOut:
    response.headers["Cache-Control"] = "no-store"
    return _to_out(*_visible_document(db, request, auth, ctx, fdr_id, document_id))


def _content_disposition(filename: str) -> str:
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\;%' else "_" for c in filename) or "document"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/fdrs/{fdr_id}/documents/{document_id}/content")
def download_document(
    fdr_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(readers),
    store: DocumentStore = Depends(get_document_store),
) -> Response:
    doc, _name = _visible_document(db, request, auth, ctx, fdr_id, document_id)

    def unavailable(reason: str) -> HTTPException:
        return deny(
            db, request, auth, ctx,
            action="document.download", status_code=404,
            message="The stored file for this document is unavailable",
            reason=reason, target_fdr_id=str(fdr_id),
        )

    try:
        data = store.read(doc.storage_key)
    except StorageError:
        raise unavailable("storage_error") from None
    if data is None:
        raise unavailable("file_missing")
    if len(data) != doc.size_bytes or sha256_hex(data) != doc.sha256:
        # The file on disk is not the file that was uploaded. Never serve it.
        raise unavailable("integrity_check_failed")

    _audit(db, ctx, auth, "document.download", doc, category=doc.category, size_bytes=doc.size_bytes)
    db.commit()
    return Response(
        content=data,
        media_type=doc.content_type,
        headers={
            "Content-Disposition": _content_disposition(doc.original_filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
        },
    )


# ---- upload ----------------------------------------------------------------------------------

async def _receive(request: Request, store: DocumentStore, max_bytes: int) -> tuple[Path, int, str, bytes]:
    """Stream the body into a private temporary file. Returns (path, size, sha256, first bytes).
    The size limit applies to the bytes actually received, whatever Content-Length claimed."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status_code=413, detail=f"The file is larger than {max_bytes // (1024 * 1024)} MB")

    path = await run_in_threadpool(store.new_incoming_path)
    digest = hashlib.sha256()
    total = 0
    head = b""
    try:
        with open(path, "xb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413, detail=f"The file is larger than {max_bytes // (1024 * 1024)} MB"
                    )
                if len(head) < SIGNATURE_BYTES:
                    head += chunk[: SIGNATURE_BYTES - len(head)]
                digest.update(chunk)
                await run_in_threadpool(out.write, chunk)
    except BaseException:
        store.discard(path)
        raise
    return path, total, digest.hexdigest(), head


def _precheck(db, request, auth, ctx, fdr_id: uuid.UUID) -> None:
    """Refuse before reading the body when the upload could never succeed."""
    _require_editable(db, request, auth, ctx, _visible_fdr(db, request, auth, ctx, fdr_id), "document.upload")


def _reject(db, request, auth, ctx, fdr_id: uuid.UUID, exc: UploadRejected) -> HTTPException:
    return deny(
        db, request, auth, ctx,
        action="document.upload", status_code=exc.status_code, message=exc.message,
        reason=exc.reason, target_fdr_id=str(fdr_id),
    )


def _store_document(
    db: Session, request: Request, auth: AuthContext, ctx: RequestContext, store: DocumentStore, settings: Settings,
    *, fdr_id: uuid.UUID, category: DocumentCategory, incoming: Path, size: int, checksum: str,
    display_name: str, content_type: str,
) -> DocumentOut:
    fdr = locked_visible_fdr(db, request, auth, ctx, fdr_id)
    _require_editable(db, request, auth, ctx, fdr, "document.upload")
    active = db.scalar(
        select(func.count()).select_from(FDRDocument).where(
            FDRDocument.fdr_id == fdr.id, FDRDocument.status == DocumentStatus.ACTIVE.value
        )
    ) or 0
    if active >= settings.document_max_per_fdr:
        raise deny(
            db, request, auth, ctx,
            action="document.upload", status_code=409,
            message=f"An FDR can have at most {settings.document_max_per_fdr} documents",
            reason="document_limit_reached", target_fdr_id=str(fdr.id),
        )

    key = store.new_key()
    doc = FDRDocument(
        fdr_id=fdr.id, category=category.value, storage_key=key, original_filename=display_name,
        content_type=content_type, size_bytes=size, sha256=checksum, uploaded_by=auth.user.id,
        uploaded_at=utcnow(),
    )
    try:
        db.add(doc)
        db.flush()
        _audit(db, ctx, auth, "document.upload", doc, category=doc.category, size_bytes=size, content_type=content_type)
        store.promote(incoming, key)  # file first: metadata must never outlive a missing file
        db.commit()
    except (StorageError, SQLAlchemyError):
        db.rollback()
        store.delete(key)  # the key is freshly generated, so removing it can only remove our own file
        raise HTTPException(status_code=503, detail="The document could not be stored. Try again.") from None
    except BaseException:
        db.rollback()
        store.delete(key)
        raise
    return _to_out(doc, auth.user.full_name)


@router.post("/fdrs/{fdr_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    fdr_id: uuid.UUID,
    request: Request,
    response: Response,
    category: DocumentCategory = Query(..., description="Document category"),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(writers),
    settings: Settings = Depends(get_settings),
    store: DocumentStore = Depends(get_document_store),
) -> DocumentOut:
    response.headers["Cache-Control"] = "no-store"
    await run_in_threadpool(_precheck, db, request, auth, ctx, fdr_id)

    incoming, size, checksum, head = await _receive(request, store, settings.document_max_bytes)
    try:
        if size == 0:
            raise await run_in_threadpool(
                _reject, db, request, auth, ctx, fdr_id, UploadRejected(422, "The file is empty", "empty_file")
            )
        try:
            checked = validate_upload(request.headers.get(FILENAME_HEADER), request.headers.get("content-type"), head)
        except UploadRejected as exc:
            raise await run_in_threadpool(_reject, db, request, auth, ctx, fdr_id, exc) from None
        return await run_in_threadpool(
            _store_document, db, request, auth, ctx, store, settings,
            fdr_id=fdr_id, category=category, incoming=incoming, size=size, checksum=checksum,
            display_name=checked.display_name, content_type=checked.content_type,
        )
    finally:
        store.discard(incoming)  # a no-op once the file was moved into place


# ---- delete ----------------------------------------------------------------------------------

@router.delete("/fdrs/{fdr_id}/documents/{document_id}", status_code=204)
def delete_document(
    fdr_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(writers),
    store: DocumentStore = Depends(get_document_store),
) -> Response:
    """Remove a document from the FDR (soft delete: the row stays, hidden from every listing). The file
    itself is erased only if the FDR was never submitted; once a reviewer may have seen it, the bytes
    are kept so the earlier submission remains reconstructable. There is no purge job yet."""
    fdr = locked_visible_fdr(db, request, auth, ctx, fdr_id)
    _require_editable(db, request, auth, ctx, fdr, "document.delete")
    doc = db.scalars(
        select(FDRDocument)
        .where(FDRDocument.id == document_id, FDRDocument.fdr_id == fdr.id, FDRDocument.status == DocumentStatus.ACTIVE.value)
        .with_for_update()
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.status = DocumentStatus.DELETED.value
    doc.deleted_at = utcnow()
    doc.deleted_by = auth.user.id
    _audit(db, ctx, auth, "document.delete", doc, category=doc.category, size_bytes=doc.size_bytes)
    db.commit()
    if fdr.submitted_at is None:
        store.delete(doc.storage_key)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
