"""Read-only bank reference data.

Banks are a shared, non-tenant lookup list (code + name), so every authenticated role may read all of
them; institution users need it to choose a bank. There is no create/update/delete here, and no
external bank integration: these are the rows already in the `banks` table.
"""

import math

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_authenticated
from app.db import get_db
from app.models import Bank
from app.schemas.fdrs import BankList, BankOut

router = APIRouter(prefix="/banks", tags=["banks"])


@router.get("", response_model=BankList)
def list_banks(
    response: Response,
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _auth: AuthContext = Depends(require_authenticated),
) -> BankList:
    response.headers["Cache-Control"] = "no-store"
    total = db.scalar(select(func.count()).select_from(Bank)) or 0
    rows = db.scalars(
        select(Bank).order_by(Bank.name, Bank.id).limit(page_size).offset((page - 1) * page_size)
    ).all()
    return BankList(
        items=[BankOut.model_validate(b) for b in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=math.ceil(total / page_size),
    )
