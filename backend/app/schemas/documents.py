import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.document import DocumentCategory


class DocumentOut(BaseModel):
    """What the API says about a stored document. The storage key is deliberately absent."""

    id: uuid.UUID
    fdr_id: uuid.UUID
    category: DocumentCategory
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by_name: str
    uploaded_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]
