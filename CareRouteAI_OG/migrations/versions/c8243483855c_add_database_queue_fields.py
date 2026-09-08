"""Add database queue fields

Revision ID: c8243483855c
Revises: f1d882b04650
Create Date: 2026-08-28 00:24:11.590075

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8243483855c'
down_revision = 'f1d882b04650'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("queue_tokens", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("public_id", sa.String(length=20), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_queue_tokens_public_id",
            ["public_id"],
        )


def downgrade():
    with op.batch_alter_table("queue_tokens", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_queue_tokens_public_id",
            type_="unique",
        )
        batch_op.drop_column("public_id")

    # ### end Alembic commands ###
