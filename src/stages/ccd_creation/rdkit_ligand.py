"""RDKit helpers for ligand kekulization, stereochemistry, and diagrams."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rdkit import Chem
from rdkit.Chem import AllChem, Draw, inchi
from rdkit.Chem.EnumerateStereoisomers import (
    EnumerateStereoisomers,
    StereoEnumerationOptions,
)
from rdkit.Geometry import Point3D


LOGGER = logging.getLogger(__name__)


class AtomLike(Protocol):
    atom_id: str
    type_symbol: str


class BondLike(Protocol):
    atom_id_1: str
    atom_id_2: str
    bond_type: str


class InstanceLike(Protocol):
    comp_id: str
    instance_index: int
    rows: tuple[object, ...]


@dataclass(frozen=True)
class RdkitLigandAnalysis:
    bond_value_orders: dict[tuple[str, str, int], str]
    inchikey: str | None
    isomeric_smiles: str | None
    chiral_centers: tuple[tuple[str, str], ...]
    stereoisomer_count: int
    diagram_path: Path | None


def analyze_ligand(
    atoms: list[AtomLike],
    bonds: list[BondLike],
    instance: InstanceLike,
    diagram_path: Path | None = None,
) -> RdkitLigandAnalysis:
    """Build an RDKit molecule, kekulize aromatic bonds, and inspect stereochemistry."""
    atom_index_by_id, atom_id_by_index, mol = _build_mol(atoms, bonds, instance)
    Chem.AssignStereochemistryFrom3D(mol)
    Chem.AssignStereochemistry(mol, force=True, cleanIt=True)

    stereoisomers = _enumerate_unassigned_stereoisomers(mol)
    analysis_mol = stereoisomers[0] if stereoisomers else mol

    inchikey = _safe_inchikey(analysis_mol)
    isomeric_smiles = Chem.MolToSmiles(analysis_mol, isomericSmiles=True)
    chiral_centers = _chiral_centers(analysis_mol, atom_id_by_index)
    written_diagram = _write_diagram(analysis_mol, diagram_path)

    if len(stereoisomers) > 1:
        LOGGER.warning(
            "Ambiguous stereochemistry for comp_id=%s instance=%03d: RDKit enumerated %s variants",
            instance.comp_id,
            instance.instance_index,
            len(stereoisomers),
        )

    return RdkitLigandAnalysis(
        bond_value_orders=_bond_value_orders(mol, bonds, atom_index_by_id),
        inchikey=inchikey,
        isomeric_smiles=isomeric_smiles,
        chiral_centers=tuple(chiral_centers),
        stereoisomer_count=max(1, len(stereoisomers)),
        diagram_path=written_diagram,
    )


def _build_mol(
    atoms: list[AtomLike],
    bonds: list[BondLike],
    instance: InstanceLike,
) -> tuple[dict[str, int], dict[int, str], Chem.Mol]:
    rw_mol = Chem.RWMol()
    atom_index_by_id: dict[str, int] = {}
    atom_id_by_index: dict[int, str] = {}

    for atom in atoms:
        rdkit_atom = Chem.Atom(atom.type_symbol.title())
        atom_index = rw_mol.AddAtom(rdkit_atom)
        atom_index_by_id[atom.atom_id] = atom_index
        atom_id_by_index[atom_index] = atom.atom_id

    for bond in bonds:
        atom_1 = atom_index_by_id.get(bond.atom_id_1)
        atom_2 = atom_index_by_id.get(bond.atom_id_2)
        if atom_1 is None or atom_2 is None:
            continue
        rw_mol.AddBond(atom_1, atom_2, _rdkit_bond_type(bond.bond_type))

    mol = rw_mol.GetMol()
    _add_conformer(mol, atom_index_by_id, instance)
    Chem.SanitizeMol(mol)
    Chem.Kekulize(mol, clearAromaticFlags=True, canonical=True)
    return atom_index_by_id, atom_id_by_index, mol


def _add_conformer(
    mol: Chem.Mol,
    atom_index_by_id: dict[str, int],
    instance: InstanceLike,
) -> None:
    coordinate_by_atom_id = {getattr(row, "label_atom_id"): row for row in instance.rows}
    conformer = Chem.Conformer(mol.GetNumAtoms())
    conformer.Set3D(True)
    for atom_id, atom_index in atom_index_by_id.items():
        row = coordinate_by_atom_id.get(atom_id)
        if row is None:
            continue
        try:
            conformer.SetAtomPosition(
                atom_index,
                Point3D(
                    float(getattr(row, "cartn_x")),
                    float(getattr(row, "cartn_y")),
                    float(getattr(row, "cartn_z")),
                ),
            )
        except (TypeError, ValueError):
            LOGGER.debug(
                "Skipping non-numeric coordinates for comp_id=%s instance=%03d atom=%s",
                instance.comp_id,
                instance.instance_index,
                atom_id,
            )
    mol.AddConformer(conformer, assignId=True)


def _rdkit_bond_type(bond_type: str) -> Chem.BondType:
    normalized = bond_type.lower()
    bond_type_map = {
        "single": Chem.BondType.SINGLE,
        "sing": Chem.BondType.SINGLE,
        "double": Chem.BondType.DOUBLE,
        "doub": Chem.BondType.DOUBLE,
        "triple": Chem.BondType.TRIPLE,
        "trip": Chem.BondType.TRIPLE,
        "aromatic": Chem.BondType.AROMATIC,
        "arom": Chem.BondType.AROMATIC,
    }
    return bond_type_map.get(normalized, Chem.BondType.SINGLE)


def _bond_value_orders(
    mol: Chem.Mol,
    source_bonds: list[BondLike],
    atom_index_by_id: dict[str, int],
) -> dict[tuple[str, str, int], str]:
    value_orders: dict[tuple[str, str, int], str] = {}
    for index, source_bond in enumerate(source_bonds):
        atom_1 = atom_index_by_id.get(source_bond.atom_id_1)
        atom_2 = atom_index_by_id.get(source_bond.atom_id_2)
        if atom_1 is None or atom_2 is None:
            continue
        rdkit_bond = mol.GetBondBetweenAtoms(atom_1, atom_2)
        if rdkit_bond is None:
            continue
        value_orders[(source_bond.atom_id_1, source_bond.atom_id_2, index)] = _ccd_value_order(
            rdkit_bond.GetBondType()
        )
    return value_orders


def _ccd_value_order(bond_type: Chem.BondType) -> str:
    if bond_type == Chem.BondType.DOUBLE:
        return "DOUB"
    if bond_type == Chem.BondType.TRIPLE:
        return "TRIP"
    return "SING"


def _enumerate_unassigned_stereoisomers(mol: Chem.Mol) -> list[Chem.Mol]:
    options = StereoEnumerationOptions(onlyUnassigned=True, unique=True)
    return list(EnumerateStereoisomers(mol, options=options))


def _safe_inchikey(mol: Chem.Mol) -> str | None:
    try:
        return inchi.MolToInchiKey(mol)
    except Exception as error:
        LOGGER.warning("Could not calculate InChIKey with RDKit: %s", error)
        return None


def _chiral_centers(
    mol: Chem.Mol,
    atom_id_by_index: dict[int, str],
) -> list[tuple[str, str]]:
    centers = Chem.FindMolChiralCenters(
        mol,
        force=True,
        includeUnassigned=True,
        includeCIP=True,
    )
    return [(atom_id_by_index.get(atom_index, str(atom_index)), label) for atom_index, label in centers]


def _write_diagram(mol: Chem.Mol, diagram_path: Path | None) -> Path | None:
    if diagram_path is None:
        return None

    diagram_path.parent.mkdir(parents=True, exist_ok=True)
    mol_for_drawing = Chem.Mol(mol)
    mol_for_drawing.RemoveAllConformers()
    AllChem.Compute2DCoords(mol_for_drawing)
    drawer = Draw.MolDraw2DCairo(500, 500)
    drawer.drawOptions().addAtomIndices = True
    drawer.DrawMolecule(mol_for_drawing)
    drawer.FinishDrawing()
    diagram_path.write_bytes(drawer.GetDrawingText())
    return diagram_path
