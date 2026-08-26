#!/usr/bin/env python3
"""
Script to harmonize resolution and statistics values in CIF files.

Operations:
- round: Round resolution values (default: 2 decimal places)
- round_stats: Round values in _reflns and _reflns_shell, accept for resolution (default: 3 decimal places)
- sort_shells: Sort shell rows by high resolution cutoff (lowest first) and update pdbx_ordinal
- compare: Compare cutoff values between _reflns and _refine categories
- sync: Sync low resolution cutoff from _reflns to _refine (if different)

All operations are enabled by default. Which ones run is controlled only by the YAML
config (--config); there are no per-operation command line flags.

Uses gemmi for CIF parsing and writing.
Configuration for input file location and processes run can be loaded from a YAML file,
but only when it is passed explicitly via --config <path.yaml>; otherwise built-in defaults are used.

This script addresses a blocker that can occur for submission to wwPDB: 
aka where the resolution cutoff values in categories _reflns (data collection stats) and _refine (refinement stats) do not match.

Usage:
    # Single file mode
    python resolution_cleanup.py -i <cif_file>
    python resolution_cleanup.py -i <cif_file> --dry-run
    python resolution_cleanup.py -i <cif_file> --config resolution_cleanup_config.yaml
    
    # Batch mode (process all CIF files in directory)
    python resolution_cleanup.py -i <folder> --batch
    python resolution_cleanup.py -i <folder> --batch --config resolution_cleanup_config.yaml
"""

import argparse
import yaml
import sys
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging

try:
    import gemmi
except ImportError:
    print("Error: gemmi is required. Install with: pip install gemmi")
    sys.exit(1)

logger = logging.getLogger(__name__)

# Log files are written to a 'logs' subfolder next to this script
LOG_DIR = Path(__file__).resolve().parent / 'logs'


def quote_command(argv):
    """Formats the command from the terminal as list so that can be captured in a 
    log file, so it can be pasted back into the shell of the current platform.

    Quoting rules differ between platforms. For example, on Linux or MacOS
    logged '-p *_struc.cif' would then glob-expand when pasted, silently 
    becoming a different command, so shlex.join is used there instead.
    """
    if os.name == 'nt':  # Platfom = Windows
        return subprocess.list2cmdline(argv)
    return shlex.join(argv) # Plaform = Linux or MacOS


def setup_logging(verbose=False):
    """Log to the terminal and to a timestamped file in a subfolder logs/.
    
    Per-file detail (captured/restored items, per-item rounding) is logged at
    DEBUG and only appears when verbose is True. Called from main() rather than
    at import time so that importing this module does not create log files.
    
    Returns the log file path, or None if the file could not be created
    (terminal logging still works in that case).
    """
    log_fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
    
    console = logging.StreamHandler()
    console.setFormatter(log_fmt)
    handlers = [console]
    
    log_path = None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"resolution_cleanup_{datetime.now():%Y%m%d_%H%M%S}.log"
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(log_fmt)
        handlers.append(file_handler)
    except OSError as e:
        log_path = None
        print(f"Warning: could not create log file in {LOG_DIR}: {e}", file=sys.stderr)
    
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in handlers:
        root.addHandler(handler)
    
    return log_path

class ResolutionConfig:
    """Resolution category configuration (static CIF dictionary terms)."""

    # Cutoff type constants
    CUTOFF_HIGH_RES = 'high_res_cutoff'
    CUTOFF_LOW_RES = 'low_res_cutoff'
    
    # Overall categories (single values) - used by CutoffComparator
    OVERALL = {
        '_reflns': {'d_resolution_high': 'high_res_cutoff', 'd_resolution_low': 'low_res_cutoff'},
        '_refine': {'ls_d_res_high': 'high_res_cutoff', 'ls_d_res_low': 'low_res_cutoff'},
    }
    
    # Shell categories (loop/table values) - used by ShellSorter
    SHELL = {
        '_reflns_shell': {'d_res_high': 'high_res_cutoff', 'd_res_low': 'low_res_cutoff'},
        '_refine_ls_shell': {'d_res_high': 'high_res_cutoff', 'd_res_low': 'low_res_cutoff'},
    }
    
    # Statistics items to round (both overall in _reflns and per shell in _reflns_shell)
    STATS = {
        '_reflns': [
            'pdbx_CC_half',
            'pdbx_Rmerge_I_obs',
            'pdbx_Rpim_I_all',
            'pdbx_Rrim_I_all',
            'pdbx_netI_over_sigmaI',
            'pdbx_netI_over_av_sigmaI',
            'pdbx_redundancy',
            'percent_possible_obs',
        ],
        '_reflns_shell': [
            'pdbx_CC_half',
            'Rmerge_I_obs',
            'pdbx_Rpim_I_all',
            'pdbx_Rrim_I_all',
            'pdbx_netI_over_sigmaI_obs',
            'meanI_over_sigI_obs',
            'pdbx_redundancy',
            'percent_possible_obs',
        ]
    }
    
    # Combined for operations that need all (e.g., rounding)
    ALL = {**OVERALL, **SHELL}


