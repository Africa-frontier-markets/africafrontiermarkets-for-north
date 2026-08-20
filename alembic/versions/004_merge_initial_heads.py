"""Merge the retired initial marker with the deployed user-auth chain.

Revision ID: 004_merge_initial_heads
Revises: 003_user_oauth_subject, 001_initial
"""

from typing import Sequence, Union


revision: str = "004_merge_initial_heads"
down_revision: Union[str, tuple[str, str]] = ("003_user_oauth_subject", "001_initial")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Unify Alembic history without modifying the business schema."""


def downgrade() -> None:
    """The merge revision has no schema effect to reverse."""

