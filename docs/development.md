---
title: Developer guide
---

[Documentation home](index.md) · [Architecture](architecture.md) · [Command internals](commands/index.md)

# Developer guide

## Add or extend a command

1. Put reusable processing in `src/deposition_toolkit/stages/<stage>/`. Use explicit file/value parameters and a small result object or dictionary; give it an independent parser if operators need a script.
2. Add `src/deposition_toolkit/commands/<command>.py` with `register(subparsers)` and `run(args)`. Keep toolkit argument parsing here.
3. Import the module and add it to `COMMAND_MODULES` in `commands/__init__.py`.
4. For structure processing, use shared discovery/exclusion helpers, results locations and a named manifest step. Interpret stage results explicitly, including no-input and warning cases.
5. Put database queries/transactions in `ledger/`, call them from the command and pass plain values into the stage. Persist stage results after it returns. Do not pass sessions or ORM objects across that boundary.
6. Update the [CLI reference](cli.md), relevant internals page and [configuration](configuration.md). Document mutations, retry behavior, and which diagnostics fail a command.

The existing CCD finaliser's direct MySQL access is an exception to improve, not a pattern for new database features. Stage-level manifest usage is currently allowed and used for provenance; avoid duplicating manifest field logic across modules.

## Running stages independently

From the checkout with its dependencies installed, prefix the module invocations below with `uv run python -m`. Module execution preserves package imports. These are distinct parsers from the toolkit commands; check `--help` for all options.

| Module after `python -m` | Minimal arguments / mode |
| --- | --- |
| `deposition_toolkit.stages.prepare_dataset.prepare_dataset` | `/input/sample.tgz --work-dir /processing/sample` |
| `deposition_toolkit.stages.prepare_dataset.dep_report` | `/input/structure-parent G_01`; positional-only script, no `--help` parser |
| `deposition_toolkit.stages.validate.mmcif_validate` | `/input/sample_struc.cif --dictionary /input/mmcif.dic --log-dir /output/logs` |
| `deposition_toolkit.stages.analyse_chains.analysis_polymer_chains` | `/input/sample_struc.cif`; directory mode uses `--auto` |
| `deposition_toolkit.stages.metadata_enrichment.enrich_cif` | `--input-cif /input/sample_struc.cif --input-json /input/metadata.json --output-cif /output/sample_struc.cif` |
| `deposition_toolkit.stages.ccd_creation.ccd_creator` | `--input-dir /input/structure --output-dir /output/ccd` |
| `deposition_toolkit.stages.ccd_creation.annotate_comp` | `/input/stage_1 /output/stage_2 --log-root /output/logs` |
| `deposition_toolkit.stages.ccd_creation.finalise_step.finalize_ccds` | `--input-root /input/stage_2 --output-dir /output/final --existing-descriptors-file /input/descriptors.csv` |
| `deposition_toolkit.stages.ccd_creation.ligand_renaming.lig_renaming` | `/input/ccd_processing_details.csv /input/structure-parent`; still requires instance manifests |
| `deposition_toolkit.stages.resolution_cleanup.resolution_cleanup` | `--input /input/sample_struc.cif --dry-run`; separate standalone overwrite confirmation and batch mode |
| `deposition_toolkit.stages.deposit.deposition` | `/input/structure --dry-run`; real resume accepts `--session-id UUID` |
| `deposition_toolkit.stages.validation_report.val_report` | `D_123456 --base-path /onedep/deposit --output /output/dashboard.html` |

A direct call to `deposit_structure()` or its standalone script does **not** perform toolkit ledger lookup/upsert or write wrapper step status. The caller owns persistence/recovery. Similarly, standalone stage successes may have different exit semantics. `scripts/` contains historical recipes with assumptions about working directories, imports and deployment paths; use the package modules for current behavior.

## Post-deposition validation dashboard

`stages/validation_report/val_report.py` has no registered toolkit command. It validates `D_<digits>` IDs, reads each deposition directory under `DEPOSITION_ROOT` or `--base-path`, chooses the highest numeric `.V*` version of validation XML and model CIF, and extracts metrics, residue issues, entry title and PDB ID. It writes self-contained HTML plus a metadata text/CSV file. Optional `--collect-val-pdf` packages latest validation PDFs into a ZIP. Missing/unreadable inputs appear as problem reports and cause exit `2`; normal completion is `0`. This consumes existing OneDep files; it does not fetch remote results or update manifests/registry.

## Database changes

Derive SQLAlchemy models from `ledger.models.Base` and import them in `ledger/models/__init__.py` for Alembic discovery. Validate operation inputs/outputs using `LedgerSchema` subclasses. Use `session_scope()` within ledger operations and finish transactions before stage calls.

```bash
uv run alembic -c src/deposition_toolkit/ledger/alembic.ini revision --autogenerate -m "Describe schema change"
# Review the generated migration, then apply:
uv run deposition-toolkit ledger upgrade
```

The Alembic configuration also supports explicit `history`, offline SQL generation (`upgrade head --sql`), and intentional downgrades. Generate revisions in a checkout and ship them in the package. Do not auto-call `Base.metadata.create_all()` during normal runtime.

## Verification and test map

Current tests use Python's unittest framework:

```bash
uv run python -m unittest discover -s tests
```

| Test module | Main contract |
| --- | --- |
| `test_config.py` | Settings sources, defaults and CLI overrides |
| `test_ledger.py` | Lazy database boundaries, settings, models/migration behavior |
| `test_deposition_registry.py` | Session lookup/upsert, command persistence and failures |
| `test_deposition.py` | OneDep stage results, upload/resume behavior with fakes |
| `test_ligand_provenance.py` | Per-instance creation, finalisation mapping and safe renaming |
| `test_enrich_metadata.py` | Enrichment and dictionary validation behavior |

Use fixtures/fakes for remote services and allocation pools; avoid production uploads, code consumption or CVS changes in tests. When changing scientific rules, include counterexamples such as alternate conformations, nonconsecutive atom IDs, missing categories, duplicated references and partial failures.