@dataclass
class BatchConfig:
    """Configuration for batch processing of CIF files."""
    
    coordinate_file_pattern: str = '*_struc.cif'
    output_suffix: str = ''  # Empty = overwrite input
    # Default decimal places for rounding resolution values
    res_decimal_places: int = 2
    # Default decimal places for rounding statistics values
    stats_decimal_places: int = 3
    # Block index to process (0 = first block, only single block processing supported)
    block_index: int = 0  
    do_round: bool = True
    do_round_stats: bool = True
    do_sort: bool = True
    do_compare: bool = True
    do_sync: bool = True
    
    @classmethod
    def from_yaml(cls, path) -> "BatchConfig":
        """Load configuration from an explicit YAML file path (--config CLI argument).
        
        A YAML file is only read when the caller passes a path; otherwise the
        hardcoded defaults above are used. Keys missing from the YAML keep their
        default value.
        
        Raises:
            FileNotFoundError: if the path does not exist
            ValueError: if the file cannot be parsed as a YAML mapping
        """
        path = Path(path)
        
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        if not isinstance(data, dict):
            raise ValueError(f"Config file must contain a YAML mapping: {path}")
        
        ops = data.get('default_operations') or {}
        defaults = cls()
        config = cls(
            coordinate_file_pattern=data.get('coordinate_file_pattern', defaults.coordinate_file_pattern),
            output_suffix=data.get('output_file_suffix', defaults.output_suffix),
            res_decimal_places=data.get('res_decimal_places', defaults.res_decimal_places),
            stats_decimal_places=data.get('stats_decimal_places', defaults.stats_decimal_places),
            block_index=data.get('default_block', defaults.block_index),
            do_round=ops.get('round', defaults.do_round),
            do_round_stats=ops.get('round_stats', defaults.do_round_stats),
            do_sort=ops.get('sort_shells', defaults.do_sort),
            do_compare=ops.get('compare', defaults.do_compare),
            do_sync=ops.get('sync', defaults.do_sync),
        )
        logger.info(f"Loaded config from: {path}")
        return config


class CifEditor:
    """Read and modify CIF files using gemmi."""

    def __init__(self, cif_file):
        """Load CIF file with gemmi."""
        self.cif_path = Path(cif_file)
        if self.cif_path.suffix.lower() not in ('.cif', '.mmcif'):
            raise ValueError(f"Not a CIF file: {self.cif_path.name}")
        self.doc = gemmi.cif.read(str(self.cif_path))
        self.modified = False

    def get_block(self, block_index=0):
        """Get block by index (0 = first). Returns block or None."""
        if 0 <= block_index < len(self.doc):
            return self.doc[block_index]
        logger.warning(f"Block index {block_index} out of range (0-{len(self.doc)-1})")
        return None
    
    def capture_category_data(self, block):
        """Capture all data for categories in ResolutionConfig. Returns dict {cat_name: {item: [values]}}."""
        data = {}
        for cat_name in ResolutionConfig.ALL.keys():
            # Use get_mmcif_category to get all items and values as dict
            cat_data = block.get_mmcif_category(cat_name, raw=True)
            if cat_data:
                data[cat_name] = cat_data
                logger.debug(f"    {cat_name}: captured {len(cat_data)} items")
        return data
    
    def restore_category_data(self, block, data):
        """Restore category data from captured dict. Preserves all items."""
        for cat_name, cat_data in data.items():
            if cat_data:
                block.set_mmcif_category(cat_name, cat_data, raw=True)
                logger.debug(f"    {cat_name}: restored {len(cat_data)} items")
    
    @staticmethod
    def modify_resolution_in_data(data, category_name, item_name, new_value, index=0):
        """Modify a resolution value in captured data dict. Returns True if modified, False if not found."""
        if category_name not in data:
            return False
        cat_data = data[category_name]
        if item_name not in cat_data:
            return False
        values = cat_data[item_name]
        if index < len(values):
            values[index] = str(new_value)
            return True
        return False
    
    def save(self, output_suffix=''):
        """Write changes to CIF file. If output_suffix provided, saves to new file."""
        if self.modified:
            if output_suffix:
                # Create new filename with suffix before extension
                stem = self.cif_path.stem
                ext = self.cif_path.suffix
                output_path = self.cif_path.parent / f"{stem}{output_suffix}{ext}"
            else:
                output_path = self.cif_path
            
            # newline='\n' so output is byte-identical on Windows and Linux.
            # Without it, text mode on Windows rewrites every \n as \r\n, and
            # the same input would produce different files on the two platforms.
            with open(output_path, 'w', newline='\n') as f:
                f.write(self.doc.as_string(gemmi.cif.Style.Pdbx))
            logger.debug(f"Saved: {output_path.name}")
            return True
        return False


