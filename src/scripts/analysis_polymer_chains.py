#!/usr/bin/env python3
"""
Analyze and list polymer chains in mmCIF coordinate files.

This script identifies polymer chains in structure files and outputs analysis, 
which is packaged as a logfile if running in batch-mode.

Usage:
    # Single file - list polymer chains
    python analysis_polymer_chains.py <input.cif>
    
    # Single file - verbose output with residue counts
    python analysis_polymer_chains.py <input.cif> --verbose
    
    # Batch mode - process directory
    python analysis_polymer_chains.py /path/to/cif_files/ --auto
    
    # Batch mode with verbose output
    python analysis_polymer_chains.py /path/to/cif_files/ --auto --verbose

Examples:
    python analysis_polymer_chains.py structure.cif
    python analysis_polymer_chains.py structure.cif --verbose
    python analysis_polymer_chains.py /data/structures/ --auto
    python analysis_polymer_chains.py /data/structures/ --auto --verbose

Output:
    Lists polymer chain names found in coordinate files.
    In verbose mode, shows residue counts and composition details.
"""

import argparse
import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from datetime import datetime

try:
    import gemmi
except ImportError:
    print("Error: gemmi is required. Install with: pip install gemmi")
    sys.exit(1)


def setup_logger(directory: Path) -> logging.Logger:
    """Setup logger with date-stamped log file.
    
    Args:
        directory: Directory being processed
        
    Returns:
        Configured logger instance
    """
    # Get folder name (last component of path)
    folder_name = directory.name
    
    # Get current date in YYYY_MM_DD format
    date_str = datetime.now().strftime("%Y_%m_%d")
    
    # Create log filename in current working directory
    log_filename = f"analysis_{folder_name}_{date_str}.log"
    log_path = Path.cwd() / log_filename
    
    # Configure logger
    logger = logging.getLogger('polymer_chain_analysis')
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # File handler
    file_handler = logging.FileHandler(log_path, mode='w')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logger.info(f"Log file: {log_path}")
    logger.info("="*60)
    
    return logger


def find_coordinate_block(doc: gemmi.cif.Document) -> Optional[int]:
    """Find the first block containing coordinates.
    
    Args:
        doc: gemmi.cif.Document
        
    Returns:
        Index of first coordinate block, or None if not found
    """
    for idx, block in enumerate(doc):
        atom_site_dict = block.get_mmcif_category('_atom_site')
        # get_mmcif_category returns empty dict {} if category doesn't exist
        # Check if dictionary is non-empty to confirm _atom_site is present
        if atom_site_dict:  # True if dict has keys, False if empty
            return idx
    return None


def get_polymer_chains(st: gemmi.Structure, verbose: bool = False) -> List[Dict]:
    """Extract polymer chain information from a Structure.
    
    Args:
        st: gemmi.Structure object
        verbose: If True, include detailed composition info
        
    Returns:
        List of dictionaries with polymer chain information
    """
    polymer_chains = []
    
    for model_idx, model in enumerate(st):
        for chain in model:
            # Count polymer residues in this chain
            polymer_count = 0
            total_residues = 0
            residue_types = set()
            
            for residue in chain:
                total_residues += 1
                residue_types.add(residue.name)
                
                # Check if residue is part of a polymer
                if residue.entity_type == gemmi.EntityType.Polymer:
                    polymer_count += 1
            
            # Only include chains with polymer residues
            if polymer_count > 0:
                chain_info = {
                    'model': model_idx,
                    'chain_name': chain.name,
                    'polymer_residues': polymer_count,
                    'total_residues': total_residues,
                    'unique_types': len(residue_types)
                }
                
                if verbose:
                    chain_info['residue_types'] = sorted(residue_types)
                    # Get first and last residue for sequence range
                    if len(chain) > 0:
                        first_res = chain[0]
                        last_res = chain[-1]
                        chain_info['seq_start'] = f"{first_res.name}:{first_res.seqid.num}"
                        chain_info['seq_end'] = f"{last_res.name}:{last_res.seqid.num}"
                
                polymer_chains.append(chain_info)
    
    return polymer_chains


