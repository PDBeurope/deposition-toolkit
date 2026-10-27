"""Base for validated inputs and outputs of future ledger operations."""

from pydantic import BaseModel, ConfigDict


class LedgerSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
