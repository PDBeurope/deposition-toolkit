from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from pathlib import Path

from deposition_toolkit.config import get_settings
from deposition_toolkit.stages.common.manifest import load_manifest, update_step
from deposition_toolkit.stages.deposit.deposition import dataset_from_manifest
from deposition_toolkit.stages.validation_report import val_report

from .shared import (
    STEP_DEPOSITION,
    STEP_VALIDATION_REPORT,
    print_run_summary,
    relative_outputs,
    require_structures,
    results_path,
    structure_log,
)


LOGGER = logging.getLogger("deposition_toolkit")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validation-report", help="Create validation dashboards and collect OneDep PDFs after deposition."
    )
    parser.add_argument("work_dir", help="Toolkit work directory containing deposited structure manifests.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    structures = require_structures(work_dir, STEP_VALIDATION_REPORT)
    if not structures:
        return 1

    groups = defaultdict(list)
    for structure_dir in structures:
        manifest = load_manifest(structure_dir)
        deposition = manifest["steps"].get(STEP_DEPOSITION, {})
        if deposition.get("status") != "done" or deposition.get("reports", {}).get("dry_run"):
            with structure_log(work_dir, structure_dir, STEP_VALIDATION_REPORT):
                update_step(structure_dir, STEP_VALIDATION_REPORT, "not applicable",
                            extra={"reason": "No successful deposition recorded."})
            continue
        dataset = dataset_from_manifest(manifest) or work_dir.name
        groups[dataset].append((structure_dir, manifest.get("deposition_id") or deposition.get("deposition_id")))

    # Keep distinct datasets distinct even when their filesystem-safe names collide.
    used_names = set()
    base_path = get_settings().deposition_root.expanduser()
    for dataset, entries in sorted(groups.items()):
        stem = re.sub(r"[^\w.-]+", "_", dataset).strip(".") or "dataset"
        candidate, suffix = stem, 2
        while candidate.casefold() in used_names:
            candidate = f"{stem}_{suffix}"
            suffix += 1
        stem = candidate
        used_names.add(stem.casefold())
        output_dir = results_path(work_dir, STEP_VALIDATION_REPORT)
        dashboard = output_dir / f"{stem}_validation_dashboard.html"
        metadata = output_dir / f"{stem}_validation_metadata.csv"
        pdf_zip = output_dir / f"{stem}_validation_pdfs.zip"
        reports, outcomes = {}, {}
        for structure_dir, deposition_id in entries:
            with structure_log(work_dir, structure_dir, STEP_VALIDATION_REPORT):
                if not deposition_id:
                    raise ValueError("Successful deposition has no deposition ID in its manifest.")
                if deposition_id not in reports:
                    reports[deposition_id], outcomes[deposition_id] = val_report.process_deposition(deposition_id, base_path)
                problems = outcomes[deposition_id]
                update_step(structure_dir, STEP_VALIDATION_REPORT, "failed" if problems else "done",
                            error="; ".join(problems) or None,
                            extra={"dataset": dataset, "deposition_id": deposition_id,
                                   "warnings": reports[deposition_id].get("warnings", [])})
        missing_pdb_ids = sum(not report.get("pdb_id") for report in reports.values())
        if missing_pdb_ids:
            print(f"WARNING: PDB IDs missing for {missing_pdb_ids} deposition(s) in dataset {dataset}.")
        try:
            dashboard.write_text(val_report.build_html(list(reports.values())), encoding="utf-8")
            val_report.write_metadata_file(metadata, list(reports.values()))
            pdf_problems = val_report.collect_validation_pdfs(list(reports), base_path, pdf_zip)
        except OSError as exc:
            LOGGER.error("Could not write validation reports for %s: %s", dataset, exc)
            for structure_dir, _ in entries:
                step = load_manifest(structure_dir)["steps"][STEP_VALIDATION_REPORT]
                update_step(structure_dir, STEP_VALIDATION_REPORT, "failed",
                            error=f"Could not write validation outputs: {exc}",
                            extra={"log": step["log"]})
                with (work_dir / step["log"]).open("a", encoding="utf-8") as log:
                    log.write(f"Validation output generation failed: {exc}\n")
            continue
        for structure_dir, deposition_id in entries:
            step = load_manifest(structure_dir)["steps"][STEP_VALIDATION_REPORT]
            problems = list(outcomes.get(deposition_id, []))
            if deposition_id in pdf_problems:
                problems.append("Full validation PDF is missing or unreadable.")
            if step["status"] == "failed" and step.get("error") and not problems:
                problems.append(step["error"])
            update_step(structure_dir, STEP_VALIDATION_REPORT, "failed" if problems else "done",
                        outputs=relative_outputs([dashboard, metadata, pdf_zip], work_dir),
                        error="; ".join(problems) or None,
                        extra={"dataset": dataset, "deposition_id": deposition_id, "log": step["log"],
                               "warnings": step.get("warnings", [])})
            with (work_dir / step["log"]).open("a", encoding="utf-8") as log:
                log.write("Final validation report result: " + ("; ".join(problems) if problems else "done") + "\n")
        for output in (dashboard, metadata, pdf_zip):
            print(output.relative_to(work_dir))

    failed = print_run_summary(work_dir, STEP_VALIDATION_REPORT, structures)
    if failed:
        print("OneDep reports may still be generating. Check the recorded errors and rerun validation-report later.")
    return 1 if failed else 0
