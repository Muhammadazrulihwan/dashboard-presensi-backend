"""app settings

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-20 12:00:00.000000

Tabel pengaturan aplikasi (key-value) untuk pembagi hari kerja Rekap Payroll (PRD F29).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('app_settings',
    sa.Column('kunci', sa.String(length=60), nullable=False),
    sa.Column('nilai', sa.String(length=255), nullable=False),
    sa.Column('diperbarui_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('kunci')
    )
    # Nilai awal: pembagi hari kerja 25 (PRD bagian 13).
    op.execute("INSERT INTO app_settings (kunci, nilai) VALUES ('payroll_workdays', '25')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('app_settings')
