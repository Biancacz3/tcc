"""Adiciona credenciais individuais aos dispositivos."""

from alembic import op
import sqlalchemy as sa


revision = "0002_device_tokens"
down_revision = "0001_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "api_token_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )

    op.create_unique_constraint(
        "uq_devices_api_token_hash",
        "devices",
        ["api_token_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_devices_api_token_hash",
        "devices",
        type_="unique",
    )

    op.drop_column(
        "devices",
        "api_token_hash",
    )