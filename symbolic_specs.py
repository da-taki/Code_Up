import re
from typing import Dict, List, Optional


NUMBER_WORDS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "panch": 5,
    "paanch": 5,
    "che": 6,
    "chhe": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
}

ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}

SYMBOL_PHRASES = [
    ("greater than or equal to", ">="),
    ("less than or equal to", "<="),
    ("double equals", "=="),
    ("equals equals", "=="),
    ("equal equal", "=="),
    ("greater than", ">"),
    ("less than", "<"),
    ("open parenthesis", "("),
    ("left parenthesis", "("),
    ("opening parenthesis", "("),
    ("close parenthesis", ")"),
    ("right parenthesis", ")"),
    ("closing parenthesis", ")"),
    ("open bracket", "["),
    ("left bracket", "["),
    ("opening bracket", "["),
    ("close bracket", "]"),
    ("right bracket", "]"),
    ("closing bracket", "]"),
    ("open brace", "{"),
    ("left brace", "{"),
    ("curly brace", "{"),
    ("opening brace", "{"),
    ("close brace", "}"),
    ("right brace", "}"),
    ("closing brace", "}"),
    ("double quote", '"'),
    ("single quote", "'"),
    ("asterisks", "*"),
    ("asterisk", "*"),
    ("stars", "*"),
    ("star", "*"),
    ("hashes", "#"),
    ("hash", "#"),
    ("pounds", "#"),
    ("pound", "#"),
    ("plus", "+"),
    ("minus", "-"),
    ("dash", "-"),
    ("divided by", "/"),
    ("slash", "/"),
    ("percent", "%"),
    ("modulo", "%"),
    ("equal to", "="),
    ("equals", "="),
    ("quote", '"'),
    ("comma", ","),
    ("colon", ":"),
    ("semicolon", ";"),
    ("underscore", "_"),
    ("dot", "."),
    ("point", "."),
]

REPEATABLE_SYMBOLS = {"*", "#", "+", "-", "/", "%"}
WORD_PATTERN = re.compile(r"\b[a-zA-Z]+\b")


def _num_pattern() -> str:
    words = sorted(set(NUMBER_WORDS) | set(ORDINAL_WORDS), key=len, reverse=True)
    return r"(?:\d+|" + "|".join(re.escape(word) for word in words) + r")"


NUM_RE = _num_pattern()


def parse_spoken_number(value: str) -> Optional[int]:
    token = str(value or "").strip().lower().strip(".,")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token, ORDINAL_WORDS.get(token))


def normalize_spoken_symbols(text: str) -> str:
    value = " " + re.sub(r"[-\u2010-\u2015]", " ", str(text or "").lower()) + " "
    value = re.sub(r"(\d+)\s*[xX]\s*(\d+)", r"\1 by \2", value)
    for phrase, symbol in SYMBOL_PHRASES:
        value = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", f" {symbol} ", value)

    def replace_number(match: re.Match) -> str:
        number = parse_spoken_number(match.group(0))
        return str(number) if number is not None else match.group(0)

    value = WORD_PATTERN.sub(replace_number, value)
    return re.sub(r"\s+", " ", value).strip()


def _find_symbol(text: str) -> Optional[str]:
    normalized = normalize_spoken_symbols(text)
    for symbol in ["*", "#", "+", "-", "/", "%"]:
        if re.search(rf"(^|\s){re.escape(symbol)}(\s|$)", normalized):
            return symbol
    return None


def _dimensions(normalized: str) -> tuple[Optional[int], Optional[int]]:
    by_match = re.search(rf"\b({NUM_RE})\s*(?:by|x|times)\s*({NUM_RE})\b", normalized)
    if by_match:
        return parse_spoken_number(by_match.group(1)), parse_spoken_number(by_match.group(2))

    rows_match = re.search(rf"\b({NUM_RE})\s+rows?\b", normalized)
    cols_match = re.search(rf"\b({NUM_RE})\s+col(?:umn)?s?\b", normalized)
    rows = parse_spoken_number(rows_match.group(1)) if rows_match else None
    cols = parse_spoken_number(cols_match.group(1)) if cols_match else None
    if rows is not None and cols is not None:
        return rows, cols
    if rows is not None and "square" in normalized:
        return rows, rows
    if cols is not None and "square" in normalized:
        return cols, cols
    return None, None


def _row_exception(normalized: str) -> tuple[Optional[int], Optional[int]]:
    patterns = [
        rf"\b(?:row|line)\s+({NUM_RE})\b.*?\b(?:has|have|having|contains?|should\s+have|with)\s+({NUM_RE})\b",
        rf"\b({NUM_RE})\s+(?:row|line)\b.*?\b(?:has|have|having|contains?|should\s+have|with)\s+({NUM_RE})\b",
    ]
    match = next((m for pattern in patterns if (m := re.search(pattern, normalized))), None)
    if not match:
        return None, None
    return parse_spoken_number(match.group(1)), parse_spoken_number(match.group(2))


def is_exact_symbol_task(text: str) -> bool:
    normalized = normalize_spoken_symbols(text)
    if not normalized:
        return False
    if re.search(
        r"\b(?:run|open|read project files|map my code|code map|walk me through|start tutorial|exit tutorial|use macro|save macro|remember this as)\b",
        normalized,
    ):
        return False
    exact_words = {"pattern", "row", "rows", "line", "lines", "column", "columns", "exactly", "quote", "quotes", "bracket", "brackets", "brace", "braces", "parenthesis", "parentheses"}
    has_exact_word = any(re.search(rf"\b{word}\b", normalized) for word in exact_words)
    has_symbol = _find_symbol(normalized) is not None or any(ch in normalized for ch in "[]{}()\"'")
    return has_symbol or has_exact_word


