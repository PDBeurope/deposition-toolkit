"""Command reporting preserves diagnostics while keeping batch output concise."""

import argparse
import io
import logging
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from deposition_toolkit.commands import ccd_creation, ccd_annotate, enrich_metadata, mmcif_validation
from deposition_toolkit.cli import build_parser
from deposition_toolkit.commands.shared import structure_log, print_run_summary, require_structures
from deposition_toolkit.stages.common.manifest import load_manifest, update_step, set_top_level_fields


class CommandReportingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name).resolve()
        self.root = logging.getLogger()
        original_level = self.root.level
        self.root.setLevel(logging.INFO)
        self.addCleanup(self.root.setLevel, original_level)

    def structure(self, name):
        path = self.work / name
        path.mkdir(parents=True)
        (path / 'sample_struc.cif').write_text('data_sample\n')
        return path

    def log(self, structure, step):
        entry = load_manifest(structure)['steps'][step]
        path = self.work / entry['log']
        self.assertTrue(path.is_file())
        return path.read_text()

    def test_logs_capture_prints_warnings_and_tracebacks_without_terminal_noise(self):
        structure = self.structure('sample')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with structure_log(self.work, structure, 'example'):
                print('first diagnostic')
                logging.getLogger('stage').warning('second diagnostic')
                print('third diagnostic')
                raise ValueError('bad input')
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')
        text = self.log(structure, 'example')
        for expected in ('first diagnostic', 'second diagnostic', 'third diagnostic', 'Traceback', 'bad input'):
            self.assertIn(expected, text)
        self.assertEqual(load_manifest(structure)['steps']['example']['status'], 'failed')

    def test_verbose_keeps_prints_in_both_log_and_terminal_and_restores_handlers(self):
        structure = self.structure('sample')
        self.root.setLevel(logging.DEBUG)
        handlers = list(self.root.handlers)
        out = io.StringIO()
        with redirect_stdout(out), structure_log(self.work, structure, 'example'):
            print('verbose diagnostic')
            update_step(structure, 'example', 'done')
        self.assertIn('verbose diagnostic', out.getvalue())
        self.assertIn('verbose diagnostic', self.log(structure, 'example'))
        self.assertEqual(self.root.handlers, handlers)
        self.assertEqual(self.root.level, logging.DEBUG)

    def test_nested_structures_have_isolated_logs_and_reruns_replace_previous_detail(self):
        first, second = self.structure('group1/sample'), self.structure('group2/sample')
        for structure, text in ((first, 'first'), (second, 'second'), (first, 'rerun')):
            with structure_log(self.work, structure, 'example'):
                print(text)
                update_step(structure, 'example', 'done')
        first_text, second_text = self.log(first, 'example'), self.log(second, 'example')
        self.assertIn('rerun', first_text)
        self.assertNotIn('first\n', first_text)
        self.assertNotIn('second\n', first_text)
        self.assertIn('second\n', second_text)
        self.assertNotIn('rerun', second_text)

    def test_interrupts_propagate_and_logging_is_restored(self):
        structure = self.structure('sample')
        handlers = list(self.root.handlers)
        with self.assertRaises(KeyboardInterrupt):
            with structure_log(self.work, structure, 'example'):
                raise KeyboardInterrupt()
        self.assertEqual(self.root.handlers, handlers)
        self.assertEqual(self.root.level, logging.INFO)

    def test_ccd_batch_summary_distribution_and_failed_structure_logs(self):
        for name in ('one', 'two', 'three', 'empty', 'broken', 'excluded'):
            self.structure(name)
        set_top_level_fields(self.work / 'excluded', excluded=True)

        def build(builder):
            print('detailed CCD processing ' + builder.input_dir.name)
            if builder.input_dir.name == 'broken':
                raise ValueError('cannot build')
            counts = {'one': 1, 'two': 2, 'three': 3, 'empty': 0}
            return [builder.output_dir / f'{i}.cif' for i in range(counts[builder.input_dir.name])]

        out = io.StringIO()
        with patch('deposition_toolkit.stages.ccd_creation.ccd_creator.CcdDirectoryBuilder.run', autospec=True, side_effect=build), redirect_stdout(out):
            result = ccd_creation.run(argparse.Namespace(work_dir=str(self.work), audit_details=None))
        self.assertEqual(result, 1)
        text = out.getvalue()
        self.assertIn('5 structures; 3 succeeded, 2 failed, 0 not applicable, 1 excluded', text)
        self.assertIn('CCD files created: 6', text)
        for count in (1, 2, 3):
            self.assertIn(f'{count} ligand(s): 1 structure(s)', text)
        self.assertNotIn('detailed CCD processing', text)
        self.assertIn('Traceback', self.log(self.work / 'broken', 'ccd_creation'))
        self.assertIn('No CCD files generated', self.log(self.work / 'empty', 'ccd_creation'))
        for name in ('one', 'two', 'three'):
            self.assertIn('detailed CCD processing ' + name, self.log(self.work / name, 'ccd_creation'))

    def test_enrichment_continues_after_failure_and_counts_validation(self):
        self.structure('bad')
        self.structure('good')
        metadata = self.work / 'metadata.json'
        metadata.write_text('{}')

        def enrich(cif, *args, **kwargs):
            logging.getLogger('enrich').info('details for %s', cif.parent.name)
            if cif.parent.name == 'bad':
                raise RuntimeError('enrichment error')
            return {'output_cif': str(cif), 'validation_errors': 0, 'validation_warnings': 2}

        args = argparse.Namespace(work_dir=str(self.work), metadata_json=str(metadata), skip_validation=True, entry_id='model')
        out = io.StringIO()
        with patch('deposition_toolkit.stages.metadata_enrichment.enrich_cif.enrich_cif_file', side_effect=enrich), redirect_stdout(out):
            self.assertEqual(enrich_metadata.run(args), 1)
        self.assertIn('enrich-metadata: 2 structures; 1 succeeded, 1 failed', out.getvalue())
        self.assertIn('Validation: 0 errors, 2 warnings', out.getvalue())
        self.assertIn('Traceback', self.log(self.work / 'bad', 'metadata_enrichment'))
        self.assertIn('details for good', self.log(self.work / 'good', 'metadata_enrichment'))

    def test_not_applicable_has_a_log_and_is_not_counted_as_success(self):
        structure = self.structure('sample')
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ccd_annotate.run(argparse.Namespace(work_dir=str(self.work), annotate_bin='unused')), 0)
        self.assertIn('0 succeeded, 0 failed, 1 not applicable', out.getvalue())
        self.assertIn('No stage-1 CCD CIF files', self.log(structure, 'ccd_annotate'))

    def test_all_excluded_still_prints_summary(self):
        structure = self.structure('sample')
        set_top_level_fields(structure, excluded=True)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(require_structures(self.work, 'example'), [])
        self.assertIn('0 structures; 0 succeeded, 0 failed, 0 not applicable, 1 excluded', out.getvalue())

    def test_missing_dictionary_records_failed_logs_and_preserves_exit_code(self):
        structure = self.structure('sample')
        out = io.StringIO()
        args = argparse.Namespace(work_dir=str(self.work), dictionary=str(self.work / 'missing.dic'))
        with redirect_stdout(out):
            self.assertEqual(mmcif_validation.run(args), 2)
        self.assertIn('1 structures; 0 succeeded, 1 failed', out.getvalue())
        self.assertIn('Dictionary file not found', self.log(structure, 'mmcif_validation'))

    def test_finalisation_without_stage_inputs_logs_not_applicable(self):
        structure = self.structure('sample')
        args = build_parser().parse_args(['ccd-finalise', str(self.work)])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(args.func(args), 0)
        self.assertIn('0 succeeded, 0 failed, 1 not applicable', out.getvalue())
        self.assertIn('No stage-2 CCD directory', self.log(structure, 'ccd_finalise'))

    def test_finaliser_quiet_logging_preserves_file_diagnostics(self):
        from deposition_toolkit.stages.ccd_creation.finalise_step import finalize_ccds

        logger = finalize_ccds.logger
        previous_handlers, previous_level, previous_propagate = logger.handlers, logger.level, logger.propagate
        logger.handlers = []
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                path = finalize_ccds.configure_logging(self.work, logging.INFO, quiet=True)
                logger.info('detail in file')
                logger.warning('visible warning')
            self.assertNotIn('detail in file', out.getvalue())
            self.assertEqual(out.getvalue().count('visible warning'), 1)
            self.assertIn('detail in file', path.read_text())
            self.assertIn('visible warning', path.read_text())
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers, logger.level, logger.propagate = previous_handlers, previous_level, previous_propagate


if __name__ == '__main__':
    unittest.main()
