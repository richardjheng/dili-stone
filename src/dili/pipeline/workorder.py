"""Work orders for the AI that writes a section, and for the AI that cross-reviews it."""

from dili.models import Blueprint, Lesson

from .runs import Run

KIND_RULES = {
    "single_choice": """\
- 單題四選一。題型混合：單字題（四個選項都必須是範圍內字彙，同詞性）、文法句型題、對話題（`A: … B: ______`）。
- 「必須涵蓋的考點」中的每個文法點至少各出一題；單字考點各出一題（可放在選項或題幹空格）。
- 可以有 1～3 題「Choose the WRONG one.」或「Choose the CORRECT one.」，這類題目設定 `polarity` 為 `wrong`／`correct`。
- 題幹空格用 `______`。""",
    "cloze": """\
- 每組一篇原創短文（80～130 字），空格依序寫成 `{{題號}}`（例如 `{{26}}`），每格對應一個子題；
  子題的 `stem` 寫簡短說明，例如 `Choose the best answer for blank 26.`。
- 兩篇主題不同（例如社團生活、問路與交通、家事、職業夢想），各自混合單字與文法考點。
- 子題選項必須能放回空格讀通，只有一個正確答案。""",
    "reading": """\
- 共 4 組，題組依序為：
  1. **街道地圖**（`street_map`）：考問路或位置關係（next to／across from／between／on the corner／in front of）。
     若題目考「照指示走會到哪裡」，在地圖的 `directions` 與 `arrival` 寫出路線，正確答案必須等於程式推出的地點。
  2. **時刻表或路線**（`table` 的 timetable，或 `route`）：考時間計算、搭乘方式。
  3. **筆記或比較**（`table` 的 notes，或 `venn`）：考比較與歸納。
  4. **純文字短文**（120～180 字）：考主旨、細節、推論、字義。
- 有圖的題組可以另附一小段文字說明情境（`passage`）。
- 題目要真的需要讀圖或讀文才能作答。""",
    "word_in_context": """\
- 每題一個英文句子，空格處給首字母與底線，例如 `My uncle wants to be a l________ in the future.`。
- `prompt.task` 為 `word_in_context`，`prompt.instruction` 寫「依句意及字首填入適當的單字」，`first_letter` 填首字母。
- `answer.accepted_text` 寫句中正確的形式（可以是變化形，例如過去式、複數），必須是「必須涵蓋的考點」中的單字。
- 每題考一個指定單字，句意要能唯一指向該字。""",
    "guided_writing": """\
- 題型混合：句子改寫（`rewrite`，依提示改寫）、句子合併（`combine`）、中翻英（`translate_zh_en`）。
- `stem` 寫原句或中文句；`prompt.instruction` 寫提示，例如「用 It … to … 改寫」「用 after 合併兩句」。
- `answer.accepted_text` 列出所有可接受的答案寫法（大小寫、縮寫、同義寫法），解析說明關鍵句型。
- 每題考一個「必須涵蓋的考點」中的文法點。""",
}

COMMON_RULES = """\
## 共同規則
1. **原創**：不得使用課本、講義、備課用書或任何範本考卷的句子、課文、例句、練習題，也不要改寫它們。
2. **範圍**：考點只能是下表的字彙與文法。題幹與情境可以使用教育部 2000 基本字詞
   （`data/wordlists/moe2000.csv`）。兩者都不在的字，要在該題或題組的 `glossed` 附中文註解；
   範圍外的句型一律不要用。
3. **人名**只用 `data/lemma/names.txt` 裡的名字。
4. **答案唯一**：每題只能有一個正確答案，其他選項要明確錯誤，不要出模稜兩可的題目。
5. **解析**：`explanation.correct_zh` 用中文說明為什麼這個答案正確；選擇題的
   `explanation.distractors` 為另外三個選項各寫一句中文，說明為什麼不選。
6. `targets` 填這題考的字彙／文法 ID（見下表），至少一個。
7. 不要寫 `status`、`content_hash`、`reviews`；所有題目都會是「待審」，由 Richard 人工確認。
8. **格式**：一個 Batch JSON，結構見 `schemas/batch.json`，範例見 `examples/synthetic_batch.json`、
   `examples/synthetic_group.json`、`examples/figures.json`。
"""


def first_numbers(blueprint: Blueprint) -> dict[str, int]:
    numbers, n = {}, 1
    for section in blueprint.sections:
        numbers[section.id] = n
        n += section.item_count
    return numbers