class CIFValidator:
    """Validates CIF files by checking item counts before and after transformation."""
    
    @staticmethod
    def capture_category_item_counts(block):
        """Capture item counts for all categories in ResolutionConfig. Returns dict {cat_name: count}."""
        counts = {}
        for cat_name in ResolutionConfig.ALL.keys():
            # Count all items (tags) in this category by iterating block items
            item_count = 0
            prefix = cat_name + '.'
            for item in block:
                if item.loop is not None:
                    # Loop - count tags that match this category
                    for tag in item.loop.tags:
                        if tag.startswith(prefix):
                            item_count += 1
                elif item.pair is not None:
                    # Pair - check if tag matches this category
                    if item.pair[0].startswith(prefix):
                        item_count += 1
            counts[cat_name] = item_count
            if item_count > 0:
                logger.info(f"    {cat_name}: {item_count} items")
        return counts
    
    @staticmethod
    def compare_counts(before_counts, after_counts):
        """Compare item counts before and after transformation.
        
        Returns:
            True if all counts match, False if any items were lost
        """
        items_lost = False
        for cat_name, before_count in before_counts.items():
            after_count = after_counts.get(cat_name, 0)
            if after_count == 0:
                continue  # Skip categories that don't exist after
            if before_count != after_count:
                logger.error(f"    {cat_name}: items lost: {before_count} -> {after_count}")
                items_lost = True
        return not items_lost


class ResolutionRounder:
    """Round resolution values in CIF blocks."""
    
    @staticmethod
    def _round_value_in_data(data, category_name, item_name, decimal_places=2):
        """Round resolution values in captured data dict. Returns count of values rounded."""
        if category_name not in data:
            return 0
        cat_data = data[category_name] # category is the data dict key and value is the dict of items
        if item_name not in cat_data:
            return 0
        
        values = cat_data[item_name] # values is the list of values for this item. both loops and single values are in lists
        count = 0
        for i, value in enumerate(values):
            if value in ('?', '.', None, False):
                continue
            try:
                num_val = float(value)
                new_val = f"{num_val:.{decimal_places}f}"
                if str(value) != new_val:
                    values[i] = new_val
                    count += 1
            except (ValueError, TypeError):
                logger.warning(f"      {category_name}.{item_name}[{i}]: cannot round non-numeric value '{value}'")
                pass
        
        return count
    
    @classmethod
    def round_resolution_values_in_data(cls, data, res_decimal_places=None):
        """Round all resolution values in captured data. Returns total count."""
        if res_decimal_places is None:
            res_decimal_places = BatchConfig.res_decimal_places
        
        total = 0
        for cat_name, items in ResolutionConfig.ALL.items():
            for item_name in items.keys():
                count = cls._round_value_in_data(data, cat_name, item_name, res_decimal_places)
                if count > 0:
                    if count == 1:
                        # Single value - show before/after
                        val = data[cat_name][item_name][0]
                        logger.debug(f"      {cat_name}.{item_name}: rounded to {val}") # If overall resolution cutoffs (single value) report actually values in log file
                    else:
                        logger.debug(f"      {cat_name}.{item_name}: rounded {count} values")
                    total += count
        
        return total
    
    @classmethod
    def round_stats_in_data(cls, data, stats_decimal_places=None):
        """Round statistics items in _reflns and _reflns_shell. Returns total count."""
        if stats_decimal_places is None:
            stats_decimal_places = BatchConfig.stats_decimal_places
        
        total = 0
        for cat_name, items in ResolutionConfig.STATS.items():
            for item_name in items:
                count = cls._round_value_in_data(data, cat_name, item_name, stats_decimal_places)
                if count > 0:
                    logger.debug(f"      {cat_name}.{item_name}: rounded {count} values")
                    total += count
        
        return total


