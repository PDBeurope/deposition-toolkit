#!/usr/bin/env python3
"""Build CCD-style component CIFs from a directory of restraint and coordinate mmCIF files."""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import gemmi

try:
    from .rdkit_ligand import RdkitLigandAnalysis, analyze_ligand
except ImportError:  # pragma: no cover - keeps direct script execution working.
    from rdkit_ligand import RdkitLigandAnalysis, analyze_ligand


LOGGER = logging.getLogger(__name__)
MISSING_VALUES = {"", ".", "?"}
AUDIT_ACTION_TYPE = "Create component"
AUDIT_PROCESSING_SITE = "PDBE"
AUDIT_ANNOTATOR = "GE"
AUDIT_DETAILS = "Created for OpenBind"

class CifProcessingError(RuntimeError):
    """Raised when the input data does not satisfy the expected constraints."""


@dataclass(frozen=True)
class ChemCompRow:
    comp_id: str
    type: str
    pdbx_type: str
    pdbx_initial_date: str
    pdbx_modified_date: str
    pdbx_release_status: str
    pdbx_model_coordinates_missing_flag: str
    pdbx_ideal_coordinates_missing_flag: str


@dataclass(frozen=True)
class AtomRow:
    comp_id: str
    atom_id: str
    type_symbol: str
    ideal_x: str
    ideal_y: str
    ideal_z: str
    partial_charge: str | None = None


@dataclass(frozen=True)
class BondRow:
    comp_id: str
    atom_id_1: str
    atom_id_2: str
    bond_type: str


@dataclass(frozen=True)
class CoordinateRow:
    label_comp_id: str
    label_atom_id: str
    type_symbol: str
    cartn_x: str
    cartn_y: str
    cartn_z: str
    occupancy: str | None
    row_index: int
    label_alt_id: str | None
    label_asym_id: str | None
    label_entity_id: str | None
    label_seq_id: str | None
    pdbx_pdb_ins_code: str | None
    auth_seq_id: str | None
    auth_asym_id: str | None

    @property
    def instance_key(self) -> tuple[str | None, ...]:
        return (
            self.label_alt_id,
            self.label_comp_id,
            self.label_asym_id,
            self.label_entity_id,
            self.label_seq_id,
            self.pdbx_pdb_ins_code,
            self.auth_seq_id,
            self.auth_asym_id,
        )


@dataclass(frozen=True)
class LigandInstance:
    comp_id: str
    key: tuple[str | None, ...]
    rows: tuple[CoordinateRow, ...]
    instance_index: int

    @property
    def distinct_atom_count(self) -> int:
        return len({row.label_atom_id for row in self.rows})


@dataclass(frozen=True)
class RestraintDefinition:
    path: Path
    comp_id: str
    atoms: tuple[AtomRow, ...]
    bonds: tuple[BondRow, ...]


@dataclass(frozen=True)
class FileDiscovery:
    coordinate_path: Path
    restraint_paths: tuple[Path, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create CCD-style input CIFs from a directory of mmCIF files."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing one coordinate CIF and one or more restraint CIFs.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for generated CIFs. Defaults to <input-dir>/generated_ccd_inputs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s: %(message)s",
    )

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir / "generated_ccd_inputs"
    builder = CcdDirectoryBuilder(input_dir=input_dir, output_dir=output_dir)
    outputs = builder.run()

    LOGGER.info("Generated %s CCD-style CIF file(s) in %s", len(outputs), output_dir)
    for output_path in outputs:
        LOGGER.info("Created %s", output_path)
    return 0


