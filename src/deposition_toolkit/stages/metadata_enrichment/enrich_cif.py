#!/usr/bin/env python3

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
import gemmi
from Bio import Align
import requests
import time
from datetime import datetime, timedelta
# from pdbe_sifts.mmcif.curator.cif_sequence_update import CifSequenceUpdater



ASYM_REFERENCES = [
    ("_atom_site", "label_asym_id"),
    ("_struct_conn", "ptnr1_label_asym_id"),
    ("_struct_conn", "ptnr2_label_asym_id"),
]

ENTITY_REFERENCES = [
    ("_struct_asym", "entity_id"),
    ("_atom_site", "label_entity_id"),
    ("_entity_poly", "entity_id"),
    ("_entity_src_gen", "entity_id"),
    ("_entity_poly_seq", "entity_id"),
]

ENTRY_REFERNCES =[
    ("_cell", "entry_id"),
    ("_symmetry", "entry_id"),
    ("_refine", "entry_id"),
    ("_struct", "entry_id"),
    ("_struct_keywords", "entry_id"),
    ("_pdbx_database_status", "entry_id"),
    ("_exptl", "entry_id"),
    ("_reflns", "entry_id"),
    ("_refine_analyze", "entry_id"),
]

logger = logging.getLogger("enrich")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

class CifDataStorage:
    """A container for holding and writing mmCIF data."""

    def __init__(self):
        self.data: Dict[str, Dict[str, Any]] = {}

    def set_items(self, category_name: str, items_dict: Dict[str, Any]):
        if category_name not in self.data:
            self.data[category_name] = {}
        for item, values in items_dict.items():
            self.data[category_name][item] = values

    def set_item(self, category_name: str, item_name: str, item_value: Any):
        if category_name not in self.data:
            self.data[category_name] = {}
        self.data[category_name][item_name] = item_value

    def get_data(self) -> Dict[str, Dict[str, Any]]:
        return self.data

    def populate_from_cif_block(self, cif_block: gemmi.cif.Block):
        """Initializes the storage from a gemmi cif.Block."""
        for category in cif_block.get_mmcif_category_names():
            items = cif_block.get_mmcif_category(category)
            # Gemmi is adding a trailing dot to category names
            self.set_items(category[:-1], items)

    def convert_values_to_none(self):
        """Converts all '?' values to None."""
        for category, items in self.data.items():
            for item, values in items.items():
                if isinstance(values, list):
                    for i in range(len(values)):
                        if values[i] == "?":
                            values[i] = None
                else:
                    if values == "?":
                        self.data[category][item] = None

    def write_to_cif(self, output_file: str, block_name: str = "model"):
        """Writes the stored data to an mmCIF file."""
        logger.info("Writing CIF file...")
        self.convert_values_to_none()

        doc = gemmi.cif.Document()
        block = doc.add_new_block(block_name)

        for category, items in self.data.items():
            try:
                block.set_mmcif_category(category, items)
            except Exception as e:
                logger.error(f"Error setting mmCIF category {category}: {e}")
                raise


        write_options = gemmi.cif.WriteOptions()
        write_options.align_loops = 50
        write_options.align_pairs = 50
        write_options.prefer_pairs = True
        doc.write_file(output_file, write_options)
        logger.info(f"mmCIF file written to: {output_file}")

class CCDCacheManager:
    def __init__(self, cache_dir=".ccd_cache", expiry_days=30):
        self.cache_dir = Path(cache_dir)
        self.details_file = self.cache_dir / "cache_details.json"
        self.expiry_days = expiry_days
        self.cache_dir.mkdir(exist_ok=True)
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict[str, float]:
        if self.details_file.exists():
            with open(self.details_file, "r") as f:
                return json.load(f)
        return {}

    def _save_metadata(self):
        with open(self.details_file, "w") as f:
            json.dump(self.metadata, f, indent=4)

    def get_ccd_block(self, comp_id: str) -> gemmi.cif.Block:
        comp_id = comp_id.upper()
        # Path: .ccd_cache/A/ATP/ATP.cif
        res_dir = self.cache_dir / comp_id[0] / comp_id
        res_file = res_dir / f"{comp_id}.cif"
        
        now = time.time()
        is_stale = False
        
        if comp_id in self.metadata:
            last_fetch = self.metadata[comp_id]
            if datetime.fromtimestamp(now) > datetime.fromtimestamp(last_fetch) + timedelta(days=self.expiry_days):
                is_stale = True

        if not res_file.exists() or is_stale:
            self._download_ccd(comp_id, res_file)
            self.metadata[comp_id] = now
            self._save_metadata()

        doc = gemmi.cif.read_file(str(res_file))
        return doc.sole_block()

    def _download_ccd(self, comp_id: str, dest_path: Path):
        url = f"https://ftp.ebi.ac.uk/pub/databases/msd/pdbechem_v2/ccd/{comp_id[0]}/{comp_id}/{comp_id}.cif"
        logger.info(f"Downloading {comp_id} from EBI (Cache miss or stale)...")
        
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "w") as f:
            f.write(response.text)

