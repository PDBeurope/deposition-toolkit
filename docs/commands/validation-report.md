---
title: Post-deposition validation reports
---

[CLI reference](../cli.md) · [Deposition](deposit.md) · [Configuration](../configuration.md)

# validation-report

```bash
uv run deposition-toolkit validation-report /processing/sample
```

Run this as the final stage, after OneDep has generated validation reports. Its only argument is the toolkit work directory. It reads `DEPOSITION_ROOT` from the shared configuration; no ID list, output paths or PDF collection flag is needed.

The command discovers structures using the usual toolkit rules and skips excluded structures, unsuccessful depositions and dry-run results. It uses each successful structure's top-level `deposition_id`, falling back to `steps.deposition.deposition_id`. A successful deposition without an ID is recorded as failed for this report step.

## Outputs

Outputs are grouped by dataset under `<work-dir>/results/validation_report/`:

- `<dataset>_validation_dashboard.html`: self-contained interactive dashboard.
- `<dataset>_validation_metadata.csv`: title, deposition ID and PDB accession.
- `<dataset>_validation_pdfs.zip`: all available full validation PDFs, named by deposition ID inside the archive.

Dataset identity follows deposition's `source_tarball` naming rule, with the work directory name as a fallback. Filenames replace unsafe characters; colliding dataset names receive numeric suffixes. A work directory containing multiple datasets produces a separate set for each. Repeated deposition IDs within a dataset appear once in the dashboard, metadata and ZIP.

For each ID, the stage reads `<DEPOSITION_ROOT>/<deposition-id>/` and selects the highest numeric `.V*` version of:

- `<id>_val-data_P1.xml.V*`
- `<id>_model_P1.cif.V*`
- `<id>_val-report-full_P1.pdf.V*`

Model CIF metadata supplies the entry title and PDB accession. The existing dashboard metrics, residue issue views and review-priority heuristics are preserved. Review-priority findings do not cause command failure.

## Incomplete results and retries

OneDep can take time to produce reports after a successful upload. This command checks available files immediately and does not wait or poll. Missing or malformed XML, missing model metadata, and missing or unreadable PDFs mark affected structures as failed while retaining available outputs. Exit status is `1` for any failed structure or no active structures, otherwise `0`. Undeposited and dry-run structures are marked `not applicable`.

Rerun the same command later to refresh all selected reports and replace previous outputs. It reads OneDep files without modifying them or starting another upload. Per-structure `steps.validation_report` records status, errors, dataset, deposition ID and output paths relative to the work directory. Diagnostics are saved under `results/logs/validation_report/<structure-relative-path>/run.log`.