class CcdDirectoryBuilder:
    """Convert a directory of coordinate/restraint CIFs into CCD-style output files."""

    def __init__(self, input_dir: Path, output_dir: Path):
        self.input_dir = input_dir
        self.output_dir = output_dir

    def run(self) -> list[Path]:
        self._validate_directories()
        discovery = _discover_cif_files(self.input_dir)
        restraint_definitions = _load_restraint_definitions(discovery.restraint_paths)
        coordinate_rows = _read_coordinate_rows(_load_coordinate_block(discovery.coordinate_path))

        self.output_dir.mkdir(parents=True, exist_ok=True)

        outputs: list[Path] = []
        for restraint in restraint_definitions:
            matched_rows = [row for row in coordinate_rows if row.label_comp_id == restraint.comp_id]
            self._validate_coordinate_coverage(restraint, matched_rows)
            instances = _group_instances(restraint.comp_id, matched_rows)

            LOGGER.info(
                "Matched restraint %s (comp_id=%s) to %s ligand instance(s) in %s",
                restraint.path.name,
                restraint.comp_id,
                len(instances),
                discovery.coordinate_path.name,
            )

            for instance in instances:
                LOGGER.info(
                    "Preparing comp_id=%s instance=%03d with %s coordinate row(s) and key=%s",
                    instance.comp_id,
                    instance.instance_index,
                    len(instance.rows),
                    _format_instance_key(instance.key),
                )
                output_path = self.output_dir / f"{restraint.comp_id}__{instance.instance_index:03d}.cif"
                diagram_path = self.output_dir / f"{restraint.comp_id}__{instance.instance_index:03d}.png"
                rdkit_analysis = _analyze_ligand_with_rdkit(restraint, instance, diagram_path)
                _log_rdkit_analysis(instance, rdkit_analysis)
                output_document = _build_output_document(
                    chem_comp=_build_chem_comp(restraint.comp_id, instance),
                    chem_comp_atom=_build_chem_comp_atom_rows(
                        restraint_atoms=list(restraint.atoms),
                        restraint_bonds=list(restraint.bonds),
                        instance=instance,
                        restraint_file_name=restraint.path.name,
                    ),
                    chem_comp_bond=_build_chem_comp_bond_rows(
                        restraint_bonds=list(restraint.bonds),
                        rdkit_analysis=rdkit_analysis,
                    ),
                )
                output_path.write_text(output_document.as_string(), encoding="utf-8")
                outputs.append(output_path)
        return outputs

    def _validate_directories(self) -> None:
        if not self.input_dir.is_dir():
            raise CifProcessingError(f"Input directory not found: {self.input_dir}")

    def _validate_coordinate_coverage(
        self,
        restraint: RestraintDefinition,
        matched_rows: list[CoordinateRow],
    ) -> None:
        if not matched_rows:
            raise CifProcessingError(
                f"Component id {restraint.comp_id!r} from restraints was not found in _atom_site."
            )

        restraint_atom_ids = {atom.atom_id for atom in restraint.atoms}
        coordinate_atom_ids = {row.label_atom_id for row in matched_rows}
        unexpected_atom_ids = sorted(coordinate_atom_ids - restraint_atom_ids)
        if unexpected_atom_ids:
            raise CifProcessingError(
                "Coordinate file contains atom ids not found in restraint "
                f"_chem_comp_atom for component {restraint.comp_id!r}: {', '.join(unexpected_atom_ids)}"
            )


def _discover_cif_files(input_dir: Path) -> FileDiscovery:
    cif_paths = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".cif")
    if not cif_paths:
        raise CifProcessingError(f"No .cif files found in {input_dir}")

    coordinate_paths: list[Path] = []
    restraint_paths: list[Path] = []

    for path in cif_paths:
        document = _try_read_cif_file(path)
        if document is None:
            continue
        is_coordinate = _document_has_category(document, "_atom_site.")
        is_restraint = _restraint_document_has_required_categories(document, path)

        if is_coordinate:
            coordinate_paths.append(path)
            LOGGER.info("Classified %s as coordinate CIF", path.name)
        elif is_restraint:
            restraint_paths.append(path)
            LOGGER.info("Classified %s as restraint CIF", path.name)
        else:
            LOGGER.warning("Ignoring %s because it could not be classified", path.name)

    if len(coordinate_paths) != 1:
        raise CifProcessingError(
            "Expected exactly one coordinate CIF containing _atom_site; "
            f"found {len(coordinate_paths)} in {input_dir}"
        )
    if not restraint_paths:
        raise CifProcessingError(f"No restraint CIFs found in {input_dir}")

    return FileDiscovery(
        coordinate_path=coordinate_paths[0],
        restraint_paths=tuple(restraint_paths),
    )


def _try_read_cif_file(path: Path) -> gemmi.cif.Document | None:
    try:
        return gemmi.cif.read_file(str(path))
    except Exception as error:
        LOGGER.warning("Ignoring unreadable CIF %s: %s", path.name, error)
        return None


def _document_has_category(document: gemmi.cif.Document, category_name: str) -> bool:
    for block in document:
        category = block.get_mmcif_category(category_name)
        if category:
            return True
    return False


def _restraint_document_has_required_categories(document: gemmi.cif.Document, path: Path) -> bool:
    try:
        block = _select_restraint_block(document, path)
    except CifProcessingError:
        return False
    return bool(block.get_mmcif_category("_chem_comp_atom.")) and bool(
        block.get_mmcif_category("_chem_comp_bond.")
    )


