"""model finding raw

Revision ID: d8e3f61a9c42
Revises: c4de1a9f2b33
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8e3f61a9c42'
down_revision = 'c4de1a9f2b33'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('rule_results', sa.Column('model_finding_raw', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('rule_results', 'model_finding_raw')
