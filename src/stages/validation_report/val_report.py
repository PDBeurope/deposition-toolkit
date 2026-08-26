#!/usr/bin/env python3
"""Build a self-contained PDBe validation summary dashboard from OneDep IDs.

Examples:
    python val_report.py D_1292159901 D_1292159902
    python val_report.py --id-file deposition_ids.txt -o report.html
    python val_report.py --id-file deposition_ids.txt --collect-val-pdf

For each deposition ID the program resolves the latest validation XML and model
mmCIF under DEPOSITION_ROOT, enriches the validation report with PDB accession
and entry title, and writes a self-contained HTML dashboard plus a CSV-formatted
metadata text file.  Optionally it also collects the latest full validation PDFs
into a ZIP archive.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import shlex
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


DEPOSITION_ROOT = Path("/nfs/public/services/onedep_gpfs/data/production/deposit")
PAGE_TITLE = "PDBe interactive validation report"
PAGE_HEADING = "PDBe Validation Summary"
PAGE_SUBTITLE = "Deposition Toolkit - Validation summary report"


METRICS = [
    {
        "key": "rfree",
        "label": "Rfree",
        "group": "Refinement",
        "percentile": ["relative-percentile-DCC_Rfree"],
        "raw": ["DCC_Rfree", "PDB-Rfree"],
        "raw_label": "Rfree",
        "format": "float3",
    },
    {
        "key": "rsrz",
        "label": "RSRZ",
        "group": "Model ↔ data",
        "percentile": ["relative-percentile-percent-RSRZ-outliers"],
        "raw": ["percent-RSRZ-outliers"],
        "raw_label": "% outliers",
        "format": "percent1",
    },
    {
        "key": "rotamer",
        "label": "Rotamers",
        "group": "Side chains",
        "percentile": ["relative-percentile-percent-rota-outliers"],
        "raw": ["percent-rota-outliers"],
        "raw_label": "% outliers",
        "format": "percent2",
    },
    {
        "key": "clash",
        "label": "Clashes",
        "group": "Sterics",
        "percentile": ["relative-percentile-clashscore"],
        "raw": ["clashscore"],
        "raw_label": "score",
        "format": "float2",
    },
    {
        "key": "rama",
        "label": "Rama",
        "group": "Backbone",
        "percentile": ["relative-percentile-percent-rama-outliers"],
        "raw": ["percent-rama-outliers"],
        "raw_label": "% outliers",
        "format": "percent2",
    },
]

POLYMER_RESNAMES = {
    "ALA","ARG","ASN","ASP","CYS","GLN","GLU","GLY","HIS","ILE",
    "LEU","LYS","MET","PHE","PRO","SER","THR","TRP","TYR","VAL",
    "ASX","GLX","SEC","PYL","UNK",
    "A","C","G","U","DA","DC","DG","DT","DI","DU","I",
}


def as_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def first_attr(attrs: Dict[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        if name in attrs and attrs[name] not in (None, ""):
            return attrs[name]
    return None


def format_raw(value: Optional[float], mode: str) -> str:
    if value is None:
        return "—"
    if mode == "float3":
        return f"{value:.3f}"
    if mode == "float2":
        return f"{value:.2f}"
    if mode == "percent1":
        return f"{value:.1f}%"
    if mode == "percent2":
        return f"{value:.2f}%"
    return f"{value:g}"


def percentile_band(p: Optional[float]) -> Tuple[str, int]:
    """Return human-readable band and numeric severity (higher = worse)."""
    if p is None:
        return "unknown", 0
    if p <= 1.0:
        return "critical", 4
    if p <= 5.0:
        return "poor", 3
    if p <= 20.0:
        return "review", 2
    if p < 80.0:
        return "ok", 1
    return "excellent", 0


def raw_fallback_severity(key: str, raw: Optional[float]) -> int:
    """Conservative display-only fallback when a relative percentile is absent.

    These are not intended as scientific pass/fail criteria. They only keep the
    overview useful for XML reports missing percentile fields.
    """
    if raw is None:
        return 0
    if key == "rfree":
        return 4 if raw >= 0.40 else 3 if raw >= 0.35 else 2 if raw >= 0.30 else 1
    if key == "rsrz":
        return 4 if raw >= 40 else 3 if raw >= 20 else 2 if raw >= 10 else 1
    if key == "rotamer":
        return 4 if raw >= 10 else 3 if raw >= 5 else 2 if raw >= 2 else 1
    if key == "clash":
        return 4 if raw >= 30 else 3 if raw >= 20 else 2 if raw >= 10 else 1
    if key == "rama":
        return 4 if raw >= 2 else 3 if raw >= 1 else 2 if raw > 0 else 0
    return 0


def residue_issue_score(attrs: Dict[str, str], child_tags: List[str]) -> Tuple[int, List[str]]:
    score = 0
    issues: List[str] = []

    rsrz = as_float(attrs.get("rsrz"))
    rscc = as_float(attrs.get("rscc"))
    ediam = as_float(attrs.get("EDIAm"))
    rsr = as_float(attrs.get("rsr"))
    occ = as_float(attrs.get("avgoccu"))

    if rsrz is not None:
        if rsrz >= 5:
            score = max(score, 4); issues.append(f"RSRZ {rsrz:.2f}")
        elif rsrz >= 3:
            score = max(score, 3); issues.append(f"RSRZ {rsrz:.2f}")
        elif rsrz >= 2:
            score = max(score, 2); issues.append(f"RSRZ {rsrz:.2f}")

    if rscc is not None:
        if rscc < 0.40:
            score = max(score, 4); issues.append(f"RSCC {rscc:.2f}")
        elif rscc < 0.60:
            score = max(score, 3); issues.append(f"RSCC {rscc:.2f}")
        elif rscc < 0.70:
            score = max(score, 2); issues.append(f"RSCC {rscc:.2f}")

    if ediam is not None:
        if ediam < 0.10:
            score = max(score, 4); issues.append(f"EDIA {ediam:.2f}")
        elif ediam < 0.30:
            score = max(score, 3); issues.append(f"EDIA {ediam:.2f}")

    if rsr is not None and rsr >= 0.40:
        score = max(score, 4 if rsr >= 0.60 else 3); issues.append(f"RSR {rsr:.2f}")

    if attrs.get("rota", "").upper() == "OUTLIER":
        score = max(score, 3); issues.append("rotamer outlier")

    rama = attrs.get("rama", "")
    if rama and rama.lower() not in ("favored", "allowed"):
        score = max(score, 3); issues.append(f"Rama {rama}")

    if occ is not None and occ < 0.50:
        score = max(score, 2); issues.append(f"occupancy {occ:.2f}")

    tag_counts: Dict[str, int] = {}
    for tag in child_tags:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    if tag_counts.get("clash"):
        n = tag_counts["clash"]
        score = max(score, 2 if n == 1 else 3)
        issues.append(f"{n} clash" + ("es" if n != 1 else ""))
    geom = sum(tag_counts.get(t, 0) for t in ("bond-outlier", "angle-outlier", "mogul-bond-outlier", "mogul-angle-outlier"))
    if geom:
        score = max(score, 3); issues.append(f"{geom} geometry outlier" + ("s" if geom != 1 else ""))

    return score, issues


def parse_validation_xml(path: Path, deposition_id: str, pdb_id: Optional[str] = None, entry_title: Optional[str] = None, model_file: Optional[Path] = None, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return error_report(
            deposition_id,
            f"XML parse error: {exc}",
            xml_file=path,
            model_file=model_file,
            pdb_id=pdb_id,
            entry_title=entry_title,
            warnings=warnings,
        )

    root = tree.getroot()
    entry = root.find("Entry")
    if entry is None:
        entry = root.find(".//Entry")
    attrs = dict(entry.attrib) if entry is not None else {}

    # In validation XML, Entry@pdbid is the OneDep deposition identifier.
    # Keep the explicit input deposition ID as the canonical dashboard identity.
    xml_deposition_id = attrs.get("pdbid")
    resolution = as_float(attrs.get("PDB-resolution"))

    metrics: List[Dict[str, Any]] = []
    for m in METRICS:
        p = as_float(first_attr(attrs, m["percentile"]))
        raw = as_float(first_attr(attrs, m["raw"]))
        band, sev = percentile_band(p)
        if p is None:
            sev = raw_fallback_severity(m["key"], raw)
            band = {4: "critical", 3: "poor", 2: "review", 1: "ok", 0: "unknown"}[sev]
        metrics.append({
            "key": m["key"],
            "label": m["label"],
            "group": m["group"],
            "percentile": p,
            "raw": raw,
            "raw_text": format_raw(raw, m["format"]),
            "raw_label": m["raw_label"],
            "band": band,
            "severity": sev,
        })

    worst_metric = max(metrics, key=lambda x: (x["severity"], -(x["percentile"] if x["percentile"] is not None else 101))) if metrics else None
    overall_sev = worst_metric["severity"] if worst_metric else 0
    review_priority = {4: "High", 3: "High", 2: "Low", 1: "OK", 0: "OK"}[overall_sev]
    dominant = worst_metric["group"] if worst_metric and worst_metric["severity"] >= 2 else "No dominant issue"

    residues: List[Dict[str, Any]] = []
    nonpolymer: List[Dict[str, Any]] = []
    for node in root.findall(".//ModelledSubgroup"):
        a = dict(node.attrib)
        child_tags = [child.tag.split("}")[-1] for child in list(node)]
        score, issues = residue_issue_score(a, child_tags)
        resname = a.get("resname", "?")
        seq = a.get("seq", ".")
        chain = a.get("chain", "?")
        resnum = a.get("resnum", "?")
        alt = a.get("altcode", "").strip()
        rid = f"{chain}:{resname}{resnum}{alt if alt else ''}"
        rec = {
            "id": rid,
            "chain": chain,
            "resnum": resnum,
            "resname": resname,
            "alt": alt,
            "seq": seq,
            "score": score,
            "issues": issues,
            "rsrz": as_float(a.get("rsrz")),
            "rscc": as_float(a.get("rscc")),
            "ediam": as_float(a.get("EDIAm")),
            "rsr": as_float(a.get("rsr")),
            "occupancy": as_float(a.get("avgoccu")),
            "rota": a.get("rota"),
            "rama": a.get("rama"),
            "clashes": child_tags.count("clash"),
            "geometry": sum(child_tags.count(t) for t in ("bond-outlier", "angle-outlier", "mogul-bond-outlier", "mogul-angle-outlier")),
        }
        is_polymer = seq not in (".", "") and (resname in POLYMER_RESNAMES or a.get("ent") == "1")
        if is_polymer:
            residues.append(rec)
        else:
            nonpolymer.append(rec)

    # Ranking strongly favours explicit/local severe problems, then poor density.
    top_residues = sorted(
        [r for r in residues if r["score"] > 0],
        key=lambda r: (
            r["score"],
            r["rsrz"] if r["rsrz"] is not None else -999,
            -(r["rscc"] if r["rscc"] is not None else 2),
        ),
        reverse=True,
    )[:8]
    top_nonpolymer = sorted(
        [r for r in nonpolymer if r["score"] > 0],
        key=lambda r: (r["score"], r["rsr"] if r["rsr"] is not None else -1),
        reverse=True,
    )[:8]

    chain_counts: Dict[str, int] = {}
    for r in residues:
        chain_counts[r["chain"]] = chain_counts.get(r["chain"], 0) + 1

    key_data = {
        "PDB R": as_float(attrs.get("PDB-R")),
        "PDB Rfree": as_float(attrs.get("PDB-Rfree")),
        "DCC R": as_float(attrs.get("DCC_R")),
        "DCC Rfree": as_float(attrs.get("DCC_Rfree")),
        "Completeness": as_float(attrs.get("DataCompleteness")),
        "I/σ": attrs.get("IoverSigma"),
        "Anisotropy": as_float(attrs.get("DataAnisotropy")),
        "Fo/Fc corr.": as_float(attrs.get("Fo_Fc_correlation")),
        "Bond RMSZ": as_float(attrs.get("bonds_rmsz")),
        "Angle RMSZ": as_float(attrs.get("angles_rmsz")),
    }

    return {
        "file": path.name,
        "path": str(path),
        "xml_file": str(path),
        "model_file": str(model_file) if model_file else None,
        "id": deposition_id,
        "deposition_id": deposition_id,
        "xml_deposition_id": xml_deposition_id,
        "pdb_id": pdb_id,
        "entry_title": entry_title,
        "resolution": resolution,
        "resolution_text": f"{resolution:.2f} Å" if resolution is not None else "—",
        "xml_date": attrs.get("XMLcreationDate"),
        "metrics": metrics,
        "severity": overall_sev,
        "review_priority": review_priority,
        "status": review_priority,
        "dominant": dominant,
        "residues": residues,
        "nonpolymer": nonpolymer,
        "top_residues": top_residues,
        "top_nonpolymer": top_nonpolymer,
        "chain_counts": chain_counts,
        "key_data": key_data,
        "entry_attrs": {k: v for k, v in attrs.items() if k not in {"coordinatesInputFilename", "reflectionsInputFilename"}},
        "warnings": list(warnings or []),
    }


def version_number(path: Path) -> int:
    match = re.search(r"\.V(\d+)$", path.name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else -1


def latest_matching(directory: Path, pattern: str) -> Optional[Path]:
    matches = [p for p in directory.glob(pattern) if p.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: (version_number(p), p.name))


def log_problem(deposition_id: str, message: str) -> None:
    print(f"ERROR [{deposition_id}] {message}", file=sys.stderr)


def normalise_missing(value: Optional[str]) -> Optional[str]:
    if value is None or value in {"?", ".", ""}:
        return None
    return value


def cif_tokens(path: Path) -> Iterator[str]:
    """Yield enough mmCIF tokens for metadata extraction without dependencies.

    Handles quoted values, comments, and semicolon-delimited multi-line values.
    This is deliberately a small reader, not a general mmCIF implementation.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(";"):
            block: List[str] = [line[1:]]
            i += 1
            while i < len(lines) and not lines[i].startswith(";"):
                block.append(lines[i])
                i += 1
            yield "\n".join(block).strip()
            if i < len(lines):
                i += 1
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        try:
            for token in shlex.split(line, comments=True, posix=True):
                yield token
        except ValueError:
            # A malformed line should not make the entire deposition unusable.
            for token in line.split():
                if token.startswith("#"):
                    break
                yield token
        i += 1


