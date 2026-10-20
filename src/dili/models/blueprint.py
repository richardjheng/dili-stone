"""Exam structure. Counts always mean scored sub-items, not group headings."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, computed_field, model_validator

from .common import Id, Model, NonNegativeInt, PositiveInt, Reviewable, Text, unique

ItemKind = Literal["single_choice", "cloze", "reading", "word_in_context", "guided_writing"]
Points = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class ExamInfo(Model):
    title: Text
    exam_type: Literal["quiz", "term", "review"]
    academic_year: Text
    semester: Literal[1, 2]
    duration_minutes: PositiveInt


class Header(Model):
    school_name: Text
    grade: PositiveInt
    show_class_field: bool = True
    show_seat_field: bool = True
    show_name_field: bool = True
    scope_text: Text
    show_points: bool = True


class Prerequisite(Reviewable):
    wordlist_id: Id
    lesson_ids: tuple[Id, ...]  # required, even if explicitly empty
    allow_basic_1200: bool = True
    allow_extended_800: bool = True

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(self.lesson_ids, "prerequisite lesson IDs")
        if self.allow_extended_800 and not self.allow_basic_1200:
            raise ValueError("allow_extended_800 requires allow_basic_1200")
        return self


class Section(Model):
    id: Id
    title: Text
    part: Text | None = None
    kind: ItemKind
    item_count: PositiveInt
    points_per_item: Points
    group_count: NonNegativeInt = 0
    items_per_group: tuple[PositiveInt, ...] = ()
    allow_figures: bool = False

    @model_validator(mode="after")
    def counts_match(self) -> Self:
        if self.kind in {"cloze", "reading"}:
            if self.group_count < 1:
                raise ValueError("cloze/reading sections require groups")
            if len(self.items_per_group) != self.group_count:
                raise ValueError("group_count does not match items_per_group")
            if sum(self.items_per_group) != self.item_count:
                raise ValueError("group sub-item counts do not match item_count")
        elif self.group_count or self.items_per_group:
            raise ValueError("only cloze/reading sections use groups")
        return self


class PartSummary(Model):
    """A display heading and its sections; all scores are derived."""

    title: Text
    sections: tuple[Section, ...] = Field(min_length=1)

    @computed_field
    @property
    def total_items(self) -> int:
        return sum(s.item_count for s in self.sections)

    @computed_field
    @property
    def total_points(self) -> Decimal:
        return sum((s.item_count * s.points_per_item for s in self.sections), Decimal(0))

    @computed_field
    @property
    def points_per_item(self) -> Decimal | None:
        scores = {s.points_per_item for s in self.sections}
        return next(iter(scores)) if len(scores) == 1 else None


class Blueprint(Reviewable):
    id: Id
    exam: ExamInfo
    header: Header
    scope: tuple[Id, ...] = Field(min_length=1)
    prerequisite: Prerequisite
    sections: tuple[Section, ...] = Field(min_length=1)
    total_items: PositiveInt
    total_points: Points

    def parts(self) -> tuple[PartSummary, ...]:
        """Keep section order; only adjacent, explicitly named parts merge."""
        groups: list[list[Section]] = []
        for section in self.sections:
            if groups and section.part is not None and groups[-1][-1].part == section.part:
                groups[-1].append(section)
            else:
                groups.append([section])
        return tuple(PartSummary(title=group[0].part or group[0].title, sections=tuple(group))
                     for group in groups)

    @model_validator(mode="after")
    def totals_match(self) -> Self:
        unique(self.scope, "scope lesson IDs")
        unique((s.id for s in self.sections), "section IDs")
        seen_parts: set[str] = set()
        previous = None
        for section in self.sections:
            if section.part is not None and section.part != previous:
                if section.part in seen_parts:
                    raise ValueError("sections with the same part must be contiguous")
                seen_parts.add(section.part)
            previous = section.part
        if set(self.scope) & set(self.prerequisite.lesson_ids):
            raise ValueError("scope and prerequisite lessons must be disjoint")
        if sum(s.item_count for s in self.sections) != self.total_items:
            raise ValueError("total_items does not match section counts")
        points = sum((s.item_count * s.points_per_item for s in self.sections), Decimal(0))
        if points != self.total_points:
            raise ValueError("total_points does not match section scores")
        return self
