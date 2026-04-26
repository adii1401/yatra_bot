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
    # --- 1. MANUALLY ADD THE MISSING TABLES ---
    op.create_table('trip_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=True),
        sa.Column('uploader_id', sa.BigInteger(), nullable=True),
        sa.Column('file_id', sa.String(), nullable=True),
        sa.Column('file_type', sa.String(), nullable=True),
        sa.Column('caption', sa.String(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['chat_id'], ['trip_groups.chat_id'], ),
        sa.ForeignKeyConstraint(['uploader_id'], ['users.telegram_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trip_documents_id'), 'trip_documents', ['id'], unique=False)

    op.create_table('landmarks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['chat_id'], ['trip_groups.chat_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_landmarks_id'), 'landmarks', ['id'], unique=False)

    # --- 2. KEEP THE AUTO-DETECTED COLUMNS AND CONSTRAINTS ---
    op.create_unique_constraint('_chat_user_uc', 'group_members', ['chat_id', 'user_id'])
    op.add_column('trip_groups', sa.Column('dest_lat', sa.Float(), nullable=True))
    op.add_column('trip_groups', sa.Column('dest_lon', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # --- 1. REVERT AUTO-DETECTED CHANGES ---
    op.drop_column('trip_groups', 'dest_lon')
    op.drop_column('trip_groups', 'dest_lat')
    op.drop_constraint('_chat_user_uc', 'group_members', type_='unique')
    
    # --- 2. REVERT THE MANUAL TABLES ---
    op.drop_index(op.f('ix_landmarks_id'), table_name='landmarks')
    op.drop_table('landmarks')
    
    op.drop_index(op.f('ix_trip_documents_id'), table_name='trip_documents')
    op.drop_table('trip_documents')