"""Manifest validation, approval and atomic database loading."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from deposition_toolkit.cli import build_parser
from deposition_toolkit.commands import ledger
from deposition_toolkit.ledger.models import Base, Structure
from deposition_toolkit.ledger.structures import load_manifests


class ManifestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        engine_patch = patch("deposition_toolkit.ledger.database.get_engine", return_value=self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.confirm = Mock(return_value=True)

    def manifest(self, name="model", **fields):
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        payload = {"group_id": "G_1", "structure_id": name,
                   "steps": {"prepare_dataset": {"source_tarball": "/input/data.tar.gz"}},
                   "excluded": True, "custom": {"values": [1, None, "text"]}, **fields}
        (directory / "manifest.json").write_text(json.dumps(payload))
        return payload

    def rows(self):
        with Session(self.engine) as session:
            return [(row.id, row.group_id, row.dataset, row.structure_id, row.structure_details)
                    for row in session.scalars(select(Structure).order_by(Structure.id))]

    def test_new_load_stores_complete_manifest_including_excluded(self):
        payload = self.manifest()
        self.assertEqual(load_manifests(self.root, confirm=self.confirm), 1)
        self.confirm.assert_not_called()
        self.assertEqual(self.rows()[0][1:], ("G_1", "data", "model", payload))

    def test_existing_group_requires_approval_even_when_only_new_structures(self):
        self.manifest()
        load_manifests(self.root, confirm=self.confirm)
        (self.root / "model" / "manifest.json").unlink()
        self.manifest("new")
        self.confirm.return_value = False
        before = self.rows()
        self.assertIsNone(load_manifests(self.root, confirm=self.confirm))
        self.confirm.assert_called_once_with("G_1", 0, 1)
        self.assertEqual(self.rows(), before)

    def test_override_updates_inserts_and_retains_other_rows(self):
        self.manifest()
        self.manifest("retained")
        load_manifests(self.root, confirm=self.confirm)
        original_id = self.rows()[0][0]
        (self.root / "retained" / "manifest.json").unlink()
        payload = self.manifest(custom={"replacement": True})
        self.manifest("new")
        self.assertEqual(load_manifests(self.root, confirm=self.confirm), 2)
        self.confirm.assert_called_once_with("G_1", 1, 1)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], original_id)
        self.assertEqual(rows[0][-1], payload)

    def test_invalid_batch_writes_nothing(self):
        for fields in ({"group_id": ""}, {"group_id": "G_2"},
                       {"structure_id": "model"}, {"steps": {}, "dataset": ""},
                       {"dataset": "other"}):
            with self.subTest(fields=fields):
                self.manifest()
                self.manifest("bad", **fields)
                with self.assertRaises(ValueError):
                    load_manifests(self.root, confirm=self.confirm)
                self.assertEqual(self.rows(), [])
        self.confirm.assert_not_called()

    def test_empty_missing_and_malformed_inputs(self):
        for root in (self.root, self.root / "missing"):
            with self.assertRaises((ValueError, OSError)):
                load_manifests(root, confirm=self.confirm)
        self.manifest()
        path = self.root / "model" / "manifest.json"
        for content in ("{", "[]", '{"group_id": NaN}'):
            path.write_text(content)
            with self.assertRaises(ValueError):
                load_manifests(self.root, confirm=self.confirm)
        path.unlink()
        (path.parent / "model_struc.cif").touch()
        with self.assertRaisesRegex(ValueError, "manifest missing"):
            load_manifests(self.root, confirm=self.confirm)

    def test_concurrent_new_structure_requires_new_review(self):
        self.manifest()
        load_manifests(self.root, confirm=self.confirm)

        def concurrent_load(*_):
            with Session(self.engine) as session, session.begin():
                session.add(Structure(group_id="G_1", dataset="data", structure_id="concurrent",
                                      structure_details={}))
            return True

        self.manifest(custom={"changed": True})
        with self.assertRaisesRegex(ValueError, "group changed"):
            load_manifests(self.root, confirm=concurrent_load)
        self.assertNotIn("changed", self.rows()[0][-1]["custom"])

    def test_cli_confirmation_defaults_to_no_and_accepts_yes(self):
        self.manifest()
        args = build_parser().parse_args(["ledger", "load-manifest", str(self.root)])
        self.assertEqual(ledger.run(args), 0)
        self.manifest(custom={"updated": True})
        before = self.rows()
        for answer in ("", "no", "anything", EOFError(), KeyboardInterrupt()):
            kwargs = {"side_effect": answer} if isinstance(answer, BaseException) else {"return_value": answer}
            with patch("builtins.input", **kwargs) as prompt:
                self.assertEqual(ledger.run(args), 0)
            prompt.assert_called_once()
            self.assertEqual(self.rows(), before)
        with patch("builtins.input", return_value="yes"):
            self.assertEqual(ledger.run(args), 0)
        self.assertEqual(self.rows()[0][-1]["custom"], {"updated": True})


if __name__ == "__main__":
    unittest.main()
