"""Add hospital hours and consultation-time configuration.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("hospitals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("open_hours", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("avg_consult_mins_json", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("hospitals", schema=None) as batch_op:
        batch_op.drop_column("avg_consult_mins_json")
        batch_op.drop_column("open_hours")