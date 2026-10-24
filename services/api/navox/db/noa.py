"""Owner-selected Noa appearance; no inferred personal facts."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class NoaSettings(Base):
    __tablename__ = "noa_settings"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_noa_settings_owner"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    look: Mapped[str] = mapped_column(String(8), default="girl")
    vibe: Mapped[str] = mapped_column(String(16), default="midnight")
    revision: Mapped[int] = mapped_column(Integer, default=1)
