#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


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


def deposit_structure(
    structure_dir: Path,
    *,
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
    structure_dir = Path(structure_dir).resolve()
    structure_id = structure_dir.name
    coord_file = first_matching_file(structure_dir, coord_patterns)
    sf_file = first_matching_file(structure_dir, sf_patterns)

    if coord_file is None:
        return DepositionResult(structure_id=structure_id, status="failed", error="No coordinate CIF found.")
    if sf_file is None:
        return DepositionResult(structure_id=structure_id, status="failed", error="No structure factor file found.")

    if dry_run:
        return DepositionResult(
            structure_id=structure_id,
            status="done",
            coord_file=str(coord_file),
            sf_file=str(sf_file),
            reports={"dry_run": True},
        )

    email = email or os.environ.get("WWPDB_EMAIL", "")
    user_ids = parse_users(users)
    if not email:
        return DepositionResult(structure_id=structure_id, status="failed", error="WWPDB email is required.")
    if not user_ids:
        return DepositionResult(structure_id=structure_id, status="failed", error="At least one WWPDB user id is required.")

    try:
        import onedep_lib as dsp
        from onedep_lib import check_auth_key
        from onedep_lib.config import DepositConfig
    except ImportError as exc:
        return DepositionResult(
            structure_id=structure_id,
            status="failed",
            error=f"onedep-lib is required for deposition: {exc}",
        )

    try:
        config = DepositConfig.load()
        country_value = getattr(dsp.Country, country.upper())
        experiment_value = getattr(dsp.ExperimentType, experiment_type.upper())

        dep = dsp.deposit_init(
            email=email,
            users=user_ids,
            country=country_value,
            config=config,
        )
        dep.set_experiment_type(experiment_value)
        auth_ok = bool(check_auth_key(config=config))

        coord_id = dep.add_file(str(coord_file), dsp.FileType.MMCIF_COORD)
        sf_id = dep.add_file(str(sf_file), dsp.FileType.CRYSTAL_STRUC_FACTORS)

        required_report = dep.check_required_files()
        reports = {
            "auth_key_ok": auth_ok,
            "required_files": report_to_dict(required_report),
            "coord_mmcif": report_to_dict(dep.check_mmcif_file(coord_id)),
            "coord_atom_site": report_to_dict(dep.check_mmcif_category(coord_id, "_atom_site")),
            "coord_file_type": report_to_dict(dep.check_file_type(coord_id, dsp.FileType.MMCIF_COORD)),
            "sf_mmcif": report_to_dict(dep.check_mmcif_file(sf_id)),
            "sf_file_type": report_to_dict(dep.check_file_type(sf_id, dsp.FileType.CRYSTAL_STRUC_FACTORS)),
        }

        if not auth_ok:
            return DepositionResult(
                structure_id=structure_id,
                status="failed",
                coord_file=str(coord_file),
                sf_file=str(sf_file),
                file_ids={"coord": coord_id, "sf": sf_id},
                reports=reports,
                error="Auth key check failed.",
            )
        if not required_report.ok:
            return DepositionResult(
                structure_id=structure_id,
                status="failed",
                coord_file=str(coord_file),
                sf_file=str(sf_file),
                file_ids={"coord": coord_id, "sf": sf_id},
                reports=reports,
                error="Required files check failed.",
            )

        dep_id = dep.deposit()
        final_status: dict[str, Any] | None = None
        status = "done"
        error = ""
        if poll:
            for _ in range(max_polls):
                current = dep.get_status()
                final_status = status_to_dict(current)
                current_status = str(final_status.get("status", "")).lower()
                if current_status == "error":
                    status = "failed"
                    error = final_status.get("details", "Deposition processing failed.")
                    break
                if current_status == "finished":
                    break
                time.sleep(poll_interval)

        return DepositionResult(
            structure_id=structure_id,
            status=status,
            deposition_id=str(dep_id or ""),
            coord_file=str(coord_file),
            sf_file=str(sf_file),
            file_ids={"coord": coord_id, "sf": sf_id},
            reports=reports,
            final_status=final_status,
            error=error,
        )
    except Exception as exc:
        LOGGER.exception("Deposition failed for %s", structure_id)
        return DepositionResult(
            structure_id=structure_id,
            status="failed",
            coord_file=str(coord_file),
            sf_file=str(sf_file),
            error=f"{type(exc).__name__}: {exc}",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deposit one structure directory through OneDep.")
    parser.add_argument("structure_dir", help="Directory containing coordinate and structure factor files.")
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
