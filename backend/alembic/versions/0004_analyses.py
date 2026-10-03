"""Resultados de inferencia, preservando fotografias e capturas originais."""
from alembic import op
import sqlalchemy as sa

revision = "0004_analyses"
down_revision = "0003_captures"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("capture_id", sa.Uuid(as_uuid=True), sa.ForeignKey("captures.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("model_name", sa.String(80), nullable=False),
        sa.Column("model_sha256", sa.String(64), nullable=False),
        sa.Column("configuration_sha256", sa.String(64), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("confidence_threshold", sa.Float(), nullable=False),
        sa.Column("iou_threshold", sa.Float(), nullable=False),
        sa.Column("processing_ms", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("detection_count", sa.Integer(), nullable=False),
        sa.Column("detections", sa.JSON(), nullable=False),
        sa.UniqueConstraint("capture_id", "configuration_sha256", name="uq_analysis_configuration"),
    )
    op.create_index("ix_analyses_capture_id", "analyses", ["capture_id"])


def downgrade():
    op.drop_index("ix_analyses_capture_id", table_name="analyses")
    op.drop_table("analyses")