class ShellSorter:
    """Sort shell category rows by resolution (high_res_cutoff), lowest value first."""
    
    @staticmethod
    def _sort_shell_in_data(data, cat_name):
        """Sort rows in captured data by d_res_high (lowest first). Updates pdbx_ordinal.
        
        Returns the number of rows that actually changed position, not the total
        row count, so the figure reflects how much the file was really altered.
        """
        if cat_name not in data:
            return 0
        
        cat_data = data[cat_name]
        
        # Get sort item from config (d_res_high for shell categories)
        items = ResolutionConfig.SHELL.get(cat_name, {})
        sort_item = next((k for k, v in items.items() if v == ResolutionConfig.CUTOFF_HIGH_RES), None)
        if not sort_item or sort_item not in cat_data:
            logger.warning(f"    {cat_name}: sort item {sort_item} not found")
            return 0
        
        sort_values = cat_data[sort_item]
        if len(sort_values) <= 1:
            return 0
        
        # Convert to floats for sorting
        float_values = []
        for val in sort_values:
            try:
                float_values.append(float(val) if val not in ('?', '.', None, False) else float('inf'))
            except (ValueError, TypeError):
                float_values.append(float('inf'))
        
        # Get sorted indices (smallest first)
        sorted_indices = sorted(range(len(float_values)), key=lambda i: float_values[i])
        
        # Check if already sorted
        if sorted_indices == list(range(len(float_values))):
            logger.debug(f"    {cat_name}: already sorted")
            return 0
        
        # Rows whose position changed (sorted_indices[new] is the old index)
        moved = sum(1 for new_pos, old_pos in enumerate(sorted_indices) if new_pos != old_pos)
        
        # Reorder ALL items in this category by sorted indices
        for item_name, values in cat_data.items():
            cat_data[item_name] = [values[i] for i in sorted_indices]
        
        # Update pdbx_ordinal to be 1, 2, 3, ... after sorting
        if 'pdbx_ordinal' in cat_data:
            cat_data['pdbx_ordinal'] = [str(i + 1) for i in range(len(sorted_indices))]
            logger.debug(f"    {cat_name}: moved {moved} of {len(sorted_indices)} rows by {sort_item}, updated pdbx_ordinal")
        else:
            logger.debug(f"    {cat_name}: moved {moved} of {len(sorted_indices)} rows by {sort_item}")
        
        return moved
    
    @classmethod
    def sort_all_in_data(cls, data):
        """Sort all shell categories in captured data. Returns total rows moved."""
        total = 0
        for cat_name in ResolutionConfig.SHELL.keys():
            count = cls._sort_shell_in_data(data, cat_name)
            total += count
        return total


