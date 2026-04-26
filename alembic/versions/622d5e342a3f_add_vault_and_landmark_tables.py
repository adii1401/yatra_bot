"""add_vault_and_landmark_tables

Revision ID: 622d5e342a3f
Revises: 9a9301b41dad
Create Date: 2026-04-26 20:08:22.609726

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '622d5e342a3f'
down_revision: Union[str, Sequence[str], None] = '9a9301b41dad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # We removed the manual table creations because they already exist in the previous migration!
    # We only need the missing columns and constraints here:
    op.create_unique_constraint('_chat_user_uc', 'group_members', ['chat_id', 'user_id'])
    op.add_column('trip_groups', sa.Column('dest_lat', sa.Float(), nullable=True))
    op.add_column('trip_groups', sa.Column('dest_lon', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('trip_groups', 'dest_lon')
    op.drop_column('trip_groups', 'dest_lat')
    op.drop_constraint('_chat_user_uc', 'group_members', type_='unique')