def _load_coordinate_block(path: Path) -> gemmi.cif.Block:
    document = gemmi.cif.read_file(str(path))
    for block in document:
        if block.get_mmcif_category("_atom_site."):
            return block
    raise CifProcessingError(f"No data block with _atom_site found in {path}")


def _load_restraint_definitions(paths: tuple[Path, ...]) -> list[RestraintDefinition]:
    definitions: list[RestraintDefinition] = []
    seen_comp_ids: dict[str, Path] = {}
    for path in paths:
        document = gemmi.cif.read_file(str(path))
        block = _select_restraint_block(document, path)
        atoms = tuple(_read_restraint_atoms(block))
        bonds = tuple(_read_restraint_bonds(block))
        comp_id = _determine_comp_id(list(atoms), list(bonds))

        if comp_id in seen_comp_ids:
            raise CifProcessingError(
                f"Duplicate restraint definitions found for comp_id {comp_id!r}: "
                f"{seen_comp_ids[comp_id]} and {path}"
            )

        seen_comp_ids[comp_id] = path
        LOGGER.info("Loaded restraint %s for comp_id=%s", path.name, comp_id)
        definitions.append(RestraintDefinition(path=path, comp_id=comp_id, atoms=atoms, bonds=bonds))
    return definitions


def _select_restraint_block(document: gemmi.cif.Document, path: Path) -> gemmi.cif.Block:
    if len(document) == 0:
        raise CifProcessingError(f"No data blocks found in {path}")
    if len(document) >= 2:
        LOGGER.info(
            "Using second data block (%s) from restraint CIF %s and ignoring the first",
            document[1].name,
            path.name,
        )
        return document[1]
    return document[0]


def _read_restraint_atoms(block: gemmi.cif.Block) -> list[AtomRow]:
    category = block.get_mmcif_category("_chem_comp_atom.")
    required_columns = ["comp_id", "atom_id", "type_symbol", "x", "y", "z"]
    _require_columns("_chem_comp_atom", category, required_columns)
    rows = _category_rows(category, required_columns + ["partial_charge"])
    atoms: list[AtomRow] = []
    for row in rows:
        atoms.append(
            AtomRow(
                comp_id=row["comp_id"],
                atom_id=row["atom_id"],
                type_symbol=row["type_symbol"],
                ideal_x=row["x"],
                ideal_y=row["y"],
                ideal_z=row["z"],
                partial_charge=row.get("partial_charge"),
            )
        )
    if not atoms:
        raise CifProcessingError("No rows found in restraint _chem_comp_atom category.")
    return atoms


def _read_restraint_bonds(block: gemmi.cif.Block) -> list[BondRow]:
    category = block.get_mmcif_category("_chem_comp_bond.")
    required_columns = ["comp_id", "atom_id_1", "atom_id_2", "type"]
    _require_columns("_chem_comp_bond", category, required_columns)
    rows = _category_rows(category, required_columns)
    bonds = [
        BondRow(
            comp_id=row["comp_id"],
            atom_id_1=row["atom_id_1"],
            atom_id_2=row["atom_id_2"],
            bond_type=row["type"].lower(),
        )
        for row in rows
    ]
    if not bonds:
        raise CifProcessingError("No rows found in restraint _chem_comp_bond category.")
    return bonds


def _determine_comp_id(restraint_atoms: list[AtomRow], restraint_bonds: list[BondRow]) -> str:
    comp_ids = {atom.comp_id for atom in restraint_atoms}
    comp_ids.update(bond.comp_id for bond in restraint_bonds)
    if len(comp_ids) != 1:
        raise CifProcessingError(
            "Expected exactly one component id across restraint atoms and bonds; "
            f"found {sorted(comp_ids)}"
        )
    return next(iter(comp_ids))


