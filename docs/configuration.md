---
title: Configuration
---

[Documentation home](index.md) · [Getting started](getting-started.md) · [CLI reference](cli.md)

# Configuration

## Settings sources

`config.Settings` uses Pydantic Settings and reads `.env` from the **current working directory**, not automatically from the repository or dataset. Exported environment values override `.env`; implemented CLI overrides take precedence over those settings. `get_settings()` caches the result per process. Unknown `.env` keys are ignored. Ports and positive timeout fields are validated when settings load, including during parser construction.

```mermaid
flowchart LR
    A[Built-in Settings defaults] --> B[Working-directory .env]
    B --> C[Exported environment]
    C --> D[Supported CLI override]
    D --> E[Stage parameters]
```

This hierarchy describes toolkit settings. `WWPDB_*` and the OneDep library use separate configuration paths, explained below. Pydantic loading `.env` does not export its entries into `os.environ`.

## Settings by stage

All paths below are deployment inputs; they are not downloaded or provisioned by the configuration loader.

| Command | Settings it needs | Why / alternative |
| --- | --- | --- |
| `prepare-dataset` | `DEPOSITION_TOOLKIT_WORKROOT` | Destination root; `--work-dir` chooses a specific destination, `--work-root` changes the root |
| `submission-report`, `analyse-chains`, `ccd-creation`, `lig-rename` | No stage-specific environment settings | Work-directory files and normal Python dependencies |
| `mmcif-validation` | `MMCIF_DICTIONARY_PATH` | Local mmCIF dictionary; override with `--dictionary` |
| `enrich-metadata` | `MMCIF_DICTIONARY_PATH` | Output validation; override with `--dictionary`, or disable with `--skip-validation`; CCD enrichment also uses network/cache |
| `ccd-annotate` | `TOOLS_ROOT`, optional `ANNOTATE_BIN` | Executable, helper tools, libraries and license; CLI executable override `--annotate-bin` |
| `ccd-finalise` descriptor lookup | `CCD_DB_HOST/PORT/USER/PASSWORD/TIMEOUT` | Existing OneDep MySQL descriptors; `--existing-descriptors-file` avoids MySQL |
| `ccd-finalise --allocate-codes` | `UNUSED_CCD_FILE_PATH`, `CCD_CVS_PATH` | Consume pool IDs and check their existing component paths |
| `ccd-finalise` CVS mode | `CCD_CVS_PATH` | Checkout destination; `--cvs-path` overrides the commit helper only, not allocation checks |
| `resolution-cleanup` | No environment setting | Optional YAML via `--config`; otherwise `BatchConfig` defaults |
| `ledger`, real `deposit` | `POSTGRES_HOST/PORT/USER/PASSWORD`, optional `POSTGRES_DATABASE` | Toolkit registry; `deposit --dry-run` bypasses it |
| New real deposition | `WWPDB_EMAIL`, `WWPDB_USERS` or CLI equivalents; OneDep authentication | Depositor identity and remote authentication |
| Standalone validation dashboard | `DEPOSITION_ROOT` | Existing OneDep output tree; `--base-path` overrides it |

## Toolkit settings reference

Defaults come from `src/deposition_toolkit/config.py`; `.env.example` is the editable template.

| Variable | Default / example |
| --- | --- |
| `DEPOSITION_TOOLKIT_WORKROOT` | `/nfs/public/services/onedep_gpfs/deposition-toolkit-processing` |
| `MMCIF_DICTIONARY_PATH` | `<default workroot>/mmcif_pdbx_v50.dic` as a fixed default path; changing workroot does not derive a new dictionary path |
| `DEPOSITION_ROOT` | `/nfs/public/services/onedep_gpfs/data/production/deposit` |
| `UNUSED_CCD_FILE_PATH` | `/nfs/public/services/onedep_gpfs/data/production/reference/id_codes/unusedCodes.lst` |
| `CCD_CVS_PATH` | `/nfs/public/services/onedep_gpfs/data/production/reference/components/ligand-dict-v3` |
| `TOOLS_ROOT` | `/nfs/public/services/onedep_gpfs/resources/tools/tools-centos-7.9` |
| `ANNOTATE_BIN` | Unset; derives `<TOOLS_ROOT>/packages/cc-tools-v2/bin/annotateComp` |
| `CCD_DB_HOST` | `mysql-pdbe-onedep-prod.ebi.ac.uk` |
| `CCD_DB_PORT` | `4437` |
| `CCD_DB_USER` | `pdberead` |
| `CCD_DB_PASSWORD` | Empty in code; example file has `place-holder`; supply the real credential for MySQL |
| `CCD_DB_TIMEOUT` | `30` seconds; positive integer |
| `POSTGRES_HOST` | `pgsql-hlvm-172` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_USER` | `pdb_dep` |
| `POSTGRES_PASSWORD` | `place-holder`; replace for real ledger access |
| `POSTGRES_DATABASE` | Unset/blank uses `POSTGRES_USER` |

Passwords are `SecretStr` inside toolkit settings. Database adapters unwrap them only when creating client connections. `.env` is ignored by Git; do not include real credentials in documentation or command examples.

## Annotation tools

`default_environment()` builds an environment for the subprocess with `CC_TOOLS`, `OE_DIR`, `OE_LICENSE`, `CACTVS_DIR`, `CORINA_DIR`, `BABEL_DIR`, `BABEL_DATADIR`, `INCHI_DIR`, `BABEL_LIB`, and `LOCAL_APPS_LIB` beneath `TOOLS_ROOT`. It prepends OpenBabel and local library directories to `LD_LIBRARY_PATH`.

An executable override alone does not relocate those helper paths or the OpenEye license. Use a compatible tools tree and runtime. The package does not install annotateComp or its companion tools/licenses.

## OneDep identity, authentication and saved sessions

The toolkit reads `WWPDB_EMAIL` and comma-separated `WWPDB_USERS` directly from the process environment. **Putting them only in the toolkit `.env` does not populate these defaults.** Use `--email` / `--users`, or export them:

```bash
export WWPDB_EMAIL=depositor@example.org
export WWPDB_USERS=0000-0002-8612-9539
```

`deposit_structure()` delegates authentication and session configuration to `onedep_lib.config.DepositConfig.load()`. The installed library inspected for these docs uses `~/.config/onedep/config.toml`, a `[default]` section for ordinary settings, and host-specific `[auths.…]` token entries. It also reads process variables `ONEDEP_ACCESS_TOKEN`, `ONEDEP_REFRESH_TOKEN`, `ONEDEP_HOSTNAME`, `ONEDEP_SSL_VERIFY`, `ONEDEP_REDIRECT`, and `ONEDEP_SCHEMA_URL`. Obtain credentials through the OneDep setup used by your installation.

Local sessions default to `~/.onedep/sessions`; `session_dir` can be configured through the library. The toolkit does not expose a `--session-dir` flag. Resume needs those local files and the saved UUID, not just the remote deposition ID. Library configuration behavior is dependency-owned and should be rechecked when upgrading `onedep-lib`.

## Resolution cleanup YAML

Only an explicit `--config /path/cleanup.yml` loads YAML. Missing keys keep defaults:

```yaml
coordinate_file_pattern: '*_struc.cif'
output_file_suffix: ''
res_decimal_places: 2
stats_decimal_places: 3
default_block: 0
default_operations:
  round: true
  round_stats: true
  sort_shells: true
  compare: true
  sync: true
```

CLI `--pattern`, `--output-suffix`, and `--block` override corresponding YAML values. Block indices are zero-based. Detailed transformations are in [cleanup internals](commands/resolution-cleanup.md).
