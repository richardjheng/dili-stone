"""One generated section per batch; references can be checked against a catalog."""

from typing import Self

from pydantic import model_validator

from .blueprint import Blueprint
from .common import Id, Model, Text, unique
from .item import Group, Item
from .lesson import Lesson


class Batch(Model):
    run_id: Id
    section_id: Id
    produced_by: Text
    items: tuple[Item, ...] = ()
    groups: tuple[Group, ...] = ()

    def all_items(self) -> tuple[Item, ...]:
        return (*self.items, *(item for group in self.groups for item in group.items))

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if bool(self.items) == bool(self.groups):
            raise ValueError("batch requires either standalone items or groups")
        all_items = self.all_items()
        unique((x.id for x in (*self.groups, *all_items)), "batch object IDs")
        unique((x.number for x in all_items), "question numbers")
        if any(x.section_id != self.section_id for x in (*self.groups, *all_items)):
            raise ValueError("batch section_id does not match its contents")
        if len({x.kind for x in all_items}) != 1:
            raise ValueError("a batch must contain one section kind")
        if self.items and any(i.kind in {"reading", "cloze"} for i in self.items):
            raise ValueError("reading/cloze items must be delivered inside groups")
        return self

    def validate_against(self, blueprint: Blueprint, *, lessons: tuple[Lesson, ...] | None = None) -> Self:
        """Structural check only; semantic scope/ambiguity analysis belongs to M2."""
        sections = {s.id: s for s in blueprint.sections}
        if self.section_id not in sections:
            raise ValueError("batch section is absent from blueprint")
        section = sections[self.section_id]
        all_items = self.all_items()
        if len(all_items) != section.item_count or any(i.kind != section.kind for i in all_items):
            raise ValueError("batch kind/count does not match blueprint")
        if len(self.groups) != section.group_count or tuple(len(g.items) for g in self.groups) != section.items_per_group:
            raise ValueError("batch group sizes do not match blueprint")
        offset = 1
        for prior in blueprint.sections:
            if prior.id == section.id:
                break
            offset += prior.item_count
        if [i.number for i in all_items] != list(range(offset, offset + section.item_count)):
            raise ValueError("question numbers do not match the blueprint section range")
        if not section.allow_figures and any(x.figure is not None for x in (*self.groups, *all_items)):
            raise ValueError("figures are not permitted in this section")
        if lessons is not None:
            unique((lesson.id for lesson in lessons), "catalog lesson IDs")
            catalog = {lesson.id: lesson for lesson in lessons}
            if not set(blueprint.scope) <= catalog.keys():
                raise ValueError("catalog is missing scope lessons")
            scoped = [catalog[lesson_id] for lesson_id in blueprint.scope]
            vocab = [v.id for lesson in scoped for v in lesson.vocab]
            grammar = [g.id for lesson in scoped for g in lesson.grammar]
            unique([*vocab, *grammar], "catalog target IDs")
            for item in all_items:
                if not set(item.targets.vocab_ids) <= set(vocab) or not set(item.targets.grammar_ids) <= set(grammar):
                    raise ValueError(f"{item.id}: targets must reference vocabulary/grammar in scope")
        return self
