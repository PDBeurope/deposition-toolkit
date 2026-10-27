"""One current OneDep registration per dataset and structure."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class DepositionRegistry(Base):
    __tablename__ = "deposition_registry"
    __table_args__ = (UniqueConstraint("dataset", "structure_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dep_id: Mapped[str] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(Text)
    structure_id: Mapped[str] = mapped_column(Text)
    # Manual depositions may have no resumable local OneDep session.
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    automated_depostion: Mapped[bool] = mapped_column(Boolean, server_default=true())
    deposited_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
