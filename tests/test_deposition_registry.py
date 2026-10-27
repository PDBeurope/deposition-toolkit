"""Exercise registry transactions and command orchestration without remote services."""

import argparse
from datetime import datetime, timezone
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from deposition_toolkit.commands import deposit
from deposition_toolkit.ledger.depositions import find_session_id, save_deposition
from deposition_toolkit.ledger.models import Base, DepositionRegistry
from deposition_toolkit.stages.common.manifest import load_manifest, write_manifest
from deposition_toolkit.stages.deposit.deposition import DepositionResult


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        engine_patch = patch('deposition_toolkit.ledger.database.get_engine', return_value=self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.record = {
            'dep_id': 'D_1234', 'dataset': 'dataset', 'structure_id': 'model',
            'session_id': 'session-1',
        }


class RegistryTests(RegistryFixture):
    def test_insert_lookup_and_reupload_update_one_registry_row(self):
        self.assertIsNone(find_session_id('dataset', 'model'))
        save_deposition(self.record)
        with Session(self.engine) as session, session.begin():
            entry = session.scalar(select(DepositionRegistry))
            original_id = entry.id
            self.assertTrue(entry.automated_depostion)
            self.assertIsNotNone(entry.deposited_on)
            entry.deposited_on = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(find_session_id('dataset', 'model'), 'session-1')
        save_deposition({**self.record, 'session_id': 'session-2'})
        with Session(self.engine) as session:
            entries = session.scalars(select(DepositionRegistry)).all()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, original_id)
            self.assertEqual(entries[0].session_id, 'session-2')
            self.assertEqual(entries[0].dep_id, 'D_1234')
            self.assertGreater(entries[0].deposited_on.year, 2000)

    def test_lookup_is_scoped_to_dataset_and_structure(self):
        save_deposition(self.record)
        save_deposition({**self.record, 'dataset': 'other', 'dep_id': 'D_other', 'session_id': 'other-session'})
        self.assertEqual(find_session_id('dataset', 'model'), 'session-1')
        self.assertEqual(find_session_id('other', 'model'), 'other-session')
        self.assertIsNone(find_session_id('dataset', 'other-model'))

    def test_manual_entry_without_session_blocks_duplicate_deposition(self):
        with Session(self.engine) as session, session.begin():
            session.add(DepositionRegistry(
                dataset='dataset', structure_id='model', dep_id='D_manual',
                automated_depostion=False,
            ))
        with self.assertRaisesRegex(ValueError, 'no saved OneDep session'):
            find_session_id('dataset', 'model')

    def test_invalid_records_fail_before_database_access(self):
        with patch('deposition_toolkit.ledger.depositions.session_scope') as scope:
            for key in ('dep_id', 'dataset', 'structure_id', 'session_id'):
                with self.subTest(key=key), self.assertRaises(ValidationError):
                    save_deposition({**self.record, key: ''})
            with self.assertRaises(ValidationError):
                find_session_id('', 'model')
            scope.assert_not_called()

    def test_migration_upgrade_and_downgrade(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        migration = importlib.import_module(
            'deposition_toolkit.ledger.migrations.versions.0002_deposition_registry'
        )
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            self.assertIn('deposition_registry', inspect(connection).get_table_names())
            self.assertEqual(
                inspect(connection).get_unique_constraints('deposition_registry')[0]['column_names'],
                ['dataset', 'structure_id'],
            )
            migration.downgrade()
            self.assertNotIn('deposition_registry', inspect(connection).get_table_names())


class DepositCommandRegistryTests(RegistryFixture):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work_dir = Path(directory.name)
        self.structure_dir = self.work_dir / 'model-directory'
        self.structure_dir.mkdir()
        write_manifest(self.structure_dir, {
            'structure_id': 'model',
            'steps': {'prepare_dataset': {'source_tarball': '/incoming/dataset.tgz'}},
        })
        self.args = argparse.Namespace(
            work_dir=str(self.work_dir), email='', users='', country='UK',
            experiment_type='XRAY', no_poll=True, poll_interval=5, max_polls=64, dry_run=False,
        )

    def result(self, **overrides):
        return DepositionResult(
            **{'structure_id': 'model', 'dataset': 'dataset', 'session_id': 'session-1',
               'deposition_id': 'D_1234', 'status': 'done', **overrides}
        )

    def test_default_command_creates_then_resumes_registry_session(self):
        with patch.object(deposit, 'deposit_structure', side_effect=[self.result(), self.result()]) as stage:
            self.assertEqual(deposit.run(self.args), 0)
            self.assertEqual(deposit.run(self.args), 0)
        self.assertEqual([call.kwargs['session_id'] for call in stage.call_args_list], [None, 'session-1'])
        self.assertEqual(find_session_id('dataset', 'model'), 'session-1')
        manifest = load_manifest(self.structure_dir)
        self.assertEqual(manifest['steps']['deposition']['deposition_id'], 'D_1234')
        self.assertEqual(manifest['steps']['deposition']['deposition_record'], self.record)
        with Session(self.engine) as session:
            self.assertEqual(len(session.scalars(select(DepositionRegistry)).all()), 1)

    def test_lookup_failure_prevents_upload(self):
        with patch('deposition_toolkit.ledger.depositions.find_session_id',
                   side_effect=OperationalError('query', {}, Exception('offline'))), \
             patch.object(deposit, 'deposit_structure') as stage, \
             self.assertLogs('deposition_toolkit', level='ERROR'):
            self.assertEqual(deposit.run(self.args), 1)
        stage.assert_not_called()

    def test_save_failure_keeps_recovery_details_in_manifest(self):
        with patch('deposition_toolkit.ledger.depositions.save_deposition',
                   side_effect=OperationalError('query', {}, Exception('offline'))), \
             patch.object(deposit, 'deposit_structure', return_value=self.result()), \
             self.assertLogs('deposition_toolkit', level='ERROR'):
            self.assertEqual(deposit.run(self.args), 1)
        manifest = load_manifest(self.structure_dir)
        self.assertEqual(manifest['deposition_id'], 'D_1234')
        self.assertEqual(manifest['session_id'], 'session-1')
        self.assertEqual(manifest['steps']['deposition']['deposition_record'], self.record)

    def test_dry_run_skips_registry_reads_and_writes(self):
        self.args.dry_run = True
        with patch('deposition_toolkit.ledger.depositions.find_session_id') as lookup, \
             patch('deposition_toolkit.ledger.depositions.save_deposition') as save, \
             patch.object(deposit, 'deposit_structure', return_value=self.result(deposition_id='', session_id='')):
            self.assertEqual(deposit.run(self.args), 0)
        lookup.assert_not_called()
        save.assert_not_called()

    def test_failure_without_remote_id_does_not_register(self):
        with patch.object(deposit, 'deposit_structure', return_value=self.result(
            status='failed', deposition_id='', error='validation failed',
        )), patch('deposition_toolkit.ledger.depositions.save_deposition') as save:
            self.assertEqual(deposit.run(self.args), 1)
        save.assert_not_called()

    def test_partial_upload_failure_retains_registry_identity_for_retry(self):
        with patch.object(deposit, 'deposit_structure', return_value=self.result(
            status='failed', error='upload interrupted after remote creation',
        )):
            self.assertEqual(deposit.run(self.args), 1)
        self.assertEqual(find_session_id('dataset', 'model'), 'session-1')


if __name__ == '__main__':
    unittest.main()
