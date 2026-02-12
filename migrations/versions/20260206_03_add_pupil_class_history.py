"""add pupil_class_history table

Revision ID: 20260206_03
Revises: 20260206_02
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260206_03"
down_revision = "20260206_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pupil_class_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pupil_id", sa.Integer(), sa.ForeignKey("pupils.id"), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_years.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("pupil_id", "class_id", "academic_year_id", name="uq_pupil_class_history"),
    )
    op.create_index("ix_pupil_class_history_pupil_id", "pupil_class_history", ["pupil_id"])
    op.create_index("ix_pupil_class_history_class_id", "pupil_class_history", ["class_id"])
    op.create_index("ix_pupil_class_history_academic_year_id", "pupil_class_history", ["academic_year_id"])


def downgrade():
    op.drop_index("ix_pupil_class_history_academic_year_id", table_name="pupil_class_history")
    op.drop_index("ix_pupil_class_history_class_id", table_name="pupil_class_history")
    op.drop_index("ix_pupil_class_history_pupil_id", table_name="pupil_class_history")
    op.drop_table("pupil_class_history")
