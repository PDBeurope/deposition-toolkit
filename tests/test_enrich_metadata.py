"""Regression checks for enrichment validation logs and manifest updates."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import gemmi

from deposition_toolkit.commands import enrich_metadata
from deposition_toolkit.stages.common.manifest import load_manifest, update_step
from deposition_toolkit.stages.metadata_enrichment import enrich_cif


class EnrichmentLogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work_dir = Path(directory.name).resolve()
        self.structure_dir = self.work_dir / "RFKAT2B-x0006"
        self.structure_dir.mkdir()
        self.cif_path = self.structure_dir / "RFKAT2B-x0006_struc.cif"
        self.cif_path.write_text("data_original\n_entry.id original\n_struct.title 'Original title'\n")
        self.metadata = self.work_dir / "metadata.json"
        self.metadata.write_text(json.dumps({"_struct": {"title": "Enriched title"}}))
        self.validation_log = (
            self.work_dir / "results/logs/metadata_enrichment" / self.structure_dir.name
            / "RFKAT2B-x0006_struc_validation_errors.txt"
        )
        self.args = argparse.Namespace(
            work_dir=str(self.work_dir), metadata_json=str(self.metadata), entry_id="model",
            dictionary=str(self.work_dir / "dictionary.dic"), skip_validation=False,
        )
        # Keep real CIF processing and manifest writes; avoid external CCD lookups.
        lookup = patch.object(enrich_cif, "enrich_chem_comp")
        lookup.start()
        self.addCleanup(lookup.stop)

    def step(self):
        return load_manifest(self.structure_dir)["steps"]["metadata_enrichment"]

    @staticmethod
    def finding(severity):
        return SimpleNamespace(line=7, item="_struct.title", message="Test finding", severity=severity)

    def test_nested_logs_and_manifest_for_clean_warning_and_error_results(self):
        for severities, status, exit_code in (([], "done", 0), (["warning"], "done", 0),
                                               (["error", "warning"], "failed", 1)):
            with self.subTest(severities=severities):
                update_step(self.structure_dir, "metadata_enrichment", "failed", error="Previous failure")
                with patch("validate_mmcif.validate", return_value=[self.finding(s) for s in severities]) as validate:
                    self.assertEqual(enrich_metadata.run(self.args), exit_code)
                validate.assert_called_once_with(Path(self.args.dictionary), self.cif_path)
                self.assertTrue(self.validation_log.is_file())
                step = self.step()
                self.assertEqual(step["status"], status)
                self.assertEqual(step["validation_errors"], severities.count("error"))
                self.assertEqual(step["validation_warnings"], severities.count("warning"))
                self.assertNotIn("error", step)
                self.assertEqual(step["outputs"], [
                    str(self.cif_path.relative_to(self.work_dir)),
                    str(self.validation_log.relative_to(self.work_dir)),
                ])
                content = self.validation_log.read_text()
                for severity in severities:
                    self.assertIn(severity, content)
                self.assertEqual(gemmi.cif.read_file(str(self.cif_path)).sole_block().find_value("_struct.title"),
                                 "'Enriched title'")

    def test_counts_and_log_outputs_accumulate_across_coordinate_files(self):
        second_cif = self.structure_dir / "second_struc.cif"
        second_cif.write_text(self.cif_path.read_text())
        with patch("validate_mmcif.validate", return_value=[self.finding("error"), self.finding("warning")]):
            self.assertEqual(enrich_metadata.run(self.args), 1)
        step = self.step()
        self.assertEqual(step["validation_errors"], 2)
        self.assertEqual(step["validation_warnings"], 2)
        self.assertEqual(len(step["outputs"]), 4)
        self.assertTrue(all((self.work_dir / output).is_file() for output in step["outputs"]))

    def test_skip_validation_records_cif_without_validation_logs(self):
        self.args.skip_validation = True
        with patch("validate_mmcif.validate") as validate:
            self.assertEqual(enrich_metadata.run(self.args), 0)
        validate.assert_not_called()
        self.assertFalse(self.validation_log.exists())
        step = self.step()
        self.assertEqual(step["status"], "done")
        self.assertEqual(step["outputs"], [str(self.cif_path.relative_to(self.work_dir))])
        self.assertEqual(step["validation_errors"], 0)
        self.assertEqual(step["validation_warnings"], 0)

    def test_direct_enrichment_call_creates_custom_log_parents(self):
        log_path = self.work_dir / "custom/nested/validation.txt"
        with patch("validate_mmcif.validate", return_value=[self.finding("warning")]):
            result = enrich_cif.enrich_cif_file(
                self.cif_path, self.metadata, dictionary_path=Path(self.args.dictionary),
                validation_log_path=log_path,
            )
        self.assertTrue(log_path.is_file())
        self.assertEqual(result["validation_log"], str(log_path))
        self.assertEqual(result["validation_warnings"], 1)


if __name__ == "__main__":
    unittest.main()