class CutoffComparator:
    """Compare and sync resolution cutoff values between _reflns and _refine."""
    
    @staticmethod
    def _get_cutoff_value(data, cat_name, cutoff_type):
        """Get first available cutoff value from captured data. Returns (item, value) or (None, None)."""
        if cat_name not in data:
            return None, None
        
        cat_data = data[cat_name]
        cat_items = ResolutionConfig.OVERALL.get(cat_name, {})
        items = [k for k, v in cat_items.items() if v == cutoff_type]
        
        for item in items:
            if item in cat_data:
                values = cat_data[item]
                if values and values[0] not in ('?', '.', None, False):
                    try:
                        return item, float(values[0])
                    except (ValueError, TypeError):
                        pass
        return None, None
    
    @staticmethod
    def compare(data, cat1_name='_reflns', cat2_name='_refine'):
        """Compare cutoff values between two categories using captured data. Returns results dict.
        
        This only observes and records; it logs at DEBUG only. The user-facing
        verdict is emitted later by report_cutoffs(), which runs after any sync
        so that it can describe the final state of the file rather than the
        state before syncing.
        """
        results = {}
        
        for cutoff_type in [ResolutionConfig.CUTOFF_HIGH_RES, ResolutionConfig.CUTOFF_LOW_RES]:
            cat1_item, cat1_value = CutoffComparator._get_cutoff_value(data, cat1_name, cutoff_type)
            cat2_item, cat2_value = CutoffComparator._get_cutoff_value(data, cat2_name, cutoff_type)
            
            match = (cat1_value == cat2_value) if (cat1_value is not None and cat2_value is not None) else None
            
            # Direction verdict, low_res only. The refined low-resolution limit may
            # sit inside the measured range (_reflns >= _refine), which is normal;
            # the reverse claims data beyond what was collected, so flag it.
            # Deliberately not applied to high_res, where the comparison inverts.
            verdict = None
            if cutoff_type == ResolutionConfig.CUTOFF_LOW_RES and match is False:
                verdict = 'OK' if cat1_value > cat2_value else 'CHECK'
            
            results[cutoff_type] = {
                'cat1': {'name': cat1_name, 'item': cat1_item, 'value': cat1_value},
                'cat2': {'name': cat2_name, 'item': cat2_item, 'value': cat2_value},
                'match': match,
                'verdict': verdict
            }
            
            if cat1_value is not None and cat2_value is not None:
                status = "MATCH" if match else "DIFFER"
                if verdict:
                    status = f"{status} {verdict}"
                logger.debug(f"  {cutoff_type}: {cat1_name}={cat1_value}, {cat2_name}={cat2_value} [{status}]")
            else:
                logger.debug(f"  {cutoff_type}: incomplete ({cat1_name}={cat1_value}, {cat2_name}={cat2_value})")
        
        return results
    
    @staticmethod
    def report_cutoffs(results, low_res_synced=False):
        """Log one line per unresolved cutoff problem. Returns summary fields.
        
        Called after any sync, so a low_res mismatch that sync repaired is not
        reported at all: the file is consistent by the time it is written, and a
        CHECK on an already-fixed value would be misleading.
        
        Low and high resolution are reported as separate fields, appended to the
        per-file summary line and each present only when that cutoff is still
        outstanding, e.g. ' lowres=CHECK', ' highres=DIFFER',
        ' lowres=OK highres=DIFFER', or '' when there is nothing to report.
        
        Differences are printed at the precision of their own scale: low_res
        gaps run to tens of Angstroms, high_res gaps to thousandths, so a shared
        format would either lose real high_res differences or pad low_res ones.
        """
        fields = {}
        
        for cutoff_type, field, diff_dp in (
            (ResolutionConfig.CUTOFF_HIGH_RES, 'highres', 3),
            (ResolutionConfig.CUTOFF_LOW_RES, 'lowres', 2),
        ):
            r = results.get(cutoff_type)
            if not r:
                continue
            
            c1, c2 = r['cat1'], r['cat2']
            
            if r['match'] is None:
                fields[field] = 'INCOMPLETE'
                logger.warning(f"  {cutoff_type}: INCOMPLETE "
                               f"({c1['name']}={c1['value']}, {c2['name']}={c2['value']})")
                continue
            
            if r['match']:
                continue
            
            # Resolved in this run; sync already logged the values it wrote
            if cutoff_type == ResolutionConfig.CUTOFF_LOW_RES and low_res_synced:
                continue
            
            diff = abs(c1['value'] - c2['value'])
            verdict = r['verdict']
            fields[field] = verdict or 'DIFFER'
            
            label = f"DIFFER {verdict}" if verdict else "DIFFER"
            logger.info(f"  {cutoff_type}: {c1['name']}={c1['value']}, {c2['name']}={c2['value']} "
                        f"diff={diff:.{diff_dp}f} [{label}]")
        
        # lowres first in the summary: it is the field that usually needs action
        ordered = [f"{k}={fields[k]}" for k in ('lowres', 'highres') if k in fields]
        return (' ' + ' '.join(ordered)) if ordered else ''
    
    @staticmethod
    def sync_low_res_in_data(data, results, max_diff=0.5, res_decimal_places=None):
        """Sync low_res_cutoff in captured data. 
        Uses higher value for both. Returns True if synced.
        
        The synced value is written with the same precision as the rounding
        operation, so syncing cannot reintroduce a cutoff mismatch.
        """
        if res_decimal_places is None:
            res_decimal_places = BatchConfig.res_decimal_places
        
        low_res = results.get(ResolutionConfig.CUTOFF_LOW_RES)
        if not low_res:
            return False
        
        if low_res['match'] is True:
            logger.debug(f"    {ResolutionConfig.CUTOFF_LOW_RES} already in sync")
            return False
        
        # cat1='_reflns', cat2='_refine' (from compare() defaults)
        cat1, cat2 = low_res['cat1'], low_res['cat2']
        
        if cat1['value'] is None or cat2['value'] is None:
            logger.debug("    Cannot sync: one or both values are None")
            return False
        
        if cat1['item'] is None or cat2['item'] is None:
            logger.debug("    Cannot sync: one or both items not found")
            return False
        
        diff = abs(cat1['value'] - cat2['value'])
        if diff > max_diff:
            # report_cutoffs() surfaces this as an unresolved DIFFER line with the diff
            logger.debug(f"    Difference {diff:.2f} exceeds threshold {max_diff}. Not syncing.")
            return False
        
        higher_val = max(cat1['value'], cat2['value'])
        new_val = f"{higher_val:.{res_decimal_places}f}"
        
        # Modify in captured data instead of block
        CifEditor.modify_resolution_in_data(data, cat1['name'], cat1['item'], new_val)
        CifEditor.modify_resolution_in_data(data, cat2['name'], cat2['item'], new_val)
        logger.info(f"  {ResolutionConfig.CUTOFF_LOW_RES}: {cat1['name']}={cat1['value']}, "
                    f"{cat2['name']}={cat2['value']} diff={diff:.2f} -> both {new_val} [SYNCED]")
        logger.debug(f"    {cat1['name']}.{cat1['item']}: {cat1['value']} -> {new_val}")
        logger.debug(f"    {cat2['name']}.{cat2['item']}: {cat2['value']} -> {new_val}")
        return True