def parse_model_metadata(path: Path) -> Tuple[Optional[str], Optional[str]]:
    tokens = list(cif_tokens(path))
    title: Optional[str] = None
    pdb_id: Optional[str] = None

    # Scalar form, including _struct.title.
    for i, token in enumerate(tokens[:-1]):
        low = token.lower()
        if low == "_struct.title":
            title = normalise_missing(tokens[i + 1])
        elif low == "_database_2.pdbx_database_accession" and not pdb_id:
            candidate = normalise_missing(tokens[i + 1])
            if candidate and candidate.lower().startswith("pdb_"):
                pdb_id = candidate

    # Loop form used by _database_2 in deposited model files.
    i = 0
    while i < len(tokens):
        if tokens[i].lower() != "loop_":
            i += 1
            continue
        i += 1
        headers: List[str] = []
        while i < len(tokens) and tokens[i].startswith("_"):
            headers.append(tokens[i].lower())
            i += 1
        if not headers:
            continue
        row_width = len(headers)
        values: List[str] = []
        while i < len(tokens):
            t = tokens[i]
            tl = t.lower()
            if tl == "loop_" or tl == "stop_" or tl.startswith("data_") or (t.startswith("_") and len(values) % row_width == 0):
                break
            values.append(t)
            i += 1
        wanted_db = "_database_2.database_id"
        wanted_accession = "_database_2.pdbx_database_accession"
        if wanted_db in headers and wanted_accession in headers:
            db_idx = headers.index(wanted_db)
            acc_idx = headers.index(wanted_accession)
            for off in range(0, len(values) - row_width + 1, row_width):
                row = values[off:off + row_width]
                if row[db_idx].upper() == "PDB":
                    pdb_id = normalise_missing(row[acc_idx])
                    break
        if pdb_id and title:
            break
    return pdb_id, title


