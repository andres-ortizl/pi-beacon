"""Store retained daily and model-attributed usage for dashboard analytics.

Revision ID: 0003_dashboard_history
Revises: 0002_source_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_dashboard_history"
down_revision: str | None = "0002_source_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("sessionfile")}
    if "aggregation_version" not in columns:
        with op.batch_alter_table("sessionfile") as batch:
            batch.add_column(
                sa.Column("aggregation_version", sa.Integer(), nullable=False, server_default="0")
            )

    tables = set(inspector.get_table_names())
    if "sessionday" not in tables:
        op.create_table(
            "sessionday",
            sa.Column("source_path", sa.String(), nullable=False),
            sa.Column("day", sa.String(), nullable=False),
            sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("messages", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_activity", sa.String(), nullable=True),
            sa.Column("last_activity", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["source_path"], ["sessionfile.path"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("source_path", "day"),
        )

    if "sessionmodelusage" not in tables:
        op.create_table(
            "sessionmodelusage",
            sa.Column("source_path", sa.String(), nullable=False),
            sa.Column("day", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("responses", sa.Integer(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(["source_path"], ["sessionfile.path"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("source_path", "day", "model"),
        )

    inspector = sa.inspect(connection)
    session_day_indexes = {index["name"] for index in inspector.get_indexes("sessionday")}
    if "ix_sessionday_day_last_activity" not in session_day_indexes:
        op.create_index(
            "ix_sessionday_day_last_activity",
            "sessionday",
            ["day", "last_activity"],
        )
    model_usage_indexes = {index["name"] for index in inspector.get_indexes("sessionmodelusage")}
    if "ix_sessionmodelusage_day_model" not in model_usage_indexes:
        op.create_index(
            "ix_sessionmodelusage_day_model",
            "sessionmodelusage",
            ["day", "model"],
        )


def downgrade() -> None:
    op.drop_index("ix_sessionmodelusage_day_model", table_name="sessionmodelusage")
    op.drop_table("sessionmodelusage")
    op.drop_index("ix_sessionday_day_last_activity", table_name="sessionday")
    op.drop_table("sessionday")
    with op.batch_alter_table("sessionfile") as batch:
        batch.drop_column("aggregation_version")
