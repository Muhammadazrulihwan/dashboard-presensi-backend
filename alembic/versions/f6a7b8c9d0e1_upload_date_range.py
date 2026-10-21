"""upload date range

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-21 11:00:00.000000

Rentang tanggal data per paket upload (PRD F16) agar riwayat upload menampilkan periode data.
Per file sumber, jumlah baris, rentang, dan peringatan sudah tersedia di upload_files.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('date_from', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('date_to', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.drop_column('date_to')
        batch_op.drop_column('date_from')
