"""Rename recorded ligand instances using CIF categories, preserving unrelated text."""
from __future__ import annotations

from collections import defaultdict

import gemmi


UNALLOCATED = {"", ".", "?", "PENDING_ALLOCATION", "EXISTS_IN_DB"}


def normalized(value):
    return None if value in (None, False, "", ".", "?") else str(value)


def rename_document(document: gemmi.cif.Document, ligands: list[dict]) -> int:
    if not ligands:
        raise ValueError("No ligand provenance; rerun ccd-creation, ccd-annotate and ccd-finalise.")
    blocks = defaultdict(list)
    for ligand in ligands:
        code = ligand.get("CCD")
        if not isinstance(code, str) or not code.isalnum() or code in UNALLOCATED:
            raise ValueError(f"No allocated CCD for {ligand['ligand_name']}; run ccd-finalise.")
        blocks[ligand["source_block"]].append(ligand)
    replacements = 0
    for block_name, records in blocks.items():
        block = document.find_block(block_name)
        if block is None:
            raise ValueError(f"Recorded coordinate block {block_name!r} is missing.")
        atoms = block.get_mmcif_category("_atom_site.")
        ids = atoms.get("id", [])
        if not ids or any(normalized(value) is None for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("_atom_site.id must be present and unique.")
        indices = {value: i for i, value in enumerate(ids)}
        changes = {}
        for record in records:
            wanted = record.get("atom_site_ids", [])
            if not wanted or len(set(wanted)) != len(wanted) or set(wanted) - indices.keys():
                raise ValueError(f"Missing or invalid atom-site IDs for {record['ligand_name']}.")
            if not record.get("instance") or "label_comp_id" not in record["instance"]:
                raise ValueError(f"Missing residue identifiers for {record['ligand_name']}.")
            matching_ids = set()
            for i, atom_id in enumerate(ids):
                matches = True
                for column, expected in record["instance"].items():
                    value = atoms[column][i] if column in atoms else None
                    allowed = {record["original_comp_id"], record["CCD"]} if column == "label_comp_id" else {expected}
                    if normalized(value) not in {normalized(v) for v in allowed}:
                        matches = False
                        break
                if matches:
                    matching_ids.add(atom_id)
            if matching_ids != set(wanted):
                raise ValueError(f"Atom-site IDs no longer match the complete recorded instance for {record['ligand_name']}.")
            for atom_id in wanted:
                i = indices[atom_id]
                if i in changes:
                    raise ValueError(f"Overlapping ligand provenance at atom-site ID {atom_id}.")
                changes[i] = record["CCD"]
        # Keep original row context for references and validate everything before writing.
        contexts = []
        for i in range(len(ids)):
            context = {key: values[i] for key, values in atoms.items()}
            context["target"] = changes.get(i, context["label_comp_id"])
            contexts.append(context)
        replacements += rename_references(block, contexts)
        for i, target in changes.items():
            for column in ("label_comp_id", "auth_comp_id"):
                if column in atoms and atoms[column][i] != target:
                    atoms[column][i] = target
                    replacements += 1
        block.set_mmcif_category("_atom_site.", atoms)
    return replacements


def rename_references(block, atoms):
    """Resolve component references by residue context; expand component definitions."""
    count = 0
    # Each tuple: category, component columns, reference identifiers -> atom-site identifiers.
    references = [
        ("_pdbx_nonpoly_scheme.", ("mon_id", "pdb_mon_id", "auth_mon_id"),
         {"asym_id": "label_asym_id", "entity_id": "label_entity_id",
          "pdb_seq_num": "auth_seq_id", "pdb_strand_id": "auth_asym_id", "pdb_ins_code": "pdbx_PDB_ins_code"}),
        ("_pdbx_entity_nonpoly.", ("comp_id",), {"entity_id": "label_entity_id"}),
        ("_atom_site_anisotrop.", ("pdbx_label_comp_id", "pdbx_auth_comp_id"), {"id": "id"}),
    ]
    for partner in ("ptnr1", "ptnr2"):
        references.append(("_struct_conn.", (f"{partner}_label_comp_id", f"{partner}_auth_comp_id"), {
            f"{partner}_label_asym_id": "label_asym_id",
            f"{partner}_label_seq_id": "label_seq_id",
            f"{partner}_auth_asym_id": "auth_asym_id",
            f"{partner}_auth_seq_id": "auth_seq_id",
            f"pdbx_{partner}_PDB_ins_code": "pdbx_PDB_ins_code",
            f"pdbx_{partner}_label_alt_id": "label_alt_id",
            f"{partner}_label_atom_id": "label_atom_id",
        }))
    for category_name, comp_columns, selectors in references:
        category = block.get_mmcif_category(category_name)
        if not category:
            continue
        for column in comp_columns:
            for i, old in enumerate(category.get(column, [])):
                candidates = [atom for atom in atoms if old in (atom.get("label_comp_id"), atom.get("auth_comp_id"), atom["target"])]
                for reference, source in selectors.items():
                    if reference in category and (normalized(category[reference][i]) is not None
                                                  or (category[reference][i] is False and source == "pdbx_PDB_ins_code")):
                        candidates = [atom for atom in candidates if normalized(atom.get(source)) == normalized(category[reference][i])]
                targets = {atom["target"] for atom in candidates}
                if len(targets) > 1:
                    raise ValueError(f"Ambiguous component reference in {category_name}{column} row {i + 1}.")
                if targets and old != next(iter(targets)):
                    category[column][i] = next(iter(targets))
                    count += 1
        block.set_mmcif_category(category_name, category)
    category = block.get_mmcif_category("_chem_comp.")
    if category and "id" in category:
        expanded = {column: [] for column in category}
        seen = set()
        for i, old in enumerate(category["id"]):
            targets = {atom["target"] for atom in atoms if atom["label_comp_id"] == old} or {old}
            for target in sorted(targets):
                if target in seen:
                    continue
                seen.add(target)
                for column, values in category.items():
                    expanded[column].append(target if column == "id" else values[i])
                count += target != old
        block.set_mmcif_category("_chem_comp.", expanded)
    return count
