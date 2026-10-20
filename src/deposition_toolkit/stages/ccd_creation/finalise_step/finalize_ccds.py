#!/usr/bin/env python3
"""Finalise annotated CCD CIFs after annotateComp.

The safe default is a preview: scan ligands, compare InChIKeys with the CCD
database, write summaries, and do not allocate CCD IDs or commit to CVS.
Use --allocate-codes only when the operator is ready to consume IDs from the
unused-code pool.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shlex
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from deposition_toolkit.config import get_settings

DB_QUERY = (
    "SELECT comp_id, descriptor "
    "FROM compv4.pdbx_chem_comp_descriptor "
    "WHERE type='inChikey'"
)
DEFAULT_COPY_TO_STRUCTURE_FOLDERS = False
PENDING_CCD = "PENDING_ALLOCATION"
SCRIPT_DIR = Path(__file__).resolve().parent
COMMIT_SCRIPT = SCRIPT_DIR / "commit_ccds_to_cvs.py"
MINIMUM_DATABASE_ROWS=55650

logger = logging.getLogger("ccd_finalizer")


@dataclass(frozen=True)
class LigandRecord:
    structure: str
    ligand_name: str
    path: Path
    inchi_key: str | None

    @property
    def label(self) -> str:
        return f"{self.structure}/{self.ligand_name}"


@dataclass(frozen=True)
class Decision:
    record: LigandRecord
    deposit: str
    ccd: str
    status: str
    db_matches: tuple[str, ...] = ()
    representative: str = ""


def normalize_inchikey(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text or text in {".", "?"}:
        return None
    return text.upper()


def normalize_ccd_id(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).strip().strip('"').strip("'")
    if not text or text in {".", "?"}:
        return ""
    return text.upper()


def as_list(value: object | None) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def extract_inchikey(cif_path: Path) -> str | None:
    try:
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    except ImportError as exc:
        raise RuntimeError(
            "BioPython is required to parse CCD CIF files. Run through the "
            "project environment or install biopython."
        ) from exc

    try:
        data = MMCIF2Dict(str(cif_path))
    except Exception as exc:
        raise RuntimeError(f"Could not parse CIF file {cif_path}: {exc}") from exc

    descriptor_types = as_list(data.get("_pdbx_chem_comp_descriptor.type"))
    descriptors = as_list(data.get("_pdbx_chem_comp_descriptor.descriptor"))
    for descriptor_type, descriptor in zip(descriptor_types, descriptors):
        if str(descriptor_type).strip().lower() == "inchikey":
            return normalize_inchikey(descriptor)
    return None


def discover_ligands(input_root: Path) -> list[LigandRecord]:
    if not input_root.is_dir():
        raise FileNotFoundError(f"CCD input root does not exist: {input_root}")

    records: list[LigandRecord] = []
    for cif_path in sorted(input_root.glob("*/*.cif")):
        structure = cif_path.parent.name
        records.append(
            LigandRecord(
                structure=structure,
                ligand_name=cif_path.name,
                path=cif_path,
                inchi_key=extract_inchikey(cif_path),
            )
        )

    if not records:
        raise RuntimeError(f"No CCD CIF files found under {input_root}/*/*.cif")

    missing = [record.label for record in records if not record.inchi_key]
    logger.info("Discovered %d ligand CIF file(s)", len(records))
    if missing:
        logger.warning("Missing InChIKey for %d ligand(s): %s", len(missing), ", ".join(missing))
    return records


def grouped_by_inchikey(records: Iterable[LigandRecord]) -> dict[str, list[LigandRecord]]:
    groups: dict[str, list[LigandRecord]] = defaultdict(list)
    for record in records:
        if record.inchi_key:
            groups[record.inchi_key].append(record)
    return dict(groups)


def log_duplicate_groups(groups: dict[str, list[LigandRecord]]) -> int:
    duplicate_count = 0
    for inchi_key, records in sorted(groups.items()):
        if len(records) <= 1:
            continue
        duplicate_count += 1
        labels = ", ".join(record.label for record in records)
        structures = ", ".join(sorted({record.structure for record in records}))
        logger.info(
            "Duplicate InChIKey %s appears in %d ligand(s): %s; structures: %s",
            inchi_key,
            len(records),
            labels,
            structures,
        )
    if duplicate_count == 0:
        logger.info("No duplicate ligand InChIKeys found within this batch")
    return duplicate_count


def read_existing_descriptor_file(path: Path) -> dict[str, set[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Existing descriptor file does not exist: {path}")

    existing: dict[str, set[str]] = defaultdict(set)
    with path.open("r", newline="", encoding="utf-8") as csv_file:
        sample = csv_file.read(4096)
        csv_file.seek(0)
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
        if sample and "," not in sample and "\t" in sample:
            delimiter = "\t"
        reader = csv.reader(csv_file, delimiter=delimiter)
        rows = [row for row in reader if row and any(cell.strip() for cell in row)]

    if not rows:
        return {}

    header = [cell.strip().lower() for cell in rows[0]]
    has_header = "descriptor" in header or "inchi_key" in header or "inchikey" in header
    data_rows = rows[1:] if has_header else rows

    descriptor_idx = 0
    ccd_idx: int | None = None
    if has_header:
        for candidate in ("descriptor", "inchi_key", "inchikey"):
            if candidate in header:
                descriptor_idx = header.index(candidate)
                break
        for candidate in ("comp_id", "ccd", "ccd_id", "id"):
            if candidate in header:
                ccd_idx = header.index(candidate)
                break
    elif rows and len(rows[0]) >= 2:
        ccd_idx = 0
        descriptor_idx = 1

    for row in data_rows:
        if descriptor_idx >= len(row):
            continue
        descriptor = normalize_inchikey(row[descriptor_idx])
        if not descriptor:
            continue
        ccd = normalize_ccd_id(row[ccd_idx]) if ccd_idx is not None and ccd_idx < len(row) else ""
        if ccd:
            existing[descriptor].add(ccd)
        else:
            existing[descriptor]

    logger.info("Loaded %d existing InChIKey descriptor(s) from %s", len(existing), path)
    return dict(existing)


def query_existing_descriptors(args: argparse.Namespace) -> dict[str, set[str]]:
    if not args.db_password:
        raise RuntimeError("Set CCD_DB_PASSWORD in .env or the environment, or pass --db-password.")
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError(
            "mysql.connector is required for the CCD database check. "
            "Install mysql-connector-python or provide --existing-descriptors-file."
        ) from exc

    logger.info(
        "Querying existing CCD InChIKeys from %s:%s with read-only user %s",
        args.db_host,
        args.db_port,
        args.db_user,
    )
    connection = mysql.connector.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        connection_timeout=args.db_timeout,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(DB_QUERY)
        existing: dict[str, set[str]] = defaultdict(set)
        for row in cursor:
            if len(row) == 1:
                comp_id = ""
                descriptor = row[0]
            else:
                comp_id = row[0]
                descriptor = row[1]
            inchi_key = normalize_inchikey(descriptor)
            if not inchi_key:
                continue
            ccd = normalize_ccd_id(comp_id)
            if ccd:
                existing[inchi_key].add(ccd)
            else:
                existing[inchi_key]
        logger.info("Loaded %d existing CCD InChIKey descriptor(s) from database", len(existing))
        if len(existing) < MINIMUM_DATABASE_ROWS:
            logger.error(f"Rows returned are less than {MINIMUM_DATABASE_ROWS}. Make sure DB is loaded correctly")
            SystemExit(0)
        return dict(existing)
    finally:
        connection.close()


def load_existing_descriptors(args: argparse.Namespace) -> dict[str, set[str]]:
    if args.existing_descriptors_file:
        return read_existing_descriptor_file(Path(args.existing_descriptors_file).expanduser().resolve())
    return query_existing_descriptors(args)


def write_descriptor_cache(path: Path, existing: dict[str, set[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["comp_id", "descriptor"])
        for descriptor, ccds in sorted(existing.items()):
            if ccds:
                for ccd in sorted(ccds):
                    writer.writerow([ccd, descriptor])
            else:
                writer.writerow(["", descriptor])
    logger.info("Wrote database descriptor cache to %s", path)


def load_code_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"CCD code map file does not exist: {path}")

    code_map: dict[str, str] = {}
    with path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames:
            fields = {field.lower(): field for field in reader.fieldnames}
            inchi_field = fields.get("inchi_key") or fields.get("inchikey") or fields.get("descriptor")
            ccd_field = fields.get("ccd") or fields.get("ccd_id") or fields.get("comp_id")
            if inchi_field and ccd_field:
                for row in reader:
                    inchi_key = normalize_inchikey(row.get(inchi_field))
                    ccd = normalize_ccd_id(row.get(ccd_field))
                    if inchi_key and ccd:
                        code_map[inchi_key] = ccd
                logger.info("Loaded %d CCD code mapping(s) from %s", len(code_map), path)
                return code_map

    raise RuntimeError(
        f"Could not read {path} as a CSV with inchi_key/descriptor and CCD columns"
    )


def allocate_codes(
    inchi_keys: Sequence[str],
    output_dir: Path,
    verbose: bool,
) -> dict[str, str]:
    if not inchi_keys:
        return {}

    try:
        from .get_new_ccd_code import get_next_unused_code
    except ImportError:
        try:
            from get_new_ccd_code import get_next_unused_code
        except ImportError as exc:
            raise RuntimeError(f"Could not import get_new_ccd_code.py from {SCRIPT_DIR}") from exc

    manifest_path = output_dir / "allocated_ccd_codes.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    allocations: dict[str, str] = {}
    logger.warning(
        "Allocating %d CCD code(s). This consumes IDs from the unused CCD pool.",
        len(inchi_keys),
    )
    with manifest_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["inchi_key", "ccd", "allocated_at"])
        for inchi_key in inchi_keys:
            code, remaining = get_next_unused_code(
                site_id="PDBE_PROD",
                dry_run=False,
                verbose=verbose,
            )
            if not code:
                raise RuntimeError(f"No unused CCD code available for {inchi_key}")
            ccd = normalize_ccd_id(code)
            allocations[inchi_key] = ccd
            writer.writerow([inchi_key, ccd, datetime.now(timezone.utc).isoformat()])
            csv_file.flush()
            logger.info(
                "Allocated CCD %s for InChIKey %s (%s codes remaining)",
                ccd,
                inchi_key,
                remaining,
            )
    logger.info("Wrote allocation manifest to %s", manifest_path)
    return allocations


def choose_representative(records: list[LigandRecord]) -> LigandRecord:
    return sorted(records, key=lambda record: (record.structure, record.ligand_name))[0]


def build_decisions(
    records: list[LigandRecord],
    existing: dict[str, set[str]],
    code_map: dict[str, str],
) -> list[Decision]:
    groups = grouped_by_inchikey(records)
    decisions: list[Decision] = []
    record_to_decision: dict[Path, Decision] = {}

    for inchi_key, group_records in sorted(groups.items()):
        representative = choose_representative(group_records)
        db_matches = tuple(sorted(existing.get(inchi_key, set())))
        if inchi_key in existing:
            ccd = db_matches[0] if db_matches else "EXISTS_IN_DB"
            status = "exists_in_database"
            for record in sorted(group_records, key=lambda item: (item.structure, item.ligand_name)):
                record_to_decision[record.path] = Decision(
                    record=record,
                    deposit="N",
                    ccd=ccd,
                    status=status,
                    db_matches=db_matches,
                    representative=representative.label,
                )
            continue

        ccd = code_map.get(inchi_key, PENDING_CCD)
        for record in sorted(group_records, key=lambda item: (item.structure, item.ligand_name)):
            is_representative = record.path == representative.path
            record_to_decision[record.path] = Decision(
                record=record,
                deposit="Y" if is_representative else "N",
                ccd=ccd,
                status="new_representative" if is_representative else "duplicate_in_batch",
                representative=representative.label,
            )

    for record in records:
        if record.path in record_to_decision:
            decisions.append(record_to_decision[record.path])
        else:
            decisions.append(
                Decision(
                    record=record,
                    deposit="N",
                    ccd="",
                    status="missing_inchikey",
                )
            )
    return sorted(decisions, key=lambda item: (item.record.structure, item.record.ligand_name))


def log_database_matches(decisions: Sequence[Decision]) -> int:
    existing_rows = 0
    matches_by_inchikey: dict[str, list[Decision]] = defaultdict(list)
    for decision in decisions:
        if decision.status != "exists_in_database":
            continue
        existing_rows += 1
        matches_by_inchikey[decision.record.inchi_key or ""].append(decision)

    if existing_rows == 0:
        logger.info("No batch ligands matched existing CCD database InChIKeys")
        return existing_rows

    logger.info(
        "Found %d ligand(s) whose InChIKey descriptor exists in the CCD database across %d descriptor group(s)",
        existing_rows,
        len(matches_by_inchikey),
    )
    for inchi_key, group in sorted(matches_by_inchikey.items()):
        ccd_matches = sorted({ccd for decision in group for ccd in decision.db_matches})
        matches = ", ".join(ccd_matches) if ccd_matches else "descriptor present, CCD ID unavailable"
        structures = ", ".join(sorted({decision.record.structure for decision in group}))
        logger.info(
            "Database descriptor exists: InChIKey=%s CCD=%s structures=%s ligand_count=%d",
            inchi_key or "UNKNOWN",
            matches,
            structures,
            len(group),
        )
        for decision in sorted(group, key=lambda item: (item.record.structure, item.record.ligand_name)):
            logger.info(
                "Database descriptor exists for structure=%s ligand=%s CCD=%s InChIKey=%s",
                decision.record.structure,
                decision.record.ligand_name,
                matches,
                inchi_key or "UNKNOWN",
            )
    return existing_rows


def write_processing_summary(path: Path, decisions: Sequence[Decision]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["structure", "ligand_name", "deposit", "CCD"])
        for decision in decisions:
            writer.writerow(
                [
                    decision.record.structure,
                    decision.record.ligand_name,
                    decision.deposit,
                    decision.ccd,
                ]
            )
    logger.info("Wrote CCD processing summary to %s", path)


def write_processing_details(path: Path, decisions: Sequence[Decision]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "structure",
                "ligand_name",
                "inchi_key",
                "deposit",
                "CCD",
                "status",
                "representative",
                "db_matches",
                "source_path",
            ]
        )
        for decision in decisions:
            writer.writerow(
                [
                    decision.record.structure,
                    decision.record.ligand_name,
                    decision.record.inchi_key or "",
                    decision.deposit,
                    decision.ccd,
                    decision.status,
                    decision.representative,
                    ";".join(decision.db_matches),
                    str(decision.record.path),
                ]
            )
    logger.info("Wrote detailed CCD processing report to %s", path)


def is_real_ccd_id(ccd: str) -> bool:
    return bool(ccd and ccd != PENDING_CCD and ccd != "EXISTS_IN_DB")


def import_rename_function():
    try:
        from .rename_ccd import update_cif_ligand
    except ImportError:
        try:
            from rename_ccd import update_cif_ligand
        except ImportError as exc:
            raise RuntimeError(f"Could not import rename_ccd.py from {SCRIPT_DIR}") from exc
    return update_cif_ligand


def rewrite_ccd_files(
    decisions: Sequence[Decision],
    output_dir: Path,
    old_id: str,
    copy_to_structure_folders: bool,
) -> tuple[Path, Path, Path]:
    update_cif_ligand = import_rename_function()
    new_ccds_dir = output_dir / "new_ccds"
    mapped_dir = output_dir / "renamed_by_structure"
    commit_list_path = output_dir / "ccds_to_commit.txt"
    new_ccds_dir.mkdir(parents=True, exist_ok=True)
    mapped_dir.mkdir(parents=True, exist_ok=True)

    commit_ids: set[str] = set()
    for decision in decisions:
        if not is_real_ccd_id(decision.ccd):
            continue

        mapped_output = (
            mapped_dir
            / decision.record.structure
            / f"{decision.ccd}.cif"
        )
        mapped_output.parent.mkdir(parents=True, exist_ok=True)
        update_cif_ligand(
            str(decision.record.path),
            str(mapped_output),
            old_id,
            decision.ccd,
        )
        logger.info("Wrote renamed mapped CCD file %s", mapped_output)

        if copy_to_structure_folders:
            structure_output = decision.record.path.parent / f"{decision.ccd}.cif"
            if structure_output.exists():
                logger.warning(
                    "Not overwriting existing structure-level CCD output: %s",
                    structure_output,
                )
            else:
                update_cif_ligand(
                    str(decision.record.path),
                    str(structure_output),
                    old_id,
                    decision.ccd,
                )
                logger.info("Wrote renamed CCD file beside source ligand %s", structure_output)

        if decision.deposit == "Y" and decision.status == "new_representative":
            deposit_output = new_ccds_dir / f"{decision.ccd}.cif"
            update_cif_ligand(
                str(decision.record.path),
                str(deposit_output),
                old_id,
                decision.ccd,
            )
            commit_ids.add(decision.ccd)
            logger.info("Wrote deposit-ready CCD file %s", deposit_output)

    with commit_list_path.open("w", encoding="utf-8") as list_file:
        for ccd in sorted(commit_ids):
            list_file.write(f"{ccd}\n")
    logger.info("Wrote %d CCD ID(s) for commit to %s", len(commit_ids), commit_list_path)
    return new_ccds_dir, mapped_dir, commit_list_path


def collect_commit_ids(folder: Path) -> list[str]:
    ids = []
    for cif_path in sorted(folder.rglob("*.cif")):
        ccd_id = normalize_ccd_id(cif_path.stem)
        if len(ccd_id) in {3, 5} and ccd_id.isalnum():
            ids.append(ccd_id)
    return sorted(set(ids))


def confirm_cvs_commit(folder: Path, commit_ids: Sequence[str]) -> None:
    if not sys.stdin.isatty():
        raise RuntimeError("CVS commit approval requires an interactive terminal")
    print()
    print("CVS commit is almost non-reversible.")
    print(f"Folder: {folder}")
    print(f"CCD IDs: {', '.join(commit_ids) if commit_ids else '(none)'}")
    response = input("Type COMMIT_CCDS to run cvs commit, or anything else to cancel: ")
    if response != "COMMIT_CCDS":
        raise RuntimeError("CVS commit cancelled by operator")


def run_cvs_commit(
    folder: Path,
    commit_message: str,
    mode: str,
    cvs_path: str | None,
) -> None:
    if mode == "never":
        logger.info("CVS commit disabled by --commit-mode never")
        return
    if not folder.is_dir():
        raise FileNotFoundError(f"Commit folder does not exist: {folder}")
    if not commit_message:
        raise RuntimeError("--commit-message is required when --commit-mode is not never")

    commit_ids = collect_commit_ids(folder)
    logger.info("Commit folder %s contains %d CCD ID(s)", folder, len(commit_ids))
    if mode == "prompt":
        confirm_cvs_commit(folder, commit_ids)
    elif mode != "dry-run":
        raise RuntimeError(f"Unsupported commit mode: {mode}")

    command = [
        sys.executable,
        str(COMMIT_SCRIPT),
        "--ccd_folder",
        str(folder),
        "--commit_message",
        commit_message,
    ]
    if cvs_path:
        command.extend(["--cvs_path", cvs_path])
    if mode == "dry-run":
        command.append("--dry-run")

    logger.info("Running CVS helper: %s", shlex.join(command))
    subprocess.run(command, check=True)


def write_result_json(
    path: Path,
    *,
    decisions: Sequence[Decision],
    duplicate_groups: int,
    existing_rows: int,
    new_ccds_dir: Path,
    mapped_dir: Path,
    summary_csv: Path,
    details_csv: Path,
    commit_list: Path,
) -> None:
    pending_rows = sum(1 for decision in decisions if decision.ccd == PENDING_CCD)
    deposit_rows = sum(1 for decision in decisions if decision.deposit == "Y")
    result = {
        "ligands": len(decisions),
        "duplicate_groups": duplicate_groups,
        "existing_database_rows": existing_rows,
        "deposit_rows": deposit_rows,
        "pending_allocation_rows": pending_rows,
        "summary_csv": str(summary_csv),
        "details_csv": str(details_csv),
        "new_ccds_dir": str(new_ccds_dir),
        "renamed_by_structure_dir": str(mapped_dir),
        "commit_list": str(commit_list),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("Wrote CCD processing result JSON to %s", path)


def configure_logging(output_dir: Path, level: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "ccd_processing.log"
    logger.setLevel(level)
    logger.handlers = []

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    console_handler.setLevel(level)
    logger.addHandler(console_handler)
    return log_path


def run_processing(args: argparse.Namespace) -> int:
    input_root = Path(args.input_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    log_path = configure_logging(output_dir, logging.DEBUG if args.debug else logging.INFO)

    logger.info("CCD finalization log: %s", log_path)
    logger.info("Input CCD root: %s", input_root)
    logger.info("Output directory: %s", output_dir)

    records = discover_ligands(input_root)
    groups = grouped_by_inchikey(records)
    duplicate_groups = log_duplicate_groups(groups)
    existing = load_existing_descriptors(args)
    if args.write_db_cache:
        write_descriptor_cache(Path(args.write_db_cache).expanduser().resolve(), existing)

    code_map_path = Path(args.code_map_file).expanduser().resolve() if args.code_map_file else None
    code_map = load_code_map(code_map_path)
    new_inchi_keys = sorted(key for key in groups if key not in existing)
    missing_code_keys = [key for key in new_inchi_keys if key not in code_map]

    if missing_code_keys and args.allocate_codes:
        code_map.update(
            allocate_codes(
                missing_code_keys,
                output_dir,
                verbose=args.verbose_allocation,
            )
        )
    elif missing_code_keys:
        logger.warning(
            "%d new CCD InChIKey group(s) need codes. Code allocation is disabled; "
            "summary rows will use %s.",
            len(missing_code_keys),
            PENDING_CCD,
        )

    decisions = build_decisions(records, existing, code_map)
    existing_rows = log_database_matches(decisions)

    summary_csv = output_dir / "ccd_processing_summary.csv"
    details_csv = output_dir / "ccd_processing_details.csv"
    write_processing_summary(summary_csv, decisions)
    write_processing_details(details_csv, decisions)

    new_ccds_dir, mapped_dir, commit_list = rewrite_ccd_files(
        decisions,
        output_dir,
        old_id=args.old_id,
        copy_to_structure_folders=args.copy_to_structure_folders,
    )

    result_json = output_dir / "ccd_processing_result.json"
    write_result_json(
        result_json,
        decisions=decisions,
        duplicate_groups=duplicate_groups,
        existing_rows=existing_rows,
        new_ccds_dir=new_ccds_dir,
        mapped_dir=mapped_dir,
        summary_csv=summary_csv,
        details_csv=details_csv,
        commit_list=commit_list,
    )

    run_cvs_commit(
        folder=Path(args.commit_folder).expanduser().resolve() if args.commit_folder else new_ccds_dir,
        commit_message=args.commit_message,
        mode=args.commit_mode,
        cvs_path=args.cvs_path,
    )

    logger.info("CCD finalization complete")
    return 0


def run_commit_only(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    log_path = configure_logging(output_dir, logging.DEBUG if args.debug else logging.INFO)
    logger.info("CCD finalization commit-only log: %s", log_path)
    if not args.commit_folder:
        raise RuntimeError("--commit-folder is required with --commit-only")
    run_cvs_commit(
        folder=Path(args.commit_folder).expanduser().resolve(),
        commit_message=args.commit_message,
        mode=args.commit_mode,
        cvs_path=args.cvs_path,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Finalize annotated CCD CIFs and optionally invoke CVS commit.",
    )
    parser.add_argument(
        "--input-root",
        help="Root containing structure folders with annotated CCD CIF files.",
    )
    parser.add_argument(
        "--output-dir",
        default="ccd_processing_result",
        help="Directory for summaries, logs, renamed CCD files, and commit list.",
    )
    parser.add_argument(
        "--existing-descriptors-file",
        help=(
            "Optional CSV/TSV/text cache of existing descriptors. Use columns "
            "comp_id,descriptor when available. If omitted, the production DB is queried."
        ),
    )
    parser.add_argument(
        "--write-db-cache",
        help="Optional path to write the queried descriptor cache as comp_id,descriptor CSV.",
    )
    parser.add_argument(
        "--code-map-file",
        help=(
            "Optional CSV with inchi_key/descriptor and CCD columns. Useful for "
            "resuming without allocating more CCD IDs."
        ),
    )
    parser.add_argument(
        "--allocate-codes",
        action="store_true",
        help="Consume CCD IDs from the unused-code pool for new InChIKey groups.",
    )
    parser.add_argument(
        "--verbose-allocation",
        action="store_true",
        help="Pass verbose logging through to get_new_ccd_code.py.",
    )
    parser.add_argument(
        "--old-id",
        default="LIG",
        help="Component ID to replace in annotated ligand CIFs.",
    )
    parser.add_argument(
        "--copy-to-structure-folders",
        action="store_true",
        default=DEFAULT_COPY_TO_STRUCTURE_FOLDERS,
        help="Also write renamed CCD files beside each source ligand CIF.",
    )
    parser.add_argument(
        "--commit-mode",
        choices=("never", "dry-run", "prompt"),
        default="never",
        help="CVS commit behavior. prompt requires typing COMMIT_CCDS.",
    )
    parser.add_argument(
        "--commit-folder",
        help="Folder of CCD CIF files to pass to commit_ccds_to_cvs.py.",
    )
    parser.add_argument(
        "--commit-message",
        default="",
        help="CVS commit message. Required when --commit-mode is not never.",
    )
    parser.add_argument(
        "--cvs-path",
        help="Optional CVS repository root passed through to commit_ccds_to_cvs.py.",
    )
    parser.add_argument(
        "--commit-only",
        action="store_true",
        help="Only run the CVS helper against --commit-folder; skip scanning and DB checks.",
    )
    parser.add_argument("--db-host", default=settings.ccd_db_host)
    parser.add_argument("--db-port", type=int, default=settings.ccd_db_port)
    parser.add_argument("--db-user", default=settings.ccd_db_user)
    parser.add_argument("--db-password", default=settings.ccd_db_password.get_secret_value())
    parser.add_argument("--db-timeout", type=int, default=settings.ccd_db_timeout)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.commit_only:
            return run_commit_only(args)
        if not args.input_root:
            parser.error("--input-root is required unless --commit-only is used")
        return run_processing(args)
    except Exception as exc:
        logger.exception("CCD finalization failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
