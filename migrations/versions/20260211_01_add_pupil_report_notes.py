"""add pupil_report_notes table

Revision ID: 20260211_01
Revises: 20260206_03
Create Date: 2026-02-11
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260211_01"
down_revision = "20260206_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pupil_report_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pupil_id", sa.Integer(), sa.ForeignKey("pupils.id"), nullable=False),
        sa.Column("year_id", sa.Integer(), sa.ForeignKey("academic_years.id"), nullable=False),
        sa.Column("term_id", sa.String(length=10), nullable=False),
        sa.Column("strengths_text", sa.Text(), nullable=True),
        sa.Column("next_steps_text", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("teachers.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("pupil_id", "year_id", "term_id", name="uq_pupil_report_note"),
    )
    op.create_index("ix_pupil_report_notes_lookup", "pupil_report_notes", ["pupil_id", "year_id", "term_id"])


def downgrade():
    op.drop_index("ix_pupil_report_notes_lookup", table_name="pupil_report_notes")
    op.drop_table("pupil_report_notes")
