# deposition-toolkit

Tools for preparing crystallographic deposition datasets, creating CCDs, updating
mmCIF metadata, and depositing structures through OneDep.

The command-line entry point is:

```bash
uv run deposition-toolkit <command> [args]
```

For local source-tree development before installation, this also works:

```bash
python main.py <command> [args]
```

## Configuration

Deployment paths and database settings are defined in
`deposition_toolkit.config.Settings` and loaded with `pydantic-settings`.
Create your local configuration before running commands:

```bash
cp .env.example .env
```

Edit `.env` for your installation, including the OneDep `CCD_DB_PASSWORD`.
The local `.env` is ignored by Git. It is read from the directory where you
launch the command, so launch from the repository root when using its `.env`.
For installed commands launched elsewhere, put `.env` in the working directory
or export the settings as environment variables.

Command-line options take precedence over environment variables, which take
precedence over `.env`, followed by the defaults in `Settings`. Settings are
validated and cached on first use; restart the command after editing `.env`.

| Setting | Purpose |
| --- | --- |
| `UNUSED_CCD_FILE_PATH` | File containing the unused CCD code pool |
| `CCD_CVS_PATH` | Component dictionary checkout used for allocation checks and CVS commits |
| `TOOLS_ROOT` | Root for the annotateComp tools and their runtime environment |
| `ANNOTATE_BIN` | Optional annotateComp executable override; defaults to `<TOOLS_ROOT>/packages/cc-tools-v2/bin/annotateComp` |
| `DEPOSITION_TOOLKIT_WORKROOT` | Default root for prepared datasets |
| `DEPOSITION_ROOT` | OneDep deposition root used by the validation report |
| `MMCIF_DICTIONARY_PATH` | Default mmCIF dictionary for validation and metadata enrichment; defaults to `/nfs/public/services/onedep_gpfs/deposition-toolkit-processing/mmcif_pdbx_v50.dic` |
| `CCD_DB_HOST`, `CCD_DB_PORT`, `CCD_DB_USER`, `CCD_DB_PASSWORD`, `CCD_DB_TIMEOUT` | Existing OneDep MySQL connection for CCD finalisation |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE` | Toolkit ledger PostgreSQL connection settings |

The PostgreSQL defaults are host `pgsql-hlvm-172`, port `5432`, user `pdb_dep`,
and password `place-holder`. Set the actual connection credentials to use the
ledger. The database name defaults to `POSTGRES_USER`; optionally set
`POSTGRES_DATABASE` if the database name differs. The `deposit` command uses this
database, except for dry runs. Other processing commands do not require PostgreSQL.

Python callers can use `from deposition_toolkit.config import get_settings`.
Passwords use `SecretStr`; call `.get_secret_value()` only when passing a password
to a database client.

## Ledger

The persistence layer lives in `src/deposition_toolkit/ledger`: SQLAlchemy ORM
models in `models/`, Pydantic input/output schemas in `schemas.py`, connection and
transaction management in `database.py`, and Alembic migrations in `migrations/`.
Migration `0002` adds the `deposition_registry` table after the empty baseline.
PostgreSQL must already be provisioned; migrations manage its schema, not the
database or database users.

After setting `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` in `.env`:

```bash
uv run deposition-toolkit ledger check
uv run deposition-toolkit ledger upgrade
uv run deposition-toolkit ledger current
```

`check` executes `SELECT 1`; `upgrade` applies migrations up to the latest revision
and creates Alembic's version table. Connections are lazy and use a ten-second
connection timeout. Importing the ledger or displaying command help does not
connect to PostgreSQL. Schema changes are applied explicitly through Alembic,
never automatically during normal command execution.

Only command modules call ledger operations. Keep queries and transactions
inside the ledger, validate their inputs and outputs with Pydantic, and return
plain values or validated schemas to commands. Commands pass those values as
stage parameters and may persist stage results afterward. Stages must never
import the ledger, hold database sessions, or receive ORM objects. Keep database
transactions short and finish them before invoking a stage.

For future features, derive ORM models from `ledger.models.Base` and import them
in `ledger/models/__init__.py` for Alembic discovery. Derive validation models
from `ledger.schemas.LedgerSchema`. Inside ledger operations,
`with session_scope() as session:` commits on success and rolls back on failure,
always closing the session. Do not use `Base.metadata.create_all()` at runtime.

Generate a migration from the repository root after adding models:

```bash
uv run alembic -c src/deposition_toolkit/ledger/alembic.ini revision --autogenerate -m "Describe the schema change"
# Review the generated migration before applying it.
uv run deposition-toolkit ledger upgrade
```

Use the same Alembic configuration for `history`, offline SQL generation
(`upgrade head --sql`), or an explicit `downgrade`. Migration files and templates
are included in the installed package. Generate new revisions in the source
checkout and ship them with the application.

## Workflow

The intended sequence is:

1. `prepare-dataset`
2. `submission-report`
3. `mmcif-validation`
4. `analyse-chains`
5. `enrich-metadata`
6. `ccd-creation`
7. `ccd-annotate`
8. `ccd-finalise`
9. `lig-rename`
10. `resolution-cleanup`
11. `deposit`

Each structure directory has a `manifest.json` that records top-level state and
per-step status. Writes are atomic: the manifest is written to a temporary file
in the same directory and then renamed into place.

Example structure manifest:

```json
{
  "structure_id": "A71EV2A-x0836",
  "group_id": "G_01",
  "chains": "single",
  "excluded": false,
  "steps": {
    "prepare_dataset": {
      "status": "done",
      "timestamp": "2026-08-25T20:41:06+00:00",
      "outputs": ["pdb_depo_files/A71EV2A-x0836/A71EV2A-x0836_struc.cif"]
    }
  }
}
```

If `excluded` is `true`, the wrapper skips that structure for later commands and
records the step as `not applicable`.

## Commands

| Command | Main command module | Stage implementation |
| --- | --- | --- |
| `prepare-dataset <tarball>` | `deposition_toolkit.commands.prepare_dataset` | `stages.prepare_dataset.prepare_dataset` |
| `submission-report <work-dir> <group-id>` | `deposition_toolkit.commands.submission_report` | `stages.prepare_dataset.dep_report` |
| `mmcif-validation <work-dir>` | `deposition_toolkit.commands.mmcif_validation` | `stages.validate.mmcif_validate` |
| `analyse-chains <work-dir>` | `deposition_toolkit.commands.analyse_chains` | `stages.analyse_chains.analysis_polymer_chains` |
| `enrich-metadata <work-dir> <metadata-json>` | `deposition_toolkit.commands.enrich_metadata` | `stages.metadata_enrichment.enrich_cif` |
| `ccd-creation <work-dir>` | `deposition_toolkit.commands.ccd_creation` | `stages.ccd_creation.ccd_creator` |
| `ccd-annotate <work-dir>` | `deposition_toolkit.commands.ccd_annotate` | `stages.ccd_creation.annotate_comp` |
| `ccd-finalise <work-dir>` | `deposition_toolkit.commands.ccd_finalise` | `stages.ccd_creation.finalise_step.finalize_ccds` |
| `lig-rename <work-dir>` | `deposition_toolkit.commands.lig_rename` | `stages.ccd_creation.ligand_renaming.lig_renaming` |
| `resolution-cleanup <work-dir>` | `deposition_toolkit.commands.resolution_cleanup` | `stages.resolution_cleanup.resolution_cleanup` |
| `deposit <work-dir>` | `deposition_toolkit.commands.deposit` | `stages.deposit.deposition` |

Each command module owns its own argument parser and runner. The top-level CLI in
`deposition_toolkit.cli` only registers command modules and dispatches to the
selected runner.

## Examples

Prepare a work directory and initialize manifests:

```bash
uv run deposition-toolkit prepare-dataset \
  /nfs/ftp/private/pdbedeb-transfer/xchem/A71EV2A_datapack_depo_20260615.tgz
