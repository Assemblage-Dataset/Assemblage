"""add buildopt.codegen_backend

Revision ID: e9d4c1f2a3b5
Revises: d3e4f5a6b7c8
Create Date: 2026-07-16 00:00:00.000000

Rust rollout (R1): every rustc codegen backend (llvm / cranelift / gcc) gets
its own buildopt row, so the backend joins the registration identity. Existing
C/C++ rows keep the '' default — C builders re-register onto their existing
rows with no churn (their wire default is also '').
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9d4c1f2a3b5'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "buildopt",
        sa.Column("codegen_backend", sa.String(32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("buildopt", "codegen_backend")
