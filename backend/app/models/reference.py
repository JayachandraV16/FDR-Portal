from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Bank(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reference list of banks used as a lookup. Not a bank integration."""

    __tablename__ = "banks"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Institution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Foundation record only; institution management arrives in Checkpoint 2."""

    __tablename__ = "institutions"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    institution_type: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
