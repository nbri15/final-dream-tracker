"""add term config thresholds

Revision ID: 20260310_01
Revises: 20260303_01
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa


revision = "20260310_01"
down_revision = "20260303_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("term_configs", sa.Column("maths_wts_max", sa.Float(), nullable=False, server_default="55"))
    op.add_column("term_configs", sa.Column("maths_ot_max", sa.Float(), nullable=False, server_default="75"))
    op.add_column("term_configs", sa.Column("reading_wts_max", sa.Float(), nullable=False, server_default="65"))
    op.add_column("term_configs", sa.Column("reading_ot_max", sa.Float(), nullable=False, server_default="85"))
    op.add_column("term_configs", sa.Column("spag_wts_max", sa.Float(), nullable=False, server_default="65"))
    op.add_column("term_configs", sa.Column("spag_ot_max", sa.Float(), nullable=False, server_default="85"))


def downgrade():
    op.drop_column("term_configs", "spag_ot_max")
    op.drop_column("term_configs", "spag_wts_max")
    op.drop_column("term_configs", "reading_ot_max")
    op.drop_column("term_configs", "reading_wts_max")
    op.drop_column("term_configs", "maths_ot_max")
    op.drop_column("term_configs", "maths_wts_max")
