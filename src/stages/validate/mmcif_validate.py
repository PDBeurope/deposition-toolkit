#!/usr/bin/env python3

import argparse
import glob
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from validate_mmcif import validate


logger = logging.getLogger("mmcif_validate")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate mmCIF files with validate_mmcif and write one consolidated "
            "log file for each input glob or path."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help=(
            "One or more mmCIF files, directories, or glob patterns. Quote glob "
            "patterns so this script can create one log per pattern."
        ),
    )
    parser.add_argument(
        "-d",
        "--dictionary",
        default="./mmcif_pdbx_v50.dic",
        help="Path to the mmCIF dictionary file. Defaults to ./mmcif_pdbx_v50.dic.",
    )
    parser.add_argument(
        "-l",
        "--log-dir",
        default=".",
        help="Directory where validation logs will be written. Defaults to the current directory.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Enable recursive glob matching, for example with **/*.cif.",
    )
    return parser.parse_args()


def expand_input(input_path: str, recursive: bool = False) -> list[Path]:
    """Expand a file, directory, or glob pattern into a sorted list of CIF files."""
    path = Path(input_path)

    if path.is_dir():
        return sorted(p for p in path.rglob("*.cif") if p.is_file())

    if has_glob_magic(input_path):
        return sorted(
            Path(match)
            for match in glob.glob(input_path, recursive=recursive)
            if Path(match).is_file()
        )

    return [path] if path.is_file() else []


def has_glob_magic(input_path: str) -> bool:
    return any(char in input_path for char in "*?[")


def log_path_for_input(input_path: str, log_dir: Path) -> Path:
    """Create stable, readable log names such as struc_validation.log or sf_validation.log."""
    name = Path(input_path).name
    stem = Path(name).stem or "mmcif"
    label = re.sub(r"[\*\?\[\]]+", "", stem).strip("._-") or "mmcif"
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    return log_dir / f"{label}_validation.log"


def format_error(error: object) -> str:
    line = getattr(error, "line", "")
    item = getattr(error, "item", "")
    message = getattr(error, "message", "")
    severity = getattr(error, "severity", "")
    column = getattr(error, "column", None)

    location = f"line={line}"
    if column is not None:
        location = f"{location} column={column}"

    return f"{severity.upper()}: {location} item={item} message={message}"


def validate_file(dictionary_path: Path, cif_path: Path) -> tuple[list[object], Exception | None]:
    try:
        return list(validate(dictionary_path, cif_path)), None
    except Exception as exc:
        return [], exc


def count_by_severity(errors: Iterable[object]) -> tuple[int, int]:
    error_count = 0
    warning_count = 0

    for error in errors:
        severity = getattr(error, "severity", "")
        if severity == "error":
            error_count += 1
        elif severity == "warning":
            warning_count += 1

    return error_count, warning_count


def write_group_log(
    dictionary_path: Path,
    input_path: str,
    files: Sequence[Path],
    log_path: Path,
) -> tuple[int, int, int]:
    failed_files = 0
    total_errors = 0
    total_warnings = 0

    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write("mmCIF validation log\n")
        log_file.write(f"created: {datetime.now().isoformat(timespec='seconds')}\n")
        log_file.write(f"dictionary: {dictionary_path}\n")
        log_file.write(f"input: {input_path}\n")
        log_file.write(f"matched_files: {len(files)}\n\n")

        if not files:
            log_file.write("No matching files found.\n")
            return 1, 0, 0

        for cif_path in files:
            log_file.write("=" * 80 + "\n")
            log_file.write(f"file: {cif_path}\n")

            errors, exception = validate_file(dictionary_path, cif_path)
            if exception is not None:
                failed_files += 1
                total_errors += 1
                log_file.write("status: VALIDATOR_ERROR\n")
                log_file.write(f"exception: {type(exception).__name__}: {exception}\n\n")
                continue

            file_errors, file_warnings = count_by_severity(errors)
            total_errors += file_errors
            total_warnings += file_warnings

            if file_errors:
                failed_files += 1
                status = "FAILED"
            elif file_warnings:
                status = "PASSED_WITH_WARNINGS"
            else:
                status = "PASSED"

            log_file.write(
                f"status: {status} errors: {file_errors} warnings: {file_warnings}\n"
            )

            if errors:
                for error in errors:
                    log_file.write(f"{format_error(error)}\n")
            else:
                log_file.write("No validation errors or warnings.\n")

            log_file.write("\n")

        log_file.write("=" * 80 + "\n")
        log_file.write(
            "summary: "
            f"files={len(files)} failed_files={failed_files} "
            f"errors={total_errors} warnings={total_warnings}\n"
        )

    return failed_files, total_errors, total_warnings


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = parse_args()
    dictionary_path = Path(args.dictionary)
    log_dir = Path(args.log_dir)

    if not dictionary_path.is_file():
        logger.error("Dictionary file not found: %s", dictionary_path)
        return 2

    log_dir.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    for input_path in args.inputs:
        files = expand_input(input_path, recursive=args.recursive)
        log_path = log_path_for_input(input_path, log_dir)

        logger.info(
            "Validating %d file(s) from %s into %s",
            len(files),
            input_path,
            log_path,
        )
        failed_files, error_count, warning_count = write_group_log(
            dictionary_path=dictionary_path,
            input_path=input_path,
            files=files,
            log_path=log_path,
        )

        if failed_files:
            exit_code = 1

        logger.info(
            "Finished %s: failed_files=%d errors=%d warnings=%d",
            input_path,
            failed_files,
            error_count,
            warning_count,
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
