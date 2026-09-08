"""Add patient and triage details to appointments.

Revision ID: b2c3d4e5f6a7
Revises: b7c8d9e0f1a2
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
	with op.batch_alter_table("appointments", schema=None) as batch_op:
		batch_op.add_column(sa.Column("patient_name", sa.String(length=100), nullable=True))
		batch_op.add_column(sa.Column("phone", sa.String(length=20), nullable=True))
		batch_op.add_column(sa.Column("gender", sa.String(length=20), nullable=True))
		batch_op.add_column(sa.Column("urgency", sa.String(length=20), nullable=True))
		batch_op.add_column(sa.Column("primary_concern", sa.String(length=200), nullable=True))
		batch_op.add_column(sa.Column("key_symptoms_json", sa.Text(), nullable=True))
		batch_op.add_column(sa.Column("duration", sa.String(length=50), nullable=True))
		batch_op.add_column(sa.Column("doctor_summary", sa.Text(), nullable=True))
		batch_op.add_column(sa.Column("conversation_log_json", sa.Text(), nullable=True))


def downgrade():
	with op.batch_alter_table("appointments", schema=None) as batch_op:
		batch_op.drop_column("conversation_log_json")
		batch_op.drop_column("doctor_summary")
		batch_op.drop_column("duration")
		batch_op.drop_column("key_symptoms_json")
		batch_op.drop_column("primary_concern")
		batch_op.drop_column("urgency")
		batch_op.drop_column("gender")
		batch_op.drop_column("phone")
		batch_op.drop_column("patient_name")
