"""Public early-access registrations, separate from application accounts."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    school_domain: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WaitlistRateLimit(Base):
    __tablename__ = "waitlist_rate_limits"

    client_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer)
