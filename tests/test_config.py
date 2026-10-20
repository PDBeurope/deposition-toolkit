"""Configuration loading and integration checks without production services."""

import argparse
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from deposition_toolkit.config import Settings, get_settings
from stages.ccd_creation import annotate_comp
from stages.ccd_creation.finalise_step import commit_ccds_to_cvs, finalize_ccds, get_new_ccd_code
from stages.prepare_dataset import prepare_dataset
from stages.validation_report import val_report


@contextmanager
def configured_directory(contents):
    """Load an isolated .env with no settings inherited from the test host."""
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
        original = Path.cwd()
        root = Path(directory).resolve()
        (root / ".env").write_text(contents, encoding="utf-8")
        os.chdir(root)
        get_settings.cache_clear()
        try:
            yield root
        finally:
            get_settings.cache_clear()
            os.chdir(original)


class SettingsTests(unittest.TestCase):
    def test_dotenv_loads_typed_paths_credentials_and_legacy_names(self):
        with configured_directory(
            "UNUSED_CCD_FILE_PATH=/custom/unused.lst\n"
            "CCD_CVS_PATH=/custom/cvs\n"
            "TOOLS_ROOT=/custom/tools\n"
            "ANNOTATE_BIN=/custom/annotateComp\n"
            "DEPOSITION_TOOLKIT_WORKROOT=/custom/work\n"
            "DEPOSITION_ROOT=/custom/deposit\n"
            "CCD_DB_HOST=custom-mysql\nCCD_DB_PORT=4406\nCCD_DB_USER=reader\n"
            "CCD_DB_PASSWORD='test-secret'\nCCD_DB_TIMEOUT=12\n"
            "POSTGRES_HOST=custom-pg\nPOSTGRES_PORT=5433\nPOSTGRES_USER=writer\n"
            "POSTGRES_PASSWORD='test-pg-secret'\nPOSTGRES_DATABASE=toolkit\n"
            "UNRELATED_SETTING=allowed\n"
        ):
            settings = get_settings()
            self.assertEqual(settings.unused_ccd_file_path, Path("/custom/unused.lst"))
            self.assertEqual(settings.ccd_cvs_path, Path("/custom/cvs"))
            self.assertEqual(settings.tools_root, Path("/custom/tools"))
            self.assertEqual(settings.annotate_comp_path, Path("/custom/annotateComp"))
            self.assertEqual(settings.workroot, Path("/custom/work"))
            self.assertEqual(settings.deposition_root, Path("/custom/deposit"))
            self.assertEqual((settings.ccd_db_host, settings.ccd_db_port, settings.ccd_db_user),
                             ("custom-mysql", 4406, "reader"))
            self.assertEqual(settings.ccd_db_timeout, 12)
            self.assertEqual(settings.ccd_db_password.get_secret_value(), "test-secret")
            self.assertEqual((settings.postgres_host, settings.postgres_port, settings.postgres_user),
                             ("custom-pg", 5433, "writer"))
            self.assertEqual(settings.postgres_password.get_secret_value(), "test-pg-secret")
            self.assertEqual(settings.postgres_database, "toolkit")
            self.assertNotIn("test-secret", repr(settings))
            self.assertNotIn("test-pg-secret", settings.model_dump_json())

    def test_environment_overrides_dotenv(self):
        with configured_directory("CCD_DB_PORT=4406\nDEPOSITION_TOOLKIT_WORKROOT=/dotenv\n"):
            with patch.dict(os.environ, {"CCD_DB_PORT": "4407", "DEPOSITION_TOOLKIT_WORKROOT": "/env"}):
                self.assertEqual(get_settings().ccd_db_port, 4407)
                self.assertEqual(get_settings().workroot, Path("/env"))

    def test_defaults_work_without_dotenv_and_do_not_connect(self):
        with configured_directory("") as root:
            (root / ".env").unlink()
            settings = get_settings()
            self.assertEqual(settings.postgres_host, "pgsql-hlvm-172")
            self.assertEqual(settings.postgres_user, "pdb_dep")
            self.assertEqual(settings.postgres_password.get_secret_value(), "place-holder")
            self.assertEqual(settings.postgres_port, 5432)
            self.assertIsNone(settings.postgres_database)
            self.assertEqual(settings.ccd_db_password.get_secret_value(), "")
            self.assertEqual(settings.annotate_comp_path,
                             settings.tools_root / "packages/cc-tools-v2/bin/annotateComp")

    def test_settings_are_cached_until_explicitly_cleared(self):
        with configured_directory("TOOLS_ROOT=/first\n") as root:
            first = get_settings()
            (root / ".env").write_text("TOOLS_ROOT=/second\n")
            self.assertIs(get_settings(), first)
            get_settings.cache_clear()
            self.assertEqual(get_settings().tools_root, Path("/second"))

    def test_invalid_database_settings_fail_validation(self):
        with configured_directory(""):
            for setting, value in (("ccd_db_port", 0), ("postgres_port", 65536),
                                   ("ccd_db_timeout", 0), ("ccd_db_port", "invalid")):
                with self.subTest(setting=setting, value=value), self.assertRaises(ValidationError):
                    Settings(_env_file=None, **{setting: value})

    def test_committed_template_loads(self):
        template = Path(__file__).resolve().parents[1] / ".env.example"
        with configured_directory(""):
            settings = Settings(_env_file=template)
            self.assertEqual(settings.postgres_host, "pgsql-hlvm-172")
            self.assertEqual(settings.ccd_db_password.get_secret_value(), "place-holder")


