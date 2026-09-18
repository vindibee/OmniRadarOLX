"""rename subscriptions to filters

Слово «подписка» занимает биллинг, а фильтры поиска в интерфейсе всегда назывались фильтрами.
Переименование без потери данных: ALTER TABLE ... RENAME, ни одна строка не пересоздаётся.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18 16:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имена ограничений задаёт NAMING_CONVENTION, поэтому их тоже переименовываем —
# иначе следующий autogenerate увидит расхождение и начнёт их пересоздавать.
RENAMES = [
    ("ALTER TABLE subscriptions RENAME TO filters", "ALTER TABLE filters RENAME TO subscriptions"),
    (
        "ALTER TABLE deliveries RENAME COLUMN subscription_id TO filter_id",
        "ALTER TABLE deliveries RENAME COLUMN filter_id TO subscription_id",
    ),
    (
        "ALTER INDEX pk_subscriptions RENAME TO pk_filters",
        "ALTER INDEX pk_filters RENAME TO pk_subscriptions",
    ),
    (
        "ALTER TABLE filters RENAME CONSTRAINT fk_subscriptions_user_id_users"
        " TO fk_filters_user_id_users",
        "ALTER TABLE subscriptions RENAME CONSTRAINT fk_filters_user_id_users"
        " TO fk_subscriptions_user_id_users",
    ),
    (
        "ALTER TABLE deliveries RENAME CONSTRAINT fk_deliveries_subscription_id_subscriptions"
        " TO fk_deliveries_filter_id_filters",
        "ALTER TABLE deliveries RENAME CONSTRAINT fk_deliveries_filter_id_filters"
        " TO fk_deliveries_subscription_id_subscriptions",
    ),
    (
        "ALTER INDEX ix_subscriptions_user_id RENAME TO ix_filters_user_id",
        "ALTER INDEX ix_filters_user_id RENAME TO ix_subscriptions_user_id",
    ),
    (
        "ALTER INDEX ix_subscriptions_active_marketplace RENAME TO ix_filters_active_marketplace",
        "ALTER INDEX ix_filters_active_marketplace RENAME TO ix_subscriptions_active_marketplace",
    ),
]


def upgrade() -> None:
    for forward, _ in RENAMES:
        op.execute(forward)


def downgrade() -> None:
    for _, backward in reversed(RENAMES):
        op.execute(backward)