def _read_coordinate_rows(block: gemmi.cif.Block) -> list[CoordinateRow]:
    category = block.get_mmcif_category("_atom_site.")
    required_columns = [
        "label_comp_id",
        "label_atom_id",
        "type_symbol",
        "Cartn_x",
        "Cartn_y",
        "Cartn_z",
        "occupancy",
        "label_alt_id",
        "label_asym_id",
        "label_entity_id",
        "label_seq_id",
        "pdbx_PDB_ins_code",
        "auth_seq_id",
        "auth_asym_id",
    ]
    _require_columns("_atom_site", category, required_columns)
    rows = _category_rows(category, required_columns)
    coordinate_rows: list[CoordinateRow] = []
    for index, row in enumerate(rows):
        coordinate_rows.append(
            CoordinateRow(
                label_comp_id=row["label_comp_id"],
                label_atom_id=row["label_atom_id"],
                type_symbol=row["type_symbol"],
                cartn_x=row["Cartn_x"],
                cartn_y=row["Cartn_y"],
                cartn_z=row["Cartn_z"],
                occupancy=row.get("occupancy"),
                row_index=index,
                label_alt_id=_normalize_optional_value(row.get("label_alt_id")),
                label_asym_id=_normalize_optional_value(row.get("label_asym_id")),
                label_entity_id=_normalize_optional_value(row.get("label_entity_id")),
                label_seq_id=_normalize_optional_value(row.get("label_seq_id")),
                pdbx_pdb_ins_code=_normalize_optional_value(row.get("pdbx_PDB_ins_code")),
                auth_seq_id=_normalize_optional_value(row.get("auth_seq_id")),
                auth_asym_id=_normalize_optional_value(row.get("auth_asym_id")),
            )
        )
    return coordinate_rows


def _group_instances(comp_id: str, rows: list[CoordinateRow]) -> list[LigandInstance]:
    if not rows:
        raise CifProcessingError(f"No ligand rows found in _atom_site for comp_id {comp_id!r}")

    grouped: dict[tuple[str | None, ...], list[CoordinateRow]] = defaultdict(list)
    for row in rows:
        grouped[row.instance_key].append(row)

    instances: list[LigandInstance] = []
    for index, (key, instance_rows) in enumerate(sorted(grouped.items(), key=_instance_sort_key), start=1):
        instances.append(
            LigandInstance(
                comp_id=comp_id,
                key=key,
                rows=tuple(sorted(instance_rows, key=lambda row: row.row_index)),
                instance_index=index,
            )
        )
    return instances


def _instance_sort_key(item: tuple[tuple[str | None, ...], list[CoordinateRow]]) -> tuple[int, tuple[str, ...]]:
    key, rows = item
    first_row = min(row.row_index for row in rows)
    key_as_text = tuple(value or "." for value in key)
    return (first_row, key_as_text)


def _build_chem_comp(comp_id: str, instance: LigandInstance) -> ChemCompRow:
    today = date.today().isoformat()
    has_model_coordinates = any(
        _normalize_optional_value(row.cartn_x) is not None
        and _normalize_optional_value(row.cartn_y) is not None
        and _normalize_optional_value(row.cartn_z) is not None
        for row in instance.rows
    )
    return ChemCompRow(
        comp_id=comp_id,
        type="non-polymer",
        pdbx_type="HETAIN",
        pdbx_initial_date=today,
        pdbx_modified_date=today,
        pdbx_release_status="HOLD",
        pdbx_model_coordinates_missing_flag="N" if has_model_coordinates else "Y",
        pdbx_ideal_coordinates_missing_flag="Y",
    )


def _build_chem_comp_atom_rows(
    restraint_atoms: list[AtomRow],
    restraint_bonds: list[BondRow],
    instance: LigandInstance,
    restraint_file_name: str,
) -> list[dict[str, str]]:
    aromatic_atoms = _derive_aromatic_atoms(restraint_bonds)
    coordinate_by_atom_id = {row.label_atom_id: row for row in instance.rows}

    output_rows: list[dict[str, str]] = []
    for ordinal, atom in enumerate(restraint_atoms, start=1):
        coordinate = coordinate_by_atom_id.get(atom.atom_id)
        model_x, model_y, model_z = None, None, None
        if coordinate is not None:
            if coordinate.type_symbol != atom.type_symbol:
                raise CifProcessingError(
                    "Type symbol mismatch between restraint and coordinate rows for "
                    f"atom {atom.atom_id!r}: {atom.type_symbol!r} vs {coordinate.type_symbol!r}"
                )
            model_x, model_y, model_z = coordinate.cartn_x, coordinate.cartn_y, coordinate.cartn_z

        _log_non_zero_partial_charge(restraint_file_name, atom)

        output_rows.append(
            {
                "comp_id": atom.comp_id,
                "atom_id": atom.atom_id,
                "type_symbol": atom.type_symbol,
                "charge": "0",
                "pdbx_align": "1",
                "pdbx_aromatic_flag": "Y" if atom.atom_id in aromatic_atoms else "N",
                "model_Cartn_x": model_x,
                "model_Cartn_y": model_y,
                "model_Cartn_z": model_z,
                "pdbx_model_Cartn_x_ideal": atom.ideal_x,
                "pdbx_model_Cartn_y_ideal": atom.ideal_y,
                "pdbx_model_Cartn_z_ideal": atom.ideal_z,
                "pdbx_component_atom_id": atom.atom_id,
                "pdbx_component_comp_id": atom.comp_id,
                "pdbx_ordinal": str(ordinal),
            }
        )
    return output_rows


