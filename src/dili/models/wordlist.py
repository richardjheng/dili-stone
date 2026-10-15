"""Load the M1 CSV/YAML contract without treating the word list as exam targets."""

import csv
from datetime import date
from pathlib import Path
from typing import Self

import yaml
from pydantic import AnyHttpUrl, Field, model_validator

from .common import Id, Model, NonNegativeInt, Reviewable, Text, unique


class WordEntry(Model):
    headword: Text
    pos: str  # preserve source notation, including an explicitly unknown empty POS
    basic_1200: bool
    note: str = ""


class WordListMetadata(Model):
    source_url: AnyHttpUrl
    source_document: Text | None = None
    version: Text
    retrieved_date: date
    usage_terms: Text
    entry_count: NonNegativeInt
    basic_1200_count: NonNegativeInt | None = None
    method: Text | None = None
    known_issues: tuple[Text, ...] = ()


class WordList(Reviewable):
    id: Id
    metadata: WordListMetadata
    entries: tuple[WordEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def count_matches(self) -> Self:
        if len(self.entries) != self.metadata.entry_count:
            raise ValueError("word list row count does not match metadata")
        if (self.metadata.basic_1200_count is not None
                and sum(e.basic_1200 for e in self.entries) != self.metadata.basic_1200_count):
            raise ValueError("basic_1200 count does not match metadata")
        unique(((e.headword, e.pos) for e in self.entries), "headword/POS pairs")
        return self


def load_wordlist(csv_path: str | Path, metadata_path: str | Path | None = None,
                  *, wordlist_id: str | None = None) -> WordList:
    """Read UTF-8 (optional BOM), strict headers/booleans, and safe YAML.

    Metadata defaults to <csv stem>.meta.yaml; the ID defaults to the CSV stem.
    Loading never creates a human review, even for the official basic word list.
    """
    csv_path = Path(csv_path)
    metadata_path = Path(metadata_path) if metadata_path else csv_path.with_suffix(".meta.yaml")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8-sig"))
    if not isinstance(metadata, dict):
        raise ValueError("word list metadata must be a mapping")
    entries = []
    booleans = {"true": True, "false": False, "1": True, "0": False,
                "yes": True, "no": False}
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["headword", "pos", "basic_1200", "note"]:
            raise ValueError("CSV headers must be headword,pos,basic_1200,note")
        for line, row in enumerate(reader, start=2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"CSV line {line}: wrong number of columns")
            flag = row["basic_1200"].strip().lower()
            if flag not in booleans:
                raise ValueError(f"CSV line {line}: invalid basic_1200 boolean")
            entries.append(WordEntry(headword=row["headword"], pos=row["pos"].strip(),
                                     basic_1200=booleans[flag], note=row["note"].strip()))
    return WordList(id=wordlist_id or csv_path.stem, metadata=metadata, entries=tuple(entries))
