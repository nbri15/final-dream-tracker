"""add test_papers table

Revision ID: 20260303_01
Revises: 20260213_01
Create Date: 2026-03-03
"""
from alembic import op
import sqlalchemy as sa


revision = "20260303_01"
down_revision = "20260213_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "test_papers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("year_group", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(length=10), nullable=False),
        sa.Column("subject", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("paper_type", sa.String(length=20), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("uploaded_by_teacher_id", sa.Integer(), sa.ForeignKey("teachers.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("stored_filename", name="uq_test_papers_stored_filename"),
    )
    op.create_index("ix_test_papers_year_group", "test_papers", ["year_group"])
    op.create_index("ix_test_papers_term", "test_papers", ["term"])
    op.create_index("ix_test_papers_subject", "test_papers", ["subject"])


def downgrade():
    op.drop_index("ix_test_papers_subject", table_name="test_papers")
    op.drop_index("ix_test_papers_term", table_name="test_papers")
    op.drop_index("ix_test_papers_year_group", table_name="test_papers")
    op.drop_table("test_papers")
