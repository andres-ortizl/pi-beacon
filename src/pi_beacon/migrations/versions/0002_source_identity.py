"""Track source identity for safe incremental reads.

Revision ID: 0002_source_identity
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_source_identity"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("sessionfile")}
    with op.batch_alter_table("sessionfile") as batch:
        if "cursor_fingerprint" not in columns:
            batch.add_column(
                sa.Column("cursor_fingerprint", sa.String(), nullable=False, server_default="")
            )
        if "source_dev" not in columns:
            batch.add_column(
                sa.Column("source_dev", sa.Integer(), nullable=False, server_default="0")
            )
        if "source_inode" not in columns:
            batch.add_column(
                sa.Column("source_inode", sa.Integer(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    with op.batch_alter_table("sessionfile") as batch:
        batch.drop_column("source_inode")
        batch.drop_column("source_dev")
        batch.drop_column("cursor_fingerprint")
