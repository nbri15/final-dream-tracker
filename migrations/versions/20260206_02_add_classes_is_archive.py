"""add classes.is_archive field

Revision ID: 20260206_02
Revises: 20260206_01
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260206_02"
down_revision = "20260206_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("classes", sa.Column("is_archive", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("classes", "is_archive")
