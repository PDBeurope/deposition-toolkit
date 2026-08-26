#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from stages.common.manifest import (
    discover_structure_dirs,
    ensure_manifest,
    find_matching_files,
    relative_to_work_dir,
    utc_now,
)


DEFAULT_WORKROOT = "/nfs/public/services/onedep_gpfs/ccd_creation"
LOGGER = logging.getLogger("prepare_dataset")


@dataclass(frozen=True)
class PrepareDatasetResult:
    tarball: Path
    work_dir: Path
    copied_tarball: Path | None
    structure_dirs: tuple[Path, ...]


def dataset_name_from_tarball(tarball: Path) -> str:
    name = tarball.name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return tarball.stem


def default_work_dir(tarball: Path, work_root: Path) -> Path:
    return work_root / "work" / dataset_name_from_tarball(tarball)


def _validate_tar_member(destination: Path, member: tarfile.TarInfo) -> None:
    target = (destination / member.name).resolve()
    destination = destination.resolve()
    if target != destination and destination not in target.parents:
        raise RuntimeError(f"Refusing to extract unsafe tar member: {member.name}")


def extract_tarball(tarball: Path, destination: Path) -> None:
    with tarfile.open(tarball, "r:*") as archive:
        for member in archive.getmembers():
            _validate_tar_member(destination, member)
        archive.extractall(destination)


def initialise_structure_manifests(
    work_dir: Path,
    tarball: Path,
    structure_dirs: Sequence[Path],
) -> None:
    timestamp = utc_now()
    for structure_dir in structure_dirs:
        outputs = [
            relative_to_work_dir(path, work_dir)
            for path in find_matching_files(
                structure_dir,
                ("*_struc.cif", "*_lig.cif", "*_sf.cif", "*_sf.ent", "*.mmcif"),
            )
        ]
        ensure_manifest(
            structure_dir,
            initial_step={
                "status": "done",
                "timestamp": timestamp,
                "source_tarball": str(tarball),
                "work_dir": str(work_dir),
                "outputs": outputs,
            },
        )


def prepare_dataset(
    tarball: Path,
    *,
    work_root: Path | None = None,
    work_dir: Path | None = None,
    copy_tarball: bool = True,
    extract: bool = True,
) -> PrepareDatasetResult:
    tarball = tarball.expanduser().resolve()
    if not tarball.is_file():
        raise FileNotFoundError(f"Tarball not found: {tarball}")

    root = Path(
        work_root
        or os.environ.get("DEPOSITION_TOOLKIT_WORKROOT")
        or DEFAULT_WORKROOT
    ).expanduser()
    destination = (work_dir.expanduser() if work_dir else default_work_dir(tarball, root)).resolve()
    log_dir = destination / "results" / "logs"
    destination.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    copied_tarball: Path | None = None
    extract_source = tarball
    if copy_tarball:
        copied_tarball = destination / tarball.name
        if copied_tarball.resolve() != tarball:
            LOGGER.info("Copying %s to %s", tarball, copied_tarball)
            shutil.copy2(tarball, copied_tarball)
        extract_source = copied_tarball

    if extract:
        LOGGER.info("Extracting %s into %s", extract_source, destination)
        extract_tarball(extract_source, destination)

    structure_dirs = tuple(discover_structure_dirs(destination))
    initialise_structure_manifests(destination, tarball, structure_dirs)
    LOGGER.info("Prepared %s structure directorie(s) in %s", len(structure_dirs), destination)
    return PrepareDatasetResult(
        tarball=tarball,
        work_dir=destination,
        copied_tarball=copied_tarball,
        structure_dirs=structure_dirs,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy and extract a deposition tarball.")
    parser.add_argument("tarball", help="Input .tar/.tgz/.tar.gz dataset.")
    parser.add_argument(
        "--work-root",
        default=os.environ.get("DEPOSITION_TOOLKIT_WORKROOT", DEFAULT_WORKROOT),
        help="Root containing the work/ directory.",
    )
    parser.add_argument("--work-dir", help="Explicit destination work directory.")
    parser.add_argument("--no-copy", action="store_true", help="Extract from the original tarball path.")
    parser.add_argument("--no-extract", action="store_true", help="Only create/copy into the work directory.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
    args = build_parser().parse_args(argv)
    try:
        result = prepare_dataset(
            Path(args.tarball),
            work_root=Path(args.work_root) if args.work_root else None,
            work_dir=Path(args.work_dir) if args.work_dir else None,
            copy_tarball=not args.no_copy,
            extract=not args.no_extract,
        )
    except Exception as exc:
        LOGGER.error("%s", exc)
        return 1
    print(result.work_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