def print_polymer_chains(filepath: Path, polymer_chains: List[Dict], verbose: bool = False, logger: Optional[logging.Logger] = None) -> None:
    """Print polymer chain information.
    
    Args:
        filepath: Path to the CIF file
        polymer_chains: List of polymer chain dictionaries
        verbose: If True, print detailed information
        logger: Logger instance (if None, use print)
    """
    log = logger.info if logger else print
    
    # Get subfolder (parent directory name)
    subfolder = filepath.parent.name
    
    log(f"\nFile: {filepath.name}  Subfolder: {subfolder}")
    
    if not polymer_chains:
        log("  No polymer chains found")
        return
    
    log(f"  Polymer chains: {len(polymer_chains)}")
    
    if verbose:
        for chain_info in polymer_chains:
            log(f"\n  Chain '{chain_info['chain_name']}' (Model {chain_info['model']}):")
            log(f"    Polymer residues: {chain_info['polymer_residues']}")
            log(f"    Total residues: {chain_info['total_residues']}")
            log(f"    Unique residue types: {chain_info['unique_types']}")
            
            if 'seq_start' in chain_info:
                log(f"    Sequence range: {chain_info['seq_start']} to {chain_info['seq_end']}")
            
            if 'residue_types' in chain_info:
                res_types_display = chain_info['residue_types'][:10]
                if len(chain_info['residue_types']) > 10:
                    res_types_display.append('...')
                log(f"    Residue types: {', '.join(res_types_display)}")
    else:
        # Simple output: just chain names
        chain_names = [chain_info['chain_name'] for chain_info in polymer_chains]
        log(f"  Chain names: {', '.join(chain_names)}")


def process_single_file(filepath: Path, verbose: bool = False, logger: Optional[logging.Logger] = None) -> Tuple[Optional[List[Dict]], Optional[str], bool]:
    """Process a single CIF file and extract polymer chains.
    
    Args:
        filepath: Path to CIF file
        verbose: If True, print detailed information
        logger: Logger instance (if None, use print)
        
    Returns:
        Tuple of (polymer chain list or None, error message or None, has_coordinates)
    """
    try:
        doc = gemmi.cif.read(str(filepath))
    except Exception as e:
        return None, f"Error reading {filepath.name}: {e}", False
    
    # Find coordinate block
    block_idx = find_coordinate_block(doc)
    if block_idx is None:
        # Not an error - just no coordinates (e.g., restraint files, reflection files)
        return [], None, False
    
    # Load structure
    try:
        block = doc[block_idx]
        st = gemmi.make_structure_from_block(block)
    except Exception as e:
        return None, f"Error loading structure from {filepath.name}: {e}", True
    
    # Get polymer chains
    polymer_chains = get_polymer_chains(st, verbose=verbose)
    
    # Print results (only for files with polymer chains)
    if len(polymer_chains) > 0:
        print_polymer_chains(filepath, polymer_chains, verbose=verbose, logger=logger)
    
    return polymer_chains, None, True


