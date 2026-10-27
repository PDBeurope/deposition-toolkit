"""Ledger infrastructure checks without production credentials or services."""

import ast
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from deposition_toolkit.config import Settings
from deposition_toolkit.ledger.database import get_engine, session_scope
from deposition_toolkit.ledger.models import Base
from deposition_toolkit.ledger.schemas import LedgerSchema
from deposition_toolkit.ledger.settings import LedgerSettings, get_ledger_settings


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "src/deposition_toolkit/ledger"


class LedgerTests(unittest.TestCase):
    def test_four_connection_settings_default_database_to_user(self):
        for database, expected in ((None, "ledger_user"), ("", "ledger_user"),
                                   ("  ", "ledger_user"), ("custom_db", "custom_db")):
            with self.subTest(database=database), patch.dict(os.environ, {
                "POSTGRES_HOST": "localhost",
                "POSTGRES_PORT": "5433",
                "POSTGRES_USER": "ledger_user",
                "POSTGRES_PASSWORD": "test-secret",
            }, clear=True):
                settings = Settings(_env_file=None, postgres_database=database)
            with patch("deposition_toolkit.ledger.settings.get_settings", return_value=settings):
                ledger = get_ledger_settings()
            self.assertEqual(ledger.url.database, expected)
            self.assertEqual(ledger.url.username, "ledger_user")
            self.assertEqual(ledger.url.host, "localhost")
            self.assertEqual(ledger.url.port, 5433)
            self.assertEqual(ledger.url.password, "test-secret")

    def test_url_preserves_special_credentials_and_hides_password(self):
        settings = LedgerSettings(host="localhost", port=5432, user="user@name",
                                  password="p@ss:/%word", database="ledger")
        self.assertEqual(settings.url.drivername, "postgresql+psycopg")
        self.assertEqual(settings.url.password, "p@ss:/%word")
        self.assertNotIn("p@ss:/%word", str(settings.url))
        self.assertNotIn("p@ss:/%word", repr(settings))

    def test_engine_is_lazy_and_reused(self):
        settings = LedgerSettings(host="localhost", port=5432, user="user",
                                  password="test", database="ledger")
        get_engine.cache_clear()
        try:
            with patch("deposition_toolkit.ledger.database.get_ledger_settings", return_value=settings), \
                 patch("psycopg.connect", side_effect=AssertionError("Unexpected connection")):
                engine = get_engine()
                self.assertIs(get_engine(), engine)
                engine.dispose()
        finally:
            get_engine.cache_clear()

    def test_transaction_commits_success_and_rolls_back_failure(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE transaction_test (value INTEGER)"))
            with patch("deposition_toolkit.ledger.database.get_engine", return_value=engine):
                with session_scope() as session:
                    session.execute(text("INSERT INTO transaction_test VALUES (1)"))
                with self.assertRaisesRegex(RuntimeError, "abort"):
                    with session_scope() as session:
                        session.execute(text("INSERT INTO transaction_test VALUES (2)"))
                        raise RuntimeError("abort")
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT value FROM transaction_test")).scalars().all(), [1])
            with patch("deposition_toolkit.ledger.database.get_engine", return_value=engine):
                with self.assertRaises(OperationalError):
                    with session_scope() as session:
                        session.execute(text("INSERT INTO missing_table VALUES (3)"))
                with session_scope() as session:
                    self.assertEqual(session.scalar(text("SELECT count(*) FROM transaction_test")), 1)
        finally:
            engine.dispose()

    def test_validation_schema_supports_attributes_and_rejects_extra_fields(self):
        class Example(LedgerSchema):
            value: int

        class Result:
            value = 3

        self.assertEqual(Example.model_validate(Result()).value, 3)
        with self.assertRaises(ValidationError):
            Example(value=3, unexpected=True)

    def test_postgres_offline_migration_has_only_version_table(self):
        settings = LedgerSettings(host="localhost", port=5432, user="user",
                                  password="test", database="ledger")
        output = StringIO()
        config = Config(str(LEDGER / "alembic.ini"), output_buffer=output)
        with patch("deposition_toolkit.ledger.settings.get_ledger_settings", return_value=settings):
            command.upgrade(config, "head", sql=True)
        sql = output.getvalue()
        self.assertIn("CREATE TABLE alembic_version", sql)
        self.assertIn("0001", sql)
        self.assertEqual(sql.count("CREATE TABLE"), 1)
        self.assertEqual(len(Base.metadata.tables), 0)

    def test_cli_help_does_not_import_database_dependencies(self):
        # A fresh interpreter catches eager imports, even when tests imported ledger.
        script = '''
import sys
from deposition_toolkit.cli import build_parser
build_parser().parse_args(["ledger", "check"])
assert "deposition_toolkit.ledger.database" not in sys.modules
'''
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True)

    def test_stage_code_does_not_import_ledger_or_postgres_stack(self):
        for path in (ROOT / "src/deposition_toolkit/stages").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                    modules += [f"{node.module}.{alias.name}" for alias in node.names]
                for module in modules:
                    self.assertFalse(
                        "ledger" in module.split(".") or module.split(".")[0] in {"sqlalchemy", "alembic", "psycopg"},
                        f"Stage {path.relative_to(ROOT)} imports database dependency {module}",
                    )


if __name__ == "__main__":
    unittest.main()