def _vocab_table(lessons: list[Lesson]) -> str:
    rows = ["| ID | 字詞 | 詞性 | 中文 | 教材分類 |", "| --- | --- | --- | --- | --- |"]
    for lesson in lessons:
        for v in lesson.vocab:
            meanings = "；".join(s.meaning_zh for s in v.senses)
            category = "應用" if v.textbook_category == "application" else "認識"
            rows.append(f"| `{v.id}` | {v.headword} | {', '.join(v.pos)} | {meanings} | {category} |")
    return "\n".join(rows)


def _grammar_table(lessons: list[Lesson]) -> str:
    rows = ["| ID | 句型 | 說明 |", "| --- | --- | --- |"]
    for lesson in lessons:
        rows += [f"| `{g.id}` | {g.name} | {g.description} |" for g in lesson.grammar]
    return "\n".join(rows)


def generation_order(run: Run, section_id: str, lessons: list[Lesson], *, worker: str,
                     previous_errors: str = "") -> str:
    blueprint = run.blueprint()
    section = next(s for s in blueprint.sections if s.id == section_id)
    first = first_numbers(blueprint)[section_id]
    last = first + section.item_count - 1
    required = run.plan()[section_id]["required"]
    groups = (f"，分 {section.group_count} 組，每組子題數依序為 {list(section.items_per_group)}"
              if section.group_count else "")
    must = []
    if required["vocab"]:
        must.append("- 字彙：" + "、".join(f"`{i}`" for i in required["vocab"]))
    if required["grammar"]:
        must.append("- 文法：" + "、".join(f"`{i}`" for i in required["grammar"]))
    retry = f"\n## 上一次交付的檢核錯誤（請全部修正）\n\n```\n{previous_errors}\n```\n" if previous_errors else ""
    return f"""# 出題工作單：{blueprint.exam.title}・{section.title}（{run.id}）

為台灣國中八年級英文段考出一個大題。範圍：{blueprint.header.scope_text}。

## 這個大題
- section_id：`{section.id}`，題型 `{section.kind}`，共 {section.item_count} 題{groups}。
- 題號 **{first}～{last}**，依序連續；每題 {section.points_per_item} 分。
- {"可以使用圖片（Figure）。" if section.allow_figures else "不使用圖片。"}

{KIND_RULES[section.kind]}

## 必須涵蓋的考點
{chr(10).join(must) if must else "- 不指定；從下表自選，主題分散在各單元。"}

{COMMON_RULES}
## 交付
- **只寫一個檔案**：`{run.batch_path(section_id).as_posix()}`，內容為
  `{{"run_id": "{run.id}", "section_id": "{section.id}", "produced_by": "{worker}", ...}}`。
- 寫完執行 `agora build`，它會檢查這份檔案（格式、題號、範圍外生字、考點）。有錯誤就修正後再跑，直到沒有錯誤。
- 不要修改其他任何檔案，不要 commit。
{retry}
## 範圍字彙
{_vocab_table(lessons)}

## 範圍文法
{_grammar_table(lessons)}
"""


def review_order(run: Run, section_id: str, lessons: list[Lesson], *, reviewer: str) -> str:
    blueprint = run.blueprint()
    section = next(s for s in blueprint.sections if s.id == section_id)
    return f"""# 交叉審題工作單：{blueprint.exam.title}・{section.title}（{run.id}）

請以國中英文老師的角度，審查另一位出題者寫的 `{run.batch_path(section_id).as_posix()}`。
程式已檢查過格式、題號與範圍外生字；你要找的是程式查不出的問題：

1. 答案是否正確、是否唯一；錯誤選項會不會其實也說得通。
2. 題意是否清楚、英文是否自然；有沒有用到範圍外的句型（範圍文法見下表）。
3. 解析是否正確說明了答案與每個錯誤選項。
4. 有圖的題組：題目與圖是否一致，答案能否從圖推出。
5. 題目之間有沒有互相洩漏答案。

## 交付
只寫一個檔案：`{run.review_path(section_id).as_posix()}`，格式：

```json
{{"reviewer": "{reviewer}", "findings": [
  {{"object_id": "題目或題組 id", "severity": "ok | minor | major", "notes": "中文說明；有問題時寫出建議修法"}}
]}}
```

- 每一題、每一個題組都要有一筆（沒問題就 `ok`，notes 簡短說明檢查了什麼）。
- `major`：答案錯誤、不唯一、超出範圍、題意不清；`minor`：措辭、解析不夠完整。
- **不要修改題目檔**，不要 commit。

## 範圍文法
{_grammar_table(lessons)}
"""