def process_directory(directory: Path, verbose: bool = False) -> None:
    """Process all .cif files in directory and subdirectories.
    
    Args:
        directory: Root directory to search
        verbose: If True, print detailed information
    """
    # Setup logger
    logger = setup_logger(directory)
    
    # Use generator to avoid loading all file paths into memory
    # For counting, we need to iterate once first
    cif_files_list = list(directory.rglob("*.cif"))
    total_cif_files = len(cif_files_list)
    
    if total_cif_files == 0:
        logger.info(f"No .cif files found in {directory}")
        return
    
    logger.info(f"Found {total_cif_files} .cif file(s) in {directory}")
    logger.info(f"{'='*60}\n")
    
    coordinate_files_analyzed = 0
    coordinate_files_failed = 0
    files_with_polymers = 0
    total_polymer_chains = 0
    errors = []
    one_chain_files = []  # Files with exactly 1 polymer chain
    more_than_one_chain_files = []  # Files with > 1 polymer chain
    
    # Process files iteratively using sorted generator
    for cif_file in sorted(cif_files_list):
        polymer_chains, error, has_coordinates = process_single_file(cif_file, verbose=verbose, logger=logger)
        
        if error:
            errors.append((cif_file.name, error))
        elif has_coordinates:
            if polymer_chains is not None:
                coordinate_files_analyzed += 1
                if len(polymer_chains) > 0:
                    files_with_polymers += 1
                    total_polymer_chains += len(polymer_chains)
                    
                    # Track one-chain vs more-than-one-chain
                    if len(polymer_chains) == 1:
                        one_chain_files.append((cif_file.name, cif_file.parent.name))
                    else:
                        more_than_one_chain_files.append((cif_file.name, cif_file.parent.name, len(polymer_chains)))
            else:
                coordinate_files_failed += 1
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Total .cif files found: {total_cif_files}")
    if errors:
        logger.info(f"Files with errors: {len(errors)}")
    logger.info(f"Files containing coordinates successfully analyzed: {coordinate_files_analyzed}")
    logger.info(f"Files containing coordinates had analysis failure: {coordinate_files_failed}")
    logger.info(f"Files with polymer chains: {files_with_polymers}")
    logger.info(f"  - One-chain files (1 chain): {len(one_chain_files)}")
    logger.info(f"  - More-than-one-chain files (>1 chain): {len(more_than_one_chain_files)}")
    logger.info(f"")
    logger.info(f"Total polymer chains found: {total_polymer_chains}")
    
    # One-chain files list
    if one_chain_files:
        logger.info(f"\n{'='*60}")
        logger.info(f"ONE-CHAIN FILES (1 polymer chain)")
        logger.info(f"{'='*60}")
        for filename, subfolder in one_chain_files:
            logger.info(f"{filename}  (Subfolder: {subfolder})")
    
    # More-than-one-chain files list
    if more_than_one_chain_files:
        logger.info(f"\n{'='*60}")
        logger.info(f"MORE-THAN-ONE-CHAIN FILES (>1 polymer chain)")
        logger.info(f"{'='*60}")
        for filename, subfolder, chain_count in more_than_one_chain_files:
            logger.info(f"{filename}  (Subfolder: {subfolder}, Chains: {chain_count})")
    
    # Generate shell script for organizing files
    if one_chain_files or more_than_one_chain_files:
        script_filename = f"organize_by_chains_{directory.name}_{datetime.now().strftime('%Y_%m_%d')}.sh"
        script_path = Path.cwd() / script_filename
        
        with open(script_path, 'w', newline='\n') as f:
            f.write("#!/bin/bash\n")
            f.write("# Shell script to organize coordinate files by polymer chain count\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Source directory: {directory}\n\n")
            
            f.write("# Create target directories\n")
            f.write(f'mkdir -p "{directory}/one-chain"\n')
            f.write(f'mkdir -p "{directory}/more-than-one-chain"\n\n')
            
            if one_chain_files:
                f.write("# Move one-chain subfolders\n")
                # Get unique subfolders for one-chain structure files
                one_chain_subfolders = sorted(set(subfolder for _, subfolder in one_chain_files))
                for subfolder in one_chain_subfolders:
                    f.write(f'mv "{directory}/{subfolder}" "{directory}/one-chain/{subfolder}"\n')
                f.write("\n")
            
            if more_than_one_chain_files:
                f.write("# Move more-than-one-chain subfolders\n")
                # Get unique subfolders for more-than-one-chain structure files
                more_than_one_chain_subfolders = sorted(set(subfolder for _, subfolder, _ in more_than_one_chain_files))
                for subfolder in more_than_one_chain_subfolders:
                    f.write(f'mv "{directory}/{subfolder}" "{directory}/more-than-one-chain/{subfolder}"\n')
                f.write("\n")
            
            f.write('echo "Organization complete!"\n')
        
        # Make script executable on Unix-like systems
        try:
            import os
            os.chmod(script_path, 0o755)
        except:
            pass  # Windows doesn't need chmod
        
        logger.info(f"\n{'='*60}")
        logger.info(f"SHELL SCRIPT GENERATED")
        logger.info(f"{'='*60}")
        logger.info(f"File: {script_path}")
        logger.info(f"")
        logger.info(f"This script will:")
        logger.info(f"  1. Create 'one-chain' and 'more-than-one-chain' folders in {directory}")
        logger.info(f"  2. Move {len(set(subfolder for _, subfolder in one_chain_files))} one-chain subfolders to 'one-chain' folder")
        logger.info(f"  3. Move {len(set(subfolder for _, subfolder, _ in more_than_one_chain_files))} more-than-one-chain subfolders to 'more-than-one-chain' folder")
        logger.info(f"")
        logger.info(f"To execute: bash {script_filename}")
    
    # Report errors at the end
    if errors:
        logger.info(f"\n{'='*60}")
        logger.info(f"ERROR DETAILS")
        logger.info(f"{'='*60}")
        for filename, error_msg in errors:
            logger.info(f"\n{filename}")
            logger.info(f"  {error_msg}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze and list polymer chains in mmCIF coordinate files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s structure.cif
  %(prog)s structure.cif --verbose
  %(prog)s /path/to/cif_files/ --auto
  %(prog)s /path/to/cif_files/ --auto --verbose
        """
    )
    
    parser.add_argument("input", help="Input mmCIF file or directory (with --auto)")
    parser.add_argument("--auto", "-a", action="store_true",
                       help="Auto mode: process all .cif files in directory and subdirectories")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Print detailed chain information including residue counts and types")
    
    args = parser.parse_args()
    
    # Validate input path
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # Auto mode: process directory
    if args.auto:
        if not input_path.is_dir():
            print(f"Error: --auto requires a directory, but got: {input_path}", file=sys.stderr)
            sys.exit(1)
        process_directory(input_path, args.verbose)
        return
    
    # Single file mode
    if input_path.is_dir():
        print(f"Error: Input is a directory. Use --auto to process directories.", file=sys.stderr)
        sys.exit(1)
    
    polymer_chains, error, has_coordinates = process_single_file(input_path, args.verbose)
    
    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
