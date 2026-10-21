"""archived file employees

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-21 10:00:00.000000

Tabel anak untuk pencarian arsip berdasarkan Employee ID (PRD F23). Satu baris per (file arsip, employee_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'archived_file_employees',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('archive_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('employee_id', sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(['archive_id'], ['archived_files.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('archive_id', 'employee_id', name='uq_archived_file_employee'),
    )
    with op.batch_alter_table('archived_file_employees', schema=None) as batch_op:
        batch_op.create_index('ix_archived_file_employees_emp', ['employee_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_archived_file_employees_archive_id'), ['archive_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('archived_file_employees')
