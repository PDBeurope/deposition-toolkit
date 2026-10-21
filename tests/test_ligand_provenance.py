"""Integration checks for CCD instance provenance and targeted CIF renaming."""
import argparse
import csv
import logging
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import gemmi

from deposition_toolkit.commands import ccd_finalise, lig_rename
from deposition_toolkit.stages.ccd_creation import ccd_creator
from deposition_toolkit.stages.ccd_creation.finalise_step import finalize_ccds
from deposition_toolkit.stages.ccd_creation.ligand_renaming import lig_renaming
from deposition_toolkit.stages.ccd_creation.rdkit_ligand import RdkitLigandAnalysis
from deposition_toolkit.stages.common.manifest import load_manifest, set_top_level_fields


COORDINATES = """data_model
_struct.title 'LIG remains in free text'
loop_
_atom_site.id
_atom_site.label_comp_id
_atom_site.auth_comp_id
_atom_site.label_atom_id
_atom_site.type_symbol
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.label_alt_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.auth_seq_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
10 LIG LIG C1 C 0 0 0 1 . B 2 . ? 101 A 1
11 LIG LIG C2 C 1 0 0 1 . B 2 . ? 101 A 1
20 LIG LIG C1 C 0 1 0 1 . C 3 . ? 102 A 1
21 LIG LIG C2 C 1 1 0 1 . C 3 . ? 102 A 1
30 HOH HOH O O 0 0 1 1 . D 4 . ? 103 A 1
loop_
_chem_comp.id
_chem_comp.type
_chem_comp.name
LIG non-polymer 'LIG description'
HOH non-polymer water
loop_
_pdbx_nonpoly_scheme.asym_id
_pdbx_nonpoly_scheme.entity_id
_pdbx_nonpoly_scheme.ndb_seq_num
_pdbx_nonpoly_scheme.mon_id
_pdbx_nonpoly_scheme.pdb_mon_id
_pdbx_nonpoly_scheme.auth_mon_id
_pdbx_nonpoly_scheme.pdb_seq_num
_pdbx_nonpoly_scheme.pdb_strand_id
B 2 1 LIG LIG LIG 101 A
C 3 1 LIG LIG LIG 102 A
loop_
_pdbx_entity_nonpoly.entity_id
_pdbx_entity_nonpoly.comp_id
2 LIG
3 LIG
loop_
_struct_conn.id
_struct_conn.ptnr1_label_comp_id
_struct_conn.ptnr1_label_asym_id
_struct_conn.ptnr1_label_seq_id
_struct_conn.ptnr2_label_comp_id
_struct_conn.ptnr2_label_asym_id
_struct_conn.ptnr2_label_seq_id
bond1 LIG B . LIG C .
"""
RESTRAINTS = """data_ligand
loop_
_chem_comp_atom.comp_id
_chem_comp_atom.atom_id
_chem_comp_atom.type_symbol
_chem_comp_atom.x
_chem_comp_atom.y
_chem_comp_atom.z
LIG C1 C 0 0 0
LIG C2 C 1 0 0
loop_
_chem_comp_bond.comp_id
_chem_comp_bond.atom_id_1
_chem_comp_bond.atom_id_2
_chem_comp_bond.type
LIG C1 C2 single
"""


class LigandProvenanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.structure = self.work / "sample"
        self.structure.mkdir()
        self.coordinates = self.structure / "sample_struc.cif"
        self.coordinates.write_text(COORDINATES)
        (self.structure / "sample_lig.cif").write_text(RESTRAINTS)
        self.stage1 = self.work / "results/ccd/stage_1/sample"
        self.builder = ccd_creator.CcdDirectoryBuilder(self.structure, self.stage1)
        analysis = RdkitLigandAnalysis({("C1", "C2", 0): "SING"}, "KEY", "CC", (), 1, None)
        with patch.object(ccd_creator, "analyze_ligand", return_value=analysis):
            self.outputs = self.builder.run()
        self.records = load_manifest(self.structure)["ccd_ligands"]

    def allocate(self, codes=("AAA", "BBB")):
        for record, code in zip(self.records, codes):
            record["CCD"] = code
        set_top_level_fields(self.structure, ccd_ligands=self.records)

    def rename(self):
        return lig_renaming.rename_ligands_for_structure(
            "sample", self.structure, self.records, logging.getLogger("test"),
        )

    def test_creator_records_every_atom_and_stable_filename(self):
        self.assertEqual([p.name for p in self.outputs], ["LIG__001.cif", "LIG__002.cif"])
        self.assertEqual([r["atom_site_ids"] for r in self.records], [["10", "11"], ["20", "21"]])
        self.assertEqual([r["instance"]["label_asym_id"] for r in self.records], ["B", "C"])
        self.assertEqual(self.records[0]["source_block"], "model")

    def test_multiple_codes_update_atoms_and_references_and_are_idempotent(self):
        self.allocate()
        result = self.rename()
        self.assertEqual(result.status, "done", result.messages)
        block = gemmi.cif.read_file(str(self.coordinates)).sole_block()
        self.assertEqual(list(block.find_values("_atom_site.label_comp_id")), ["AAA", "AAA", "BBB", "BBB", "HOH"])
        self.assertEqual(list(block.find_values("_atom_site.auth_comp_id")), ["AAA", "AAA", "BBB", "BBB", "HOH"])
        self.assertEqual(list(block.find_values("_pdbx_nonpoly_scheme.mon_id")), ["AAA", "BBB"])
        self.assertEqual(list(block.find_values("_pdbx_entity_nonpoly.comp_id")), ["AAA", "BBB"])
        self.assertEqual(block.find_value("_struct_conn.ptnr1_label_comp_id"), "AAA")
        self.assertEqual(block.find_value("_struct_conn.ptnr2_label_comp_id"), "BBB")
        self.assertEqual(set(block.find_values("_chem_comp.id")), {"AAA", "BBB", "HOH"})
        self.assertIn("LIG remains in free text", self.coordinates.read_text())
        self.assertIn("LIG description", self.coordinates.read_text())
        before = self.coordinates.read_bytes()
        self.assertEqual(self.rename().replacements, 0)
        self.assertEqual(self.coordinates.read_bytes(), before)

    def test_shared_code_is_supported(self):
        self.allocate(("AAA", "AAA"))
        self.assertEqual(self.rename().status, "done")
        block = gemmi.cif.read_file(str(self.coordinates)).sole_block()
        self.assertEqual(list(block.find_values("_chem_comp.id")), ["AAA", "HOH"])

    def test_missing_codes_ids_overlaps_or_changed_identity_do_not_write(self):
        for problem in ("pending", "database", "missing", "overlap", "identity", "null", "incomplete"):
            with self.subTest(problem=problem):
                self.allocate()
                before = self.coordinates.read_bytes()
                records = deepcopy(self.records)
                if problem == "pending":
                    self.records[0]["CCD"] = "PENDING_ALLOCATION"
                elif problem == "database":
                    self.records[0]["CCD"] = "EXISTS_IN_DB"
                elif problem == "missing":
                    self.records[0]["atom_site_ids"][0] = "999"
                elif problem == "overlap":
                    self.records.append(deepcopy(self.records[0]))
                elif problem == "null":
                    self.records[0]["CCD"] = None
                elif problem == "incomplete":
                    self.records[0]["atom_site_ids"].pop()
                else:
                    self.records[0]["instance"]["auth_seq_id"] = "999"
                self.assertEqual(self.rename().status, "failed")
                self.assertEqual(before, self.coordinates.read_bytes())
                self.records = records

    def test_all_coordinate_variants_validate_before_any_write(self):
        self.allocate()
        (self.structure / "sample_enriched_struc.cif").write_text(COORDINATES.replace("10 LIG", "99 LIG"))
        before = self.coordinates.read_bytes()
        self.assertEqual(self.rename().status, "failed")
        self.assertEqual(before, self.coordinates.read_bytes())

    def test_ambiguous_connection_is_rejected(self):
        self.allocate()
        self.coordinates.write_text(COORDINATES.replace("bond1 LIG B . LIG C .", "bond1 LIG ? . LIG C ."))
        before = self.coordinates.read_bytes()
        self.assertEqual(self.rename().status, "failed")
        self.assertEqual(before, self.coordinates.read_bytes())

    def test_alternate_conformations_and_models_are_distinct(self):
        document = gemmi.cif.read_string(COORDINATES)
        block = document.sole_block()
        atoms = block.get_mmcif_category("_atom_site.")
        atoms["label_asym_id"][:4] = ["B"] * 4
        atoms["label_entity_id"][:4] = ["2"] * 4
        atoms["auth_seq_id"][:4] = ["101"] * 4
        atoms["label_alt_id"][:4] = ["A", "A", "B", "B"]
        block.set_mmcif_category("_atom_site.", atoms)
        rows = ccd_creator._read_coordinate_rows(block)[:4]
        self.assertEqual(len(ccd_creator._group_instances("LIG", rows)), 2)
        atoms["label_alt_id"][:4] = [False] * 4
        atoms["pdbx_PDB_model_num"][:4] = ["1", "1", "2", "2"]
        block.set_mmcif_category("_atom_site.", atoms)
        rows = ccd_creator._read_coordinate_rows(block)[:4]
        self.assertEqual(len(ccd_creator._group_instances("LIG", rows)), 2)

    def finalise_args(self):
        parser = argparse.ArgumentParser()
        ccd_finalise.register(parser.add_subparsers())
        return parser.parse_args(["ccd-finalise", str(self.work)])

    def finalise(self, codes=("AAA", "BBB"), exit_code=0, names=None):
        stage2 = self.work / "results/ccd/stage_2/sample"
        stage2.mkdir(parents=True, exist_ok=True)
        final = self.work / "results/ccd/final"
        final.mkdir(parents=True, exist_ok=True)
        with (final / "ccd_processing_details.csv").open("w") as output:
            writer = csv.DictWriter(output, ["structure", "ligand_name", "CCD", "status"])
            writer.writeheader()
            # Reverse report order to verify matching by filename, never position.
            for name, code in reversed(list(zip(names or [r["ligand_name"] for r in self.records], codes))):
                writer.writerow(dict(structure="sample", ligand_name=name, CCD=code, status="new_representative"))
        with patch.object(finalize_ccds, "main", return_value=exit_code):
            return ccd_finalise.run(self.finalise_args())

    def test_finalisation_binds_by_filename_and_command_needs_no_csv(self):
        self.assertEqual(self.finalise(), 0)
        records = load_manifest(self.structure)["ccd_ligands"]
        self.assertEqual([r["CCD"] for r in records], ["AAA", "BBB"])
        (self.work / "results/ccd/final/ccd_processing_details.csv").unlink()
        self.assertEqual(lig_rename.run(argparse.Namespace(work_dir=str(self.work), summary_csv=None)), 0)

    def test_failed_preview_or_unmatched_finalisation_clears_previous_codes(self):
        for codes, exit_code, names in ((["AAA", "PENDING_ALLOCATION"], 0, None),
                                        (["AAA", "BBB"], 1, None),
                                        (["AAA", "BBB"], 0, ["unknown.cif", "LIG__002.cif"])):
            with self.subTest(codes=codes, exit_code=exit_code, names=names):
                self.allocate(("OLD", "OLD"))
                self.assertEqual(self.finalise(codes, exit_code, names), 1)
                records = load_manifest(self.structure)["ccd_ligands"]
                self.assertNotIn("OLD", [r.get("CCD") for r in records])
                self.assertTrue(any("CCD" not in r for r in records))

    def test_no_provenance_or_csv_without_instance_names_fails_safely(self):
        set_top_level_fields(self.structure, ccd_ligands=[])
        before = self.coordinates.read_bytes()
        self.assertEqual(lig_rename.run(argparse.Namespace(work_dir=str(self.work), summary_csv=None)), 1)
        self.assertEqual(before, self.coordinates.read_bytes())
        with self.assertRaises(ValueError):
            lig_renaming.manifest_ligands(self.structure, [{"CCD": "AAA"}])

    def test_failed_recreation_invalidates_old_allocations(self):
        self.allocate()
        self.coordinates.write_text("data_invalid\n_entry.id invalid\n")
        with self.assertRaises(ccd_creator.CifProcessingError):
            self.builder.run()
        self.assertEqual(load_manifest(self.structure)["ccd_ligands"], [])

    def test_explicit_csv_import_uses_names_and_persists_codes(self):
        report = self.work / "legacy.csv"
        report.write_text("structure,ligand_name,CCD\nsample,LIG__002.cif,BBB\nsample,LIG__001.cif,AAA\n")
        self.assertEqual(lig_rename.run(argparse.Namespace(work_dir=str(self.work), summary_csv=str(report))), 0)
        records = load_manifest(self.structure)["ccd_ligands"]
        self.assertEqual([r["CCD"] for r in records], ["AAA", "BBB"])
        self.assertEqual(lig_rename.run(argparse.Namespace(work_dir=str(self.work), summary_csv=None)), 0)