class StageConfigurationTests(unittest.TestCase):
    def test_allocation_checks_and_commit_use_the_same_configured_checkout(self):
        with configured_directory("UNUSED_CCD_FILE_PATH=unused.lst\nCCD_CVS_PATH=cvs\n") as root:
            used = root / "cvs" / "A" / "ATP" / "ATP.cif"
            used.parent.mkdir(parents=True)
            used.touch()
            pool = root / "unused.lst"
            pool.write_text("ATP\nABCDE\nZZZ\n")
            self.assertEqual(get_new_ccd_code.get_ccd_path("abcde"), "cvs/DE/ABCDE/ABCDE.cif")
            self.assertEqual(get_new_ccd_code.get_next_unused_code(dry_run=True), ("ABCDE", 1))
            self.assertEqual(pool.read_text(), "ATP\nABCDE\nZZZ\n")
            self.assertEqual(get_new_ccd_code.get_next_unused_code(), ("ABCDE", 1))
            self.assertEqual(pool.read_text(), "ZZZ\n")
            self.assertEqual(commit_ccds_to_cvs.resolve_cvs_root(None), root / "cvs")
            override = root / "override"
            override.mkdir()
            self.assertEqual(commit_ccds_to_cvs.resolve_cvs_root(str(override)), override)

    def test_annotation_environment_and_binary_use_settings(self):
        with configured_directory("TOOLS_ROOT=/custom/tools\n"):
            env = annotate_comp.default_environment({"LD_LIBRARY_PATH": "/existing"})
            self.assertEqual(env["OE_DIR"], "/custom/tools/packages/openeye")
            self.assertEqual(env["INCHI_DIR"], "/custom/tools/bin")
            self.assertEqual(env["LD_LIBRARY_PATH"],
                             "/custom/tools/packages/openbabel-2.2.3/lib:/custom/tools/lib:/existing")
            argv = ["input", "output", "--log-root", "logs"]
            self.assertEqual(annotate_comp.build_parser().parse_args(argv).annotate_bin,
                             "/custom/tools/packages/cc-tools-v2/bin/annotateComp")
            self.assertEqual(annotate_comp.build_parser().parse_args(
                argv + ["--annotate-bin", "/override"]).annotate_bin, "/override")

    def test_finalizer_uses_dotenv_and_preserves_cli_overrides(self):
        with configured_directory("CCD_DB_HOST=from-dotenv\nCCD_DB_PORT=4406\nCCD_DB_PASSWORD=test\n"):
            defaults = finalize_ccds.build_parser().parse_args([])
            self.assertEqual(defaults.db_host, "from-dotenv")
            self.assertEqual(defaults.db_port, 4406)
            self.assertEqual(defaults.db_password, "test")
            overridden = finalize_ccds.build_parser().parse_args(
                ["--db-host", "override", "--db-port", "4407", "--db-password", "override-secret"])
            self.assertEqual(overridden.db_host, "override")
            self.assertEqual(overridden.db_port, 4407)
            self.assertEqual(overridden.db_password, "override-secret")

    def test_missing_mysql_password_is_only_required_for_database_queries(self):
        with configured_directory(""):
            args = finalize_ccds.build_parser().parse_args([])
            with self.assertRaisesRegex(RuntimeError, "CCD_DB_PASSWORD"):
                finalize_ccds.query_existing_descriptors(args)
            args.existing_descriptors_file = "existing.csv"
            Path("existing.csv").write_text("comp_id,descriptor\nATP,TEST-INCHIKEY\n")
            self.assertEqual(finalize_ccds.load_existing_descriptors(args), {"TEST-INCHIKEY": {"ATP"}})

    def test_prepare_dataset_uses_configured_root_and_explicit_override(self):
        with configured_directory("DEPOSITION_TOOLKIT_WORKROOT=configured\n") as root:
            tarball = root / "dataset.tgz"
            tarball.touch()
            result = prepare_dataset.prepare_dataset(tarball, copy_tarball=False, extract=False)
            self.assertEqual(result.work_dir, root / "configured" / "work" / "dataset")
            result = prepare_dataset.prepare_dataset(tarball, work_root=root / "override",
                                                     copy_tarball=False, extract=False)
            self.assertEqual(result.work_dir, root / "override" / "work" / "dataset")

    def test_wrapper_parsers_use_shared_defaults_and_cli_overrides(self):
        from deposition_toolkit.commands import ccd_annotate, prepare_dataset as prepare_command

        with configured_directory("ANNOTATE_BIN=/configured/annotate\nDEPOSITION_TOOLKIT_WORKROOT=/configured/work\n"):
            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers()
            ccd_annotate.register(subparsers)
            prepare_command.register(subparsers)
            self.assertEqual(parser.parse_args(["ccd-annotate", "work"]).annotate_bin, "/configured/annotate")
            self.assertEqual(parser.parse_args(["prepare-dataset", "data.tgz"]).work_root, "/configured/work")
            self.assertEqual(parser.parse_args(["ccd-annotate", "work", "--annotate-bin", "/override"]).annotate_bin,
                             "/override")
            self.assertEqual(parser.parse_args(["prepare-dataset", "data.tgz", "--work-root", "/override"]).work_root,
                             "/override")

    def test_validation_report_uses_configured_root_and_base_path_override(self):
        with configured_directory("DEPOSITION_ROOT=/configured/deposit\n"):
            for extra, expected in (([], Path("/configured/deposit")),
                                    (["--base-path", "/override"], Path("/override"))):
                with self.subTest(extra=extra), patch.object(val_report, "process_deposition",
                                                          return_value=({}, [])) as process:
                    with patch.object(val_report, "build_html", return_value="<html></html>"), \
                         patch.object(val_report, "write_metadata_file"), patch("builtins.print"):
                        self.assertEqual(val_report.main(["D_123"] + extra), 0)
                    process.assert_called_once_with("D_123", expected)


if __name__ == "__main__":
    unittest.main()
