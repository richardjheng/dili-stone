"""Run `python -m dili.models.export_schema` from the project root."""

import json
from pathlib import Path

from . import Batch, Blueprint, Group, Item, Lesson, ReviewRecord, WordList, figure_adapter


def export_schemas(output_dir: str | Path = "schemas") -> tuple[Path, ...]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    models = {"lesson": Lesson, "wordlist": WordList, "blueprint": Blueprint,
              "item": Item, "group": Group, "batch": Batch, "review_record": ReviewRecord}
    # Unsuffixed schemas describe authored inputs; .output includes derived
    # status/hash fields and Decimal strings in normalized model_dump JSON.
    schemas = {}
    for mode, suffix in (("validation", ""), ("serialization", ".output")):
        schemas.update({name + suffix: model.model_json_schema(mode=mode)
                        for name, model in models.items()})
        schemas["figure" + suffix] = figure_adapter.json_schema(mode=mode)
    paths = []
    for name, schema in sorted(schemas.items()):
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:dili:schema:{name}:0.1.0"
        path = output_dir / f"{name}.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def main() -> None:
    for path in export_schemas():
        print(path.as_posix())


if __name__ == "__main__":
    main()
