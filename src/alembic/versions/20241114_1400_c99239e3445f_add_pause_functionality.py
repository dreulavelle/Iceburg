"""add_pause_functionality

Revision ID: c99239e3445f
revision: str = 'c99239e3445f'
Revises: c99709e3648f
Create Date: 2024-11-14 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '[generate a new revision ID]'
down_revision: Union[str, None] = 'c99709e3648f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pause-related columns to MediaItem table
    op.add_column('MediaItem',
        sa.Column('is_paused', sa.Boolean(), nullable=True, default=False))
    op.add_column('MediaItem',
        sa.Column('paused_at', sa.DateTime(), nullable=True))
    op.add_column('MediaItem',
        sa.Column('paused_by', sa.String(), nullable=True))

    # Add index for is_paused column
    op.create_index(op.f('ix_mediaitem_is_paused'), 'MediaItem', ['is_paused'])


def downgrade() -> None:
    # Remove pause-related columns from MediaItem table
    op.drop_index(op.f('ix_mediaitem_is_paused'), table_name='MediaItem')
    op.drop_column('MediaItem', 'paused_by')
    op.drop_column('MediaItem', 'paused_at')
    op.drop_column('MediaItem', 'is_paused')
