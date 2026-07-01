
import re
from typing import Iterable, List, Sequence, Tuple

_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "with", "and", "or", "in", "on", "that",
    "this", "it", "is", "are", "do", "does", "make", "create", "generate", "build",
    "write", "code", "program", "please", "now", "then", "first", "next", "value",
    "values", "thing", "things", "something", "use", "using", "want", "would",
    "should", "same", "again", "number", "numbers", "print", "show", "display",
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "twenty", "thirty",
}

_FILENAME_RE = re.compile(r"\b[\w-]+\.[a-zA-Z]{1,5}\b")
_NUMBER_RE = re.compile(r"\d+")
_WORD_RE = re.compile(r"[a-zA-Z]{2,}")


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def numbers(text: str) -> List[str]:
    return _NUMBER_RE.findall(str(text or ""))


def filenames(text: str) -> List[str]:
    return [f.lower() for f in _FILENAME_RE.findall(str(text or ""))]


def salient_terms(text: str, *, extra_stop: Iterable[str] = ()) -> List[str]:
    stop = _STOPWORDS | {s.lower() for s in extra_stop}
    out: List[str] = []
    for word in _WORD_RE.findall(str(text or "").lower()):
        if word not in stop and word not in out:
            out.append(word)
    return out


def fact_present(fact: str, text: str) -> bool:
    f = _normalize(fact)
    if not f:
        return True
    pattern = re.escape(f).replace(r"\ ", r"\s+")
    left = r"(?<!\w)" if f[0].isalnum() else ""
    right = r"(?!\w)" if f[-1].isalnum() else ""
    return re.search(left + pattern + right, _normalize(text)) is not None


def validate(
    ai_text: str,
    *,
    deterministic_text: str = "",
    required_facts: Sequence[str] = (),
    context: str = "",
    max_words: int = 40,
    max_chars: int = 240,
    single_sentence: bool = False,
) -> Tuple[bool, str]:
    ai = str(ai_text or "").strip().strip('"').strip()
    if not ai:
        return False, "empty"

    if len(ai) > max_chars:
        return False, "too_long_chars"
    if len(ai.split()) > max_words:
        return False, "too_long_words"
    if "\n\n" in ai or (single_sentence and "\n" in ai):
        return False, "multiline"
    low = ai.lower()
    if "```" in ai or any(tok in low for tok in ("print(", "def ", "import ", "input(", "for(", "while(")):
        return False, "contains_code"
    if single_sentence:
        terminals = ai.count(".") + ai.count("?") + ai.count("!")
        if terminals > 2:
            return False, "too_many_sentences"

    auto = list(numbers(deterministic_text)) + list(filenames(deterministic_text))
    for fact in list(required_facts) + auto:
        if not fact_present(fact, ai):
            return False, f"dropped_fact:{fact}"

    allowed = " ".join([deterministic_text, context, " ".join(required_facts)])
    allowed_numbers = set(numbers(allowed))
    allowed_files = set(filenames(allowed))
    for fname in filenames(ai):
        if fname not in allowed_files:
            return False, f"invented_file:{fname}"
    for num in numbers(ai):
        if num not in allowed_numbers:
            return False, f"invented_number:{num}"

    return True, "ok"


def ground(
    ai_text: str,
    deterministic_text: str,
    *,
    required_facts: Sequence[str] = (),
    context: str = "",
    max_words: int = 40,
    max_chars: int = 240,
    single_sentence: bool = False,
) -> str:
    ok, _reason = validate(
        ai_text,
        deterministic_text=deterministic_text,
        required_facts=required_facts,
        context=context,
        max_words=max_words,
        max_chars=max_chars,
        single_sentence=single_sentence,
    )
    return str(ai_text).strip().strip('"').strip() if ok else deterministic_text
