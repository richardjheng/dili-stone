"""Serializable findings; checking never changes authored or review state."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Finding:
    code: str
    item_id: str | None
    number: int | None
    message: str
    words: list[str] = field(default_factory=list)


@dataclass
class Report:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    infos: list[Finding] = field(default_factory=list)
    pending_count: int = 0

    def add(self, severity, code, message, *, obj=None, words=()):
        getattr(self, severity).append(Finding(
            code, getattr(obj, "id", None), getattr(obj, "number", None),
            message, list(words)))

    def to_dict(self):
        return asdict(self)

    def render(self, *, infos=True):
        lines = [f"檢核結果：{len(self.errors)} 個錯誤、{len(self.warnings)} 個警告、"
                 f"{len(self.infos)} 個資訊；待審題目／題組共 {self.pending_count} 筆。"]
        sections = [("錯誤", self.errors), ("警告", self.warnings)] + ([("資訊", self.infos)] if infos else [])
        for label, findings in sections:
            for finding in findings:
                where = f"第 {finding.number} 題" if finding.number is not None else "整卷／題組"
                if finding.item_id:
                    where += f" ({finding.item_id})"
                words = f" [{', '.join(finding.words)}]" if finding.words else ""
                lines.append(f"{label} {where} {finding.code}：{finding.message}{words}")
        return "\n".join(lines)
