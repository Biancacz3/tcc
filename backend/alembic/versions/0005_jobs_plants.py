"""Identidade de planta por foto e fila duravel de analise automatica."""
from alembic import op
import sqlalchemy as sa

revision = "0005_jobs_plants"
down_revision = "0004_analyses"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_ in (("plant_id", sa.String(80)), ("plant_name", sa.String(120)),
                        ("marker_id", sa.Integer()), ("mission_id", sa.String(80)),
                        ("passage", sa.Integer()), ("plant_side", sa.String(40))):
        op.add_column("captures", sa.Column(name, type_, nullable=True))
    op.create_index("ix_captures_plant_id", "captures", ["plant_id"])
    op.create_index("ix_captures_mission_id", "captures", ["mission_id"])
    op.create_table(
        "analysis_jobs",
        sa.Column("capture_id", sa.Uuid(as_uuid=True), sa.ForeignKey("captures.id"), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid(as_uuid=True)),
        sa.Column("last_error", sa.String(400)),
        sa.Column("analysis_id", sa.Uuid(as_uuid=True), sa.ForeignKey("analyses.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_analysis_jobs_next_attempt_at", "analysis_jobs", ["next_attempt_at"])
    # Fotos anteriores sem analise entram na fila; resultados antigos preservados.
    op.execute(sa.text("""INSERT INTO analysis_jobs (capture_id, status, attempts)
        SELECT c.id, 'queued', 0 FROM captures c
        WHERE NOT EXISTS (SELECT 1 FROM analyses a WHERE a.capture_id = c.id)"""))


def downgrade():
    op.drop_table("analysis_jobs")
    op.drop_index("ix_captures_mission_id", table_name="captures")
    op.drop_index("ix_captures_plant_id", table_name="captures")
    for name in ("plant_side", "passage", "mission_id", "marker_id", "plant_name", "plant_id"):
        op.drop_column("captures", name)
