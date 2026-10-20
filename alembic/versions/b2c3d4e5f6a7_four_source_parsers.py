"""four source parsers

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-20 11:00:00.000000

Tabel mentah empat sumber (Activity, Report Attendance, Time Off, Approval), master karyawan,
status periode, paket file upload, dan kolom Rekap Absensi terpadu pada tabel attendance.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('upload_files',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=False),
    sa.Column('jenis_sumber', sa.String(length=20), nullable=False),
    sa.Column('nama_file', sa.String(length=255), nullable=False),
    sa.Column('stored_path', sa.Text(), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('validasi', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('upload_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_upload_files_upload_id'), ['upload_id'], unique=False)

    op.create_table('employees',
    sa.Column('employee_id', sa.String(length=30), nullable=False),
    sa.Column('nama', sa.String(length=150), nullable=False),
    sa.Column('jabatan', sa.String(length=150), nullable=True),
    sa.Column('organisasi', sa.String(length=100), nullable=True),
    sa.Column('cabang', sa.String(length=150), nullable=True),
    sa.Column('status_kerja', sa.String(length=50), nullable=True),
    sa.Column('resign', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('employee_id')
    )

    op.create_table('periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tahun', sa.Integer(), nullable=False),
    sa.Column('bulan', sa.Integer(), nullable=False),
    sa.Column('ditutup', sa.Boolean(), nullable=False),
    sa.Column('ditutup_oleh', sa.Integer(), nullable=True),
    sa.Column('ditutup_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('alasan', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['ditutup_oleh'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tahun', 'bulan', name='uq_period_tahun_bulan')
    )

    op.create_table('raw_activity',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.String(length=30), nullable=False),
    sa.Column('tanggal', sa.Date(), nullable=False),
    sa.Column('waktu_menit', sa.Integer(), nullable=True),
    sa.Column('tipe', sa.String(length=20), nullable=True),
    sa.Column('lokasi', sa.String(length=150), nullable=True),
    sa.Column('shift_name', sa.String(length=50), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('raw_activity', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_raw_activity_upload_id'), ['upload_id'], unique=False)
        batch_op.create_index('ix_raw_activity_emp_tgl', ['employee_id', 'tanggal'], unique=False)

    op.create_table('raw_report',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.String(length=30), nullable=False),
    sa.Column('nama', sa.String(length=150), nullable=True),
    sa.Column('tanggal', sa.Date(), nullable=False),
    sa.Column('shift_name', sa.String(length=50), nullable=True),
    sa.Column('jadwal_masuk_menit', sa.Integer(), nullable=True),
    sa.Column('jadwal_pulang_menit', sa.Integer(), nullable=True),
    sa.Column('check_in_menit', sa.Integer(), nullable=True),
    sa.Column('check_out_menit', sa.Integer(), nullable=True),
    sa.Column('late_in_menit', sa.Integer(), nullable=True),
    sa.Column('early_out_menit', sa.Integer(), nullable=True),
    sa.Column('actual_hour', sa.Float(), nullable=True),
    sa.Column('real_hour', sa.Float(), nullable=True),
    sa.Column('kode_kehadiran', sa.String(length=20), nullable=True),
    sa.Column('organisasi', sa.String(length=100), nullable=True),
    sa.Column('cabang', sa.String(length=150), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('employee_id', 'tanggal', name='uq_raw_report_emp_tgl')
    )
    with op.batch_alter_table('raw_report', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_raw_report_upload_id'), ['upload_id'], unique=False)

    op.create_table('time_off_requests',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.String(length=30), nullable=False),
    sa.Column('request_id', sa.String(length=50), nullable=True),
    sa.Column('tanggal_mulai', sa.Date(), nullable=False),
    sa.Column('tanggal_selesai', sa.Date(), nullable=True),
    sa.Column('policy_code', sa.String(length=20), nullable=True),
    sa.Column('policy_name', sa.String(length=150), nullable=True),
    sa.Column('tipe', sa.String(length=30), nullable=True),
    sa.Column('durasi', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('time_off_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_time_off_requests_upload_id'), ['upload_id'], unique=False)
        batch_op.create_index('ix_time_off_emp_mulai', ['employee_id', 'tanggal_mulai'], unique=False)

    op.create_table('attendance_corrections',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('upload_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.String(length=30), nullable=False),
    sa.Column('tanggal_efektif', sa.Date(), nullable=False),
    sa.Column('jam_masuk_menit', sa.Integer(), nullable=True),
    sa.Column('jam_keluar_menit', sa.Integer(), nullable=True),
    sa.Column('alasan', sa.Text(), nullable=True),
    sa.Column('alasan_kategori', sa.String(length=50), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=True),
    sa.Column('approver', sa.String(length=150), nullable=True),
    sa.Column('diajukan_pada', sa.Date(), nullable=True),
    sa.Column('raw', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('attendance_corrections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attendance_corrections_upload_id'), ['upload_id'], unique=False)
        batch_op.create_index('ix_correction_emp_tgl', ['employee_id', 'tanggal_efektif'], unique=False)

    # Kolom Rekap Absensi terpadu pada tabel attendance (nullable agar data template lama tetap valid).
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jam_kerja_nyata', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('jumlah_ketukan', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('clock_out_hilang', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('clock_in_hilang', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('sumber', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('dikoreksi', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('koreksi_alasan', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('koreksi_approver', sa.String(length=150), nullable=True))
        batch_op.add_column(sa.Column('koreksi_setelah_tutup', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('izin_kode', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('izin_nama', sa.String(length=150), nullable=True))
        batch_op.add_column(sa.Column('izin_tipe', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('selisih_report', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('status_report', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        for col in [
            'status_report', 'selisih_report', 'izin_tipe', 'izin_nama', 'izin_kode',
            'koreksi_setelah_tutup', 'koreksi_approver', 'koreksi_alasan', 'dikoreksi', 'sumber',
            'clock_in_hilang', 'clock_out_hilang', 'jumlah_ketukan', 'jam_kerja_nyata',
        ]:
            batch_op.drop_column(col)

    with op.batch_alter_table('attendance_corrections', schema=None) as batch_op:
        batch_op.drop_index('ix_correction_emp_tgl')
        batch_op.drop_index(batch_op.f('ix_attendance_corrections_upload_id'))
    op.drop_table('attendance_corrections')

    with op.batch_alter_table('time_off_requests', schema=None) as batch_op:
        batch_op.drop_index('ix_time_off_emp_mulai')
        batch_op.drop_index(batch_op.f('ix_time_off_requests_upload_id'))
    op.drop_table('time_off_requests')

    with op.batch_alter_table('raw_report', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_raw_report_upload_id'))
    op.drop_table('raw_report')

    with op.batch_alter_table('raw_activity', schema=None) as batch_op:
        batch_op.drop_index('ix_raw_activity_emp_tgl')
        batch_op.drop_index(batch_op.f('ix_raw_activity_upload_id'))
    op.drop_table('raw_activity')

    op.drop_table('periods')
    op.drop_table('employees')

    with op.batch_alter_table('upload_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_upload_files_upload_id'))
    op.drop_table('upload_files')
