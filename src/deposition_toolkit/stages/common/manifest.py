from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_NAME = "manifest.json"
SKIPPED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".ccd_cache",
    "logs",
    "results",
    "generated_ccd_inputs",
    "one-chain",
    "more-than-one-chain",
}
STRUCTURE_FILE_PATTERNS = (
    "*_struc.cif",
    "*_struc.mmcif",
    "*_lig.cif",
    "*_lig.mmcif",
    "*_sf.cif",
    "*_sf.mmcif",
    "*_sf.ent",
)
COORDINATE_PATTERNS = (
    "*_enriched_struc.cif",
    "*_enriched_struc.mmcif",
    "*_struc.cif",
    "*_struc.mmcif",
)
RESTRAINT_PATTERNS = ("*_lig.cif", "*_lig.mmcif")
STRUCTURE_FACTOR_PATTERNS = ("*_sf.cif", "*_sf.mmcif", "*_sf.ent", "r*sf.ent")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2, sort_keys=True)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def manifest_path(structure_dir: Path) -> Path:
    return structure_dir / MANIFEST_NAME


def default_manifest(structure_dir: Path, group_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "structure_id": structure_dir.name,
        "group_id": group_id or "",
        "chains": "",
        "excluded": False,
        "steps": {},
    }
    return payload


def load_manifest(structure_dir: Path) -> dict[str, Any]:
    path = manifest_path(structure_dir)
    if not path.exists():
        return default_manifest(structure_dir)
    with path.open("r", encoding="utf-8") as manifest_file:
        payload = json.load(manifest_file)
    payload.setdefault("structure_id", structure_dir.name)
    payload.setdefault("group_id", "")
    payload.setdefault("chains", "")
    payload.setdefault("excluded", False)
    payload.setdefault("steps", {})
    return payload


def write_manifest(structure_dir: Path, payload: dict[str, Any]) -> None:
    payload.setdefault("structure_id", structure_dir.name)
    payload.setdefault("steps", {})
    atomic_write_json(manifest_path(structure_dir), payload)


def ensure_manifest(
    structure_dir: Path,
    *,
    group_id: str | None = None,
    initial_step: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_manifest(structure_dir)
    if group_id is not None:
        payload["group_id"] = group_id
    if initial_step is not None:
        steps = payload.setdefault("steps", {})
        steps.setdefault("prepare_dataset", initial_step)
    write_manifest(structure_dir, payload)
    return payload


def update_step(
    structure_dir: Path,
    step_name: str,
    status: str,
    *,
    outputs: Iterable[str | Path] | None = None,
    output: str | Path | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_manifest(structure_dir)
    step: dict[str, Any] = {
        "status": status,
        "timestamp": utc_now(),
    }
    if outputs is not None:
        step["outputs"] = [str(item) for item in outputs]
    if output is not None:
        step["output"] = str(output)
    if error:
        step["error"] = error
    if extra:
        step.update(extra)
    payload.setdefault("steps", {})[step_name] = step
    write_manifest(structure_dir, payload)
    return payload


def set_top_level_fields(structure_dir: Path, **fields: Any) -> dict[str, Any]:
    payload = load_manifest(structure_dir)
    payload.update(fields)
    write_manifest(structure_dir, payload)
    return payload


def is_excluded(structure_dir: Path) -> bool:
    return bool(load_manifest(structure_dir).get("excluded", False))


def relative_to_work_dir(path: str | Path, work_dir: Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(work_dir.resolve()))
    except ValueError:
        return str(path)


def _has_structure_files(path: Path) -> bool:
    return any(next(path.glob(pattern), None) for pattern in STRUCTURE_FILE_PATTERNS)


def _should_prune(path: Path, work_dir: Path) -> bool:
    if path == work_dir:
        return False
    return any(part in SKIPPED_DIR_NAMES for part in path.relative_to(work_dir).parts)


def discover_structure_dirs(work_dir: Path) -> list[Path]:
    work_dir = work_dir.resolve()
    if not work_dir.is_dir():
        raise FileNotFoundError(f"Work directory does not exist: {work_dir}")

    discovered: set[Path] = set()
    for path in [work_dir, *work_dir.rglob("*")]:
        if not path.is_dir() or _should_prune(path, work_dir):
            continue
        if _has_structure_files(path) or manifest_path(path).is_file():
            discovered.add(path)

    return sorted(discovered, key=lambda item: str(item.relative_to(work_dir)).lower())


def active_structure_dirs(work_dir: Path) -> list[Path]:
    return [path for path in discover_structure_dirs(work_dir) if not is_excluded(path)]


def find_structure_root(work_dir: Path, structure_dirs: Iterable[Path] | None = None) -> Path:
    structures = list(structure_dirs or discover_structure_dirs(work_dir))
    if not structures:
        return work_dir
    parents = {path.parent.resolve() for path in structures}
    if len(parents) == 1:
        return next(iter(parents))
    return work_dir


def find_matching_files(structure_dir: Path, patterns: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(structure_dir.glob(pattern), key=lambda item: item.name.lower()):
            if path.is_file() and path not in seen:
                files.append(path)
                seen.add(path)
    return files


def find_coordinate_files(structure_dir: Path, *, prefer_enriched: bool = True) -> list[Path]:
    files = find_matching_files(structure_dir, COORDINATE_PATTERNS)
    if not prefer_enriched:
        return files

    enriched = [path for path in files if "_enriched_struc" in path.name]
    if enriched:
        return enriched
    return [path for path in files if "_enriched_struc" not in path.name]


def find_restraint_files(structure_dir: Path) -> list[Path]:
    return find_matching_files(structure_dir, RESTRAINT_PATTERNS)


def find_structure_factor_files(structure_dir: Path) -> list[Path]:
    return find_matching_files(structure_dir, STRUCTURE_FACTOR_PATTERNS)
