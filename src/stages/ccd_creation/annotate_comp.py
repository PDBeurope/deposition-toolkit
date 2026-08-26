from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_ANNOTATE = "/nfs/public/services/onedep_gpfs/resources/tools/tools-centos-7.9/packages/cc-tools-v2/bin/annotateComp"
TOOLS_ROOT = "/nfs/public/services/onedep_gpfs/resources/tools/tools-centos-7.9"
ROUND_1_OPS = "stereo-cactvs|aro-cactvs|descriptor-oe|xyz-ideal-corina|xyz-model-h-oe|fix"
ROUND_2_OPS = "descriptor-cactvs|name-oe|descriptor-inchi|rename"
LOGGER = logging.getLogger("ccd_annotate")


@dataclass(frozen=True)
class AnnotationResult:
    structure_id: str
    input_file: Path
    output_file: Path
    round1_log: Path
    round2_log: Path
    status: str
    returncodes: tuple[int, ...]
    error: str = ""


def default_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env.update(
        {
            "CC_TOOLS": f"{TOOLS_ROOT}/packages/cc-tools-v2/bin",
            "OE_DIR": f"{TOOLS_ROOT}/packages/openeye",
            "OE_LICENSE": f"{TOOLS_ROOT}/packages/openeye/etc/oe_license.txt",
            "CACTVS_DIR": f"{TOOLS_ROOT}/packages/cactvs/bin",
            "CORINA_DIR": f"{TOOLS_ROOT}/packages/corina/bin",
            "BABEL_DIR": f"{TOOLS_ROOT}/packages/openbabel-2.2.3/lib",
            "BABEL_DATADIR": f"{TOOLS_ROOT}/packages/openbabel-2.2.3/share/openbabel/2.2.3",
            "INCHI_DIR": f"{TOOLS_ROOT}/bin",
            "BABEL_LIB": f"{TOOLS_ROOT}/packages/openbabel-2.2.3/lib",
            "LOCAL_APPS_LIB": f"{TOOLS_ROOT}/lib",
        }
    )
    existing_ld_path = env.get("LD_LIBRARY_PATH", "")
    ld_parts = [
        f"{TOOLS_ROOT}/packages/openbabel-2.2.3/lib",
        f"{TOOLS_ROOT}/lib",
    ]
    if existing_ld_path:
        ld_parts.append(existing_ld_path)
    env["LD_LIBRARY_PATH"] = ":".join(ld_parts)
    return env


def write_process_log(path: Path, command: Sequence[str], completed: subprocess.CompletedProcess[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as log_file:
        log_file.write("$ " + " ".join(command) + "\n")
        log_file.write(f"returncode: {completed.returncode}\n\n")
        log_file.write("stdout:\n")
        log_file.write(completed.stdout or "")
        log_file.write("\n\nstderr:\n")
        log_file.write(completed.stderr or "")
        log_file.write("\n")


def run_annotate_command(
    annotate_bin: Path,
    input_file: Path,
    output_file: Path,
    operations: str,
    log_path: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    command = [
        str(annotate_bin),
        "-vv",
        "-i",
        str(input_file),
        "-o",
        str(output_file),
        "-op",
        operations,
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    write_process_log(log_path, command, completed)
    return completed


def annotate_file(
    input_file: Path,
    output_file: Path,
    *,
    annotate_bin: Path = Path(DEFAULT_ANNOTATE),
    log_dir: Path,
    env: dict[str, str] | None = None,
) -> AnnotationResult:
    input_file = input_file.resolve()
    output_file = output_file.resolve()
    annotate_bin = annotate_bin.expanduser()
    structure_id = input_file.parent.name
    output_file.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if not annotate_bin.is_file():
        located = shutil.which(str(annotate_bin))
        if located:
            annotate_bin = Path(located)
        else:
            error = f"annotateComp binary not found: {annotate_bin}"
            return AnnotationResult(
                structure_id=structure_id,
                input_file=input_file,
                output_file=output_file,
                round1_log=log_dir / f"{input_file.stem}.round1.log",
                round2_log=log_dir / f"{input_file.stem}.round2.log",
                status="failed",
                returncodes=(),
                error=error,
            )

    run_env = default_environment(env)
    round1_log = log_dir / f"{input_file.stem}.round1.log"
    round2_log = log_dir / f"{input_file.stem}.round2.log"

    with tempfile.TemporaryDirectory(prefix="annotateComp-") as tmp_name:
        temp_cif = Path(tmp_name) / f"{input_file.stem}.cif"
        first = run_annotate_command(
            annotate_bin,
            input_file,
            temp_cif,
            ROUND_1_OPS,
            round1_log,
            run_env,
        )
        if first.returncode != 0:
            return AnnotationResult(
                structure_id=structure_id,
                input_file=input_file,
                output_file=output_file,
                round1_log=round1_log,
                round2_log=round2_log,
                status="failed",
                returncodes=(first.returncode,),
                error=f"round 1 failed with return code {first.returncode}",
            )

        second = run_annotate_command(
            annotate_bin,
            temp_cif,
            output_file,
            ROUND_2_OPS,
            round2_log,
            run_env,
        )
        for polluted in Path.cwd().glob("__LIG__CCEX__TMP*"):
            try:
                polluted.unlink()
            except OSError:
                LOGGER.warning("Could not remove annotateComp temporary file %s", polluted)
        if second.returncode != 0:
            return AnnotationResult(
                structure_id=structure_id,
                input_file=input_file,
                output_file=output_file,
                round1_log=round1_log,
                round2_log=round2_log,
                status="failed",
                returncodes=(first.returncode, second.returncode),
                error=f"round 2 failed with return code {second.returncode}",
            )

    return AnnotationResult(
        structure_id=structure_id,
        input_file=input_file,
        output_file=output_file,
        round1_log=round1_log,
        round2_log=round2_log,
        status="done",
        returncodes=(0, 0),
    )


def annotate_tree(
    input_root: Path,
    output_root: Path,
    *,
    annotate_bin: Path = Path(DEFAULT_ANNOTATE),
    log_root: Path,
) -> list[AnnotationResult]:
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    results: list[AnnotationResult] = []
    for input_file in sorted(input_root.glob("*/*.cif")):
        rel = input_file.relative_to(input_root)
        output_file = output_root / rel
        log_dir = log_root / rel.parent
        results.append(
            annotate_file(
                input_file,
                output_file,
                annotate_bin=annotate_bin,
                log_dir=log_dir,
            )
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run annotateComp over CCD CIF files.")
    parser.add_argument("input_root", help="Root containing structure folders of stage-1 CCD CIFs.")
    parser.add_argument("output_root", help="Root for annotated stage-2 CCD CIFs.")
    parser.add_argument("--log-root", required=True, help="Root for annotateComp stdout/stderr logs.")
    parser.add_argument("--annotate-bin", default=DEFAULT_ANNOTATE, help="Path to annotateComp.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
    args = build_parser().parse_args(argv)
    results = annotate_tree(
        Path(args.input_root),
        Path(args.output_root),
        annotate_bin=Path(args.annotate_bin),
        log_root=Path(args.log_root),
    )
    failed = [result for result in results if result.status != "done"]
    LOGGER.info("Annotated %d file(s), failed %d file(s)", len(results), len(failed))
    for result in failed:
        LOGGER.error("%s: %s", result.input_file, result.error)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
