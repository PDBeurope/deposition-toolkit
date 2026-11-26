"""Post-deposition report integration using a local OneDep fixture."""

import csv
import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deposition_toolkit.cli import build_parser
from deposition_toolkit.commands import validation_report
from deposition_toolkit.stages.common.manifest import load_manifest, write_manifest


class ValidationReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name) / 'work'
        self.work.mkdir()
        self.onedep = Path(temp.name) / 'production'
        self.onedep.mkdir()
        settings = patch.object(validation_report, 'get_settings', return_value=SimpleNamespace(deposition_root=self.onedep))
        settings.start()
        self.addCleanup(settings.stop)

    def structure(self, name, dep_id='D_123', status='done', **fields):
        directory = self.work / name
        directory.mkdir(parents=True)
        write_manifest(directory, {
            'structure_id': name, 'source_tarball': '/input/my_dataset.tgz',
            'deposition_id': dep_id,
            'steps': {'deposition': {'status': status}}, **fields,
        })
        return directory

    def reports(self, dep_id='D_123', pdf=True):
        directory = self.onedep / dep_id
        directory.mkdir(exist_ok=True)
        (directory / f'{dep_id}_model_P1.cif.V1').write_text(
            "data_model\n_struct.title 'Example structure'\nloop_\n"
            "_database_2.database_id\n_database_2.pdbx_database_accession\nPDB pdb_00001abc\n#\n")
        for version in (2, 10):
            (directory / f'{dep_id}_val-data_P1.xml.V{version}').write_text(
                f'<ValidationReport><Entry PDB-resolution="{version}" DCC_Rfree="0.25"/></ValidationReport>')
            if pdf:
                (directory / f'{dep_id}_val-report-full_P1.pdf.V{version}').write_bytes(f'PDF version {version}'.encode())
        return directory

    def run_command(self):
        args = build_parser().parse_args(['validation-report', str(self.work)])
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return args.func(args)

    def step(self, directory):
        return load_manifest(directory)['steps']['validation_report']

    def test_default_outputs_use_config_dataset_and_latest_versions(self):
        directory = self.structure('nested/sample')
        self.reports()
        self.assertEqual(self.run_command(), 0)
        step = self.step(directory)
        self.assertEqual(step['status'], 'done')
        self.assertEqual(len(step['outputs']), 3)
        for output in step['outputs']:
            self.assertTrue(output.startswith('results/validation_report/my_dataset_'))
            self.assertTrue((self.work / output).is_file())
        html = (self.work / step['outputs'][0]).read_text()
        self.assertIn('"resolution":10.0', html)
        with (self.work / step['outputs'][1]).open() as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[1], ['Example structure', 'D_123', 'pdb_00001abc'])
        with zipfile.ZipFile(self.work / step['outputs'][2]) as archive:
            self.assertEqual(archive.namelist(), ['D_123_val-report-full_P1.pdf'])
            self.assertEqual(archive.read(archive.namelist()[0]), b'PDF version 10')
        self.assertTrue((self.work / step['log']).is_file())

    def test_pending_reports_can_be_collected_on_rerun(self):
        directory = self.structure('sample')
        self.assertEqual(self.run_command(), 1)
        self.assertEqual(self.step(directory)['status'], 'failed')
        self.reports(pdf=False)
        self.assertEqual(self.run_command(), 1)
        self.assertIn('PDF', self.step(directory)['error'])
        self.reports()
        self.assertEqual(self.run_command(), 0)
        self.assertNotIn('error', self.step(directory))

    def test_missing_pdb_id_warns_without_failing_and_clears_previous_failure(self):
        directory = self.structure('sample')
        self.assertEqual(self.run_command(), 1)
        production = self.reports()
        (production / 'D_123_model_P1.cif.V1').write_text(
            "data_model\n_struct.title 'Example structure'\n")
        self.structure('with_pdb_id', dep_id='D_456')
        self.reports('D_456')
        args = build_parser().parse_args(['validation-report', str(self.work)])
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(args.func(args), 0)
        self.assertIn('2 succeeded, 0 failed', output.getvalue())
        self.assertIn('WARNING: PDB IDs missing for 1 deposition(s) in dataset my_dataset.', output.getvalue())
        self.assertNotIn('may still be generating', output.getvalue())
        step = self.step(directory)
        self.assertEqual(step['status'], 'done')
        self.assertNotIn('error', step)
        self.assertIn('PDB ID not found', step['warnings'][0])
        log = (self.work / step['log']).read_text()
        self.assertIn('WARNING [D_123] PDB ID not found', log)
        self.assertNotIn('ERROR [D_123]', log)
        for output_path in step['outputs']:
            self.assertTrue((self.work / output_path).is_file())
        with (self.work / step['outputs'][1]).open() as stream:
            self.assertEqual(list(csv.reader(stream))[1], ['Example structure', 'D_123', ''])

    def test_missing_pdb_id_does_not_hide_missing_pdf_failure(self):
        directory = self.structure('sample')
        production = self.reports(pdf=False)
        (production / 'D_123_model_P1.cif.V1').write_text(
            "data_model\n_struct.title 'Example structure'\n")
        self.assertEqual(self.run_command(), 1)
        step = self.step(directory)
        self.assertEqual(step['status'], 'failed')
        self.assertIn('PDF', step['error'])
        self.assertNotIn('PDB ID', step['error'])
        self.assertIn('PDB ID not found', step['warnings'][0])

    def test_skip_undeposited_dry_run_and_excluded_and_fail_missing_id(self):
        pending = self.structure('pending', status='failed')
        dry = self.structure('dry', steps={'deposition': {'status': 'done', 'reports': {'dry_run': True}}})
        excluded = self.structure('excluded', excluded=True)
        missing = self.structure('missing', dep_id=None)
        self.assertEqual(self.run_command(), 1)
        for directory in (pending, dry, excluded):
            self.assertEqual(self.step(directory)['status'], 'not applicable')
        self.assertIn('no deposition ID', self.step(missing)['error'])

    def test_multiple_datasets_deduplicate_ids_and_support_step_id(self):
        first = self.structure('a')
        self.structure('b')
        second = self.structure('c', dep_id=None, source_tarball='/input/other.tar.gz',
                                steps={'deposition': {'status': 'done', 'deposition_id': 'D_456'}})
        self.reports()
        self.reports('D_456')
        self.assertEqual(self.run_command(), 0)
        for directory in (first, second):
            with zipfile.ZipFile(self.work / self.step(directory)['outputs'][2]) as archive:
                self.assertEqual(len(archive.namelist()), 1)
        self.assertIn('other_validation_dashboard.html', self.step(second)['outputs'][0])

    def test_malformed_xml_does_not_prevent_other_reports(self):
        bad, good = self.structure('bad'), self.structure('good', dep_id='D_456')
        directory = self.reports()
        self.reports('D_456')
        (directory / 'D_123_val-data_P1.xml.V10').write_text('<broken')
        self.assertEqual(self.run_command(), 1)
        self.assertEqual(self.step(bad)['status'], 'failed')
        self.assertEqual(self.step(good)['status'], 'done')

    def test_invalid_id_cannot_collect_files_outside_deposition_root(self):
        directory = self.structure('bad', dep_id='../D_123')
        self.assertEqual(self.run_command(), 1)
        self.assertIn('Invalid deposition ID', self.step(directory)['error'])

    def test_output_write_failure_marks_every_structure_failed(self):
        first, second = self.structure('first'), self.structure('second')
        self.reports()
        with patch.object(validation_report.val_report, 'write_metadata_file', side_effect=OSError('disk full')):
            self.assertEqual(self.run_command(), 1)
        for directory in (first, second):
            self.assertIn('disk full', self.step(directory)['error'])
            self.assertTrue((self.work / self.step(directory)['log']).is_file())

    def test_dataset_filename_collisions_keep_both_bundles(self):
        first = self.structure('first', source_tarball='/input/a b.tgz')
        second = self.structure('second', dep_id='D_456', source_tarball='/input/a_b.tgz')
        self.reports()
        self.reports('D_456')
        self.assertEqual(self.run_command(), 0)
        self.assertNotEqual(self.step(first)['outputs'], self.step(second)['outputs'])
        for directory in (first, second):
            self.assertTrue(all((self.work / path).is_file() for path in self.step(directory)['outputs']))


if __name__ == '__main__':
    unittest.main()
