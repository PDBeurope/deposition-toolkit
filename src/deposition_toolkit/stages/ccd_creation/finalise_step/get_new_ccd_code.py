#!/usr/bin/env python
"""
Get the next unused CCD ligand code.

Reads a file of pre-allocated unused codes, checks each against the
chemical component sandbox to find the first one not yet in use,
then removes checked codes from the file.

Usage:
    python get_new_ccd_code.py                  # just print the next code
    python get_new_ccd_code.py --dry-run        # don't modify the file
    python get_new_ccd_code.py --show-remaining  # show how many codes are left
"""

import os
import sys

from deposition_toolkit.config import get_settings


def get_ccd_path(ccd_id: str) -> str | None:
    """Returns full path to the CCD"""
    if not ccd_id:
        return None
    ccd_id = ccd_id.upper()

    if len(ccd_id) > 3:
        hash_key = ccd_id.upper()[-2:]
    elif len(ccd_id) > 0:
        hash_key = ccd_id.upper()[0]
    else:
        hash_key = ""

    file_path = os.path.join(get_settings().ccd_cvs_path.expanduser(), hash_key, ccd_id, f"{ccd_id}.cif")

    return file_path

def get_next_unused_code(site_id="PDBE_PROD", dry_run=False, verbose=False):
    file_path = get_settings().unused_ccd_file_path.expanduser()

    if not os.path.isfile(file_path):
        print(f"ERROR: Unused CCD file not found: {file_path}", file=sys.stderr)
        return None, 0

    # Read the list of unused codes
    with open(file_path) as f:
        all_codes = [line.strip() for line in f if line.strip()]

    if verbose:
        print(f"Unused CCD file: {file_path}", file=sys.stderr)
        print(f"Codes in file: {len(all_codes)}", file=sys.stderr)

    # file path for that component (e.g. /path/to/sandbox/A/ATP/ATP.cif).
    # If the file exists on disk, that code is already taken — skip it.
    # The first code whose file does NOT exist is available for use.
    code = ""
    idx = 0
    for idx, cc_id in enumerate(all_codes, start=1):
        component_path = get_ccd_path(cc_id)
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
