"""教材欄位只存字詞與自行撰寫的句型說明，不存原文題目。"""

from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, model_validator

from .common import Id, Model, PositiveInt, Reviewable, Text, ZhText, unique


class Source(Model):
    id: Id
    filename: Text
    pdf_page: PositiveInt | None = None  # 1-based physical page
    printed_page: Text | None = None    # e.g. "U4-3", not necessarily numeric
    url: AnyHttpUrl | None = None
    usage_terms: Text


class Sense(Reviewable):
    id: Id
    meaning_zh: ZhText
    is_secondary: bool = False


class VocabEntry(Reviewable):
    id: Id
    headword: Text
    pos: tuple[Text, ...] = Field(min_length=1)
    senses: tuple[Sense, ...] = Field(min_length=1)
    is_phrase: bool = False
    textbook_category: Literal["application", "recognition"]
    category_heading: Text | None = None  # None only when the source has no heading.
    source_refs: tuple[Id, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique(self.pos, "parts of speech")
        unique((s.id for s in self.senses), "sense IDs")
        unique(self.source_refs, "source references")
        return self


class GrammarPoint(Reviewable):
    id: Id
    name: Text
    description: Text
    source_refs: tuple[Id, ...] = Field(min_length=1)


class Lesson(Reviewable):
    id: Id
    publisher: Text
    volume: PositiveInt
    unit: PositiveInt
    title: Text
    academic_year: Text
    completeness: Literal["complete", "partial"]
    sources: tuple[Source, ...] = Field(min_length=1)
    vocab: tuple[VocabEntry, ...] = ()
    grammar: tuple[GrammarPoint, ...] = ()

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        unique((s.id for s in self.sources), "source IDs")
        unique((x.id for x in (*self.vocab, *self.grammar)), "vocab/grammar IDs")
        known = {s.id for s in self.sources}
        for entry in (*self.vocab, *self.grammar):
            if not set(entry.source_refs) <= known:
                raise ValueError(f"{entry.id}: unknown source reference")
        if not self.vocab and not self.grammar:
            raise ValueError("lesson must contain vocabulary or grammar")
        return self
