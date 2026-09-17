"""Ticket graph, code associations and revision metadata."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import String, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATUSES = ["open", "in_progress", "done", "closed"]
PRIORITIES = ["low", "medium", "high"]


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    title_zh: Mapped[str] = mapped_column(String(200), default="")
    description_zh: Mapped[str] = mapped_column(Text, default="")
    # Root groups are free text; descendants inherit their root's value.
    # group_name avoids GROUP, which is an SQL keyword.
    group_name: Mapped[str] = mapped_column(String(80), default="")
    # Technical notes hold paths, symbols and implementation constraints. They stay
    # untranslated because identifiers and structure are the useful content.
    tech_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(default=1)
    source_version: Mapped[int] = mapped_column(default=1)
    translated_version: Mapped[int | None] = mapped_column(nullable=True)
    translation_due: Mapped[float | None] = mapped_column(nullable=True)
    translation_error: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )


class TicketEdge(Base):
    __tablename__ = "ticket_edges"
    blocker_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    blocked_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    __table_args__ = (CheckConstraint("blocker_id != blocked_id"),)


class CodeLink(Base):
    __tablename__ = "code_links"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    repo: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    ref: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(Text, default="")
    start_line: Mapped[int | None] = mapped_column(nullable=True)
    end_line: Mapped[int | None] = mapped_column(nullable=True)


class GraphMeta(Base):
    __tablename__ = "graph_meta"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(default=0)
    next_ticket_id: Mapped[int] = mapped_column(default=1)


class MutationReceipt(Base):
    """Only acknowledged mutation metadata; never chat or proposed text."""
    __tablename__ = "mutation_receipts"
    request_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)


class NoteWriteAttempt(Base):
    """Exact prior note bytes/absence for unfinished managed writes, never chat."""
    __tablename__ = "note_write_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    files_json: Mapped[str] = mapped_column(Text)


class Proposal(Base):
    __tablename__ = 'proposals'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(12))
    base_revision: Mapped[int] = mapped_column()
    notes_revision: Mapped[str] = mapped_column(String(64))
    basis_json: Mapped[str] = mapped_column(Text)
    operations_json: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(String(500))
    rationale: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String(12), default='pending')
    applied_revision: Mapped[int | None] = mapped_column(nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProposalReceipt(Base):
    """Minimal replay tombstone after manual cleanup; no rationale or operations."""
    __tablename__ = 'proposal_receipts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(80), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
