"""batch reviews_active flag

Revision ID: a7c5e2f94b61
Revises: e1a4b7c92d3f
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c5e2f94b61'
down_revision = 'e1a4b7c92d3f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills every pre-existing batch as False -- none of
    # them have a genuinely active run claiming them right now (if the
    # backend process that was reviewing one died, that's exactly the
    # orphaned-row case the startup recovery sweep handles separately).
    op.add_column(
        'session_note_batches',
        sa.Column('reviews_active', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('session_note_batches', 'reviews_active')
