"""index tabel usulan PRD Bab 8

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-21 13:00:00.000000

Melengkapi index yang disebut pada tabel usulan PRD Bab 8 tetapi belum dibuat:
- uploads: indeks tanggal (created_at) agar riwayat/arsip cepat disaring per waktu.
- audit_log: indeks gabungan (user_id, created_at) untuk halaman audit log.
- archived_files: indeks jenis_sumber untuk filter arsip.
- time_off_requests: indeks request_id (pencarian pengajuan). Bukan UNIQUE agar upgrade aman
  bila data lama memuat request_id ganda; keunikan tetap dijaga parser.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.create_index('ix_uploads_created_at', ['created_at'], unique=False)
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index('ix_audit_log_user_created', ['user_id', 'created_at'], unique=False)
    with op.batch_alter_table('archived_files', schema=None) as batch_op:
        batch_op.create_index('ix_archived_files_jenis_sumber', ['jenis_sumber'], unique=False)
    with op.batch_alter_table('time_off_requests', schema=None) as batch_op:
        batch_op.create_index('ix_time_off_request_id', ['request_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Index `ix_audit_log_user_created` TIDAK di-drop di sini: kolom pertamanya (user_id) dipakai
    # foreign key audit_log.user_id, dan MySQL menolak drop index yang masih dibutuhkan FK
    # (error 1553). Index itu ikut terhapus saat tabelnya di-drop oleh migrasi awal (drop_table).
    with op.batch_alter_table('time_off_requests', schema=None) as batch_op:
        batch_op.drop_index('ix_time_off_request_id')
    with op.batch_alter_table('archived_files', schema=None) as batch_op:
        batch_op.drop_index('ix_archived_files_jenis_sumber')
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.drop_index('ix_uploads_created_at')
