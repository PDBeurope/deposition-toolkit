import argparse
import csv
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import gemmi

from deposition_toolkit.stages.common.manifest import (
    find_coordinate_files, load_manifest, set_top_level_fields,
)
from deposition_toolkit.stages.ccd_creation.ligand_renaming.instance_rename import rename_document


@dataclass
class RenameResult:
    structure: str
    status: str
    files: list[str] = field(default_factory=list)
    replacements: int = 0
    messages: list[str] = field(default_factory=list)


def setup_logging(log_path: Path | None = None) -> logging.Logger:
    """Configure logging to output to both console and a log file."""
    logger = logging.getLogger("ligand_rename")
    logger.setLevel(logging.INFO)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()

    formatter = logging.Formatter("%(levelname)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, mode="w", encoding="utf-8"))

    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def load_ligand_mapping(csv_path: Path) -> dict[str, list[dict[str, str]]]:
    structures_map = {}
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            struct = row["structure"].strip()
            structures_map.setdefault(struct, []).append(row)
    return structures_map


def coordinate_cif_files(structure_dir: Path) -> list[Path]:
    return find_coordinate_files(structure_dir, prefer_enriched=False)


def manifest_ligands(structure_dir: Path, summary_rows: list[dict] | None = None) -> list[dict]:
    """An optional legacy report can supply codes, but never instance identity."""
    records = load_manifest(structure_dir).get("ccd_ligands", [])
    if summary_rows is None:
        return records
    by_name = {}
    for row in summary_rows:
        name = row.get("ligand_name", "")
        if not name or name in by_name:
            raise ValueError("CSV must contain one row per ligand_name.")
        by_name[name] = row
    if set(by_name) != {record["ligand_name"] for record in records}:
        raise ValueError("CSV ligand names do not match manifest provenance; rerun CCD creation.")
    return [dict(record, CCD=by_name[record["ligand_name"]].get("CCD", "")) for record in records]


def rename_ligands_for_structure(
    structure: str,
    structure_dir: Path,
    ligand_rows: list[dict],
    logger: logging.Logger,
) -> RenameResult:
    result = RenameResult(structure=structure, status="done")
    staged = []
    try:
        matched_files = coordinate_cif_files(structure_dir)
        if not matched_files:
            raise ValueError(f"No coordinate CIF files found in {structure_dir}.")
        # Validate all coordinate variants before replacing any files.
        for cif_path in matched_files:
            document = gemmi.cif.read_file(str(cif_path))
            replacements = rename_document(document, ligand_rows)
            staged.append((cif_path, document.as_string(), replacements))
        for cif_path, content, replacements in staged:
            if replacements:
                fd, name = tempfile.mkstemp(dir=cif_path.parent, prefix=f".{cif_path.name}.")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as output:
                        output.write(content)
                    os.chmod(name, cif_path.stat().st_mode)
                    os.replace(name, cif_path)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
            result.files.append(str(cif_path))
            result.replacements += replacements
            logger.info("%s: updated %d component fields in %s", structure, replacements, cif_path.name)
    except Exception as exc:
        message = f"Failed to rename ligands for {structure}: {exc}"
        logger.error(message)
        result.status = "failed"
        result.messages.append(message)
    return result


def process_cif_files(
    csv_path,
    root_dir,
    *,
    include_structures: Iterable[str] | None = None,
    log_path: Path | None = None,
) -> dict[str, RenameResult]:
    logger = setup_logging(log_path)
    csv_path = Path(csv_path)
    root_dir = Path(root_dir)
    try:
        structures_map = load_ligand_mapping(csv_path)
    except FileNotFoundError:
        logger.error(f"CSV file not found: {csv_path}")
        return {}

    include_set = set(include_structures or structures_map.keys())
    results: dict[str, RenameResult] = {}

    if not root_dir.is_dir():
        logger.error(f"Root directory not found: {root_dir}")
        return results

    for struct_dir_path in sorted(root_dir.iterdir(), key=lambda item: item.name.lower()):
        if not struct_dir_path.is_dir():
            continue
        structure = struct_dir_path.name
        if structure not in include_set:
            continue
        if structure not in structures_map:
            message = f"MISSING FROM CSV: Directory '{structure}' exists on disk but has no entry in the CSV. Skipped."
            logger.warning(message)
            results[structure] = RenameResult(
                structure=structure,
                status="not applicable",
                messages=[message],
            )
            continue
        try:
            ligands = manifest_ligands(struct_dir_path, structures_map[structure])
        except ValueError as exc:
            results[structure] = RenameResult(structure, "failed", messages=[str(exc)])
            continue
        results[structure] = rename_ligands_for_structure(structure, struct_dir_path, ligands, logger)
        if results[structure].status == "done":
            set_top_level_fields(struct_dir_path, ccd_ligands=ligands)
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename manifest ligand instances with codes from a CSV report.")
    parser.add_argument("csv_file", help="Path to the input CSV file")
    parser.add_argument("root_dir", help="Root directory containing the structure subfolders")
    parser.add_argument("--log-path", help="Optional path for a durable log file.")
    
    args = parser.parse_args()
    
    process_cif_files(
        args.csv_file,
        args.root_dir,
        log_path=Path(args.log_path) if args.log_path else Path("ligand_rename.log"),
    )
