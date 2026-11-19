"""Store complete structure manifests as JSONB."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("structure_id", sa.Text(), nullable=False),
        sa.Column("structure_details", JSONB(), nullable=False),
        sa.UniqueConstraint("group_id", "structure_id", name="uq_structures_group_id"),
    )
    op.create_index("ix_structures_group_id", "structures", ["group_id"])


def downgrade() -> None:
    op.drop_table("structures")