```

Create the submission report:

```bash
uv run deposition-toolkit submission-report /path/to/work-dir G_01
```

Run mmCIF validation:

```bash
uv run deposition-toolkit mmcif-validation \
  /path/to/work-dir
```

Enrich coordinate CIFs in place from metadata JSON:

```bash
uv run deposition-toolkit enrich-metadata \
  /path/to/work-dir \
  /path/to/metadata.json
```

Both commands use `MMCIF_DICTIONARY_PATH` from the shared settings. Pass
`--dictionary /path/to/other.dic` to override it for a single run.

Create, annotate, and finalize CCDs:

```bash
uv run deposition-toolkit ccd-creation /path/to/work-dir

uv run deposition-toolkit ccd-annotate \
  /path/to/work-dir \
  --annotate-bin /nfs/public/services/onedep_gpfs/resources/tools/tools-centos-7.9/packages/cc-tools-v2/bin/annotateComp

uv run deposition-toolkit ccd-finalise \
  /path/to/work-dir \
  --existing-descriptors-file /path/to/existing_descriptors.csv
```

Rename `LIG` in coordinate files using final CCD summary output:

```bash
uv run deposition-toolkit lig-rename /path/to/work-dir
```

Clean up resolution values:

```bash
uv run deposition-toolkit resolution-cleanup /path/to/work-dir --pattern '*_struc.cif'
```

Deposit active structures:

```bash
uv run deposition-toolkit deposit \
  /path/to/work-dir \
  --email depositor@example.org \
  --users 0000-0002-8612-9539
```

To replace files in an existing deposition, pass `session_id` to
`deposit_structure(...)`. Without it, a new deposition is created. For one
structure, the stage CLI also accepts the saved session UUID:

```bash
uv run python -m deposition_toolkit.stages.deposit.deposition \
  /path/to/structure --session-id SAVED_SESSION_UUID
