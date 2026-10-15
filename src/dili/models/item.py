"""Original questions, shared contexts, and numbered cloze placeholders."""

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from .blueprint import ItemKind
from .common import Id, Model, PositiveInt, Reviewable, Text, ZhText, unique
from .figure import Figure

Letter = Literal["A", "B", "C", "D"]


class Choices(Model):
    A: Text
    B: Text
    C: Text
    D: Text

    @model_validator(mode="after")
    def distinct(self) -> Self:
        unique((self.A, self.B, self.C, self.D), "option text")
        return self


class WritingPrompt(Model):
    task: Literal["word_in_context", "rewrite", "combine", "translate_zh_en"]
    instruction: Text
    first_letter: str | None = Field(default=None, pattern=r"^[A-Za-z]$")
    last_letter: str | None = Field(default=None, pattern=r"^[A-Za-z]$")
    word_count: PositiveInt | None = None
    cues: tuple[Text, ...] = ()


class Answer(Model):
    choice: Letter | None = None
    accepted_text: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def exactly_one_form(self) -> Self:
        if (self.choice is not None) == bool(self.accepted_text):
            raise ValueError("answer requires either choice or accepted_text")
        unique(self.accepted_text, "accepted answers")
        return self


class OptionExplanation(Model):
    option: Letter
    reason_zh: ZhText


class Explanation(Model):
    correct_zh: ZhText = Field(description="為何應選答案選項；書寫題則解釋可接受答案。")
    distractors: tuple[OptionExplanation, ...] = Field(
        default=(), description="為何不應選其餘選項；不代表這些選項的敘述本身錯誤。")


class Targets(Model):
    vocab_ids: tuple[Id, ...] = ()
    grammar_ids: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def has_target(self) -> Self:
        if not self.vocab_ids and not self.grammar_ids:
            raise ValueError("an item needs at least one vocabulary or grammar target")
        unique(self.vocab_ids, "vocab target IDs")
        unique(self.grammar_ids, "grammar target IDs")
        return self


class Gloss(Model):
    word: Text
    meaning_zh: ZhText


class Item(Reviewable):
    id: Id
    section_id: Id
    kind: ItemKind
    polarity: Literal["best", "wrong", "correct"] = "best"
    number: PositiveInt
    stem: Text
    choices: Choices | None = None
    prompt: WritingPrompt | None = None
    answer: Answer
    explanation: Explanation
    targets: Targets
    glossed: tuple[Gloss, ...] = ()
    figure: Figure | None = None

    @model_validator(mode="after")
    def answer_form(self) -> Self:
        if self.kind in {"single_choice", "cloze", "reading"}:
            if self.choices is None or self.prompt is not None or self.answer.choice is None:
                raise ValueError("choice items require A-D choices and a choice answer")
            letters = [d.option for d in self.explanation.distractors]
            if len(letters) != 3 or set(letters) != set("ABCD") - {self.answer.choice}:
                raise ValueError("explain each unselected option exactly once")
        else:
            if self.polarity != "best":
                raise ValueError("non-default polarity is only allowed for choice items")
            if self.choices is not None or self.prompt is None or not self.answer.accepted_text:
                raise ValueError("written items require a prompt and accepted text answers")
            if self.explanation.distractors:
                raise ValueError("written answers have no distractor options")
            if (self.kind == "word_in_context") != (self.prompt.task == "word_in_context"):
                raise ValueError("prompt task does not match item kind")
        return self


class Group(Reviewable):
    id: Id
    section_id: Id
    kind: Literal["cloze", "reading"]
    instruction: Text
    passage: Text | None = None
    figure: Figure | None = None
    glossed: tuple[Gloss, ...] = ()
    items: tuple[Item, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def context_and_numbering(self) -> Self:
        if self.passage is None and self.figure is None:
            raise ValueError("group needs a shared passage or figure")
        unique((i.id for i in self.items), "sub-item IDs")
        numbers = [i.number for i in self.items]
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise ValueError("sub-item numbers must be consecutive and ordered")
        if any(i.kind != self.kind or i.section_id != self.section_id for i in self.items):
            raise ValueError("sub-items must match group kind and section")
        blanks = re.findall(r"\{\{(.*?)\}\}", self.passage or "")
        if self.kind == "cloze":
            if self.passage is None or blanks != [str(n) for n in numbers]:
                raise ValueError("cloze passage must contain {{number}} once per sub-item, in order")
        elif blanks:
            raise ValueError("reading passages cannot contain cloze placeholders")
        return self