def validate_exact_output(code: str, prompt: str) -> bool:
    spec = build_exact_symbol_generation(prompt)
    if not spec or not spec.get("success"):
        return True
    return str(spec.get("code", "")).strip() == str(code or "").strip()


def constraint_summary(prompt: str) -> List[str]:
    normalized = normalize_spoken_symbols(prompt)
    symbol = _find_symbol(normalized)
    rows, cols = _dimensions(normalized)
    exception_row, exception_count = _row_exception(normalized)
    summary = []
    if symbol:
        summary.append(f"Use symbol: {symbol}")
    if rows is not None:
        summary.append(f"Total rows: {rows}")
    if cols is not None:
        summary.append(f"Default symbols per row: {cols}")
    if exception_row is not None and exception_count is not None:
        summary.append(f"Row {exception_row} must contain exactly {exception_count} symbols")
    if symbol:
        summary.append("Do not use any other output symbol")
    if summary:
        summary.append("Generated code must be runnable Python")
    return summary


def _voice_guidance(source: str, understood: str) -> str:
    if source != "voice":
        return ""
    return (
        "This is an exact-symbol task. I normalized the transcript, but for best "
        f"accuracy you can type it in the command box. I understood: {understood}."
    )


def _success(code: str, understood: str, source: str) -> Dict[str, object]:
    warning = _voice_guidance(source, understood)
    speech = f"I generated exact-symbol code. I understood: {understood}."
    message = speech
    if warning:
        message += "\n\n" + warning
    return {
        "success": True,
        "code": code,
        "source": "deterministic_exact",
        "speech": speech if not warning else warning + " " + speech,
        "message": message,
        "understood": understood,
        "exact_symbol": True,
    }


def _clarification() -> Dict[str, object]:
    message = (
        "This sounds like an exact-symbol task, but I could not identify the "
        "symbol or the exact counts. Please type a precise command like: "
        "generate code to make a 5 by 5 star pattern where row 3 has 6 stars."
    )
    return {
        "success": False,
        "clarification": True,
        "source": "exact_symbol_clarification",
        "error": message,
        "message": message,
        "speech": message,
    }


def build_exact_symbol_generation(prompt: str, *, source: str = "typed") -> Optional[Dict[str, object]]:
    normalized = normalize_spoken_symbols(prompt)
    if not is_exact_symbol_task(prompt):
        return None

    wrap_match = re.search(r"\bprint\s+(.+?)\s+in\s+(?:quotes?|double quotes?)\b", normalized)
    if wrap_match:
        text = wrap_match.group(1).strip()
        if re.fullmatch(r"[a-z0-9 _-]{1,60}", text):
            return _success(f"print({json_string(chr(34) + text + chr(34))})\n", f'text "{text}" wrapped in quotes', source)

    bracket_match = re.search(r"\bprint\s+(?:brackets?|square brackets?)\s+around\s+(.+)$|\bprint\s+(.+?)\s+(?:inside|in)\s+brackets?\b", normalized)
    if bracket_match:
        text = (bracket_match.group(1) or bracket_match.group(2) or "").strip()
        if re.fullmatch(r"[a-z0-9 _-]{1,60}", text):
            return _success(f"print({json_string('[' + text + ']')})\n", f"text {text} wrapped in brackets", source)

    triangle = "triangle" in normalized or "increasing" in normalized
    if triangle and "pattern" in normalized:
        rows_match = re.search(rf"\b(?:with\s+)?({NUM_RE})\s+rows?\b", normalized)
        rows = parse_spoken_number(rows_match.group(1)) if rows_match else None
        symbol = _find_symbol(normalized)
        if rows and symbol:
            code = f"for row in range(1, {rows + 1}):\n    print({json_string(symbol)} * row)\n"
            return _success(code, f"{rows} rows using {symbol}", source)
        return _clarification()

    rows, cols = _dimensions(normalized)
    symbol = _find_symbol(normalized)
    exception_row, exception_count = _row_exception(normalized)
    if rows is not None or cols is not None or "pattern" in normalized:
        if rows and cols and symbol:
            if exception_row and exception_count:
                if 1 <= exception_row <= rows:
                    code = (
                        f"for row in range({rows}):\n"
                        f"    if row == {exception_row - 1}:\n"
                        f"        print({json_string(symbol)} * {exception_count})\n"
                        "    else:\n"
                        f"        print({json_string(symbol)} * {cols})\n"
                    )
                    return _success(code, f"{rows} rows, {cols} {symbol} per row, row {exception_row} has {exception_count}", source)
                return _clarification()
            code = f"for row in range({rows}):\n    print({json_string(symbol)} * {cols})\n"
            return _success(code, f"{rows} rows and {cols} columns using {symbol}", source)
        return _clarification()

    count_symbol = re.search(rf"\bprint\s+(?:exactly\s+)?({NUM_RE})\s+([*#/+\-%])(?:\s|$)", normalized)
    if count_symbol:
        count = parse_spoken_number(count_symbol.group(1))
        symbol = count_symbol.group(2)
        if count and symbol in REPEATABLE_SYMBOLS:
            return _success(f"print({json_string(symbol)} * {count})\n", f"{count} copies of {symbol}", source)

    repeated = re.search(r"\bprint\s+((?:[*#]\s*){2,})$", normalized)
    if repeated:
        symbols = re.findall(r"[*#]", repeated.group(1))
        if symbols and len(set(symbols)) == 1:
            return _success(f"print({json_string(symbols[0])} * {len(symbols)})\n", f"{len(symbols)} copies of {symbols[0]}", source)

    if "print" in normalized and _find_symbol(normalized):
        return _clarification()
    return None


def json_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
