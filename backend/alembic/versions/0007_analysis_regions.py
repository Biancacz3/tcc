"""Persist region analysis without rewriting historical predictions."""
from alembic import op
import sqlalchemy as sa

revision = "0007_analysis_regions"
down_revision = "0006_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analyses", sa.Column("analysis_state", sa.String(24), nullable=False,
                                      server_default=sa.text("'legacy'")))
    op.add_column("analyses", sa.Column("candidates", sa.JSON(), nullable=False,
                                      server_default=sa.text("'[]'")))
    op.add_column("analyses", sa.Column("unidentified_count", sa.Integer(), nullable=False,
                                      server_default=sa.text("0")))
    op.add_column("analyses", sa.Column("warnings", sa.JSON(), nullable=False,
                                      server_default=sa.text("'[]'")))
    op.add_column("analyses", sa.Column("method", sa.String(100), nullable=False,
                                      server_default=sa.text("'legacy_full_image'")))
    op.add_column("analyses", sa.Column("plant_bbox", sa.JSON(), nullable=True))


def downgrade():
    for name in ("plant_bbox", "method", "warnings", "unidentified_count", "candidates", "analysis_state"):
        op.drop_column("analyses", name)
