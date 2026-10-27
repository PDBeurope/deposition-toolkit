from __future__ import annotations

from . import (
    analyse_chains,
    ccd_annotate,
    ccd_creation,
    ccd_finalise,
    deposit,
    enrich_metadata,
    lig_rename,
    ledger,
    mmcif_validation,
    prepare_dataset,
    resolution_cleanup,
    submission_report,
)


COMMAND_MODULES = (
    ledger,
    prepare_dataset,
    submission_report,
    mmcif_validation,
    analyse_chains,
    enrich_metadata,
    ccd_creation,
    ccd_annotate,
    ccd_finalise,
    lig_rename,
    resolution_cleanup,
    deposit,
)