class ResolutionWorkflow:
    """Main workflow for resolution editing."""
    
    @staticmethod
    def process(cif_file, do_round=False, do_round_stats=False, do_sort=False, do_compare=False, do_sync=False,
                dry_run=False, res_decimal_places=None, stats_decimal_places=None, block_index=None, output_suffix=''):
        """Process a CIF file with specified operations.
        
        Emits a single INFO summary line per file; the per-item detail behind it
        is logged at DEBUG (enable with --verbose).
        """
        name = Path(cif_file).name
        logger.debug(f"Processing: {cif_file}")
        
        try:
            editor = CifEditor(cif_file)
        except Exception as e:
            logger.error(f"{name}: error reading CIF file: {e}")
            return {'rounded': 0, 'sorted': 0, 'synced': 0, 'error': True}
        
        block = editor.get_block(block_index)
        if not block:
            logger.error(f"{name}: no block at index {block_index}")
            return {'rounded': 0, 'sorted': 0, 'synced': 0, 'error': True}
        
        # Capture all category data BEFORE any transformations
        logger.debug("  Capturing category data:")
        data = editor.capture_category_data(block)
        
        results = {'rounded': 0, 'synced': 0, 'sorted': 0, 'error': False}
        stats_count = 0
        
        # Resolve precision once, with `is None` rather than `or`, so that a
        # configured 0 is honoured instead of being treated as falsy. The same
        # value is logged and passed on, so the log cannot disagree with what
        # was actually written.
        res_dp = res_decimal_places if res_decimal_places is not None else BatchConfig.res_decimal_places
        stats_dp = stats_decimal_places if stats_decimal_places is not None else BatchConfig.stats_decimal_places
        
        # Round values in captured data
        if do_round:
            logger.debug(f"Rounding resolution values to {res_dp} decimal places:")
            logger.debug(f"  Block: {block.name}")
            results['rounded'] = ResolutionRounder.round_resolution_values_in_data(data, res_dp)
        
        # Round statistics items in _reflns_shell
        if do_round_stats:
            logger.debug(f"Rounding statistics values to {stats_dp} decimal places:")
            stats_count = ResolutionRounder.round_stats_in_data(data, stats_dp)
            results['rounded'] += stats_count
        
        # Sort shell categories in captured data
        if do_sort:
            logger.debug("  Sorting shell categories by d_res_high:")
            results['sorted'] = ShellSorter.sort_all_in_data(data)
        
        # Compare and/or sync cutoffs
        cutoff_status = ''
        if do_compare or do_sync:
            logger.debug(f"  Comparing cutoffs in block: {block.name}")
            compare_results = CutoffComparator.compare(data)
            
            # Sync first, then report, so the verdict describes the file as it
            # will be written rather than as it was read
            low_res_synced = False
            if do_sync:
                low_res_synced = CutoffComparator.sync_low_res_in_data(
                    data, compare_results, res_decimal_places=res_dp)
                if low_res_synced:
                    results['synced'] += 1
            
            cutoff_status = CutoffComparator.report_cutoffs(compare_results, low_res_synced)
        
        # Restore all category data with modifications
        if results['rounded'] > 0 or results['synced'] > 0 or results['sorted'] > 0:
            logger.debug("  Restoring modified category data:")
            editor.restore_category_data(block, data)
            editor.modified = True
        
        # Save changes
        saved = not dry_run and editor.modified and editor.save(output_suffix)
        if editor.modified:
            outcome = 'saved' if saved else 'not written'
        else:
            outcome = 'no change'
        
        res_count = results['rounded'] - stats_count
        logger.info(f"{name}: res={res_count} stats={stats_count} sorted={results['sorted']} "
                    f"synced={results['synced']}{cutoff_status} [{outcome}]")
        
        return results
    
    @classmethod
    def process_batch(cls, input_dir, pattern='*_updated.cif', do_round=False, do_round_stats=False,
                      do_sort=False, do_compare=False, do_sync=False, dry_run=False, res_decimal_places=None,
                      stats_decimal_places=None, block_index=None, output_suffix=''):
        """Process all CIF files matching pattern. Returns (processed, skipped)."""
        input_dir = Path(input_dir)
        cif_files = sorted(input_dir.rglob(pattern))
        
        if not cif_files:
            logger.warning(f"No files matching '{pattern}' found in {input_dir}")
            return 0, 0
        
        logger.info(f"Found {len(cif_files)} files matching '{pattern}'")
        logger.info("=" * 60)
        
        processed = 0
        skipped = 0
        total_rounded = 0
        total_sorted = 0
        total_synced = 0
        
        for cif_file in cif_files:
            results = cls.process(
                cif_file,
                do_round=do_round,
                do_round_stats=do_round_stats,
                do_sort=do_sort,
                do_compare=do_compare,
                do_sync=do_sync,
                dry_run=dry_run,
                res_decimal_places=res_decimal_places,
                stats_decimal_places=stats_decimal_places,
                block_index=block_index,
                output_suffix=output_suffix
            )
            
            if results.get('error'):
                skipped += 1
            else:
                processed += 1
                total_rounded += results['rounded']
                total_sorted += results['sorted']
                total_synced += results['synced']
        
        logger.info("=" * 60)
        logger.info(f"Batch complete: {processed} processed, {skipped} skipped")
        logger.info(f"Total values rounded: {total_rounded}, rows sorted: {total_sorted}, value pairs synced: {total_synced}")
        
        return processed, skipped


