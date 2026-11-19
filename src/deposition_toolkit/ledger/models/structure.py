"""Full manifests for structures within a deposition group."""

from typing import Any

from sqlalchemy import Integer, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class Structure(Base):
    __tablename__ = "structures"
    __table_args__ = (UniqueConstraint("group_id", "structure_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[str] = mapped_column(Text, index=True)
    dataset: Mapped[str] = mapped_column(Text)
    structure_id: Mapped[str] = mapped_column(Text)
    structure_details: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
