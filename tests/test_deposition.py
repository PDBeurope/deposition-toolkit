"""Deposition routing and persistence checks without contacting OneDep."""

import argparse
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from deposition_toolkit.commands import deposit
from deposition_toolkit.stages.common.manifest import load_manifest, write_manifest
from deposition_toolkit.stages.deposit.deposition import (
    DepositionResult, dataset_from_manifest, deposit_structure,
)


class DepositionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work_dir = Path(directory.name)
        self.structure_dir = self.work_dir / 'directory-name'
        self.structure_dir.mkdir()
        (self.structure_dir / 'model_struc.cif').write_text('data_model\n')
        (self.structure_dir / 'model_sf.cif').write_text('data_sf\n')
        write_manifest(self.structure_dir, {
            'structure_id': 'manifest-id',
            'steps': {'prepare_dataset': {
                'source_tarball': '/incoming/RFKAT2B_Cryo_20261014.tgz',
            }},
        })
        self.dsp = ModuleType('onedep_lib')
        self.dsp.Country = SimpleNamespace(UK='UK')
        self.dsp.ExperimentType = SimpleNamespace(XRAY='XRAY')
        self.dsp.FileType = SimpleNamespace(MMCIF_COORD='coord', CRYSTAL_STRUC_FACTORS='sf')
        self.dsp.DepositError = type('DepositError', (), {})
        self.dsp.check_auth_key = MagicMock(return_value=True)
        self.dep = MagicMock()
        self.dep.__enter__.return_value = self.dep
        self.dep.session_id = 'saved-session'
        self.dep.remote_dep_id = None
        self.dep.add_file.side_effect = ['new-coord', 'new-sf']
        for method in ('check_required_files', 'check_mmcif_file',
                       'check_mmcif_category', 'check_file_type'):
            getattr(self.dep, method).return_value = SimpleNamespace(ok=True, issues=[])
        self.dep.deposit.return_value = 'D_1234'
        self.dsp.deposit_init = MagicMock(return_value=self.dep)
        self.dsp.deposit_resume = MagicMock(return_value=self.dep)
        self.old_files = [
            SimpleNamespace(file_id='old-coord', file_type='coord'),
            SimpleNamespace(file_id='old-sf', file_type='sf'),
            SimpleNamespace(file_id='other-file', file_type='other'),
        ]
        self.dsp.list_sessions = MagicMock(return_value=[(
            SimpleNamespace(session_id='saved-session'), self.old_files,
        )])
        config_module = ModuleType('onedep_lib.config')
        config_module.DepositConfig = MagicMock()
        config_module.DepositConfig.load.return_value = SimpleNamespace(session_dir=Path('/sessions'))
        modules = patch.dict('sys.modules', {
            'onedep_lib': self.dsp, 'onedep_lib.config': config_module,
        })
        modules.start()
        self.addCleanup(modules.stop)

    def upload(self, **kwargs):
        return deposit_structure(self.structure_dir, poll=False, **kwargs)

    def test_new_upload_returns_database_record(self):
        result = self.upload(email='user@example.org', users='user-id')
        self.assertEqual(result.status, 'done')
        self.dsp.deposit_init.assert_called_once()
        self.dsp.deposit_resume.assert_not_called()
        self.dep.set_experiment_type.assert_called_once_with('XRAY')
        self.dep.__exit__.assert_called_once()
        self.assertEqual(result.to_record(), {
            'dep_id': 'D_1234', 'dataset': 'RFKAT2B_Cryo_20261014',
            'structure_id': 'manifest-id', 'session_id': 'saved-session',
        })

    def test_resume_uses_saved_settings_and_replaces_only_matching_types(self):
        self.dep.remote_dep_id = 'D_1234'
        def submit():
            self.assertEqual(self.dep.remove_file.call_args_list,
                             [unittest.mock.call('old-coord'), unittest.mock.call('old-sf')])
            return 'D_1234'
        self.dep.deposit.side_effect = submit
        with patch.dict('os.environ', {'WWPDB_EMAIL': '', 'WWPDB_USERS': ''}):
            result = self.upload(session_id='saved-session')
        self.assertEqual(result.status, 'done')
        self.dsp.deposit_resume.assert_called_once_with('saved-session', config=unittest.mock.ANY)
        self.dsp.deposit_init.assert_not_called()
        self.dep.set_experiment_type.assert_not_called()
        self.assertEqual(result.deposition_id, 'D_1234')

    def test_failed_replacement_checks_keep_old_files(self):
        self.dep.remote_dep_id = 'D_1234'
        self.dep.check_mmcif_file.return_value = SimpleNamespace(ok=False, issues=[])
        result = self.upload(session_id='saved-session')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(self.dep.remove_file.call_args_list,
                         [unittest.mock.call('new-coord'), unittest.mock.call('new-sf')])
        self.dep.deposit.assert_not_called()
        self.assertEqual(result.session_id, 'saved-session')

    def test_registration_exception_removes_partial_replacements(self):
        self.dep.remote_dep_id = 'D_1234'
        self.dep.add_file.side_effect = ['new-coord', OSError('unreadable')]
        with self.assertLogs('deposition', level='ERROR'):
            result = self.upload(session_id='saved-session')
        self.assertEqual(result.status, 'failed')
        self.dep.remove_file.assert_called_once_with('new-coord')
        self.dep.deposit.assert_not_called()

    def test_resume_without_remote_deposition_never_creates_one(self):
        with self.assertLogs('deposition', level='ERROR'):
            result = self.upload(session_id='saved-session')
        self.assertIn('no remote deposition', result.error)
        self.dsp.deposit_init.assert_not_called()
        self.dep.deposit.assert_not_called()

    def test_unknown_session_does_not_fall_back_to_new_deposition(self):
        self.dsp.deposit_resume.side_effect = KeyError('unknown session')
        with self.assertLogs('deposition', level='ERROR'):
            result = self.upload(session_id='unknown')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.session_id, 'unknown')
        self.dsp.deposit_init.assert_not_called()

    def test_upload_exception_retains_remote_and_session_ids(self):
        def fail():
            self.dep.remote_dep_id = 'D_created_before_error'
            raise RuntimeError('upload failed')
        self.dep.deposit.side_effect = fail
        with self.assertLogs('deposition', level='ERROR'):
            result = self.upload(email='user@example.org', users='user-id')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.deposition_id, 'D_created_before_error')
        self.assertEqual(result.session_id, 'saved-session')

    def test_dry_run_does_not_open_a_session(self):
        result = self.upload(session_id='saved-session', dry_run=True)
        self.assertEqual(result.status, 'done')
        self.assertEqual(result.deposition_id, '')
        self.dsp.deposit_init.assert_not_called()
        self.dsp.deposit_resume.assert_not_called()

    def test_required_files_failure_prevents_upload(self):
        self.dep.check_required_files.return_value = SimpleNamespace(ok=False, issues=[])
        result = self.upload(email='user@example.org', users='user-id')
        self.assertEqual(result.status, 'failed')
        self.dep.deposit.assert_not_called()

    def test_archive_names(self):
        for suffix in ('.tgz', '.tar.gz', '.tar.bz2', '.tar.xz', '.tar'):
            with self.subTest(suffix=suffix):
                self.assertEqual(dataset_from_manifest({'source_tarball': '/a/dataset' + suffix}), 'dataset')
        self.assertEqual(dataset_from_manifest({}), '')

    def test_command_per_structure_lookup_and_manifest_record(self):
        second = self.work_dir / 'second'
        second.mkdir()
        write_manifest(second, {'structure_id': 'second-id', 'source_tarball': '/incoming/other.tar.gz'})
        lookup = MagicMock(side_effect=lambda dataset, structure: 'saved-session' if structure == 'manifest-id' else None)
        def upload(structure_dir, **kwargs):
            manifest = load_manifest(structure_dir)
            session = kwargs['session_id'] or 'new-session'
            return DepositionResult(
                structure_id=manifest['structure_id'], dataset=dataset_from_manifest(manifest),
                session_id=session, deposition_id='D_1234', status='done',
            )
        args = argparse.Namespace(work_dir=str(self.work_dir), email='', users='', country='UK',
                                  experiment_type='XRAY', no_poll=True, poll_interval=5,
                                  max_polls=64, dry_run=False)
        with patch.object(deposit, 'deposit_structure', side_effect=upload) as stage:
            self.assertEqual(deposit.run(args, session_lookup=lookup), 0)
        self.assertEqual(lookup.call_args_list, [
            unittest.mock.call('RFKAT2B_Cryo_20261014', 'manifest-id'),
            unittest.mock.call('other', 'second-id'),
        ])
        self.assertEqual([call.kwargs['session_id'] for call in stage.call_args_list], ['saved-session', None])
        manifest = load_manifest(self.structure_dir)
        self.assertEqual(manifest['session_id'], 'saved-session')
        self.assertEqual(manifest['steps']['deposition']['deposition_record'], {
            'dep_id': 'D_1234', 'dataset': 'RFKAT2B_Cryo_20261014',
            'structure_id': 'manifest-id', 'session_id': 'saved-session',
        })


if __name__ == '__main__':
    unittest.main()
