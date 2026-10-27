"""Shared deployment settings loaded from the environment and a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    unused_ccd_file_path: Path = Path(
        "/nfs/public/services/onedep_gpfs/data/production/reference/id_codes/unusedCodes.lst"
    )
    ccd_cvs_path: Path = Path(
        "/nfs/public/services/onedep_gpfs/data/production/reference/components/ligand-dict-v3"
    )
    tools_root: Path = Path(
        "/nfs/public/services/onedep_gpfs/resources/tools/tools-centos-7.9"
    )
    annotate_bin: Path | None = None
    workroot: Path = Field(
        default=Path("/nfs/public/services/onedep_gpfs/deposition-toolkit-processing"),
        validation_alias="DEPOSITION_TOOLKIT_WORKROOT",
    )
    deposition_root: Path = Path(
        "/nfs/public/services/onedep_gpfs/data/production/deposit"
    )
    mmcif_dictionary_path: Path = Path(
        "/nfs/public/services/onedep_gpfs/deposition-toolkit-processing/mmcif_pdbx_v50.dic"
    )

    ccd_db_host: str = "mysql-pdbe-onedep-prod.ebi.ac.uk"
    ccd_db_port: int = Field(default=4437, ge=1, le=65535)
    ccd_db_user: str = "pdberead"
    ccd_db_password: SecretStr = SecretStr("")
    ccd_db_timeout: int = Field(default=30, gt=0)

    # The ledger validates required connection values only when explicitly used.
    postgres_host: str = "pgsql-hlvm-172"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "pdb_dep"
    postgres_password: SecretStr = SecretStr("place-holder")
    postgres_database: str | None = None

    @property
    def annotate_comp_path(self) -> Path:
        """Use the binary override, or locate annotateComp under the tools root."""
        path = self.annotate_bin or self.tools_root / "packages/cc-tools-v2/bin/annotateComp"
        return path.expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once per process, on first use."""
    return Settings()