def _build_chem_comp_bond_rows(
    restraint_bonds: list[BondRow],
    rdkit_analysis: RdkitLigandAnalysis,
) -> list[dict[str, str]]:
    output_rows: list[dict[str, str]] = []
    for ordinal, bond in enumerate(restraint_bonds, start=1):
        bond_key = (bond.atom_id_1, bond.atom_id_2, ordinal - 1)
        aromatic_flag = "Y" if bond.bond_type == "aromatic" else "N"
        if bond_key not in rdkit_analysis.bond_value_orders:
            raise CifProcessingError(
                "RDKit analysis did not return a CCD value_order for bond "
                f"{bond.atom_id_1}-{bond.atom_id_2}"
            )
        value_order = rdkit_analysis.bond_value_orders[bond_key]

        output_rows.append(
            {
                "comp_id": bond.comp_id,
                "atom_id_1": bond.atom_id_1,
                "atom_id_2": bond.atom_id_2,
                "value_order": value_order,
                "pdbx_aromatic_flag": aromatic_flag,
                "pdbx_ordinal": str(ordinal),
            }
        )
    return output_rows


def _build_chem_comp_audit_rows(chem_comp: ChemCompRow) -> list[dict[str, str | None]]:
    return [
        {
            "comp_id": chem_comp.comp_id,
            "action_type": AUDIT_ACTION_TYPE,
            "date": chem_comp.pdbx_initial_date,
            "processing_site": AUDIT_PROCESSING_SITE,
            "annotator": AUDIT_ANNOTATOR,
            "details": AUDIT_DETAILS,
        }
    ]


def _analyze_ligand_with_rdkit(
    restraint: RestraintDefinition,
    instance: LigandInstance,
    diagram_path: Path,
) -> RdkitLigandAnalysis:
    try:
        return analyze_ligand(
            atoms=list(restraint.atoms),
            bonds=list(restraint.bonds),
            instance=instance,
            diagram_path=diagram_path,
        )
    except Exception as error:
        raise CifProcessingError(
            f"RDKit analysis failed for comp_id={restraint.comp_id} "
            f"instance={instance.instance_index:03d}: {error}"
        ) from error


def _log_rdkit_analysis(
    instance: LigandInstance,
    rdkit_analysis: RdkitLigandAnalysis,
) -> None:
    LOGGER.info(
        "RDKit comp_id=%s instance=%03d InChIKey=%s stereoisomers=%s chiral_centers=%s diagram=%s",
        instance.comp_id,
        instance.instance_index,
        rdkit_analysis.inchikey or "unavailable",
        rdkit_analysis.stereoisomer_count,
        _format_chiral_centers(rdkit_analysis.chiral_centers),
        rdkit_analysis.diagram_path or "not written",
    )
    if rdkit_analysis.isomeric_smiles:
        LOGGER.debug(
            "RDKit comp_id=%s instance=%03d isomeric_smiles=%s",
            instance.comp_id,
            instance.instance_index,
            rdkit_analysis.isomeric_smiles,
        )


def _derive_aromatic_atoms(restraint_bonds: Iterable[BondRow]) -> set[str]:
    aromatic_atoms: set[str] = set()
    for bond in restraint_bonds:
        if bond.bond_type == "aromatic":
            aromatic_atoms.add(bond.atom_id_1)
            aromatic_atoms.add(bond.atom_id_2)
    return aromatic_atoms


