"""enum columns to varchar; add buildopt.build_type

Revision ID: d3e4f5a6b7c8
Revises: b1c2d3e4f5a6
Create Date: 2026-03-16 00:00:00.000000

RECONSTRUCTED 2026-07-15. The original file was lost when this repo's .git
was destroyed; the live database's alembic_version already reads
d3e4f5a6b7c8, and this file reproduces exactly the schema delta observed
between a fresh `alembic upgrade b1c2d3e4f5a6` database and a schema-only
pg_dump of the live database (see backend/alembic/README.md):

  * every PG-enum-typed column becomes plain VARCHAR (values keep the enum
    label strings, i.e. member NAMES such as 'SUCCESS' / 'LINUX');
  * buildopt.compiler_version and buildopt.save_assembly become nullable;
  * buildopt.build_type VARCHAR(32) NOT NULL DEFAULT 'RelWithDebInfo' is
    added;
  * the optlevel type is dropped; the other enum types remain, orphaned,
    exactly as on the live database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUM_TO_VARCHAR = [
    # (table, column, varchar length, enum type name)
    ('b_status', 'priority', 16, 'prioritystatus'),
    ('b_status', 'clone_status', 32, 'clonestatus'),
    ('b_status', 'build_status', 32, 'buildstatus'),
    ('binaries', 'optimization', 16, 'optlevel'),
    ('buildopt', 'platform', 255, 'supportedplatform'),
    ('buildopt', 'language', 255, 'supportedlanguage'),
    ('buildopt', 'compiler_name', 10, 'supportedcompiler'),
    ('buildopt', 'library', 255, 'supportedarchitecture'),
    ('projects', 'language', 255, 'supportedlanguage'),
    ('projects', 'priority', 16, 'prioritystatus'),
]


def upgrade() -> None:
    """Upgrade schema."""
    for table, column, length, _ in _ENUM_TO_VARCHAR:
        op.alter_column(
            table, column,
            type_=sa.VARCHAR(length=length),
            postgresql_using=f'{column}::text',
        )
    op.alter_column('buildopt', 'compiler_version',
                    existing_type=sa.VARCHAR(length=25), nullable=True)
    op.alter_column('buildopt', 'save_assembly',
                    existing_type=sa.Boolean(), nullable=True)
    op.add_column('buildopt', sa.Column(
        'build_type', sa.VARCHAR(length=32),
        server_default='RelWithDebInfo', nullable=False))
    # Only optlevel was dropped on the live DB; the remaining enum types
    # stay behind as orphans there, so they stay here too.
    op.execute('DROP TYPE IF EXISTS optlevel')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("CREATE TYPE optlevel AS ENUM ('NONE', 'LOW', 'MEDIUM', 'HIGH')")
    op.drop_column('buildopt', 'build_type')
    op.alter_column('buildopt', 'save_assembly',
                    existing_type=sa.Boolean(), nullable=False)
    op.alter_column('buildopt', 'compiler_version',
                    existing_type=sa.VARCHAR(length=25), nullable=False)
    for table, column, _, enum_name in reversed(_ENUM_TO_VARCHAR):
        op.alter_column(
            table, column,
            type_=sa.dialects.postgresql.ENUM(name=enum_name, create_type=False),
            postgresql_using=f'{column}::{enum_name}',
        )
