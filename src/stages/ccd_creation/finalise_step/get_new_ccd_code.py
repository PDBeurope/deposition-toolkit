#!/usr/bin/env python
"""
Get the next unused CCD ligand code.

Reads a file of pre-allocated unused codes, checks each against the
chemical component sandbox to find the first one not yet in use,
then removes checked codes from the file.

Minimal dependencies: only needs wwpdb.utils.config and wwpdb.io.locator.

Usage:
    python get_new_ccd_code.py                  # just print the next code
    python get_new_ccd_code.py --dry-run        # don't modify the file
    python get_new_ccd_code.py --show-remaining  # show how many codes are left
"""

import os
import sys


def get_next_unused_code(site_id="PDBE_PROD", dry_run=False, verbose=False):
    from wwpdb.utils.config.ConfigInfoApp import ConfigInfoAppCommon
    from wwpdb.utils.config.ConfigInfo import ConfigInfo
    from wwpdb.io.locator.ChemRefPathInfo import ChemRefPathInfo

    # Get the file path containing unused codes
    ci_app = ConfigInfoAppCommon(siteId=site_id)
    file_path = ci_app.get_unused_ccd_file()

    if not os.path.isfile(file_path):
        print(f"ERROR: Unused CCD file not found: {file_path}", file=sys.stderr)
        return None, 0

    # Read the list of unused codes
    with open(file_path) as f:
        all_codes = [line.strip() for line in f if line.strip()]

    if verbose:
        print(f"Unused CCD file: {file_path}", file=sys.stderr)
        print(f"Codes in file: {len(all_codes)}", file=sys.stderr)

    # Set up ChemRefPathInfo to check if a component already exists in the
    # CVS sandbox. The sandbox is a local checkout of the CVS repository that
    # holds all CCD component CIF files — both released and in-progress.
    # This check is essential because a code may not yet be in the public CCD
    # but could already be assigned by another annotator working in parallel.
    # Without this check, two annotators could end up using the same code.
    ci = ConfigInfo(siteId=site_id)
    crpi = ChemRefPathInfo(configObj=ci)

    # Walk through the list of candidate codes and check each one against
    # the sandbox. crpi.getFilePath(ccId, "CC") returns the expected local
    # file path for that component (e.g. /path/to/sandbox/A/ATP/ATP.cif).
    # If the file exists on disk, that code is already taken — skip it.
    # The first code whose file does NOT exist is available for use.
    code = ""
    idx = 0
    for idx, cc_id in enumerate(all_codes, start=1):
        component_path = crpi.getFilePath(cc_id, "CC")
        exists = os.path.isfile(component_path) if component_path else False

        if verbose:
            status = "EXISTS" if exists else "AVAILABLE"
            print(f"  {cc_id} -> {component_path}  [{status}]", file=sys.stderr)

        if not exists:
            code = cc_id
            break

    # Remove all checked codes (both used and the one we just claimed)
    # from the file, so they won't be offered again next time.
    # This is the allocation mechanism — consuming a code from the file
    # prevents anyone else from getting the same code.
    remaining = all_codes[idx:]
    if not dry_run and code:
        with open(file_path, "w") as f:
            f.write("\n".join(remaining) + "\n" if remaining else "")
        if verbose:
            print(f"Updated file: {len(remaining)} codes remaining", file=sys.stderr)
    elif dry_run and verbose:
        print(f"Dry run: file not modified ({len(remaining)} would remain)", file=sys.stderr)

    return code, len(remaining)


def main():
    dry_run = "--dry-run" in sys.argv
    show_remaining = "--show-remaining" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    code, remaining = get_next_unused_code(
        dry_run=dry_run,
        verbose=verbose or show_remaining,
    )

    if code:
        print(code)
    else:
        print("ERROR: No unused codes available", file=sys.stderr)
        sys.exit(1)

    if show_remaining:
        print(f"Remaining codes: {remaining}", file=sys.stderr)


if __name__ == "__main__":
    main()
