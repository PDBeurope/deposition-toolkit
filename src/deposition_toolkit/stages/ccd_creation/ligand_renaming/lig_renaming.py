import csv
import re
import logging
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


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
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in ("*_enriched_struc.cif", "*_struc.cif"):
        for path in sorted(structure_dir.glob(pattern), key=lambda item: item.name.lower()):
            if path.is_file() and path not in seen:
                files.append(path)
                seen.add(path)
    return files


def rename_ligands_for_structure(
    structure: str,
    structure_dir: Path,
    ligand_rows: list[dict[str, str]],
    logger: logging.Logger,
) -> RenameResult:
    lig_pattern = re.compile(r"\bLIG\b")

    unique_ccds = {
        row["CCD"].strip()
        for row in ligand_rows
        if row.get("CCD", "").strip()
    }

    if len(unique_ccds) > 1:
        message = (
            f"MANUAL REVIEW REQUIRED: '{structure}' maps to multiple different "
            f"CCDs ({', '.join(sorted(unique_ccds))}). Skipping automatic replacement."
        )
        logger.warning(message)
        return RenameResult(structure=structure, status="failed", messages=[message])
    if len(unique_ccds) == 0:
        message = f"SKIPPED: '{structure}' has no valid CCD values in the CSV."
        logger.warning(message)
        return RenameResult(structure=structure, status="not applicable", messages=[message])

    ccd_to_use = unique_ccds.pop()
    matched_files = coordinate_cif_files(structure_dir)
    if not matched_files:
        message = f"FILE NOT FOUND: No coordinate *_struc.cif found in directory '{structure_dir}'"
        logger.warning(message)
        return RenameResult(structure=structure, status="failed", messages=[message])

    result = RenameResult(structure=structure, status="done")
    for cif_path in matched_files:
        try:
            content = cif_path.read_text(encoding="utf-8")
            new_content, num_replacements = lig_pattern.subn(ccd_to_use, content)
            if num_replacements > 0:
                cif_path.write_text(new_content, encoding="utf-8")
                result.replacements += num_replacements
                result.files.append(str(cif_path))
                logger.info(
                    "SUCCESS: '%s' -> Replaced %d instance(s) of 'LIG' with '%s' in %s",
                    structure,
                    num_replacements,
                    ccd_to_use,
                    cif_path.name,
                )
            else:
                result.files.append(str(cif_path))
                logger.info(
                    "UNCHANGED: '%s' -> No standalone instances of 'LIG' found in %s",
                    structure,
                    cif_path.name,
                )
        except Exception as exc:
            message = f"ERROR: Failed to process file {cif_path}. Reason: {exc}"
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
        results[structure] = rename_ligands_for_structure(
            structure,
            struct_dir_path,
            structures_map[structure],
            logger,
        )
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rename 'LIG' to CCD codes in CIF files based on a CSV mapping.")
    parser.add_argument("csv_file", help="Path to the input CSV file")
    parser.add_argument("root_dir", help="Root directory containing the structure subfolders")
    parser.add_argument("--log-path", help="Optional path for a durable log file.")
    
    args = parser.parse_args()
    
    process_cif_files(
        args.csv_file,
        args.root_dir,
        log_path=Path(args.log_path) if args.log_path else Path("ligand_rename.log"),
    )
