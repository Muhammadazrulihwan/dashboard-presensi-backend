"""initial schema

Revision ID: bad32eeac6a9
Revises: 
Create Date: 2026-10-14 15:28:03.422591

Catatan v0.4: hak akses memakai departemen (tabel departments), kolom users.role dihapus.
Basis data produksi MySQL 8.0 (utf8mb4); jenis JSON memakai JSON bawaan, tanpa JSONB PostgreSQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'bad32eeac6a9'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('holidays',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tanggal', sa.Date(), nullable=False),
    sa.Column('nama', sa.String(length=150), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tanggal')
    )
    op.create_table('shift_rules',
    sa.Column('kode', sa.String(length=20), nullable=False),
    sa.Column('jam_masuk_menit', sa.Integer(), nullable=False),
    sa.Column('jam_pulang_menit', sa.Integer(), nullable=False),
    sa.Column('toleransi_menit', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('kode')
    )
    op.create_table('departments',
    sa.Column('kode', sa.String(length=40), nullable=False),
    sa.Column('nama', sa.String(length=100), nullable=False),
    sa.PrimaryKeyConstraint('kode')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=150), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('departemen', sa.String(length=40), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=50), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('uploads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('stored_path', sa.Text(), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'validated', 'saved', 'failed', 'deleted', name='uploadstatus', native_enum=False, length=20), nullable=False),
    sa.Column('error_summary', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('attendance',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=False),
    sa.Column('nik', sa.String(length=30), nullable=False),
    sa.Column('nama', sa.String(length=150), nullable=False),
    sa.Column('tanggal', sa.Date(), nullable=False),
    sa.Column('periode_ym', sa.String(length=7), nullable=False),
    sa.Column('departemen', sa.String(length=100), nullable=False),
    sa.Column('shift', sa.String(length=20), nullable=False),
    sa.Column('jam_masuk_menit', sa.Integer(), nullable=True),
    sa.Column('jam_keluar_menit', sa.Integer(), nullable=True),
    sa.Column('status_file', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('keterangan', sa.Text(), nullable=True),
    sa.Column('lokasi_kerja', sa.String(length=100), nullable=True),
    sa.Column('hari_kerja', sa.Boolean(), nullable=False),
    sa.Column('menit_terlambat', sa.Integer(), nullable=False),
    sa.Column('terlambat', sa.Boolean(), nullable=False),
    sa.Column('menit_pulang_cepat', sa.Integer(), nullable=False),
    sa.Column('durasi_kerja', sa.Float(), nullable=True),
    sa.Column('menit_lembur', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('nik', 'tanggal', name='uq_attendance_nik_tanggal')
    )
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.create_index('ix_attendance_nik', ['nik'], unique=False)
        batch_op.create_index('ix_attendance_tanggal_dept', ['tanggal', 'departemen'], unique=False)
        batch_op.create_index(batch_op.f('ix_attendance_upload_id'), ['upload_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Index pada kolom FK (attendance.upload_id) tidak di-drop terpisah: MySQL menolak menghapus
    # index yang masih dipakai foreign key; op.drop_table menghapus index beserta tabelnya.
    op.drop_table('attendance')
    op.drop_table('uploads')
    op.drop_table('audit_log')
    op.drop_table('users')
    op.drop_table('departments')
    op.drop_table('shift_rules')
    op.drop_table('holidays')
