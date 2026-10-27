"""Track OneDep depositions and resumable local sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deposition_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dep_id", sa.Text(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("structure_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("automated_depostion", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deposited_on", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dataset", "structure_id", name="uq_deposition_registry_dataset"),
    )


def downgrade() -> None:
    op.drop_table("deposition_registry")
