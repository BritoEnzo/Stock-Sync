"""add shopee_shop_id to marketplacecredential

Revision ID: 004
Revises: 003
Create Date: 2026-11-18 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketplacecredential", sa.Column("shopee_shop_id", sa.BigInteger(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_marketplacecredential_shopee_shop_id", "marketplacecredential", ["shopee_shop_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_marketplacecredential_shopee_shop_id", "marketplacecredential", type_="unique"
    )
    op.drop_column("marketplacecredential", "shopee_shop_id")
