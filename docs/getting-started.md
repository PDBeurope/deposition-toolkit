---
title: Getting started
---

[Documentation home](index.md) · [Configuration](configuration.md) · [CLI reference](cli.md)

# Getting started

## Install and configure

Use Python **3.13 or newer**, as required by `pyproject.toml`. From the checkout, with `uv` installed:

```bash
uv sync
cp .env.example .env
uv run deposition-toolkit --help
```

Alternatively install the project in an appropriate Python environment with `pip install -e .`, then use `deposition-toolkit` directly. `python main.py …` is a source-checkout launcher using that interpreter's dependencies.

Edit `.env` using the [stage-specific configuration table](configuration.md). The supplied defaults point to a PDBe deployment and are not portable local paths. Preparation, reporting, and chain analysis can use local inputs without database access. Validation needs a dictionary; annotation needs the external tool suite; real deposition needs PostgreSQL and OneDep configuration.

## Prepare and inspect

Use an explicit local work directory for an initial run:

```bash
uv run deposition-toolkit prepare-dataset /input/sample.tgz --work-dir /processing/sample
uv run deposition-toolkit submission-report /processing/sample G_01
uv run deposition-toolkit mmcif-validation /processing/sample
uv run deposition-toolkit analyse-chains /processing/sample
```

Preparation prints the resolved work directory. The report writes `results/submission_report/G_01_summary.pdf` and `structures.log`. Inspect those plus manifests and validation logs before modifying coordinates. The report can return exit code zero while recording individual structures as failed.

To exclude a structure, set its manifest's `"excluded": true`. Subsequent batch wrappers record the skipped step as `not applicable`. A failed step alone does not exclude a structure.

## Enrich and build CCDs

Create metadata JSON using the [metadata format](commands/enrich-metadata.md#metadata-format). The same JSON is applied to every selected coordinate file:

```bash
uv run deposition-toolkit enrich-metadata /processing/sample /input/metadata.json
uv run deposition-toolkit ccd-creation /processing/sample
uv run deposition-toolkit ccd-annotate /processing/sample
uv run deposition-toolkit ccd-finalise /processing/sample --existing-descriptors-file /input/descriptors.csv
```

Enrichment overwrites the selected coordinate files. CCD creation expects exactly one coordinate `.cif` per structure directory, even when both original and enriched variants would otherwise be valid inputs. Annotation requires the installation described in [configuration](configuration.md#annotation-tools).

Finalisation writes review CSVs under `results/ccd/final/`. Without the descriptor-file option it queries MySQL. New InChIKey groups remain `PENDING_ALLOCATION` by default, making the wrapper return failure until codes are provided. The preview can still produce useful reports and renamed CCDs for already known components.

After reviewing the preview, choose one allocation path:

```bash
# Reuse previously assigned codes:
uv run deposition-toolkit ccd-finalise /processing/sample --existing-descriptors-file /input/descriptors.csv --code-map-file /input/allocated_ccd_codes.csv

# Or consume new IDs from the configured shared pool:
uv run deposition-toolkit ccd-finalise /processing/sample --existing-descriptors-file /input/descriptors.csv --allocate-codes
```

Keep `results/ccd/final/allocated_ccd_codes.csv` for retries. Code allocation edits the shared pool; CVS commit is a separate opt-in action described in [finalisation internals](commands/ccd-finalise.md).

## Rename, clean up, and review

```bash
uv run deposition-toolkit lig-rename /processing/sample
uv run deposition-toolkit resolution-cleanup /processing/sample --dry-run
uv run deposition-toolkit resolution-cleanup /processing/sample
uv run deposition-toolkit mmcif-validation /processing/sample
```

Renaming verifies stored instance identity before editing. Cleanup defaults to in-place changes; `--output-suffix _cleaned` writes a separate file when there are changes. A suffixed file may not match deposition's normal coordinate patterns, so explicitly arrange the intended upload file before depositing. Review unresolved cutoff differences in the cleanup log even if the command succeeds.

## Deposit

Provision the toolkit database, configure credentials, and apply schema migrations once per database:

```bash
uv run deposition-toolkit ledger check
uv run deposition-toolkit ledger upgrade
uv run deposition-toolkit ledger current
uv run deposition-toolkit deposit /processing/sample --dry-run
```

The dry run only discovers coordinate and structure-factor files. It does not authenticate, run file checks, contact OneDep, or access the registry.

With OneDep authentication configured, run:

```bash
uv run deposition-toolkit deposit /processing/sample --email depositor@example.org --users 0000-0002-8612-9539
```

The wrapper reuses registry sessions for retries. Preserve the local OneDep session files as well as manifests. Read [deposition internals](commands/deposit.md) before relying on a successful status: polling exhaustion and `--no-poll` do not establish remote processing completion.
