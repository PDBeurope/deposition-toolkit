"""Short registry transactions used by the deposit command only."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .database import session_scope
from .models import DepositionRegistry
from .schemas import DepositionIdentity, DepositionRegistration


def find_session_id(dataset: str, structure_id: str) -> str | None:
    identity = DepositionIdentity(dataset=dataset, structure_id=structure_id)
    with session_scope() as session:
        entry = session.execute(
            select(DepositionRegistry.dep_id, DepositionRegistry.session_id).where(
                DepositionRegistry.dataset == identity.dataset,
                DepositionRegistry.structure_id == identity.structure_id,
            )
        ).one_or_none()
        if entry is None:
            return None
        if not entry.session_id:
            raise ValueError("Existing deposition has no saved OneDep session; reconcile the registry before uploading.")
        return entry.session_id


def save_deposition(record: dict[str, str]) -> None:
    registration = DepositionRegistration.model_validate(record)
    values = registration.model_dump()
    # PostgreSQL resolves simultaneous inserts atomically at the identity constraint.
    statement = insert(DepositionRegistry).values(**values, automated_depostion=True)
    statement = statement.on_conflict_do_update(
        index_elements=[DepositionRegistry.dataset, DepositionRegistry.structure_id],
        set_={
            "dep_id": statement.excluded.dep_id,
            "session_id": statement.excluded.session_id,
            "automated_depostion": True,
            "deposited_on": func.now(),
        },
    )
    with session_scope() as session:
        session.execute(statement)
