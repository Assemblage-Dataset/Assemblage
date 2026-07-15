"""add commit hexsha column

Revision ID: da9af5c6d2e0
Revises: d33a95ecc21a
Create Date: 2025-12-15 19:35:34.018268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'da9af5c6d2e0'
down_revision: Union[str, Sequence[str], None] = 'd33a95ecc21a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('commit_hexsha', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True) )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'commit_hexsha')
