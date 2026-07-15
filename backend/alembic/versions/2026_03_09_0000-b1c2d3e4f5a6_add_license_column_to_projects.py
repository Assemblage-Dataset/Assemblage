"""add license column to projects

Revision ID: b1c2d3e4f5a6
Revises: fa6e74da04d4
Create Date: 2026-03-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'fa6e74da04d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('license', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'license')