def enrich_from_json(storage: CifDataStorage, json_data: Dict[str, Any]) -> None:
    """
    Enriches the CIF storage using JSON data.

    Expected JSON structure:
    {
        "_category_name": {
            "item_name": value,
            ...
        },
        "additional_metadata": {
            ...
        }
    }
    """

    for category, items in json_data.items():

        # Handle special key
        if category == "additional_metadata":
            handle_additional_metadata(storage, items)
            continue

        if not isinstance(items, dict):
            logger.warning(f"Skipping category {category}: expected dict.")
            continue

        logger.info(f"Processing category: {category}")

        for item_name, value in items.items():
            if value is None:
                continue  # Skip nulls

            # mmCIF categories are typically looped → values must be lists
            if isinstance(value, list):
                storage.set_item(category, item_name, value)
            else:
                # Wrap scalar values in list for loop compatibility
                storage.set_item(category, item_name, [value])



def fetch_chem_comp_details(comp_id: str) -> Dict[str, Any]:
    """Downloads CCD CIF and returns the _chem_comp category data."""
    comp_id = comp_id.upper()
    
    url = f"https://ftp.ebi.ac.uk/pub/databases/msd/pdbechem_v2/ccd/{comp_id[0]}/{comp_id}/{comp_id}.cif"
    
    logger.info(f"Fetching CCD data for {comp_id}...")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        doc = gemmi.cif.read_string(response.text)
        block = doc.sole_block()
        
        # Get the _chem_comp category as a dictionary
        # Gemmi returns a dict of lists for categories
        data = block.get_mmcif_category("_chem_comp")
        return data
    except Exception as e:
        logger.error(f"Failed to fetch CCD for {comp_id}: {e}")
        return {}

def enrich_chem_comp(storage: CifDataStorage) -> None:
    """Populates _chem_comp, _chem_comp_atom, and _chem_comp_bond using cached CCDs."""
    
    if "_atom_site" not in storage.data or "label_comp_id" not in storage.data["_atom_site"]:
        logger.warning("No _atom_site.label_comp_id found. Skipping CCD enrichment.")
        return

    unique_res = sorted(list(set(storage.data["_atom_site"]["label_comp_id"])))
    if "LIG" in unique_res:
        unique_res.remove("LIG")
    cache = CCDCacheManager()
    
    # 1. Setup Data Structures
    comp_items = ["id", "type", "name", "pdbx_synonyms", "formula", "formula_weight"]
    atom_items = ["comp_id", "atom_id", "type_symbol", "pdbx_aromatic_flag", "pdbx_stereo_config"]
    bond_items = ["comp_id", "atom_id_1", "atom_id_2", "value_order", "pdbx_aromatic_flag", "pdbx_stereo_config"]

    storage.data["_chem_comp"] = {item: [] for item in comp_items}
    storage.data["_chem_comp_atom"] = {item: [] for item in atom_items + ["pdbx_ordinal"]}
    storage.data["_chem_comp_bond"] = {item: [] for item in bond_items + ["pdbx_ordinal"]}

    atom_ordinal_counter = 1
    bond_ordinal_counter = 1

    for res in unique_res:
        try:
            block = cache.get_ccd_block(res)
            
            # --- Handle _chem_comp (Summary) ---
            ccd_comp = block.get_mmcif_category("_chem_comp")
            for item in comp_items:
                val = ccd_comp.get(item, [None])[0]
                storage.data["_chem_comp"][item].append(val)

            # --- Handle _chem_comp_atom (Looped) ---
            ccd_atoms = block.get_mmcif_category("_chem_comp_atom")
            if ccd_atoms:
                num_atoms = len(next(iter(ccd_atoms.values())))
                for i in range(num_atoms):
                    for item in atom_items:
                        # Map internal CCD values to our storage
                        val = ccd_atoms.get(item, [None])[i]
                        storage.data["_chem_comp_atom"][item].append(val)
                    
                    storage.data["_chem_comp_atom"]["pdbx_ordinal"].append(str(atom_ordinal_counter))
                    atom_ordinal_counter += 1

            # --- Handle _chem_comp_bond (Looped) ---
            ccd_bonds = block.get_mmcif_category("_chem_comp_bond")
            if ccd_bonds:
                num_bonds = len(next(iter(ccd_bonds.values())))
                for i in range(num_bonds):
                    for item in bond_items:
                        val = ccd_bonds.get(item, [None])[i]
                        if item == 'value_order':
                            val = val.lower()
                        storage.data["_chem_comp_bond"][item].append(val)
                    
                    storage.data["_chem_comp_bond"]["pdbx_ordinal"].append(str(bond_ordinal_counter))
                    bond_ordinal_counter += 1

        except Exception as e:
            logger.error(f"Could not process CCD for {res}: {e}")
            # Maintain consistency for _chem_comp at least
            for item in comp_items:
                storage.data["_chem_comp"][item].append(res if item == "id" else None)

    logger.info(f"Enriched Chem Comp: {len(unique_res)} residues, "
                f"{atom_ordinal_counter-1} atoms, {bond_ordinal_counter-1} bonds.")

