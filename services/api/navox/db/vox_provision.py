"""Durable evidence for the single external provisioning attempt of an epoch."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class VoxMediaProvisionAttempt(Base):
    __tablename__ = "vox_media_provision_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in-flight', 'acknowledged', 'uncertain')",
            name="ck_vox_provision_status",
        ),
        CheckConstraint(
            "(status = 'in-flight' AND finished_at IS NULL) OR "
            "(status != 'in-flight' AND finished_at IS NOT NULL)",
            name="ck_vox_provision_finished",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_vox_provision_time",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # No foreign keys: an unknown remote operation outlives every owned row.
    connection_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