```

Re-upload resumes the saved depositor and experiment settings, checks replacement
files, replaces local registrations of the same file types, and uploads to the
existing remote deposition. The original OneDep session files must remain in
the configured `session_dir`; the remote deposition ID alone is insufficient.

Before a real upload, `deposit` queries `deposition_registry` for the manifest's
`(dataset, structure_id)`. An existing session is passed to the stage for re-upload;
an absent entry creates a new deposition. The dataset comes from the manifest's
`source_tarball` (normally under
`steps.prepare_dataset`), with the archive extension removed; `structure_id`
comes from the manifest. Both identity fields must be nonempty for database use.
`commands.deposit.run(args, session_lookup=lookup)` can override session lookup
with `lookup(dataset, structure_id) -> session_id | None`; results are still stored
in the registry.

`DepositionResult.to_record()` returns `dep_id`, `dataset`, `structure_id`, and
`session_id` for database storage. The command saves this record under
`steps.deposition.deposition_record` whenever a remote deposition ID is known,
and saves `deposition_id` and `session_id` at the top level of the manifest.
After an upload, the command inserts or updates the registry row with these four
fields, `automated_depostion=true`, and a database-generated `deposited_on` timestamp.
The table also has an integer primary key `id` and a unique constraint on
`(dataset, structure_id)`, so re-uploads update the existing entry and timestamp.
The column name `automated_depostion` follows the registry schema. Manual entries
can set it to false and leave `session_id` null; those entries block automatic
upload until a resumable session is supplied.

Known remote and session IDs are stored even when uploading or processing fails,
so retries reuse the remote deposition. A registry row identifies a deposition;
the manifest's step status records whether the upload finished successfully.
Lookup failures stop execution before upload. Save failures stop the batch with
a nonzero exit status after saving the recovery details in the manifest; reconcile
the registry using those details before retrying. Dry runs skip registry access
and discover files without creating or resuming a session. Apply the schema before
using the deposit command:

```bash
uv run deposition-toolkit ledger upgrade
```

## CCD finalisation safety

`ccd-finalise` is intentionally non-destructive by default.

- It does not allocate new CCD codes unless `--allocate-codes` is passed.
- It does not commit to CVS unless `--commit-mode dry-run` or `--commit-mode prompt` is passed.
- `--commit-mode prompt` requires an interactive confirmation in the finalizer.

If CCD codes were allocated once, reuse the generated `allocated_ccd_codes.csv`
with `--code-map-file` on later reruns to avoid consuming additional IDs.

## Outputs

Most command outputs are written under `<work-dir>/results`:

- `results/submission_report/`
- `results/logs/`
- `results/ccd/stage_1/`
- `results/ccd/stage_2/`
- `results/ccd/final/`

Per-structure state is always recorded in:

```text
<structure-dir>/manifest.json
```

## Adding a command

1. Create `src/deposition_toolkit/commands/<new_command>.py`.
2. Implement `register(subparsers)` and `run(args)`.
3. Import the command module in `src/deposition_toolkit/commands/__init__.py`.
4. Add the module to `COMMAND_MODULES`.
5. Put reusable stage logic under `src/stages/<stage_name>/`.

Keep CLI parsing in the command module and keep reusable processing logic in the
stage module. This keeps the command surface easy to scan while making stages
importable from tests or future orchestration code.

### Ligand instance tracking and renaming

`ccd-creation` records a top-level `ccd_ligands` list in each structure's
`manifest.json`. Each entry links the generated filename (for example,
`LIG__001.cif`) to its source coordinate filename and data block, the original
component ID, the exact `_atom_site.id` values, and the residue identifiers
(including alternate conformation and model number). Atom IDs are stored as
strings, so they need not be consecutive integers.

Annotation preserves those filenames. `ccd-finalise` matches its per-ligand
results by filename and adds `CCD` plus finalisation metadata to each entry.
Different instances can receive different codes; chemically identical instances
can share a code. CSV reports remain available for review.

`lig-rename <work-dir>` reads these manifest mappings directly. It parses the CIF
and updates the recorded atoms' `label_comp_id` and `auth_comp_id`, component
definitions, and matching nonpolymer scheme, entity, connection, and anisotropic
atom references. Free text is preserved. Both original and enriched coordinate
variants are checked before any files are written. Missing atom IDs, changed
residue identifiers, overlapping mappings, unallocated codes, and ambiguous
references cause a failure instead of a guessed replacement. Repeating a
successful rename leaves the files unchanged.

For existing datasets without instance provenance, rerun `ccd-creation`,
`ccd-annotate`, and `ccd-finalise` before renaming. Existing code allocations can
be reused with `ccd-finalise --code-map-file <previous-ccd_processing_details.csv>`.
An explicit `lig-rename --summary-csv <report>` can override codes by
`ligand_name`, but still requires the manifest's atom-site provenance. Recreating
CCDs or starting finalisation clears earlier mappings so a failed run cannot
silently reuse stale allocations.
