"""Add patient_name/patient_phone to symptom_sessions

Revision ID: a1c9e2f7d3b4
Revises: b2c3d4e5f6a7
Create Date: 2026-09-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c9e2f7d3b4'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("symptom_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("patient_phone", sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table("symptom_sessions", schema=None) as batch_op:
        batch_op.drop_column("patient_phone")
