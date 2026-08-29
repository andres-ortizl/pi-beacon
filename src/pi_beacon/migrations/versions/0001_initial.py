"""Create the historical session cache.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "sessionfile" not in inspector.get_table_names():
        op.create_table(
            "sessionfile",
            sa.Column("path", sa.String(), primary_key=True, nullable=False),
            sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mtime_ns", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("offset", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("day", sa.String(), nullable=False, server_default=""),
            sa.Column("session_id", sa.String(), nullable=False, server_default=""),
            sa.Column("cwd", sa.String(), nullable=False, server_default=""),
            sa.Column("model", sa.String(), nullable=False, server_default=""),
            sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("messages", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_activity", sa.String(), nullable=True),
            sa.Column("last_activity", sa.String(), nullable=True),
        )
        inspector = sa.inspect(connection)

    index_names = {index["name"] for index in inspector.get_indexes("sessionfile")}
    if "ix_sessionfile_day_last_activity" not in index_names:
        op.create_index(
            "ix_sessionfile_day_last_activity",
            "sessionfile",
            ["day", "last_activity"],
        )


def downgrade() -> None:
    op.drop_index("ix_sessionfile_day_last_activity", table_name="sessionfile")
    op.drop_table("sessionfile")
