"""original filename

Revision ID: f3a7c6e91b5d
Revises: d8e3f61a9c42
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a7c6e91b5d'
down_revision = 'd8e3f61a9c42'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('session_note_batches', sa.Column('original_filename', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('session_note_batches', 'original_filename')