def handle_additional_metadata(storage: CifDataStorage, metadata: Dict[str, Any]) -> None:
    """
    Handles special JSON key: 'additional_metadata'.

    This is intentionally flexible and can contain
    custom logic for complex CIF writing.
    """

    logger.info("Processing additional_metadata")

    xray_type = metadata.get("xray_exp_type")

    if xray_type:
        enrich_xray_experiment(storage, xray_type.lower())


def parse_args():
    # To do:
    # - Add argument for force CCD refresh
    # - Add argument for 
    parser = argparse.ArgumentParser(
        description="Enrich an mmCIF file using metadata from a JSON file."
    )

    parser.add_argument(
        "--input-cif",
        required=True,
        help="Path to input mmCIF file",
    )

    parser.add_argument(
        "--input-json",
        required=True,
        help="Path to JSON metadata file",
    )

    parser.add_argument(
        "--output-cif",
        required=True,
        help="Path to write enriched mmCIF file",
    )

    parser.add_argument(
        "--entry-id",
        default="model",
        help="Entry ID for output CIF file (default: model)",
    )
    parser.add_argument(
        "--dictionary",
        default="./mmcif_pdbx_v50.dic",
        help="Path to the mmCIF dictionary used for output validation.",
    )
    parser.add_argument(
        "--validation-log",
        help="Path for validation errors. Defaults to <output-stem>_validation_errors.txt.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Write the enriched file without running validate_mmcif.",
    )

    return parser.parse_args()
 

def enrich_xray_experiment(storage: CifDataStorage, xray_type: str) -> None:
    """
    Enrich _diffrn_radiation based on xray_exp_type.

    Allowed values:
        - 'single'
        - 'laue'
        - 'mad'

    Assumes exactly ONE diffraction experiment.
    Exits if multiple rows are detected.
    """

    import sys

    allowed = {"single", "laue", "mad"}
    if xray_type not in allowed:
        logger.error(f"Invalid xray_exp_type: {xray_type}")
        sys.exit(1)

    logger.info(f"Applying X-ray experiment type enrichment: {xray_type}")

    category = "_diffrn_radiation"

    # ------------------------------------------------------------
    # Ensure category exists
    # ------------------------------------------------------------
    if category not in storage.data:
        logger.info(f"{category} not found. Initializing.")
        storage.data[category] = {}

    items = storage.data[category]

    # ------------------------------------------------------------
    # Determine row count
    # ------------------------------------------------------------
    row_lengths = {
        len(v) for v in items.values()
        if isinstance(v, list)
    }

    if not row_lengths:
        # Completely empty category → initialize one row
        row_count = 1
    else:
        if len(row_lengths) != 1:
            logger.error(
                f"Inconsistent row lengths in {category}: {row_lengths}"
            )
            sys.exit(1)

        row_count = row_lengths.pop()

        if row_count != 1:
            logger.error(
                f"Expected exactly 1 row in {category}, "
                f"found {row_count}. Aborting."
            )
            sys.exit(1)

    # ------------------------------------------------------------
    # Required items
    # ------------------------------------------------------------
    required_items = [
        "collimation",
        "diffrn_id",
        "filter_edge",
        "inhomogeneity",
        "monochromator",
        "polarisn_norm",
        "polarisn_ratio",
        "probe",
        "type",
        "xray_symbol",
        "wavelength_id",
        "pdbx_monochromatic_or_laue_m_l",
        "pdbx_wavelength_list",
        "pdbx_wavelength",
        "pdbx_diffrn_protocol",
        "pdbx_analyzer",
        "pdbx_scattering_type",
    ]

    for item in required_items:
        if item not in items:
            items[item] = [None]

    # ------------------------------------------------------------
    # Force diffrn_id = 1
    # ------------------------------------------------------------
    items["diffrn_id"] = ["1"]

    # ------------------------------------------------------------
    # Determine protocol + M/L flag
    # ------------------------------------------------------------
    if xray_type == "laue":
        protocol = "LAUE"
        m_l_flag = "L"
    elif xray_type == "mad":
        protocol = "MAD"
        m_l_flag = "M"
    else:
        protocol = "SINGLE WAVELENGTH"
        m_l_flag = "M"

    # ------------------------------------------------------------
    # Only set if empty
    # ------------------------------------------------------------
    def set_if_empty(item_name: str, value: str):
        current = items[item_name][0]
        if current in (None, "?"):
            items[item_name][0] = value

    set_if_empty("pdbx_diffrn_protocol", protocol)
    set_if_empty("pdbx_monochromatic_or_laue_m_l", m_l_flag)
    set_if_empty("pdbx_scattering_type", "x-ray")

    logger.info("X-ray enrichment complete.")


