"""activity statement page

Revision ID: a1f2c9d84e01
Revises: 38b7745932c4
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1f2c9d84e01'
down_revision = '38b7745932c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable — a pre-existing row (uploaded before this phase) has no way
    # to retroactively know its own Activity Statement page; timesheet
    # checks correctly stay not_checkable for those rows, never guessed at.
    op.add_column(
        'person_documents',
        sa.Column('activity_statement_page', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('person_documents', 'activity_statement_page')
