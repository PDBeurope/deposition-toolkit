"""Regression coverage for progress visibility, heartbeat and successful run logs."""
import io
import logging
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deposition_toolkit.cli import build_parser
from deposition_toolkit.commands.progress import BatchProgress
from deposition_toolkit.commands.shared import structure_log
from deposition_toolkit.stages.common.manifest import load_manifest, update_step


class LiveProgressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name).resolve()
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        root.setLevel(logging.INFO)

    def structure(self, name):
        structure = self.work / name
        structure.mkdir(parents=True)
        (structure / 'model_struc.cif').write_text('data_model\n')
        (structure / 'model_sf.cif').write_text('data_sf\n')
        return structure

    def test_progress_survives_log_capture_and_stops_on_exit(self):
        structure = self.structure('sample')
        output = io.StringIO()
        with redirect_stdout(output), BatchProgress(['sample'], label='Test') as progress:
            with progress.track('sample', lambda: 'done'):
                with structure_log(self.work, structure, 'example'):
                    progress.stage('Long-running operation')
                    self.assertIn('Long-running operation', output.getvalue())
                    update_step(structure, 'example', 'done')
        self.assertFalse(progress._thread.is_alive())
        self.assertIn('1/1 structures', output.getvalue())
        self.assertNotIn('\033', output.getvalue())
        step = load_manifest(structure)['steps']['example']
        self.assertNotIn('Long-running operation', (self.work / step['log']).read_text())

    def test_heartbeat_runs_while_work_is_blocked(self):
        heartbeat = threading.Event()

        class Output(io.StringIO):
            def write(self, value):
                count = super().write(value)
                if threading.current_thread().name == 'cli-progress' and 'Blocking call' in value:
                    heartbeat.set()
                return count

        output = Output()
        with BatchProgress(['sample'], label='Test', stream=output,
                           refresh_interval=0.01, heartbeat_interval=0.01) as progress:
            with progress.track('sample', lambda: 'done'):
                progress.stage('Blocking call')
                self.assertTrue(heartbeat.wait(2), 'No heartbeat during a blocking operation')
        self.assertFalse(progress._thread.is_alive())

    def test_interruption_retains_pending_and_marks_current_interrupted(self):
        output = io.StringIO()
        with self.assertRaises(KeyboardInterrupt):
            with BatchProgress(['current', 'later'], label='Test', show_lists=True, stream=output) as progress:
                with progress.track('current', lambda: 'done'):
                    raise KeyboardInterrupt()
        self.assertEqual(progress.states, {'current': 'interrupted', 'later': 'pending'})
        self.assertIn('Pending (1): later', output.getvalue())
        self.assertIn('Interrupted (1): current', output.getvalue())
        self.assertFalse(progress._thread.is_alive())

    def test_tty_display_redraws_and_retains_all_names_in_final_lists(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        output = Terminal()
        names = [f'sample-{i}' for i in range(8)]
        with patch('deposition_toolkit.commands.progress.shutil.get_terminal_size', return_value=(80, 24)):
            with BatchProgress(names, label='Deposit', show_lists=True, success_label='Deposited', stream=output) as progress:
                self.assertIn('+3 more', output.getvalue())
                with progress.track(names[0], lambda: 'done'):
                    progress.stage('Submitting files')
                with progress.track(names[1], lambda: 'failed'):
                    pass
        text = output.getvalue()
        self.assertIn('\033[2K', text)
        self.assertIn('Deposited (1): sample-0', text)
        self.assertIn('Failed (1): sample-1', text)
        self.assertIn('sample-7', text)
        self.assertIn('2/8 structures', text)

    def test_deposit_dry_run_populates_both_manifest_log_paths(self):
        from deposition_toolkit.commands import deposit

        structure = self.structure('sample')
        args = build_parser().parse_args(['deposit', str(self.work), '--dry-run'])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(deposit.run(args), 0)
        step = load_manifest(structure)['steps']['deposition']
        for key in ('log', 'output'):
            text = (self.work / step[key]).read_text()
            self.assertIn('Discovering coordinate and structure factor files', text)
            self.assertIn('coordinates=', text)
            self.assertIn('no upload performed', text)
            self.assertIn('status=done', text)
            self.assertNotIn('Traceback', text)
        self.assertIn('Dry-run passed (1): sample', output.getvalue())
        self.assertIn('Pending (0): none', output.getvalue())

    def test_deposition_registry_failure_keeps_remaining_structures_pending(self):
        from deposition_toolkit.commands import deposit

        first = self.structure('a')
        self.structure('b')
        args = build_parser().parse_args(['deposit', str(self.work)])
        output = io.StringIO()
        with redirect_stdout(output), patch.object(deposit, 'deposit_structure') as upload:
            self.assertEqual(deposit.run(args, session_lookup=lambda *args: (_ for _ in ()).throw(ValueError('offline'))), 1)
        upload.assert_not_called()
        self.assertIn('Failed (1): a', output.getvalue())
        self.assertIn('Pending (1): b', output.getvalue())
        step = load_manifest(first)['steps']['deposition']
        self.assertIn('registry lookup failed', (self.work / step['log']).read_text())
        self.assertIn('registry lookup failed', (self.work / 'results/logs/deposition/a.log').read_text())

    def test_validation_progress_names_each_file_and_reports_failures(self):
        from deposition_toolkit.commands import mmcif_validation
        from deposition_toolkit.stages.validate import mmcif_validate

        first = self.structure('a')
        second = self.structure('b')
        dictionary = self.work / 'dictionary.dic'
        dictionary.touch()
        args = build_parser().parse_args(['mmcif-validation', str(self.work), '--dictionary', str(dictionary)])
        output = io.StringIO()

        def validate(dictionary, cif):
            # This runs inside the structure's stdout redirection.
            self.assertIn(f'Current (00:00:00): {cif.parent.name}', output.getvalue())
            self.assertIn(cif.name, output.getvalue())
            if cif.parent == first:
                return [SimpleNamespace(severity='error', line=1, item='_test', message='bad input')], None
            return [], None

        with redirect_stdout(output), patch.object(mmcif_validate, 'validate_file', side_effect=validate):
            self.assertEqual(mmcif_validation.run(args), 1)
        text = output.getvalue()
        self.assertIn('Validating file 1/2:', text)
        self.assertIn('Validating file 2/2:', text)
        self.assertIn('2/2 structures', text)
        self.assertIn('Succeeded: 1 | failed: 1 | pending: 0', text)
        self.assertEqual(load_manifest(first)['steps']['mmcif_validation']['status'], 'failed')
        self.assertEqual(load_manifest(second)['steps']['mmcif_validation']['status'], 'done')


if __name__ == '__main__':
    unittest.main()
