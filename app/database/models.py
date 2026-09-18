from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class UserModel(TimestampMixin, Base):
    __tablename__ = "users"

    # Telegram user id — естественный ключ, автоинкремент не нужен.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class SubscriptionModel(TimestampMixin, Base):
    """Фильтр поиска пользователя. Параметры поиска хранятся в JSONB."""

    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_active_marketplace", "is_active", "marketplace"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    marketplace: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(128))
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ListingModel(Base):
    """Объявление. Одно и то же объявление хранится один раз на площадку."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("marketplace", "external_id"),
        # GIN-индекс позволяет быстро искать по характеристикам: attributes @> '{"params": ...}'
        Index(
            "ix_listings_attributes",
            "attributes",
            postgresql_using="gin",
            postgresql_ops={"attributes": "jsonb_path_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    location: Mapped[str | None] = mapped_column(String(256))
    image_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DeliveryModel(Base):
    """Связь «фильтр — объявление».

    Составной первичный ключ гарантирует на уровне БД, что одно объявление
    по одному фильтру будет доставлено не более одного раза.
    """

    __tablename__ = "deliveries"
    __table_args__ = (
        Index(
            "ix_deliveries_pending",
            "subscription_id",
            postgresql_where=text("sent_at IS NULL"),
        ),
    )

    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
