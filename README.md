# deposition-toolkit

Prepare crystallographic deposition datasets, enrich mmCIF metadata, create chemical component dictionaries (CCDs), and upload structures through OneDep.

## Setup

Use Python **3.13+** and `uv`. From the repository checkout:

```bash
uv sync
cp .env.example .env
```

Edit `.env` for your installation using the [configuration guide](docs/configuration.md), then set up [OneDep identity and authentication](docs/configuration.md#onedep-identity-authentication-and-saved-sessions). Run commands from the repository root so they use this `.env`.

Initialize the ledger schema in your configured PostgreSQL database before the first upload:

```bash
uv run deposition-toolkit ledger upgrade
```

## Example workflow

Run these commands in order, reviewing each stage's results before continuing. Replace the input paths and group ID with yours. Preparation prints the work directory, captured below as `WORK_DIR`.

```bash
WORK_DIR=$(uv run deposition-toolkit prepare-dataset /input/sample.tgz)

uv run deposition-toolkit submission-report "$WORK_DIR" G_01
uv run deposition-toolkit mmcif-validation "$WORK_DIR"
uv run deposition-toolkit analyse-chains "$WORK_DIR"
uv run deposition-toolkit enrich-metadata "$WORK_DIR" /input/metadata.json

uv run deposition-toolkit ccd-creation "$WORK_DIR"
uv run deposition-toolkit ccd-annotate "$WORK_DIR"
uv run deposition-toolkit ccd-finalise "$WORK_DIR" --allocate-codes
uv run deposition-toolkit lig-rename "$WORK_DIR"
uv run deposition-toolkit resolution-cleanup "$WORK_DIR"

uv run deposition-toolkit mmcif-validation "$WORK_DIR"
uv run deposition-toolkit deposit "$WORK_DIR"
```

This example uses the configured tools, databases, and exported depositor identity. For new ligands, `--allocate-codes` consumes IDs from the configured CCD pool. See the [CLI reference](docs/cli.md) for all parameters and the [getting-started guide](docs/getting-started.md) for metadata preparation, previews, and code reuse.

Reports and generated files are under `<work-dir>/results/`; each structure has a `manifest.json` recording its progress. See [files and state](docs/data-and-state.md) for the layout.

## Documentation

Start at the [documentation home](docs/index.md), or go directly to:

- [Usage and configuration](docs/getting-started.md) — setup and a detailed walkthrough.
- [CLI reference](docs/cli.md) — commands, parameters, and defaults.
- [Architecture and dataflow](docs/architecture.md) — module responsibilities and design decisions.
- [Command internals](docs/commands/index.md) — processing logic, diagrams, and edge cases.
- [Recovery](docs/recovery.md) — troubleshooting and retries.
- [Developer guide](docs/development.md) — standalone stages, extensions, migrations, and tests.
- [GitHub Pages](docs/publishing.md) — publishing and maintaining the documentation.