def _build_output_document(
    chem_comp: ChemCompRow,
    chem_comp_atom: list[dict[str, str]],
    chem_comp_bond: list[dict[str, str]],
) -> gemmi.cif.Document:
    document = gemmi.cif.Document()
    block = document.add_new_block(chem_comp.comp_id)

    block.set_pair("_chem_comp.id", chem_comp.comp_id)
    block.set_pair("_chem_comp.type", chem_comp.type)
    block.set_pair("_chem_comp.pdbx_type", chem_comp.pdbx_type)
    block.set_pair("_chem_comp.pdbx_initial_date", chem_comp.pdbx_initial_date)
    block.set_pair("_chem_comp.pdbx_modified_date", chem_comp.pdbx_modified_date)
    block.set_pair("_chem_comp.pdbx_release_status", chem_comp.pdbx_release_status)
    block.set_pair(
        "_chem_comp.pdbx_model_coordinates_missing_flag",
        chem_comp.pdbx_model_coordinates_missing_flag,
    )
    block.set_pair(
        "_chem_comp.pdbx_ideal_coordinates_missing_flag",
        chem_comp.pdbx_ideal_coordinates_missing_flag,
    )

    block.set_mmcif_category(
        "_pdbx_chem_comp_audit.",
        _rows_to_category(
            _build_chem_comp_audit_rows(chem_comp),
            [
                "comp_id",
                "action_type",
                "date",
                "processing_site",
                "annotator",
                "details",
            ],
        ),
    )

    block.set_mmcif_category(
        "_chem_comp_atom.",
        _rows_to_category(
            chem_comp_atom,
            [
                "comp_id",
                "atom_id",
                "type_symbol",
                "charge",
                "pdbx_align",
                "pdbx_aromatic_flag",
                "model_Cartn_x",
                "model_Cartn_y",
                "model_Cartn_z",
                "pdbx_model_Cartn_x_ideal",
                "pdbx_model_Cartn_y_ideal",
                "pdbx_model_Cartn_z_ideal",
                "pdbx_component_atom_id",
                "pdbx_component_comp_id",
                "pdbx_ordinal",
            ],
        ),
    )

    block.set_mmcif_category(
        "_chem_comp_bond.",
        _rows_to_category(
            chem_comp_bond,
            [
                "comp_id",
                "atom_id_1",
                "atom_id_2",
                "value_order",
                "pdbx_aromatic_flag",
                "pdbx_ordinal",
            ],
        ),
    )
    return document


def _category_rows(category: dict[str, list[str]], columns: list[str]) -> list[dict[str, str]]:
    present_columns = [column for column in columns if column in category]
    if not present_columns:
        return []
    row_count = len(category[present_columns[0]])
    rows: list[dict[str, str]] = []
    for row_index in range(row_count):
        row: dict[str, str] = {}
        for column in present_columns:
            values = category[column]
            if len(values) != row_count:
                raise CifProcessingError(
                    f"Category has inconsistent column lengths for column {column!r}."
                )
            row[column] = values[row_index]
        rows.append(row)
    return rows


def _rows_to_category(
    rows: Sequence[Mapping[str, str | None]],
    columns: list[str],
) -> dict[str, list[str | None]]:
    return {column: [row[column] for row in rows] for column in columns}


def _require_columns(category_name: str, category: dict[str, list[str]], columns: list[str]) -> None:
    missing = [column for column in columns if column not in category]
    if missing:
        raise CifProcessingError(
            f"Category {category_name} is missing required columns: {', '.join(missing)}"
        )


def _normalize_optional_value(value: str | None) -> str | None:
    if value is None or value in MISSING_VALUES:
        return None
    return value


def _parse_float(value: str | None) -> float | None:
    normalized = _normalize_optional_value(value)
    if normalized is None:
        return None
    return float(normalized)


def _log_non_zero_partial_charge(restraint_file_name: str, atom: AtomRow) -> None:
    partial_charge = _parse_float(atom.partial_charge)
    if partial_charge is None:
        return
    if round(partial_charge, 2) != 0.00:
        LOGGER.warning(
            "Non-zero partial charge observed in restraint file %s: partial_charge=%s comp_id=%s atom_id=%s type_symbol=%s",
            restraint_file_name,
            atom.partial_charge,
            atom.comp_id,
            atom.atom_id,
            atom.type_symbol,
        )


def _format_instance_key(key: tuple[str | None, ...]) -> str:
    labels = (
        "label_alt_id",
        "label_comp_id",
        "label_asym_id",
        "label_entity_id",
        "label_seq_id",
        "pdbx_PDB_ins_code",
        "auth_seq_id",
        "auth_asym_id",
    )
    return ", ".join(f"{label}={value or '.'}" for label, value in zip(labels, key))


def _format_chiral_centers(chiral_centers: tuple[tuple[str, str], ...]) -> str:
    if not chiral_centers:
        return "none"
    return ", ".join(f"{atom_id}:{label}" for atom_id, label in chiral_centers)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CifProcessingError as error:
        LOGGER.error("%s", error)
        raise SystemExit(1)
