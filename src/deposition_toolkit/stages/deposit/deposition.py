#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from deposition_toolkit.stages.common.manifest import load_manifest


LOGGER = logging.getLogger("deposition")


@dataclass
class DepositionResult:
    structure_id: str
    status: str
    deposition_id: str = ""
    coord_file: str = ""
    sf_file: str = ""
    file_ids: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    final_status: dict[str, Any] | None = None
    error: str = ""
    dataset: str = ""
    session_id: str = ""

    def to_record(self) -> dict[str, str]:
        """Fields needed to find and resume this deposition in a future database."""
        return {
            "dep_id": self.deposition_id,
            "dataset": self.dataset,
            "structure_id": self.structure_id,
            "session_id": self.session_id,
        }


def parse_users(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        value = os.environ.get("WWPDB_USERS", "")
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def report_to_dict(report: Any) -> dict[str, Any]:
    issues = []
    for issue in getattr(report, "issues", []) or []:
        severity = getattr(getattr(issue, "severity", None), "value", getattr(issue, "severity", ""))
        issues.append(
            {
                "severity": severity,
                "code": getattr(issue, "code", ""),
                "message": getattr(issue, "message", ""),
            }
        )
    return {"ok": bool(getattr(report, "ok", False)), "issues": issues}


def status_to_dict(status: Any) -> dict[str, Any]:
    if status is None:
        return {}
    return {
        "status": getattr(status, "status", ""),
        "details": getattr(status, "details", getattr(status, "message", "")),
    }


def first_matching_file(structure_dir: Path, patterns: Sequence[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(structure_dir.glob(pattern), key=lambda item: item.name.lower())
        for match in matches:
            if match.is_file():
                return match
    return None


def dataset_from_manifest(manifest: dict[str, Any]) -> str:
    source = manifest.get("source_tarball") or manifest.get("steps", {}).get(
        "prepare_dataset", {}
    ).get("source_tarball", "")
    name = Path(source).name if source else ""
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar"):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return Path(name).stem


def deposit_structure(
    structure_dir: Path,
    *,
    session_id: str | None = None,
    email: str | None = None,
    users: str | Sequence[str] | None = None,
    country: str = "UK",
    experiment_type: str = "XRAY",
    coord_patterns: Sequence[str] = ("*_enriched_struc.cif", "*_struc.cif"),
    sf_patterns: Sequence[str] = ("*_sf.cif", "*_sf.mmcif", "*_sf.ent", "r*sf.ent"),
    poll: bool = True,
    poll_interval: int = 5,
    max_polls: int = 64,
    dry_run: bool = False,
) -> DepositionResult:
    """Upload a structure, or replace files in an existing saved OneDep session.

    Resuming requires the local OneDep session files in the configured session_dir.
    The saved depositor and experiment settings are retained on re-upload.
    """
    structure_dir = Path(structure_dir).resolve()
    manifest = load_manifest(structure_dir)
    result = DepositionResult(
        structure_id=manifest["structure_id"],
        dataset=dataset_from_manifest(manifest),
        session_id=session_id or "",
        status="failed",
    )
    coord_file = first_matching_file(structure_dir, coord_patterns)
    sf_file = first_matching_file(structure_dir, sf_patterns)
    if coord_file is None:
        result.error = "No coordinate CIF found."
        return result
    if sf_file is None:
        result.error = "No structure factor file found."
        return result
    result.coord_file = str(coord_file)
    result.sf_file = str(sf_file)

    if dry_run:
        result.status = "done"
        result.reports = {"dry_run": True}
        return result

    email = email or os.environ.get("WWPDB_EMAIL", "")
    user_ids = parse_users(users)
    if session_id is None:
        if not email:
            result.error = "WWPDB email is required."
            return result
        if not user_ids:
            result.error = "At least one WWPDB user id is required."
            return result

    try:
        import onedep_lib as dsp
        from onedep_lib import check_auth_key
        from onedep_lib.config import DepositConfig
    except ImportError as exc:
        result.error = f"onedep-lib is required for deposition: {exc}"
        return result

    dep = None
    try:
        config = DepositConfig.load()
        old_files = []
        if session_id is not None:
            dep = dsp.deposit_resume(session_id, config=config)
        else:
            dep = dsp.deposit_init(
                email=email,
                users=user_ids,
                country=getattr(dsp.Country, country.upper()),
                config=config,
            )
        with dep:
            result.session_id = dep.session_id
            result.deposition_id = str(dep.remote_dep_id or "")
            if session_id is not None:
                if dep.remote_dep_id is None:
                    raise ValueError("This session has no remote deposition to re-upload to.")
                _, old_files = next(
                    (session, files)
                    for session, files in dsp.list_sessions(base_dir=config.session_dir)
                    if session.session_id == dep.session_id
                )
            else:
                dep.set_experiment_type(getattr(dsp.ExperimentType, experiment_type.upper()))

            auth_ok = bool(check_auth_key(config=config))
            result.reports["auth_key_ok"] = auth_ok
            if not auth_ok:
                result.error = "Auth key check failed."
                return result

            # Keep the old registrations until every replacement has passed checks.
            new_ids = []
            try:
                coord_id = dep.add_file(str(coord_file), dsp.FileType.MMCIF_COORD)
                new_ids.append(coord_id)
                sf_id = dep.add_file(str(sf_file), dsp.FileType.CRYSTAL_STRUC_FACTORS)
                new_ids.append(sf_id)
                result.file_ids = {"coord": coord_id, "sf": sf_id}
                result.reports.update({
                    "coord_mmcif": report_to_dict(dep.check_mmcif_file(coord_id)),
                    "coord_atom_site": report_to_dict(dep.check_mmcif_category(coord_id, "_atom_site")),
                    "coord_file_type": report_to_dict(dep.check_file_type(coord_id, dsp.FileType.MMCIF_COORD)),
                    "sf_mmcif": report_to_dict(dep.check_mmcif_file(sf_id)),
                    "sf_file_type": report_to_dict(dep.check_file_type(sf_id, dsp.FileType.CRYSTAL_STRUC_FACTORS)),
                })
                if session_id is not None and not all(
                    report["ok"] for name, report in result.reports.items() if name != "auth_key_ok"
                ):
                    result.error = "Replacement file checks failed."
                    for file_id in new_ids:
                        dep.remove_file(file_id)
                    return result
            except Exception:
                if session_id is not None:
                    for file_id in new_ids:
                        dep.remove_file(file_id)
                raise

            replaced_types = {dsp.FileType.MMCIF_COORD, dsp.FileType.CRYSTAL_STRUC_FACTORS}
            for old_file in old_files:
                if old_file.file_type in replaced_types:
                    dep.remove_file(old_file.file_id)

            required_report = dep.check_required_files()
            result.reports["required_files"] = report_to_dict(required_report)
            if not required_report.ok:
                result.error = "Required files check failed."
                return result

            dep_id = dep.deposit()
            result.deposition_id = str(dep_id or dep.remote_dep_id or "")
            result.status = "done"
            if poll:
                for _ in range(max_polls):
                    current = dep.get_status()
                    result.final_status = status_to_dict(current)
                    current_status = str(result.final_status.get("status", "")).lower()
                    if isinstance(current, dsp.DepositError) or current_status == "error":
                        result.status = "failed"
                        result.error = result.final_status.get("details", "Deposition processing failed.")
                        break
                    if current_status == "finished":
                        break
                    time.sleep(poll_interval)
        return result
    except Exception as exc:
        LOGGER.exception("Deposition failed for %s", result.structure_id)
        # A remote ID may already exist even when uploading or polling raises.
        if dep is not None:
            result.deposition_id = str(dep.remote_dep_id or result.deposition_id)
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deposit one structure directory through OneDep.")
    parser.add_argument("structure_dir", help="Directory containing coordinate and structure factor files.")
    parser.add_argument("--session-id", help="Saved OneDep session UUID to re-upload to.")
    parser.add_argument("--email", default=os.environ.get("WWPDB_EMAIL", ""), help="Depositor email.")
    parser.add_argument("--users", default=os.environ.get("WWPDB_USERS", ""), help="Comma-separated WWPDB user IDs.")
    parser.add_argument("--country", default="UK", help="onedep_lib Country enum name.")
    parser.add_argument("--experiment-type", default="XRAY", help="onedep_lib ExperimentType enum name.")
    parser.add_argument("--no-poll", action="store_true", help="Return after deposit() without polling status.")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--max-polls", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true", help="Only discover files; do not call OneDep.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
    args = build_parser().parse_args(argv)
    result = deposit_structure(
        Path(args.structure_dir),
        session_id=args.session_id,
        email=args.email,
        users=args.users,
        country=args.country,
        experiment_type=args.experiment_type,
        poll=not args.no_poll,
        poll_interval=args.poll_interval,
        max_polls=args.max_polls,
        dry_run=args.dry_run,
    )
    if result.deposition_id:
        print(result.deposition_id)
    if result.error:
        LOGGER.error(result.error)
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
