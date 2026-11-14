---
title: CLI reference
---

[Documentation home](index.md) · [Getting started](getting-started.md) · [Command internals](commands/index.md)

# CLI reference

```bash
uv run deposition-toolkit [--verbose] <command> [arguments]
uv run deposition-toolkit <command> --help
```

Global `--verbose` precedes the command and shows detailed live diagnostics alongside the final summary. Stage-specific verbosity flags are separate. Paths are ordinary filesystem paths; examples use `/processing/sample` as the work directory. All options are optional unless stated otherwise.

| Command and required positional arguments | Options and defaults | Details |
| --- | --- | --- |
| `prepare-dataset <tarball>` | `--work-root` configured workroot; `--work-dir` explicit destination; `--no-copy`; `--no-extract` | [Preparation](commands/prepare-dataset.md) |
| `submission-report <work-dir> <group-id>` | None | [Reporting](commands/submission-report.md) |
| `mmcif-validation <work-dir>` | `--dictionary` / `-d` configured dictionary | [Validation](commands/mmcif-validation.md) |
| `analyse-chains <work-dir>` | `--verbose-chains` / `-v` | [Chains](commands/analyse-chains.md) |
| `enrich-metadata <work-dir> <metadata-json>` | `--entry-id model`; `--dictionary` configured dictionary; `--skip-validation` | [Enrichment](commands/enrich-metadata.md) |
| `ccd-creation <work-dir>` | `--audit-details TEXT` | [Creation](commands/ccd-creation.md) |
| `ccd-annotate <work-dir>` | `--annotate-bin` configured/derived executable | [Annotation](commands/ccd-annotate.md) |
| `ccd-finalise <work-dir>` | See complete option table below | [Finalisation](commands/ccd-finalise.md) |
| `lig-rename <work-dir>` | `--summary-csv` optional per-ligand code override | [Renaming](commands/lig-rename.md) |
| `resolution-cleanup <work-dir>` | `--config` / `-c`; `--pattern` / `-p`; `--output-suffix` / `-o`; `--block`; `--dry-run` / `-d`; `--verbose-resolution` / `-v` | [Cleanup](commands/resolution-cleanup.md) |
| `deposit <work-dir>` | `--email`, `--users` from exported `WWPDB_*`; `--country UK`; `--experiment-type XRAY`; `--no-poll`; `--poll-interval 5`; `--max-polls 64`; `--dry-run` | [Deposition](commands/deposit.md) |
| `ledger <check\|current\|upgrade>` | None | [Ledger](commands/ledger.md) |


## Terminal summaries and structure logs

Structure-processing commands finish with counts of selected structures, successes, failures, not-applicable outcomes and excluded structures. Summaries also show relevant totals: validation errors/warnings, polymer chain categories, CCD annotation results, renamed component fields, cleanup operations, or deposition IDs. `submission-report` retains its report-generation exit status even if report findings mark structures as failed. `ledger` manages the registry rather than processing structures.

Detailed output goes to `results/logs/<step>/<structure-relative-path>/run.log`. Each processed manifest step has a `log` field containing that path relative to the work directory. Logs include stage diagnostics, printed output, errors and the recorded outcome. Rerunning a step replaces its `run.log`; existing stage-specific logs and outputs remain available. Excluded structures are counted separately and are not processed.

For enrichment the step directory is `metadata_enrichment`. Its summary includes coordinate-file and validation totals. For CCD creation, the summary includes the total generated CCD files and the distribution of 1, 2, 3, etc. ligand instances per successful structure. These are instance counts, including separate conformations, rather than unique chemical compounds. Failed structures and their possible partial outputs are excluded from those ligand totals.

Finalisation runs across the dataset: per-structure logs contain the mapped ligand rows and links to shared outputs, including `results/ccd/final/ccd_processing.log` for the batch diagnostics. Interactive commit prompts remain visible. `--commit-only` uses the finaliser's shared log and does not update per-structure results.

Use global `--verbose` before the command to keep detailed diagnostics visible while also saving them in logs. Stage-specific verbosity flags control how much detail their stages generate.

## CCD finalisation options

| Option | Default / purpose |
| --- | --- |
| `--existing-descriptors-file PATH` | No file: query MySQL. File: CSV/TSV existing InChIKey data |
| `--write-db-cache PATH` | Optional descriptor snapshot, including when input is already a file |
| `--code-map-file PATH` | Reuse InChIKey-to-CCD allocations |
| `--allocate-codes` | Off; consume shared pool IDs for new unmapped groups |
| `--verbose-allocation` | Off; detailed allocation checks |
| `--old-id ID` | `LIG`; identifier replaced in generated CCD files |
| `--copy-to-structure-folders` | Off; also write renamed CCDs beside stage-2 inputs |
| `--commit-mode never\|dry-run\|prompt` | `never`; preview CVS operations or require interactive confirmation |
| `--commit-folder PATH` | Defaults to final output's `new_ccds/` in ordinary processing |
| `--commit-message TEXT` | Empty; required when commit mode is not `never` |
| `--cvs-path PATH` | Configured checkout; commit helper override only |
| `--commit-only` | Off; skip scanning, descriptor checks, allocation and manifest result mapping; requires `--commit-folder` |
| `--db-host`, `--db-port`, `--db-user`, `--db-password`, `--db-timeout` | Override corresponding `CCD_DB_*` settings; port and timeout are integers |
| `--debug` | Off; detailed finaliser logging |

## Exit status and reruns

Most wrappers return `0` on batch success and `1` if a processed structure fails or no active structures are found. `mmcif-validation` returns `2` for a missing dictionary. Unhandled exceptions and argparse errors also produce nonzero termination; exact values are not uniform across standalone scripts.

`submission-report` returns `0` after producing a report even when manifest results fail. Preparation can succeed with zero discovered structures. Finalisation's standalone preview can return `0` with pending codes, while the wrapper returns `1` for unresolved per-ligand mappings. Inspect manifests and outputs as well as the exit code.

Commands rerun selected structures regardless of earlier `done`/`failed` status. There is no `run-all`, automatic resume scheduler, or dependency gate. See [recovery](recovery.md) before repeating allocating or uploading operations.
