"""add audit tracking fields

Revision ID: 20260206_01
Revises:
Create Date: 2026-02-06
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260206_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pupils", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("results", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("results", sa.Column("updated_by_teacher_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_results_updated_by_teacher_id", "results", "teachers", ["updated_by_teacher_id"], ["id"])

    op.add_column("pupil_question_scores", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("pupil_question_scores", sa.Column("updated_by_teacher_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_pupil_question_scores_updated_by_teacher_id", "pupil_question_scores", "teachers", ["updated_by_teacher_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_pupil_question_scores_updated_by_teacher_id", "pupil_question_scores", type_="foreignkey")
    op.drop_column("pupil_question_scores", "updated_by_teacher_id")
    op.drop_column("pupil_question_scores", "updated_at")

    op.drop_constraint("fk_results_updated_by_teacher_id", "results", type_="foreignkey")
    op.drop_column("results", "updated_by_teacher_id")
    op.drop_column("results", "updated_at")
    op.drop_column("pupils", "updated_at")
