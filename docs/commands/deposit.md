---
title: deposit internals
---

[All commands](index.md) · Previous: [cleanup](resolution-cleanup.md) · [Configuration](../configuration.md#onedep-identity-authentication-and-saved-sessions) · [Recovery](../recovery.md)

# deposit

**Wrapper:** `commands/deposit.py` → **stage:** `stages/deposit/deposition.py`, `deposit_structure()` → `onedep-lib`.

The stage handles remote/session behavior; the wrapper owns registry lookup, manifest status and database persistence. File patterns and dataset identity are specified in [data and state](../data-and-state.md).

## Command and stage sequence

```mermaid
flowchart TD
    A[Active structure and manifest] --> S{Deposition done, excluding dry-run results?}
    S -->|Yes| T[Skip; leave manifest and logs unchanged]
    S -->|No| B{Dry-run?}
    B -->|Yes| C[Discover coordinate and SF only; return]
    B -->|No| D[Registry lookup by dataset and structure_id]
    D -->|Lookup error or entry without session| X[Stop batch before this upload]
    D --> E[Pass saved session_id or None to stage]
    E --> F[Discover first matching coordinate and SF]
    F --> G{Saved session?}
    G -->|Yes| H[Resume local session; require remote ID]
    G -->|No| I[Require email/users; initialize and set experiment]
    H --> J[Check authentication key]
    I --> J
    J --> K[Register files and run file checks]
    K --> L[For replacements: require all checks, then remove old registrations]
    L --> M[Require required-files check to pass]
    M --> N[Upload and optionally poll]
    N --> O[Return DepositionResult, including known IDs on failure]
    O --> P[Wrapper saves manifest recovery IDs and step]
    P --> Q[Upsert registry if both IDs known]
    Q -->|Save error| R[Stop batch; reconcile before retry]
```

## File checks and replacement behavior

The stage registers the coordinate as `MMCIF_COORD` and reflections as `CRYSTAL_STRUC_FACTORS`. It records coordinate mmCIF, `_atom_site`, and file-type checks, plus SF mmCIF and file-type checks. Authentication failure or missing required files stops upload.

For a resumed session, every replacement check must pass before old file registrations of those two types are removed. Failed replacement checks remove newly registered files and preserve old registrations. Saved depositor and experiment settings are retained; new CLI values do not redefine them.

For a **new** session, individual file-check results are recorded but are not all explicitly gated before upload; `check_required_files().ok` is the final explicit gate. These paths therefore have different validation guarantees. Remote validation is not a substitute for inspecting earlier toolkit results, which are not prerequisites enforced by this command.

## Progress and logs

The wrapper displays a live progress bar and lists of deposited, failed, pending and current structures. Current activity includes file checks, submission and polling, with elapsed time refreshed even while a call is blocked. `--no-poll` labels successful results as submitted, and `--dry-run` labels them as dry-run passes. These lists describe the current invocation, not earlier runs. Registry failures stop the batch with unattempted structures still pending. Interrupted runs mark the active item as interrupted in the display.

Normal activity, selected input paths, file-check results, polling statuses and final results are logged to both `results/logs/deposition/<structure-relative-path>.log` (the manifest `output`) and the structure's `run.log` (the manifest `log`). Failure diagnostics and recovery details are retained as before. A successful result therefore has useful diagnostics even when no exception occurred. Live progress itself is written to the original terminal stream rather than swallowed by log capture.

## Polling semantics

After `dep.deposit()` returns, the stage marks status `done`. With polling enabled (default), it requests status up to `64` times at `5` second intervals. A `DepositError` or status string `error` changes status to `failed`; `finished` ends polling successfully. Exhausting the poll budget leaves the current `done` status. `--no-poll` returns after upload without waiting for remote completion.

Consequently, toolkit `done` means the upload path returned without a detected failure; it does not always establish that remote processing finished. Inspect `final_status` and the remote deposition when needed.

## Persistence and recovery

`DepositionResult` carries identity, status, selected files, file IDs, check reports, final status, errors and known remote/session IDs. `to_record()` returns `dep_id`, `dataset`, `structure_id`, `session_id` as plain strings.

The wrapper stores the result under `steps.deposition`. If a remote ID is known, it also saves top-level `deposition_id`/`session_id` and `deposition_record`. When both IDs exist it upserts the registry even if processing failed, allowing retries to find the same remote deposition. A save error stops the batch after manifest recovery details are written. Lookup errors also stop the batch; earlier structures may already have uploaded.

Rerunning `deposit` skips active structures whose `steps.deposition.status` is `done`, except results marked with `reports.dry_run`. Before processing, it prints the number skipped and the number remaining to deposit (or check with `--dry-run`). Skipped structures receive no registry lookup or upload, and their manifests and logs are retained. If all active structures are already deposited, the command succeeds without uploading anything. Failed, unfinished and previously unrun structures remain eligible; the progress display and final summary cover only entries attempted in the current invocation.

**Dry-run:** bypasses PostgreSQL and OneDep imports/config/authentication/session creation; it requires matching coordinate/SF files and writes a result/log/manifest. It does not validate file content.

**Edges:** missing local session files cannot be repaired from a remote ID alone. The wrapper uses the registry rather than top-level manifest IDs for session lookup. A created session with no remote ID is not persisted to the registry by this wrapper; inspect local session storage on such failures. Remote upload and registry upsert are not one atomic operation. Retried entries with saved sessions replace remote files; completed entries are skipped.

## Python and standalone entry points

For integrations, `commands.deposit.run(args, session_lookup=lookup)` accepts a callable with the contract `lookup(dataset, structure_id) -> session_id | None`. This overrides registry lookup; normal result persistence still runs.

The stage API `deposit_structure(structure_dir, session_id=...)` resumes the supplied local OneDep session. Omitting `session_id` creates a new deposition. The equivalent standalone invocation for a replacement upload is:

```bash
uv run python -m deposition_toolkit.stages.deposit.deposition /path/to/structure --session-id SAVED_SESSION_UUID
```

Standalone execution leaves toolkit ledger lookup/upsert and wrapper manifest updates to the caller; see [running stages independently](../development.md#running-stages-independently).
