"""Validate manifests and load a dataset in one atomic transaction."""

from collections.abc import Callable
import json
from pathlib import Path

from sqlalchemy import select, text

from deposition_toolkit.stages.common.manifest import discover_structure_dirs, manifest_path
from deposition_toolkit.stages.deposit.deposition import dataset_from_manifest

from .database import session_scope
from .models import Structure
from .schemas import StructureManifest


def read_manifests(work_dir: Path) -> list[StructureManifest]:
    records = []
    identities = set()
    for directory in discover_structure_dirs(work_dir):
        path = manifest_path(directory)
        if not path.is_file():
            raise ValueError(f"Structure manifest missing: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_invalid_constant)
            if not isinstance(payload, dict):
                raise ValueError("manifest must be a JSON object")
            for field in ("group_id", "structure_id"):
                if not isinstance(payload.get(field), str) or not payload[field].strip():
                    raise ValueError(f"{field} must be a non-empty string")
            dataset = payload.get("dataset") or dataset_from_manifest(payload)
            if not isinstance(dataset, str) or not dataset.strip():
                raise ValueError("dataset or source_tarball is required")
            record = StructureManifest(
                group_id=payload["group_id"], dataset=dataset,
                structure_id=payload["structure_id"], structure_details=payload,
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid manifest {path}: {exc}") from exc
        if record.structure_id in identities:
            raise ValueError(f"Duplicate structure_id in input: {record.structure_id}")
        identities.add(record.structure_id)
        records.append(record)
    if not records:
        raise ValueError(f"No structure manifests found in {work_dir}")
    if len({(record.group_id, record.dataset) for record in records}) != 1:
        raise ValueError("All manifests must belong to the same group_id and dataset.")
    return records


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def load_manifests(
    work_dir: Path, *, confirm: Callable[[str, int, int], bool],
) -> int | None:
    records = read_manifests(work_dir)
    group_id = records[0].group_id
    query = select(Structure).where(Structure.group_id == group_id)
    with session_scope() as session:
        existing = {row.structure_id for row in session.scalars(query)}
    incoming = {record.structure_id for record in records}
    if existing and not confirm(group_id, len(existing & incoming), len(incoming - existing)):
        return None

    with session_scope() as session:
        # Serialize loaders of this group, including the first load. Never hold
        # a transaction open while waiting for an operator's answer.
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:group_id, 0))"),
                            {"group_id": group_id})
        entries = {row.structure_id: row for row in session.scalars(query)}
        if set(entries) != existing:
            raise ValueError("The group changed during loading. Rerun to review the updated counts.")
        if any(row.dataset != records[0].dataset for row in entries.values()):
            raise ValueError("The group_id already belongs to a different dataset.")
        for record in records:
            entry = entries.get(record.structure_id)
            if entry is None:
                session.add(Structure(**record.model_dump()))
            else:
                entry.dataset = record.dataset
                entry.structure_details = record.structure_details
    return len(records)
