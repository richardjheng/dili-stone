"""Shared strict, immutable value objects and content-bound review records."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ZhText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                        pattern=r"[\u3400-\u4dbf\u4e00-\u9fff]")]
Id = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Status = Literal["pending", "reviewed"]


def unique(values: Any, label: str) -> None:
    values = list(values)
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {label}")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> Self:
        """Unlike BaseModel.model_copy, updates must pass validation."""
        data = self.model_dump(mode="python", round_trip=True)
        data.update(update or {})
        return type(self).model_validate(data)


class ReviewRecord(Model):
    reviewer: Text
    role: Literal["human", "ai"]
    timestamp: datetime
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    decision: Literal["note", "approve", "request_changes"] = "note"
    notes: Text

    @model_validator(mode="after")
    def valid_review(self) -> Self:
        if self.timestamp.utcoffset() is None:
            raise ValueError("review timestamp must include a timezone")
        if self.role == "ai" and self.decision != "note":
            raise ValueError("AI review may only add notes")
        return self


class ReviewIssue(Model):
    kind: Literal["sense", "prerequisite", "grammar", "other"]
    reference: Text
    notes: Text


def _content(value: Any) -> Any:
    """Only declared content fields enter the hash, including nested content."""
    if isinstance(value, Model):
        return {name: _content(getattr(value, name)) for name in type(value).model_fields
                if name != "reviews"}
    if isinstance(value, (tuple, list)):
        return [_content(v) for v in value]
    if isinstance(value, dict):
        return {k: _content(v) for k, v in value.items()}
    return value


def _children_pending(value: Any) -> bool:
    if isinstance(value, Reviewable):
        return value.status == "pending"
    if isinstance(value, Model):
        return any(_children_pending(getattr(value, name)) for name in type(value).model_fields)
    if isinstance(value, (tuple, list)):
        return any(_children_pending(v) for v in value)
    return False


class Reviewable(Model):
    reviews: tuple[ReviewRecord, ...] = ()
    unresolved: tuple[ReviewIssue, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def derived_fields(cls, value: Any) -> Any:
        # Serialized state is informational, never an approval credential.
        if isinstance(value, dict):
            value = dict(value)
            value.pop("status", None)
            value.pop("content_hash", None)
        return value

    @computed_field
    @property
    def content_hash(self) -> str:
        payload = json.dumps(_content(self), sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @computed_field
    @property
    def status(self) -> Status:
        if self.unresolved:
            return "pending"
        for name in type(self).model_fields:
            if name not in {"reviews", "unresolved"} and _children_pending(getattr(self, name)):
                return "pending"
        decisions = [r for r in self.reviews if r.role == "human"
                     and r.content_hash == self.content_hash and r.decision != "note"]
        # Append order is the audit order, including tied timestamps.
        return "reviewed" if decisions and decisions[-1].decision == "approve" else "pending"

    def add_review(self, *, reviewer: str, role: Literal["human", "ai"], notes: str,
                   decision: Literal["note", "approve", "request_changes"] = "note",
                   timestamp: datetime | None = None) -> Self:
        record = ReviewRecord(reviewer=reviewer, role=role, notes=notes, decision=decision,
                              timestamp=timestamp or datetime.now(timezone.utc),
                              content_hash=self.content_hash)
        return self.model_copy(update={"reviews": (*self.reviews, record)})
