"""add pupil_profiles table

Revision ID: 20260213_01
Revises: 20260211_01
Create Date: 2026-02-13
"""
from alembic import op
import sqlalchemy as sa


revision = "20260213_01"
down_revision = "20260211_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pupil_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pupil_id", sa.Integer(), sa.ForeignKey("pupils.id"), nullable=False),
        sa.Column("year_group", sa.Integer(), nullable=True),
        sa.Column("lac_pla", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("send", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ehcp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vulnerable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attendance_spring1", sa.Float(), nullable=True),
        sa.Column("eyfs_gld", sa.Boolean(), nullable=True),
        sa.Column("y1_phonics", sa.Integer(), nullable=True),
        sa.Column("y2_phonics_retake", sa.Integer(), nullable=True),
        sa.Column("y2_reading", sa.String(length=30), nullable=True),
        sa.Column("y2_writing", sa.String(length=30), nullable=True),
        sa.Column("y2_maths", sa.String(length=30), nullable=True),
        sa.Column("enrichment", sa.Text(), nullable=True),
        sa.Column("interventions_note", sa.Text(), nullable=True),
        sa.UniqueConstraint("pupil_id", name="uq_pupil_profiles_pupil_id"),
    )
    op.create_index("ix_pupil_profiles_pupil_id", "pupil_profiles", ["pupil_id"])


def downgrade():
    op.drop_index("ix_pupil_profiles_pupil_id", table_name="pupil_profiles")
    op.drop_table("pupil_profiles")