def normalize_ids(storage: CifDataStorage) -> None:
    """
    Normalizes:
        - _entity.id
        - _struct_asym.id

    Rewrites all known foreign key references.

    Assumes single-model structural file.
    """

    logger.info("Starting ID normalization...")

    entity_map = _normalize_entity_ids(storage)
    asym_map = _normalize_struct_asym_ids(storage)

    _rewrite_entity_references(storage, entity_map)
    _rewrite_asym_references(storage, asym_map)

    logger.info("ID normalization complete.")

def _normalize_entity_ids(storage: CifDataStorage) -> dict[str, str]:
    category = "_entity"

    if category not in storage.data:
        logger.warning("_entity category missing. Skipping entity normalization.")
        return {}

    items = storage.data[category]
    if "id" not in items:
        logger.error("_entity.id missing.")
        raise SystemExit(1)

    old_ids = items["id"]

    unique_ids = list(dict.fromkeys(old_ids))  # preserve order

    entity_map = {
        old_id: str(i + 1)
        for i, old_id in enumerate(unique_ids)
    }

    # Rewrite entity.id itself
    items["id"] = [entity_map[x] for x in old_ids]
    logger.info(f"Entity ID map: {entity_map}")

    return entity_map

def _normalize_struct_asym_ids(storage: CifDataStorage) -> dict[str, str]:
    category = "_struct_asym"

    if category not in storage.data:
        logger.warning("_struct_asym missing. Skipping asym normalization.")
        return {}

    items = storage.data[category]
    if "id" not in items:
        logger.error("_struct_asym.id missing.")
        raise SystemExit(1)

    def get_asym_id(n: int) -> str:
        """Converts an index (0-based) to alphabetical ID (A, B, ..., Z, AA, AB...)."""
        label = ""
        while n >= 0:
            label = chr(ord('A') + (n % 26)) + label
            n = (n // 26) - 1
        return label

    old_ids = items["id"]
    unique_ids = list(dict.fromkeys(old_ids))

    # Generate alphabetical mapping instead of stringified integers
    asym_map = {
        old_id: get_asym_id(i)
        for i, old_id in enumerate(unique_ids)
    }

    items["id"] = [asym_map[x] for x in old_ids]
    logger.info(f"struct_asym ID map: {asym_map}")

    return asym_map

def _rewrite_entity_references(storage: CifDataStorage, entity_map: dict[str, str]) -> None:
    for category, field in ENTITY_REFERENCES:
        if category not in storage.data:
            continue

        items = storage.data[category]

        if field not in items:
            continue

        logger.info(f"Rewriting {category}.{field}")

        values = items[field]
        new_values = []

        for val in values:
            if val in entity_map:
                new_values.append(entity_map[val])
            else:
                logger.error(
                    f"Reference error: {val} not found in entity_map "
                    f"(category {category}.{field})"
                )
                raise SystemExit(1)

        items[field] = new_values

def _rewrite_asym_references(storage: CifDataStorage, asym_map: dict[str, str]) -> None:
    for category, field in ASYM_REFERENCES:
        if category not in storage.data:
            continue

        items = storage.data[category]

        if field not in items:
            continue

        logger.info(f"Rewriting {category}.{field}")

        values = items[field]
        new_values = []

        for val in values:
            if val in asym_map:
                new_values.append(asym_map[val])
            else:
                logger.error(
                    f"Reference error: {val} not found in asym_map "
                    f"(category {category}.{field})"
                )
                raise SystemExit(1)

        items[field] = new_values

def normalize_entry_id(storage: CifDataStorage, new_id: str = "XXXX") -> None:
    logger.info(f"Normalizing Entry ID to: {new_id}")

    # 1. Update the primary _entry category
    category = "_entry"
    if category not in storage.data:
        logger.info(f"{category} missing. Creating it.")
        storage.data[category] = {}
    
    # entry.id is almost always a single-item list in mmCIF loops
    storage.data[category]["id"] = [new_id]

    # 2. Update all foreign key references
    for category, field in ENTRY_REFERNCES:
        if category not in storage.data:
            continue

        items = storage.data[category]
        if field not in items:
            continue

        logger.info(f"Rewriting {category}.{field} to {new_id}")
        
        # Determine if we are dealing with a list or a single value
        values = items[field]
        if isinstance(values, list):
            items[field] = [new_id for _ in values]
        else:
            items[field] = new_id

    logger.info("Entry ID normalization complete.")


def enrich_cif_file(
    input_cif: Path,
    input_json: Path,
    output_cif: Path | None = None,
    *,
    entry_id: str = "model",
    dictionary_path: Path | None = Path("./mmcif_pdbx_v50.dic"),
    validation_log_path: Path | None = None,
) -> dict[str, Any]:
    input_cif = Path(input_cif)
    input_json = Path(input_json)
    output_cif = Path(output_cif or input_cif)
    logger.info("Reading input CIF...")
    doc = gemmi.cif.read_file(str(input_cif))
    block = doc.sole_block()

    storage = CifDataStorage()
    storage.populate_from_cif_block(block)

    logger.info("Reading JSON metadata...")
    with open(input_json, "r") as f:
        json_data = json.load(f)

    normalize_entry_id(storage, entry_id)
    normalize_ids(storage)
    enrich_from_json(storage, json_data)
    enrich_chem_comp(storage)
    storage.write_to_cif(str(output_cif), block_name=entry_id)

    # updater = CifSequenceUpdater(output_cif, output_cif)
    # updater.process()

    result: dict[str, Any] = {
        "input_cif": str(input_cif),
        "input_json": str(input_json),
        "output_cif": str(output_cif),
    }
    if dictionary_path is None:
        return result

    validation_log_path = validation_log_path or output_cif.with_name(
        f"{output_cif.stem}_validation_errors.txt"
    )
    from validate_mmcif import validate

    errors = list(validate(Path(dictionary_path), Path(output_cif)))
    severity_has_error = False
    error_count = 0
    warning_count = 0
    with open(validation_log_path, "w", encoding="utf-8") as f:
        for err in errors:
            # logger.info(f"{err.line}, {err.item}, {err.message}, {err.severity}")
            # We need to output the errors in a seperate file
            
            f.write(f"{err.line}, {err.item}, {err.message}, {err.severity}\n")
            if err.severity == "error":
                severity_has_error = True
                error_count += 1
            elif err.severity == "warning":
                warning_count += 1
    result.update(
        {
            "validation_log": str(validation_log_path),
            "validation_errors": error_count,
            "validation_warnings": warning_count,
        }
    )
    if not severity_has_error:
        logger.info("Validation passed.")
    else:
        logger.error(f"Validation failed. Check detailed log at {validation_log_path}")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args() if argv is None else parse_args_from(argv)
    try:
        enrich_cif_file(
            Path(args.input_cif),
            Path(args.input_json),
            Path(args.output_cif),
            entry_id=args.entry_id,
            dictionary_path=None if args.skip_validation else Path(args.dictionary),
            validation_log_path=Path(args.validation_log) if args.validation_log else None,
        )
    except Exception as exc:
        logger.error("%s", exc)
        return 1
    return 0


def parse_args_from(argv: Sequence[str]):
    parser = argparse.ArgumentParser(
        description="Enrich an mmCIF file using metadata from a JSON file."
    )
    parser.add_argument("--input-cif", required=True, help="Path to input mmCIF file")
    parser.add_argument("--input-json", required=True, help="Path to JSON metadata file")
    parser.add_argument("--output-cif", required=True, help="Path to write enriched mmCIF file")
    parser.add_argument("--entry-id", default="model", help="Entry ID for output CIF file (default: model)")
    parser.add_argument("--dictionary", default="./mmcif_pdbx_v50.dic", help="Path to the mmCIF dictionary used for output validation.")
    parser.add_argument("--validation-log", help="Path for validation errors. Defaults to <output-stem>_validation_errors.txt.")
    parser.add_argument("--skip-validation", action="store_true", help="Write the enriched file without running validate_mmcif.")
    return parser.parse_args(argv)



if __name__ == "__main__":
    raise SystemExit(main())
