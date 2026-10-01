"""auto review skipped

Revision ID: c4de1a9f2b33
Revises: a1f2c9d84e01
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4de1a9f2b33'
down_revision = 'a1f2c9d84e01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills every pre-existing batch as False — every
    # one of them, uploaded before this safeguard existed, DID auto-review
    # as normal (there was no other path yet).
    op.add_column(
        'session_note_batches',
        sa.Column('auto_review_skipped', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('session_note_batches', 'auto_review_skipped')
