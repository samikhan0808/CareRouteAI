"""Add patient_name and phone to symptom_sessions

Revision ID: a1b2c3d4e5f6
Revises: c8243483855c
Create Date: 2026-08-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'c8243483855c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("symptom_sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("patient_name", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("phone", sa.String(length=20), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("symptom_sessions", schema=None) as batch_op:
        batch_op.drop_column("phone")
        batch_op.drop_column("patient_name")

    # ### end Alembic commands ###