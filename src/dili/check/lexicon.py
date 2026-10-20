"""Dictionary-filtered morphology and longest phrase matching, without NLP downloads.

Rules propose spellings, not meanings. Only known spellings or explicitly listed
irregular lemmas survive. Cross-layer alternatives remain unresolved for review.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*(?:['’](?![A-Za-z]))?|\d+(?:[.,:]\d+)*")
POSSESSIVES = {"my", "your", "his", "her", "its", "our", "their", "one's"}
LAYER_ORDER = {"scope": 0, "prerequisite": 1, "glossed": 2, "outside": 3}
DATA = Path(__file__).resolve().parents[3] / "data/lemma"
# Contractions resolve to their host word; "not", "be", "will"... are basic words.
CONTRACTION_SUFFIXES = ("n't", "'re", "'m", "'ll", "'ve", "'d")
NEGATIVE_HOSTS = {"wo": "will", "ca": "can", "sha": "shall"}


def load_names(path=None):
    lines = Path(path or DATA / "names.txt").read_text(encoding="utf-8").splitlines()
    return {line.strip().lower() for line in lines if line.strip() and not line.startswith("#")}


def normalize(text):
    return " ".join(m.group().lower().replace("’", "'") for m in TOKEN.finditer(text))


def expand_headword(headword):
    """Return (canonical, aliases), including bracket abbreviations and suffixes."""
    headword = headword.replace("’", "'").strip()
    base = re.sub(r"\([^)]*\)", "", headword).strip()
    alternatives = base.split("/")
    canonical = normalize(alternatives[0])
    aliases = {normalize(a) for a in alternatives}
    for bracket in re.findall(r"\(([^)]*)\)", headword):
        for alternative in re.split(r"[,/]", bracket):
            alternative = alternative.strip()
            aliases.add(normalize(base + alternative if alternative in {"s", "es"} else alternative))
    aliases.discard("")
    return canonical, aliases


class Lemmatizer:
    def __init__(self, irregular_path=None):
        path = Path(irregular_path) if irregular_path else DATA / "irregular.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.irregular = data["forms"]
        if not isinstance(self.irregular, dict) or any(
            not isinstance(k, str) or not isinstance(v, list) or not v
            or any(not isinstance(x, str) or not x for x in v)
            for k, v in self.irregular.items()
        ):
            raise ValueError("不規則變化表 forms 必須是詞形至非空 lemma 清單的對照")

    def candidates(self, word, known):
        word = word.lower().replace("’", "'")
        for suffix in CONTRACTION_SUFFIXES:
            if word.endswith(suffix) and len(word) > len(suffix):
                host = word[:-len(suffix)]
                return self.candidates(NEGATIVE_HOSTS.get(host, host) if suffix == "n't" else host, known)
        if word in known:
            # A listed spelling is itself; suffix rules would only invent
            # false relatives (corner → corn + er). Irregular links still apply.
            return {word, *self.irregular.get(word, ())}
        forms = {word}
        if word.endswith("'s"):
            forms.add(word[:-2])
        elif word.endswith("'"):
            forms.add(word[:-1])
        for stem in tuple(forms):
            if stem.endswith("ies"):
                forms.add(stem[:-3] + "y")
            if stem.endswith("ied"):
                forms.add(stem[:-3] + "y")
            if stem.endswith("s") and not stem.endswith("ss"):
                forms.add(stem[:-1])
            if stem.endswith("es"):
                forms.add(stem[:-2])
            for suffix in ("ed", "ing", "er", "est"):
                if stem.endswith(suffix) and len(stem) > len(suffix) + 1:
                    short = stem[:-len(suffix)]
                    forms.update((short, short + "e"))
                    if short.endswith("i"):
                        forms.add(short[:-1] + "y")
                    if len(short) > 1 and short[-1] == short[-2] and short[-1] not in "aeiou":
                        forms.add(short[:-1])
            if stem.endswith("ying"):
                forms.add(stem[:-4] + "ie")
        candidates = forms & set(known)
        for form in tuple(forms & {word, word.removesuffix("'s"), word.removesuffix("'")}):
            candidates.update(self.irregular.get(form, ()))
        return candidates or {word}


@dataclass(frozen=True)
class Match:
    text: str
    candidates: tuple[tuple[str, str], ...]
    kind: str = "word"

    @property
    def layer(self):
        layers = {layer for _, layer in self.candidates}
        return next(iter(layers)) if len(layers) == 1 else "ambiguous"


class Lexicon:
    def __init__(self, scope=(), prerequisite=(), glossed=(), *, application=(), lemmatizer=None,
                 names=None, outside=()):
        self.lemmatizer = lemmatizer or Lemmatizer()
        self.names = load_names() if names is None else {n.lower() for n in names}
        self.entries = {}
        self.application = set()
        # Disabled wordlists remain known for morphology and diagnostics, but never allowed.
        for layer, heads in (("scope", scope), ("prerequisite", prerequisite), ("glossed", glossed),
                             ("outside", outside)):
            for head in heads:
                canonical, aliases = expand_headword(head)
                for alias in aliases:
                    current = self.entries.setdefault(alias, {})
                    old = current.get(canonical, "outside")
                    current[canonical] = min((old, layer), key=LAYER_ORDER.get)
        for head in application:
            self.application.add(expand_headword(head)[0])
        self.phrases = sorted((tuple(k.split()) for k in self.entries if " " in k),
                              key=lambda p: (-len(p), p))
        self.known = set(self.entries)
        self.known.update(part for phrase in self.phrases for part in phrase)

    def candidates(self, word):
        result = {}
        for candidate in self.lemmatizer.candidates(word, self.entries):
            for lemma, layer in self.entries.get(candidate, {candidate: "outside"}).items():
                old = result.get(lemma, "outside")
                result[lemma] = min((old, layer), key=LAYER_ORDER.get)
        return tuple(sorted(result.items()))

    def _phrase_matches(self, phrase, tokens):
        for expected, actual in zip(phrase, tokens):
            word = actual.group().lower().replace("’", "'")
            if expected == "one's" and (word in POSSESSIVES or word.endswith("'s") or word.endswith("s'")):
                continue
            if expected not in self.lemmatizer.candidates(word, self.known):
                return False
        return True

    def scan(self, text):
        tokens = list(TOKEN.finditer(text))
        result = []
        i = 0
        while i < len(tokens):
            longest = 0
            matches = {}
            for phrase in self.phrases:
                n = len(phrase)
                if n < longest:
                    break
                if i + n > len(tokens):
                    continue
                # Never join words across sentence punctuation or blanks.
                if any(not re.fullmatch(r"[\s-]+", text[a.end():b.start()])
                       for a, b in zip(tokens[i:i+n], tokens[i+1:i+n])):
                    continue
                if self._phrase_matches(phrase, tokens[i:i+n]):
                    longest = n
                    for lemma, layer in self.entries[" ".join(phrase)].items():
                        old = matches.get(lemma, "outside")
                        matches[lemma] = min((old, layer), key=LAYER_ORDER.get)
            if longest:
                result.append(Match(text[tokens[i].start():tokens[i+longest-1].end()], tuple(sorted(matches.items()))))
                i += longest
                continue
            token = tokens[i]
            word = token.group()
            candidates = self.candidates(word)
            kind = "number" if word[0].isdigit() else "word"
            prefix = text[:token.start()].rstrip()
            sentence_start = not prefix or prefix[-1] in ".!?\n。！？"
            if (kind == "word" and word[0].isupper() and all(layer == "outside" for _, layer in candidates)
                    and all(lemma not in self.entries for lemma, _ in candidates)
                    and (not sentence_start or re.sub(r"['’]s?$", "", word.lower()) in self.names)):
                kind = "proper_name"
            result.append(Match(word, candidates, kind))
            i += 1
        return result

    def is_target(self, text, *, application=False):
        matches = self.scan(text)
        # An answer/option must be ONE vocabulary entry (which may be a phrase).
        return len(matches) == 1 and matches[0].kind == "word" and all(
            layer == "scope" and (not application or lemma in self.application)
            for lemma, layer in matches[0].candidates)
