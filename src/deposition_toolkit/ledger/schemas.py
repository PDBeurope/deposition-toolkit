"""Validated inputs and outputs for ledger operations."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LedgerSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DepositionIdentity(LedgerSchema):
    dataset: str = Field(min_length=1)
    structure_id: str = Field(min_length=1)


class DepositionRegistration(DepositionIdentity):
    dep_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class StructureManifest(DepositionIdentity):
    group_id: str = Field(min_length=1)
    structure_details: dict[str, Any]
