"""add archived files

Revision ID: a1b2c3d4e5f6
Revises: 1511af4bd991
Create Date: 2026-10-20 10:48:13.885209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '1511af4bd991'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('archived_files',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=True),
    sa.Column('jenis_sumber', sa.String(length=30), nullable=False),
    sa.Column('nama_asli', sa.String(length=255), nullable=False),
    sa.Column('path_arsip', sa.Text(), nullable=False),
    sa.Column('ukuran', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('pengunggah_id', sa.Integer(), nullable=True),
    sa.Column('periode_awal', sa.Date(), nullable=True),
    sa.Column('periode_akhir', sa.Date(), nullable=True),
    sa.Column('status', sa.Enum('aktif', 'sampah', 'dihapus', name='archivestatus', native_enum=False, length=20), nullable=False),
    sa.Column('dihapus_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dihapus_oleh', sa.Integer(), nullable=True),
    sa.Column('alasan_hapus', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pengunggah_id'], ['users.id']),
    sa.ForeignKeyConstraint(['dihapus_oleh'], ['users.id']),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('archived_files', schema=None) as batch_op:
        batch_op.create_index('ix_archived_files_checksum', ['checksum'], unique=False)
        batch_op.create_index('ix_archived_files_created', ['created_at'], unique=False)
        batch_op.create_index('ix_archived_files_periode', ['periode_awal', 'periode_akhir'], unique=False)
        batch_op.create_index('ix_archived_files_status', ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_archived_files_upload_id'), ['upload_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('archived_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_archived_files_upload_id'))
        batch_op.drop_index('ix_archived_files_status')
        batch_op.drop_index('ix_archived_files_periode')
        batch_op.drop_index('ix_archived_files_created')
        batch_op.drop_index('ix_archived_files_checksum')

    op.drop_table('archived_files')
