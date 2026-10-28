"""Bulk FDR import from a CSV file. Institution users only, drafts only, no schema of its own.

  GET  /fdrs/import/template   a sample CSV with the exact columns
  POST /fdrs/import/preview    validate a file and report every row; nothing is saved
  POST /fdrs/import            validate AGAIN and create the valid rows as drafts (needs the preview's digest)
  POST /fdrs/import/errors     the rejected rows of a file as a spreadsheet-safe CSV

Safety rules, in the order they matter:
  * Every imported row becomes a DRAFT of the caller's own institution through the same function the Create FDR
    endpoint uses (`add_draft_fdr`), so it gets the same validation, history entry and audit event. There is no
    status, institution or id column: a file that has a `status` column (or any unknown column) is refused outright,
    so a file cannot create an accepted, released or otherwise privileged record, or one for another institution.
  * The server never trusts the preview: confirming re-reads and re-validates the file. The client sends the file's
    SHA-256 from the preview; if the bytes differ, nothing is imported (409), so what was reviewed is what is imported.
  * Duplicates are deterministic: a row is rejected if an earlier row of the same file, or an existing record of the
    caller's institution that is not rejected or withdrawn, has the same bank and (normalised) FDR number. Importing
    the same file twice therefore creates nothing the second time.
  * Limits: 1 MB, 500 data rows, UTF-8, comma-separated, ISO dates and plain decimals; the body is read in a stream
    and stopped at the limit whatever Content-Length says. Invalid rows are always reported, never dropped silently.
  * Rows are created inside savepoints, so one row's database error cannot undo the others; the run commits once.
  * The error report neutralises spreadsheet formulas (a cell starting with = + - @ gets a leading apostrophe).
"""

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_roles
from app.api.fdrs import add_draft_fdr
from app.audit import AuditOutcome, write_audit_event
from app.db import get_db
from app.models import FDR, Bank, FdrStatus, Role
from app.models.fdr import normalize_fdr_number
from app.schemas.fdrs import FdrCreate

router = APIRouter(prefix="/fdrs/import", tags=["fdr-import"])
institution_only = require_roles(Role.INSTITUTION_USER)

MAX_BYTES = 1_000_000
MAX_ROWS = 500
COLUMNS = ("fdr_number", "bank_code", "branch_name", "depositor_name", "purpose", "principal_amount", "interest_rate_pct", "issue_date", "maturity_date")
REQUIRED = ("fdr_number", "bank_code", "principal_amount", "issue_date", "maturity_date")
ACCEPTED_TYPES = ("text/csv", "application/csv", "application/vnd.ms-excel", "text/plain")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT = re.compile(r"^\d{1,12}(\.\d{1,2})?$")
_RATE = re.compile(r"^\d{1,3}(\.\d{1,2})?$")
_BLOCKING = (FdrStatus.REJECTED.value, FdrStatus.WITHDRAWN.value)
_FRIENDLY = {
    "decimal_parsing": "must be a plain number such as 1500000.00", "decimal_type": "must be a plain number such as 1500000.00",
    "decimal_max_places": "may have at most 2 decimal places", "decimal_max_digits": "has too many digits",
    "greater_than": "must be greater than 0", "greater_than_equal": "must be at least 0", "less_than_equal": "must be at most 100",
    "string_too_long": "is too long", "string_too_short": "is required", "missing": "is required",
}


class RowReport(BaseModel):
    line: int
    fdr_number: str
    bank_code: str
    valid: bool
    errors: list[str]


class ImportPreview(BaseModel):
    digest: str  # SHA-256 of the file; send it with the confirmation
    rows_total: int
    valid_count: int
    invalid_count: int
    rows: list[RowReport]
    max_rows: int
    note: str


class RowOutcome(BaseModel):
    line: int
    fdr_number: str
    outcome: str  # created | rejected
    fdr_id: uuid.UUID | None
    errors: list[str]


class ImportResult(BaseModel):
    created: int
    rejected: int
    rows: list[RowOutcome]
    note: str


NOTE = "Imported rows are created as drafts of your institution. Nothing is submitted, accepted or released by an import."


class ImportFileError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


@dataclass
class Row:
    line: int
    cells: dict[str, str]
    errors: list[str] = field(default_factory=list)
    payload: FdrCreate | None = None
    normalized: str = ""
    bank_id: uuid.UUID | None = None


# ---- reading and parsing the file ----------------------------------------------------------------------------------
async def _read(request: Request) -> bytes:
    kind = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if kind not in ACCEPTED_TYPES:
        raise HTTPException(status_code=415, detail="Send the file as CSV (Content-Type text/csv).")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // 1_000_000} MB.")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // 1_000_000} MB.")
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty.")
    return bytes(data)