def error_report(
    deposition_id: str,
    message: str,
    *,
    xml_file: Optional[Path] = None,
    model_file: Optional[Path] = None,
    pdb_id: Optional[str] = None,
    entry_title: Optional[str] = None,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    all_warnings = list(warnings or [])
    if message not in all_warnings:
        all_warnings.append(message)
    return {
        "file": xml_file.name if xml_file else "—",
        "path": str(xml_file) if xml_file else None,
        "xml_file": str(xml_file) if xml_file else None,
        "model_file": str(model_file) if model_file else None,
        "id": deposition_id,
        "deposition_id": deposition_id,
        "pdb_id": pdb_id,
        "entry_title": entry_title,
        "parse_error": message,
        "metrics": [],
        "residues": [],
        "nonpolymer": [],
        "top_residues": [],
        "top_nonpolymer": [],
        "key_data": {},
        "severity": 4,
        "review_priority": "High",
        "status": "High",
        "dominant": "Input / parsing error",
        "resolution": None,
        "resolution_text": "—",
        "warnings": all_warnings,
    }


def split_deposition_ids(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        for dep_id in re.split(r"[\s,]+", value.strip()):
            if not dep_id or dep_id.startswith("#"):
                continue
            if dep_id not in seen:
                seen.add(dep_id)
                result.append(dep_id)
    return result


def load_deposition_ids(cli_ids: Iterable[str], id_file: Optional[Path]) -> List[str]:
    values = list(cli_ids)
    if id_file is not None:
        if not id_file.is_file():
            raise FileNotFoundError(f"Deposition ID file does not exist: {id_file}")
        for raw_line in id_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            values.append(line)
    return split_deposition_ids(values)


def process_deposition(deposition_id: str, base_path: Path) -> Tuple[Dict[str, Any], List[str]]:
    problems: List[str] = []
    if not re.fullmatch(r"D_\d+", deposition_id, flags=re.IGNORECASE):
        msg = "Invalid deposition ID format (expected D_<digits>)"
        log_problem(deposition_id, msg)
        return error_report(deposition_id, msg), [msg]
    dep_dir = base_path / deposition_id
    if not dep_dir.is_dir():
        msg = f"Deposition directory not found: {dep_dir}"
        log_problem(deposition_id, msg)
        return error_report(deposition_id, msg), [msg]

    xml_file = latest_matching(dep_dir, f"{deposition_id}_val-data_P1.xml.V*")
    model_file = latest_matching(dep_dir, f"{deposition_id}_model_P1.cif.V*")

    pdb_id: Optional[str] = None
    entry_title: Optional[str] = None
    if model_file is None:
        msg = f"Model CIF not found matching {deposition_id}_model_P1.cif.V*"
        log_problem(deposition_id, msg)
        problems.append(msg)
    else:
        try:
            pdb_id, entry_title = parse_model_metadata(model_file)
            if not pdb_id:
                msg = f"PDB ID not found in model CIF: {model_file.name}"
                log_problem(deposition_id, msg)
                problems.append(msg)
            if not entry_title:
                msg = f"Entry title not found in model CIF: {model_file.name}"
                log_problem(deposition_id, msg)
                problems.append(msg)
        except OSError as exc:
            msg = f"Could not read model CIF {model_file.name}: {exc}"
            log_problem(deposition_id, msg)
            problems.append(msg)

    if xml_file is None:
        msg = f"Validation XML not found matching {deposition_id}_val-data_P1.xml.V*"
        log_problem(deposition_id, msg)
        problems.append(msg)
        return error_report(
            deposition_id,
            msg,
            model_file=model_file,
            pdb_id=pdb_id,
            entry_title=entry_title,
            warnings=problems,
        ), problems

    report = parse_validation_xml(
        xml_file,
        deposition_id,
        pdb_id=pdb_id,
        entry_title=entry_title,
        model_file=model_file,
        warnings=problems,
    )
    if report.get("parse_error"):
        log_problem(deposition_id, report["parse_error"])
        problems.append(report["parse_error"])
    return report, problems


def write_metadata_file(path: Path, reports: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Title", "Deposition ID", "PDB ID"])
        for report in reports:
            writer.writerow([
                report.get("entry_title") or "",
                report.get("deposition_id") or report.get("id") or "",
                report.get("pdb_id") or "",
            ])


def collect_validation_pdfs(deposition_ids: List[str], base_path: Path, zip_path: Path) -> set[str]:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    problem_ids: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for deposition_id in deposition_ids:
            dep_dir = base_path / deposition_id
            pdf_file = latest_matching(dep_dir, f"{deposition_id}_val-report-full_P1.pdf.V*") if dep_dir.is_dir() else None
            if pdf_file is None:
                log_problem(deposition_id, f"Validation PDF not found matching {deposition_id}_val-report-full_P1.pdf.V*")
                problem_ids.add(deposition_id)
                continue
            archive_name = re.sub(r"\.V\d+$", "", pdf_file.name, flags=re.IGNORECASE)
            if not archive_name.lower().endswith(".pdf"):
                archive_name += ".pdf"
            try:
                zf.write(pdf_file, arcname=archive_name)
            except OSError as exc:
                log_problem(deposition_id, f"Could not add validation PDF {pdf_file.name}: {exc}")
                problem_ids.add(deposition_id)
    return problem_ids


def build_html(reports: List[Dict[str, Any]]) -> str:
    data_json = json.dumps(reports, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    safe_title = html.escape(PAGE_TITLE)
    safe_heading = html.escape(PAGE_HEADING)
    safe_subtitle = html.escape(PAGE_SUBTITLE)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title>
<style>
:root {{
  --bg:#f6f7f9; --panel:#fff; --text:#18202a; --muted:#677383; --line:#dfe4ea;
  --critical:#b42318; --critical-bg:#fef3f2; --poor:#d92d20; --poor-bg:#fff4ed;
  --review:#b54708; --review-bg:#fffaeb; --ok:#176b43; --ok-bg:#ecfdf3;
  --excellent:#067647; --excellent-bg:#e8f8ef; --unknown:#667085; --unknown-bg:#f2f4f7;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.10);
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
header{{position:sticky;top:0;z-index:20;background:rgba(246,247,249,.96);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}}
.wrap{{max-width:1460px;margin:auto;padding:16px 22px}}
h1{{font-size:22px;margin:0 0 4px}} .subtitle{{color:var(--muted);font-size:13px}}
.summary{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}
.summary .pill{{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:5px 10px;font-size:12px}}
.controls{{display:grid;grid-template-columns:minmax(220px,1fr) auto auto auto;gap:8px;margin-top:12px}}
input,select,button{{font:inherit;border:1px solid #cfd6df;background:#fff;border-radius:7px;padding:7px 10px;color:var(--text)}}
button{{cursor:pointer}} button:hover{{background:#f8fafc}}
main.wrap{{padding-top:14px}}
.report{{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:10px;margin:0 0 9px;box-shadow:var(--shadow);overflow:hidden}}
.report.sev4{{border-left-color:var(--critical)}} .report.sev3{{border-left-color:var(--poor)}} .report.sev2{{border-left-color:var(--review)}} .report.sev1,.report.sev0{{border-left-color:#98a2b3}}
.row{{display:grid;grid-template-columns:210px minmax(530px,1fr) minmax(260px,.75fr) 26px;gap:14px;align-items:center;padding:10px 12px;cursor:pointer}}
.identity{{min-width:0}} .idline{{display:flex;align-items:center;gap:7px;white-space:nowrap}} .depid{{font-weight:750;font-size:16px;overflow:hidden;text-overflow:ellipsis}} .pdbaccession{{font-size:11px;font-weight:700;color:#475467;background:#f2f4f7;border:1px solid #e4e7ec;border-radius:5px;padding:2px 5px}}
.res{{color:var(--muted);font-variant-numeric:tabular-nums}} .status{{font-size:10px;font-weight:800;letter-spacing:.055em;padding:3px 6px;border-radius:999px}}
.status.sev4{{color:var(--critical);background:var(--critical-bg)}} .status.sev3{{color:var(--poor);background:var(--poor-bg)}} .status.sev2{{color:var(--review);background:var(--review-bg)}} .status.sev1,.status.sev0{{color:var(--ok);background:var(--ok-bg)}}
.dominant{{font-size:12px;color:var(--muted);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.metrics{{display:grid;grid-template-columns:repeat(5,minmax(86px,1fr));gap:5px}}
.metric{{position:relative;border:1px solid var(--line);border-radius:7px;padding:6px 7px 5px;min-width:0;overflow:hidden;background:#fff}}
.metric:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:#98a2b3}}
.metric.band-critical:before{{background:var(--critical)}} .metric.band-poor:before{{background:var(--poor)}} .metric.band-review:before{{background:#f79009}} .metric.band-ok:before{{background:#84ad72}} .metric.band-excellent:before{{background:#12b76a}}
.mhead{{display:flex;justify-content:space-between;gap:4px;align-items:baseline}} .mlabel{{font-size:11px;color:var(--muted);white-space:nowrap}} .pct{{font-weight:800;font-variant-numeric:tabular-nums}}
.raw{{font-size:11px;margin-top:2px;font-variant-numeric:tabular-nums;white-space:nowrap}} .bar{{height:3px;background:#eef1f4;border-radius:5px;margin-top:4px;overflow:hidden}} .bar>span{{display:block;height:100%;background:#98a2b3}}
.band-critical .bar>span{{background:var(--critical)}} .band-poor .bar>span{{background:var(--poor)}} .band-review .bar>span{{background:#f79009}} .band-ok .bar>span{{background:#84ad72}} .band-excellent .bar>span{{background:#12b76a}}
.fingerprint{{min-width:0}} .fphead{{display:flex;justify-content:space-between;color:var(--muted);font-size:10px;margin-bottom:3px}} canvas.issuebar{{width:100%;height:40px;display:block;border:1px solid var(--line);border-radius:5px;background:#fbfcfd}}
.chev{{font-size:18px;color:var(--muted);transform:rotate(0deg);transition:.15s}} .report.open .chev{{transform:rotate(90deg)}}
.details{{display:none;border-top:1px solid var(--line);background:#fcfcfd;padding:12px 14px 14px}} .report.open .details{{display:block}} .entrytitle{{border:1px solid var(--line);border-radius:8px;background:#fff;padding:10px 12px;margin-bottom:12px}} .entrytitle .label{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:4px}} .entrytitle .titletext{{font-size:14px;font-weight:650}} .entrytitle .ids{{margin-top:5px;color:var(--muted);font-size:11px}}
.detailgrid{{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:12px}} .box{{border:1px solid var(--line);border-radius:8px;background:#fff;padding:10px}}
.box h3{{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 8px}}
.kv{{display:grid;grid-template-columns:1fr auto;gap:5px 10px;font-size:12px}} .kv .v{{font-variant-numeric:tabular-nums;font-weight:650}}
table{{width:100%;border-collapse:collapse;font-size:12px}} th{{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--line);padding:4px}} td{{border-bottom:1px solid #eef1f4;padding:5px 4px;vertical-align:top}} tr:last-child td{{border-bottom:0}}
.dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:#98a2b3}} .dot.s4{{background:var(--critical)}} .dot.s3{{background:var(--poor)}} .dot.s2{{background:#f79009}}
.longbar{{margin-top:12px}} .longbar canvas{{height:auto;min-height:56px}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin:8px 0 0}}
.legend span:before{{content:"";display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:-1px;background:#e5e7eb}}
.legend .c:before{{background:var(--critical)}} .legend .p:before{{background:var(--poor)}} .legend .r:before{{background:#f79009}} .legend .m:before{{background:#cbd5e1}}
.empty{{text-align:center;color:var(--muted);padding:50px}}
.tooltip{{position:fixed;z-index:50;pointer-events:none;background:#111827;color:#fff;font-size:11px;line-height:1.35;padding:6px 8px;border-radius:6px;box-shadow:0 5px 16px rgba(0,0,0,.25);max-width:320px;display:none}}
.parseerror{{padding:8px;color:var(--critical);font-size:12px}}
@media(max-width:1050px){{.row{{grid-template-columns:190px 1fr 28px}}.fingerprint{{grid-column:1/-1}}.metrics{{grid-column:2}}.detailgrid{{grid-template-columns:1fr 1fr}}.detailgrid .wide{{grid-column:1/-1}}}}
@media(max-width:720px){{.wrap{{padding-left:10px;padding-right:10px}}.controls{{grid-template-columns:1fr 1fr}}.row{{grid-template-columns:1fr 25px;gap:8px}}.identity,.metrics,.fingerprint{{grid-column:1}}.metrics{{grid-template-columns:repeat(5,minmax(62px,1fr));overflow:auto}}.detailgrid{{grid-template-columns:1fr}}.detailgrid .wide{{grid-column:auto}}}}
</style>
</head>
<body>
<header><div class="wrap">
  <h1>{safe_heading}</h1>
  <div class="subtitle">{safe_subtitle}</div>
  <div id="summary" class="summary"></div>
  <div class="controls">
    <input id="search" placeholder="Search deposition ID, PDB ID or title…" aria-label="Search">
    <select id="statusFilter" aria-label="Review priority filter"><option value="all">All priorities</option><option value="3,4">High</option><option value="2">Low</option><option value="0,1">OK</option></select>
    <select id="sort" aria-label="Sort"><option value="severity">Highest priority first</option><option value="id">Deposition ID</option><option value="pdb">PDB ID</option><option value="resolution">Resolution</option><option value="rsrz">RSRZ percentile</option><option value="rfree">Rfree percentile</option></select>
    <button id="expand">Expand all</button>
  </div>
</div></header>
<main class="wrap"><div id="reports"></div><div class="legend"><span class="c">severe local issue</span><span class="p">strong local issue</span><span class="r">review</span><span class="m">little/no issue</span> Residue strips encode local issue severity; hover for details.</div></main>
<div id="tooltip" class="tooltip"></div>
<script>
const REPORTS={data_json};
const metricOrder=['rfree','rsrz','rotamer','clash','rama'];
const tooltip=document.getElementById('tooltip');
let expandedAll=false;
function esc(s){{return String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
function fmt(v,d=2){{return v==null?'—':Number(v).toFixed(d)}}
function pctText(p){{return p==null?'—':(p<1?Number(p).toFixed(1):Number(p).toFixed(p<10?1:0))+'%'}}
function bandClass(m){{return 'band-'+(m.band||'unknown')}}
function statusClass(r){{return 'sev'+(r.severity||0)}}
function metricBy(r,k){{return r.metrics.find(m=>m.key===k)||{{}}}}
function metricHTML(m){{
 const p=m.percentile, width=p==null?0:Math.max(1,Math.min(100,p));
 return `<div class="metric ${{bandClass(m)}}" title="${{esc(m.group)}} — relative percentile ${{pctText(p)}}">
   <div class="mhead"><span class="mlabel">${{esc(m.label)}}</span><span class="pct">${{pctText(p)}}</span></div>
   <div class="raw">${{esc(m.raw_text||'—')}} <span style="color:var(--muted)">${{esc(m.raw_label||'')}}</span></div>
   <div class="bar"><span style="width:${{width}}%"></span></div></div>`;
}}
function keyDataHTML(r){{
 const labels={{'PDB R':'','PDB Rfree':'','DCC R':'','DCC Rfree':'','Completeness':'%','I/σ':'','Anisotropy':'','Fo/Fc corr.':'','Bond RMSZ':'','Angle RMSZ':''}};
 return Object.entries(r.key_data||{{}}).map(([k,v])=>{{let t='—';if(v!=null){{if(typeof v==='number')t=(k.includes('Completeness')?v.toFixed(1):v.toFixed(3));else t=v;}}return `<div>${{esc(k)}}</div><div class="v">${{esc(t)}}${{labels[k]||''}}</div>`}}).join('');
}}
function issueRows(rows){{
 if(!rows||!rows.length)return '<div style="color:var(--muted);font-size:12px">No flagged sites by dashboard heuristics.</div>';
 return `<table><thead><tr><th>Site</th><th>Signals</th></tr></thead><tbody>${{rows.map(x=>`<tr><td><span class="dot s${{x.score}}"></span><b>${{esc(x.id)}}</b></td><td>${{esc((x.issues||[]).join(' · '))}}</td></tr>`).join('')}}</tbody></table>`;
}}
function cardHTML(r,i){{
 const dep=esc(r.deposition_id||r.id||'—'), pdb=esc(r.pdb_id||'PDB ID unavailable');
 if(r.parse_error)return `<section class="report sev4" data-i="${{i}}"><div class="row"><div class="identity"><div class="idline"><span class="depid">${{dep}}</span><span class="pdbaccession">${{pdb}}</span><span class="status sev4">Input error</span></div><div class="dominant">Review priority: <b>High</b> · ${{esc(r.parse_error)}}</div></div><div class="parseerror">${{esc(r.parse_error)}}</div><div></div><div class="chev">›</div></div></section>`;
 return `<section class="report ${{statusClass(r)}}" data-i="${{i}}">
 <div class="row" role="button" tabindex="0" aria-expanded="false">
   <div class="identity"><div class="idline"><span class="depid">${{dep}}</span><span class="status ${{statusClass(r)}}">${{esc(r.review_priority)}}</span></div><div class="dominant"><b>${{esc(r.resolution_text)}}</b> · Dominant: <b>${{esc(r.dominant)}}</b></div></div>
   <div class="metrics">${{r.metrics.map(metricHTML).join('')}}</div>
   <div class="fingerprint"><div class="fphead"><span>Residue issue fingerprint</span><span>${{r.residues.length}} polymer sites · ${{r.nonpolymer.length}} other</span></div><canvas class="issuebar" data-canvas="compact" data-i="${{i}}"></canvas></div>
   <div class="chev">›</div>
 </div>
 <div class="details">
   <div class="entrytitle"><div class="label">Entry title</div><div class="titletext">${{esc(r.entry_title||'Entry title unavailable')}}</div><div class="ids">Deposition ID: <b>${{dep}}</b> · PDB ID: <b>${{pdb}}</b></div></div>
   <div class="detailgrid">
    <div class="box"><h3>Key experimental / refinement data</h3><div class="kv">${{keyDataHTML(r)}}</div></div>
    <div class="box"><h3>Worst polymer sites</h3>${{issueRows(r.top_residues)}}</div>
    <div class="box"><h3>Ligand / ion / water sites</h3>${{issueRows(r.top_nonpolymer)}}</div>
    <div class="box wide longbar"><h3>Residue map by chain</h3><canvas class="issuebar" data-canvas="detail" data-i="${{i}}"></canvas></div>
   </div>
 </div></section>`;
}}
function severityColor(s){{return ['#eef1f4','#cbd5e1','#fdb022','#f04438','#b42318'][Math.max(0,Math.min(4,s||0))]}}
function drawCanvas(canvas,r,detail=false){{
 const chains={{}}; for(const x of r.residues) (chains[x.chain]??=[]).push(x);
 const names=Object.keys(chains); if(!names.length){{canvas.height=20;return;}}
 const cssW=Math.max(240,canvas.clientWidth||600), rowH=detail?18:Math.max(8,Math.floor(34/names.length)), labelW=detail?34:0;
 const cssH=detail?Math.max(48,names.length*rowH+8):40, dpr=window.devicePixelRatio||1;
 canvas.width=Math.round(cssW*dpr); canvas.height=Math.round(cssH*dpr); canvas.style.height=cssH+'px';
 const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,cssW,cssH);ctx.font='10px system-ui';ctx.textBaseline='middle';
 canvas._hit=[];
 names.forEach((name,ci)=>{{const arr=chains[name], y=4+ci*rowH, x0=labelW, usable=cssW-x0-2;
   if(detail){{ctx.fillStyle='#667085';ctx.fillText(name,4,y+rowH/2);}}
   arr.forEach((res,j)=>{{const xa=x0+(j/arr.length)*usable, xb=x0+((j+1)/arr.length)*usable, w=Math.max(1,xb-xa);
      ctx.fillStyle=severityColor(res.score);ctx.fillRect(xa,y,w,Math.max(2,rowH-2));canvas._hit.push({{x0:xa,x1:xb,y0:y,y1:y+rowH,res}});
   }});
 }});
}}
function bindCanvas(canvas,r,detail){{drawCanvas(canvas,r,detail);canvas.onmousemove=e=>{{
 const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top,h=(canvas._hit||[]).find(q=>x>=q.x0&&x<=q.x1&&y>=q.y0&&y<=q.y1);
 if(!h){{tooltip.style.display='none';return}};const z=h.res;tooltip.innerHTML=`<b>${{esc(z.id)}}</b><br>${{esc((z.issues||[]).join(' · ')||'No flagged issue')}}${{z.rsrz!=null?`<br>RSRZ ${{z.rsrz.toFixed(2)}}`:''}}${{z.rscc!=null?` · RSCC ${{z.rscc.toFixed(2)}}`:''}}`;tooltip.style.left=Math.min(innerWidth-330,e.clientX+12)+'px';tooltip.style.top=Math.min(innerHeight-90,e.clientY+12)+'px';tooltip.style.display='block';
 }};canvas.onmouseleave=()=>tooltip.style.display='none';}}
function visibleReports(){{
 const q=document.getElementById('search').value.trim().toLowerCase(),sf=document.getElementById('statusFilter').value,sort=document.getElementById('sort').value;
 let a=REPORTS.map((r,i)=>({{r,i}})).filter(o=>!q||String(o.r.deposition_id||o.r.id||'').toLowerCase().includes(q)||String(o.r.pdb_id||'').toLowerCase().includes(q)||String(o.r.entry_title||'').toLowerCase().includes(q)||String(o.r.file||'').toLowerCase().includes(q));
 if(sf!=='all'){{const allowed=sf.split(',').map(Number);a=a.filter(o=>allowed.includes(o.r.severity||0));}}
 const mp=(r,k)=>{{const m=metricBy(r,k);return m.percentile==null?999:m.percentile}};
 a.sort((a,b)=>{{if(sort==='id')return String(a.r.deposition_id||a.r.id).localeCompare(String(b.r.deposition_id||b.r.id));if(sort==='pdb')return String(a.r.pdb_id||'').localeCompare(String(b.r.pdb_id||''));if(sort==='resolution')return (a.r.resolution??999)-(b.r.resolution??999);if(sort==='rsrz')return mp(a.r,'rsrz')-mp(b.r,'rsrz');if(sort==='rfree')return mp(a.r,'rfree')-mp(b.r,'rfree');return (b.r.severity||0)-(a.r.severity||0)||Math.min(...a.r.metrics.map(m=>m.percentile??999))-Math.min(...b.r.metrics.map(m=>m.percentile??999));}});
 return a;
}}
function render(){{
 const items=visibleReports(), host=document.getElementById('reports'); host.innerHTML=items.length?items.map(o=>cardHTML(o.r,o.i)).join(''):'<div class="empty">No reports match the current filters.</div>';
 host.querySelectorAll('.report .row').forEach(row=>{{const sec=row.closest('.report'), toggle=()=>{{sec.classList.toggle('open');row.setAttribute('aria-expanded',sec.classList.contains('open'));if(sec.classList.contains('open'))requestAnimationFrame(()=>sec.querySelectorAll('canvas').forEach(c=>bindCanvas(c,REPORTS[+c.dataset.i],c.dataset.canvas==='detail')));}};row.onclick=toggle;row.onkeydown=e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();toggle()}}}};}});
 host.querySelectorAll('canvas[data-canvas="compact"]').forEach(c=>bindCanvas(c,REPORTS[+c.dataset.i],false));
 updateSummary(items.map(x=>x.r));
}}
function updateSummary(items){{
 const counts=[0,0,0,0,0];items.forEach(r=>counts[r.severity||0]++);const parse=items.filter(r=>r.parse_error).length;
 document.getElementById('summary').innerHTML=`<span class="pill"><b>${{items.length}}</b> structures</span><span class="pill"><b style="color:var(--critical)">${{counts[4]+counts[3]}}</b> High priority</span><span class="pill"><b style="color:var(--review)">${{counts[2]}}</b> Low priority</span><span class="pill"><b style="color:var(--ok)">${{counts[0]+counts[1]}}</b> OK</span>${{parse?`<span class="pill"><b>${{parse}}</b> input errors</span>`:''}}`;
}}
['search','statusFilter','sort'].forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',render));
document.getElementById('expand').onclick=()=>{{expandedAll=!expandedAll;document.getElementById('expand').textContent=expandedAll?'Collapse all':'Expand all';document.querySelectorAll('.report').forEach(sec=>{{sec.classList.toggle('open',expandedAll);const row=sec.querySelector('.row');if(row)row.setAttribute('aria-expanded',expandedAll)}});if(expandedAll)requestAnimationFrame(()=>document.querySelectorAll('canvas').forEach(c=>bindCanvas(c,REPORTS[+c.dataset.i],c.dataset.canvas==='detail')));}};
let resizeTimer;window.addEventListener('resize',()=>{{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>document.querySelectorAll('canvas').forEach(c=>bindCanvas(c,REPORTS[+c.dataset.i],c.dataset.canvas==='detail')),120)}});
render();
</script>
</body></html>'''


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Create the self-contained PDBe validation summary dashboard from OneDep deposition IDs."
    )
    ap.add_argument("deposition_ids", nargs="*", help="One or more deposition IDs, e.g. D_1292159901")
    ap.add_argument("-f", "--id-file", type=Path, help="Text file containing deposition IDs (one or more per line)")
    ap.add_argument("-o", "--output", type=Path, default=Path("validation_dashboard.html"), help="Output HTML path")
    ap.add_argument("--metadata-output", type=Path, help="Metadata text/CSV output path (default: <html-stem>_metadata.txt)")
    ap.add_argument("--collect-val-pdf", action="store_true", help="Collect latest full validation PDF for each deposition into a ZIP")
    ap.add_argument("--pdf-zip", type=Path, help="Validation PDF ZIP path (default: <html-stem>_validation_pdfs.zip)")
    ap.add_argument(
        "--base-path",
        type=Path,
        default=DEPOSITION_ROOT,
        help=f"OneDep deposition root (default: {DEPOSITION_ROOT})",
    )
    args = ap.parse_args(argv)

    try:
        deposition_ids = load_deposition_ids(args.deposition_ids, args.id_file)
    except (OSError, FileNotFoundError) as exc:
        ap.error(str(exc))
    if not deposition_ids:
        ap.error("Provide at least one deposition ID on the command line or with --id-file")

    reports: List[Dict[str, Any]] = []
    problem_ids = set()
    for deposition_id in deposition_ids:
        report, problems = process_deposition(deposition_id, args.base_path)
        reports.append(report)
        if problems:
            problem_ids.add(deposition_id)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(reports), encoding="utf-8")

    metadata_path = args.metadata_output or args.output.with_name(f"{args.output.stem}_metadata.txt")
    write_metadata_file(metadata_path, reports)

    pdf_zip_path: Optional[Path] = None
    if args.collect_val_pdf:
        pdf_zip_path = args.pdf_zip or args.output.with_name(f"{args.output.stem}_validation_pdfs.zip")
        problem_ids.update(collect_validation_pdfs(deposition_ids, args.base_path, pdf_zip_path))

    print(f"Wrote HTML: {args.output} ({len(reports)} deposition(s))")
    print(f"Wrote metadata: {metadata_path}")
    if pdf_zip_path is not None:
        print(f"Wrote validation PDF ZIP: {pdf_zip_path}")
    if problem_ids:
        print(f"Completed with issues for {len(problem_ids)} deposition(s): {', '.join(sorted(problem_ids))}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())