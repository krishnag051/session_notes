"""review cache

Revision ID: e1a4b7c92d3f
Revises: f3a7c6e91b5d
Create Date: 2026-10-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1a4b7c92d3f'
down_revision = 'f3a7c6e91b5d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'session_note_reviews',
        sa.Column('served_from_cache', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'review_cache',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('cache_key', sa.String(length=64), nullable=False),
        sa.Column('pipeline_version', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('cache_key', 'pipeline_version', name='uq_review_cache_key_version'),
    )
    op.create_index('ix_review_cache_cache_key', 'review_cache', ['cache_key'])
    op.create_index('ix_review_cache_pipeline_version', 'review_cache', ['pipeline_version'])


def downgrade() -> None:
    op.drop_index('ix_review_cache_pipeline_version', table_name='review_cache')
    op.drop_index('ix_review_cache_cache_key', table_name='review_cache')
    op.drop_table('review_cache')
    op.drop_column('session_note_reviews', 'served_from_cache')