def parse(data: bytes) -> list[Row]:
    try:
        text = data.decode("utf-8-sig")  # a spreadsheet's BOM is allowed; anything that is not UTF-8 is not
    except UnicodeDecodeError:
        raise ImportFileError(422, "The file is not UTF-8 text. In your spreadsheet, save it as 'CSV UTF-8'.") from None
    if "\x00" in text:
        raise ImportFileError(422, "The file contains binary data and is not a CSV file.")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise ImportFileError(422, "The file is empty.") from None
    except csv.Error:
        raise ImportFileError(422, "The file is not valid CSV.") from None
    names = [h.strip().lower() for h in header]
    unknown = [n for n in names if n not in COLUMNS]
    if unknown:
        shown = ", ".join(repr(n[:30]) for n in unknown[:5])
        hint = " An import cannot set a status: every row becomes a draft." if "status" in names else ""
        raise ImportFileError(422, f"Unknown column {shown}. Use exactly the columns of the template.{hint}")
    if len(set(names)) != len(names):
        raise ImportFileError(422, "A column appears more than once in the header.")
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise ImportFileError(422, f"Missing column{'s' if len(missing) > 1 else ''}: {', '.join(missing)}.")

    rows: list[Row] = []
    try:
        for values in reader:
            if not any(v.strip() for v in values):
                continue  # a blank line is not a row
            if len(rows) >= MAX_ROWS:
                raise ImportFileError(413, f"The file has more than {MAX_ROWS} rows. Split it into smaller files.")
            row = Row(line=reader.line_num, cells={c: "" for c in COLUMNS})
            if len(values) > len(names):
                row.errors.append("has more values than the header has columns")
            for name, value in zip(names, values):
                row.cells[name] = value.strip()
            rows.append(row)
    except csv.Error:
        raise ImportFileError(422, "The file is not valid CSV (check the quotation marks).") from None
    if not rows:
        raise ImportFileError(422, "The file has a header but no rows.")
    return rows


# ---- validating the rows ----------------------------------------------------------------------------------------
def _friendly(err: dict) -> str:
    field_name = str(err["loc"][-1]) if err.get("loc") else "row"
    kind = err.get("type", "")
    if kind.startswith("date") or kind == "date_type":
        return f"{field_name} must be a date written YYYY-MM-DD"
    if kind == "value_error":
        return str(err.get("msg", "is invalid")).removeprefix("Value error, ")
    return f"{field_name} {_FRIENDLY.get(kind, 'is not valid')}"


def validate(db: Session, auth: AuthContext, rows: list[Row]) -> list[Row]:
    """Fill in each row's errors/payload. Reads only; the same checks run for the preview and the import."""
    banks = {b.code.upper(): b for b in db.scalars(select(Bank))}
    numbers = set()
    for row in rows:
        try:
            numbers.add(normalize_fdr_number(row.cells["fdr_number"]))
        except ValueError:
            pass
    existing: dict[tuple[uuid.UUID, str], str] = {}
    if numbers:
        for bank_id, number, status in db.execute(
            select(FDR.bank_id, FDR.fdr_number_normalized, FDR.status).where(
                FDR.institution_id == auth.user.institution_id, FDR.fdr_number_normalized.in_(sorted(numbers)), FDR.status.notin_(_BLOCKING)
            )
        ):
            existing[(bank_id, number)] = status
    seen: dict[tuple[uuid.UUID, str], int] = {}
    for row in rows:
        c = row.cells
        for name in REQUIRED:
            if not c[name]:
                row.errors.append(f"{name} is required")
        for name, value in c.items():
            if any(ord(ch) < 32 for ch in value):
                row.errors.append(f"{name} contains control characters")
        bank = banks.get(c["bank_code"].upper()) if c["bank_code"] else None
        if c["bank_code"] and bank is None:
            row.errors.append(f"bank_code {c['bank_code'][:32]!r} is not a known bank code")
        if c["principal_amount"] and not _AMOUNT.match(c["principal_amount"]):
            row.errors.append("principal_amount must be a plain number such as 1500000.00 (no commas or currency signs)")
        if c["interest_rate_pct"] and not _RATE.match(c["interest_rate_pct"]):
            row.errors.append("interest_rate_pct must be a plain number such as 7.25")
        for name in ("issue_date", "maturity_date"):
            if c[name] and not _DATE.match(c[name]):
                row.errors.append(f"{name} must be a date written YYYY-MM-DD")
        if row.errors:
            continue
        try:
            row.normalized = normalize_fdr_number(c["fdr_number"])
            row.bank_id = bank.id
            row.payload = FdrCreate.model_validate({
                "bank_id": bank.id, "fdr_number": c["fdr_number"], "branch_name": c["branch_name"] or None, "depositor_name": c["depositor_name"] or None,
                "purpose": c["purpose"] or None, "principal_amount": Decimal(c["principal_amount"]),
                "interest_rate_pct": Decimal(c["interest_rate_pct"]) if c["interest_rate_pct"] else None, "issue_date": c["issue_date"], "maturity_date": c["maturity_date"],
            })
        except ValidationError as exc:
            row.errors.extend(_friendly(e) for e in exc.errors())
            continue
        except ValueError:
            row.errors.append("fdr_number is required")
            continue
        key = (row.bank_id, row.normalized)
        if key in seen:
            row.errors.append(f"duplicate of line {seen[key]} in this file (same bank and FDR number)")
        elif key in existing:
            row.errors.append(f"an FDR with this number already exists at this bank in your institution (status {existing[key].replace('_', ' ').title()})")
        else:
            seen[key] = row.line
        if row.errors:
            row.payload = None
    return rows


