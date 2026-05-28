"""add_nickname_to_civicrm_members

Revision ID: a1b2c3d4e5f6
Revises: 74e9ab60ea7e
Create Date: 2026-05-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '74e9ab60ea7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('civicrm_members', sa.Column('nickname', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('civicrm_members', 'nickname')
