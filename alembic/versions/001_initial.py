"""Historical marker for the retired parallel initial migration.

Revision ID: 001_initial
Revises: 0001_initial

The original parallel revision duplicated tables created by 0001_initial and
was never part of the deployed payment schema. Keeping it as a no-op marker
allows Alembic to converge its graph without replaying conflicting DDL.
"""

from typing import Sequence, Union


revision: str = "001_initial"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record the retired branch without changing the deployed schema."""


def downgrade() -> None:
    """The historical marker has no schema effect to reverse."""