def confirm_overwrite(targets, assume_yes=False):
    """Ask the user to confirm irreversible in-place overwriting.

    Called only when no output suffix is set and the run is not a dry run,
    because in that case the input files are rewritten with no backup.

    Returns True if the run may proceed. Refuses when stdin is not interactive
    unless assume_yes is set, so an unattended run cannot silently overwrite
    while waiting on a prompt nobody can answer.
    """
    count = len(targets)
    noun = "file" if count == 1 else "files"
    logger.warning(f"No output suffix provided: {count} input {noun} will be OVERWRITTEN in place, with no backup.")
    for path in targets[:5]:
        logger.warning(f"    {path.name}")
    if count > 5:
        logger.warning(f"    ... and {count - 5} more")

    if assume_yes:
        logger.warning("Proceeding: --yes was given.")
        return True

    if not sys.stdin.isatty():
        logger.error("Refusing to overwrite without confirmation (stdin is not interactive). "
                     "Pass --output-suffix to write new files, --yes to confirm, or --dry-run to preview.")
        return False

    try:
        reply = input(f"Overwrite {count} {noun} in place? [y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        logger.error("Aborted: no confirmation received.")
        return False

    if reply not in ('y', 'yes'):
        logger.error(f"Aborted: expected 'y' or 'yes', got '{reply}'. No files were changed.")
        return False

    logger.info("Confirmed: overwriting in place.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Edit resolution values in CIF files."
    )
    parser.add_argument("-i", "--input", required=True, 
                        help="Input CIF file or directory (with --batch)")
    parser.add_argument("--batch", action="store_true",
                        help="Process all CIF files in directory tree")
    parser.add_argument("--pattern", "-p",
                        help="Glob pattern for batch mode (default: from config)")
    parser.add_argument("-o", "--output-suffix",
                        help="Suffix for output filenames, e.g. _test (default: from config; "
                             "empty overwrites the input file)")
    parser.add_argument("--config", "-c",
                        help="Path to YAML config file (default config is read unless this is given)")
    parser.add_argument("--block", type=int,
                        help="Block index to process (default: 0, first block)")
    parser.add_argument("-d", "--dry-run", action="store_true",
                        help="Show changes without writing to file")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip the confirmation prompt shown before overwriting files in place")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Log per-item detail (DEBUG level) instead of one line per file")
    args = parser.parse_args()
    
    log_path = setup_logging(verbose=args.verbose)
    if log_path:
        logger.info(f"Logging to: {log_path}")
    
    # Record the invocation so a log can be traced back to the exact command
    logger.info(f"Command: {quote_command([sys.executable, *sys.argv])}")
    logger.info(f"Working directory: {Path.cwd()}")
    
    # Load config only when explicitly requested with --config
    if args.config:
        try:
            config = BatchConfig.from_yaml(args.config)
        except Exception as e:
            logger.error(f"Failed to load --config: {e}")
            return 1
    else:
        config = BatchConfig()
        logger.info("No --config given. Using built-in defaults.")
    
    # Operations and rounding precision come from config only; CLI overrides the rest
    block_index = args.block if args.block is not None else config.block_index
    pattern = args.pattern if args.pattern else config.coordinate_file_pattern
    # `is not None` so an explicit -o "" still means "overwrite in place"
    output_suffix = args.output_suffix if args.output_suffix is not None else config.output_suffix
    
    # State the mode up front, and say where output goes, so a log is never
    # ambiguous about whether it describes real changes on disk
    logger.info("=" * 60)
    if args.dry_run:
        logger.info("DRY RUN - no files will be written")
    elif output_suffix:
        logger.info(f"LIVE RUN - writing new files with suffix '{output_suffix}'")
    else:
        logger.info("LIVE RUN - OVERWRITING input files in place")
    
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        logger.error(f"Input not found: {input_path}")
        return 1
    
    # Batch or single file mode
    if args.batch:
        if not input_path.is_dir():
            logger.error("--batch requires a directory as input")
            return 1
        
        logger.info("=" * 60)
        logger.info("Resolution Cleanup - Batch Mode")
        logger.info("=" * 60)
        
        # Confirm before the first write; the same glob process_batch will use
        if not output_suffix and not args.dry_run:
            targets = sorted(input_path.rglob(pattern))
            if not targets:
                logger.warning(f"No files matching '{pattern}' found in {input_path}")
                return 0
            if not confirm_overwrite(targets, assume_yes=args.yes):
                return 1
        
        processed, skipped = ResolutionWorkflow.process_batch(
            input_path,
            pattern=pattern,
            do_round=config.do_round,
            do_round_stats=config.do_round_stats,
            do_sort=config.do_sort,
            do_compare=config.do_compare,
            do_sync=config.do_sync,
            dry_run=args.dry_run,
            res_decimal_places=config.res_decimal_places,
            stats_decimal_places=config.stats_decimal_places,
            block_index=block_index,
            output_suffix=output_suffix
        )
    else:
        if input_path.is_dir():
            logger.error("Input is a directory. Use --batch for directory processing.")
            return 1
        
        if not output_suffix and not args.dry_run:
            if not confirm_overwrite([input_path], assume_yes=args.yes):
                return 1
        
        results = ResolutionWorkflow.process(
            input_path,
            do_round=config.do_round,
            do_round_stats=config.do_round_stats,
            do_sort=config.do_sort,
            do_compare=config.do_compare,
            do_sync=config.do_sync,
            dry_run=args.dry_run,
            res_decimal_places=config.res_decimal_places,
            stats_decimal_places=config.stats_decimal_places,
            block_index=block_index,
            output_suffix=output_suffix
        )
    
    return 0


if __name__ == "__main__":
    exit(main())