def _prepare(db: Session, request: Request, auth: AuthContext, ctx: RequestContext, data: bytes) -> list[Row]:
    if auth.user.institution_id is None:  # an inconsistent account: fail closed
        raise deny(db, request, auth, ctx, reason="no_institution")
    try:
        return validate(db, auth, parse(data))
    except ImportFileError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(value) -> str:
    """A CSV cell that a spreadsheet will not treat as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(header: list[str], rows: list[list], filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows([[_safe(c) for c in row] for row in rows])
    return Response(
        content="﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


# ---- endpoints -------------------------------------------------------------------------------------------------
@router.get("/template")
def template(db: Session = Depends(get_db), auth: AuthContext = Depends(institution_only)) -> Response:
    bank = db.scalars(select(Bank).order_by(Bank.code).limit(1)).first()
    example = ["SAMPLE-0001", bank.code if bank else "BANKCODE", "Main Branch", "Example Depositor", "Security deposit", "1500000.00", "7.10", "2026-01-15", "2027-01-15"]
    return _csv(list(COLUMNS), [example], "fdr-import-template.csv")


@router.post("/preview", response_model=ImportPreview)
async def preview(
    request: Request, response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(institution_only),
) -> ImportPreview:
    response.headers["Cache-Control"] = "no-store"
    data = await _read(request)
    rows = _prepare(db, request, auth, ctx, data)
    reports = [RowReport(line=r.line, fdr_number=r.cells["fdr_number"][:64], bank_code=r.cells["bank_code"][:32], valid=not r.errors, errors=r.errors) for r in rows]
    valid = sum(1 for r in reports if r.valid)
    return ImportPreview(digest=_digest(data), rows_total=len(rows), valid_count=valid, invalid_count=len(rows) - valid, rows=reports, max_rows=MAX_ROWS, note=NOTE)


@router.post("", response_model=ImportResult)
async def run_import(
    request: Request, response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(institution_only), digest: str | None = Header(default=None, alias="X-Import-Digest"),
) -> ImportResult:
    response.headers["Cache-Control"] = "no-store"
    data = await _read(request)
    if digest is None or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != _digest(data):
        raise HTTPException(status_code=409, detail="The file is not the one that was previewed. Preview it again before importing.")
    rows = _prepare(db, request, auth, ctx, data)

    outcomes: list[RowOutcome] = []
    created = 0
    for row in rows:
        shown = row.cells["fdr_number"][:64]
        if row.errors or row.payload is None:
            outcomes.append(RowOutcome(line=row.line, fdr_number=shown, outcome="rejected", fdr_id=None, errors=row.errors))
            continue
        try:
            with db.begin_nested():  # a database error on this row leaves the other rows alone
                fdr = add_draft_fdr(db, ctx, auth, row.payload, auth.user.institution_id, row.normalized)
        except IntegrityError:
            outcomes.append(RowOutcome(line=row.line, fdr_number=shown, outcome="rejected", fdr_id=None, errors=["the database refused this row"]))
            continue
        created += 1
        outcomes.append(RowOutcome(line=row.line, fdr_number=shown, outcome="created", fdr_id=fdr.id, errors=[]))
    rejected = len(rows) - created
    write_audit_event(
        db, action="fdr.import", outcome=AuditOutcome.SUCCESS, actor_user_id=auth.user.id, actor_role=auth.user.role, entity_type="fdr",
        entity_id=None, ip_address=ctx.ip_address, request_id=ctx.request_id, details={"result_count": created, "filters": [f"rejected:{rejected}"]},
    )
    db.commit()
    return ImportResult(created=created, rejected=rejected, rows=outcomes, note=NOTE)


@router.post("/errors")
async def error_report(
    request: Request, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(institution_only),
) -> Response:
    data = await _read(request)
    rows = _prepare(db, request, auth, ctx, data)
    return _csv(
        ["line", "fdr_number", "bank_code", "problems"],
        [[r.line, r.cells["fdr_number"], r.cells["bank_code"], "; ".join(r.errors)] for r in rows if r.errors],
        "fdr-import-rejected-rows.csv",
    )
