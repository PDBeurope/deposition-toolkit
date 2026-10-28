---
title: Command internals
---

[Documentation home](../index.md) · [CLI reference](../cli.md) · [Architecture](../architecture.md)

# Command internals

Each page separates wrapper responsibilities from stage logic and explains inputs, outputs, decisions, and failure behavior. Module paths identify the implementation rather than a proposed design.

| Command | Processing type | Inspect |
| --- | --- | --- |
| `prepare-dataset` | Archive/filesystem operations | [Copy, extraction and discovery](prepare-dataset.md) |
| `submission-report` | CIF classification, cross-structure checks, PDF generation | [Report decisions](submission-report.md) |
| `mmcif-validation` | Adapter around Python dictionary validator | [Validation and severity](mmcif-validation.md) |
| `analyse-chains` | Gemmi model/entity inspection | [Chain counting](analyse-chains.md) |
| `enrich-metadata` | Ordered transformations, JSON overlay, cached HTTP lookups | [Enrichment details](enrich-metadata.md) |
| `ccd-creation` | Restraint/coordinate joins and RDKit chemistry | [Per-instance creation](ccd-creation.md) |
| `ccd-annotate` | External executable in two rounds | [Exact subprocess operations](ccd-annotate.md) |
| `ccd-finalise` | Batch chemistry matching, deduplication and optional side effects | [Finalisation decision tree](ccd-finalise.md) |
| `lig-rename` | Provenance-checked CIF edits | [Identity and reference checks](lig-rename.md) |
| `resolution-cleanup` | Numeric transformations and comparisons | [Cleanup sequence](resolution-cleanup.md) |
| `deposit` | Remote workflow with local sessions and database recovery | [Deposition lifecycle](deposit.md) |
| `ledger` | Database connectivity/schema management | [Transactions and migrations](ledger.md) |

Common to most wrappers: excluded structures get `not applicable`; otherwise a command runs without checking earlier step statuses. See [shared state rules](../data-and-state.md).
