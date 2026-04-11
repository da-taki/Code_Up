"""
Smart Intent Parser for Voice Commands

Converts natural language voice input into structured intents and slots.
This replaces simple string matching with a robust grammar-based system.

Examples:
    "go to line fifteen"      -> {intent: "goto_line",      slots: {line_number: 15}}
    "read function analyze"   -> {intent: "read_function",  slots: {function_name: "analyze"}}
    "find class Parser"       -> {intent: "find_class",     slots: {class_name: "Parser"}}
    "jump to error"           -> {intent: "locate_error",   slots: {}}

Supported spoken numbers: zero–nineteen, tens (twenty, thirty … ninety), and
two-word compounds such as "twenty five" (→ 25), "forty two" (→ 42).
"""

import re
import threading
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Spoken-number vocabulary
# ---------------------------------------------------------------------------

_ONES: Dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS: Dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
WORD_TO_NUMBER: Dict[str, int] = {**_ONES, **_TENS, "hundred": 100, "thousand": 1000}


class IntentParser:
    """Parse voice commands into structured intents and slots."""

    # -----------------------------------------------------------------------
    # Navigation patterns
    # -----------------------------------------------------------------------

    GOTO_LINE_PATTERNS = [
        r"(?:go|jump|navigate|move)\s+to\s+line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"(?:go|jump|navigate|move)\s+to\s+line\s+(\w+)",
    ]

    READ_LINE_PATTERNS = [
        r"read\s+(?:the\s+)?line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"read\s+(?:the\s+)?line\s+(\w+)",
    ]

    READ_FUNCTION_PATTERNS = [
        r"read\s+(?:the\s+)?function\s+(\w+)",
    ]

    FIND_CLASS_PATTERNS = [
        r"(?:find|locate)\s+class\s+(\w+)",
        r"(?:go\s+)?to\s+class\s+(\w+)",
    ]

    FIND_FUNCTION_PATTERNS = [
        r"(?:find|locate)\s+function\s+(\w+)",
        r"(?:go\s+)?to\s+function\s+(\w+)",
    ]

    # -----------------------------------------------------------------------
    # Execution patterns
    RUN_PATTERNS = [
        r"^run\s*(?:code|program|it)?$",
        r"^execute\s*(?:code|program|it)?$",
        r"^start\s*(?:code|program|it)?$",
    ]

    ANALYZE_PATTERNS = [
        r"^analyze\s*(?:(?:the\s+)?code)?$",
        r"^analyse\s*(?:(?:the\s+)?code)?$",
        r"^(?:check|review|explain)\s+(?:the\s+)?code$",
    ]

    FIX_PATTERNS = [
        r"^fix\s*(?:(?:my\s+)?code)?$",
        r"^auto\s*fix$",
        r"^repair\s+(?:my\s+)?code$",
        r"^correct\s+(?:my\s+)?code$",
    ]

    ADVISE_PATTERNS = [
        r"^advise(?:\s+on\s+(?:the\s+)?code)?$",
        r"^(?:give\s+)?advice(?:\s+on\s+(?:the\s+)?code)?$",
        r"how\s+can\s+i\s+improve\s+(?:this\s+)?code",
        r"what\s+features\s+can\s+i\s+add",
        r"^improve\s+(?:my\s+)?code$",
    ]

    SPEAK_OUTPUT_PATTERNS = [
        r"^(?:speak|read|say)\s+(?:the\s+)?output$",
    ]

    SHOW_STRUCTURE_PATTERNS = [
        r"^(?:show|display|list)\s+(?:code\s+)?structure$",
        r"^show\s+(?:code\s+)?map$",
        r"^structure\s+panel$",
    ]

    # -----------------------------------------------------------------------
    # Sonification patterns
    # -----------------------------------------------------------------------

    SONIFY_FUNCTION_PATTERNS = [
        r"sonify\s+(?:the\s+)?function\s+(\w+)",
    ]

    SONIFY_CLASS_PATTERNS = [
        r"sonify\s+(?:the\s+)?class\s+(\w+)",
    ]

    SONIFY_BLOCK_PATTERNS = [
        r"^sonify\s+(?:block|current\s+block)$",
        r"^sonify$",
        r"^(?:audio|hear|play|sound\s+out)\s+(?:code\s+)?structure$",
    ]

    # -----------------------------------------------------------------------
    # Error patterns
    # -----------------------------------------------------------------------

    ERROR_PATTERNS = [
        r"(?:find|locate|go\s+to|jump\s+to)\s+(?:the\s+)?error",
        r"where\s+is\s+(?:the\s+)?error",
        r"next\s+error",
    ]

    DESCRIBE_LINE_PATTERNS = [
        r"describe\s+(?:the\s+)?line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"describe\s+(?:the\s+)?line\s+(\w+)",
        r"what\s+is\s+on\s+line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"what\s+is\s+on\s+line\s+(\w+)",
    ]

    CLEAR_EDITOR_PATTERNS = [
        r"^(?:clear|reset)\s+(?:editor|code|file|the\s+editor)$",
    ]

    DELETE_LINE_PATTERNS = [
        r"delete\s+(?:the\s+)?line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"delete\s+(?:the\s+)?line\s+(\w+)",
    ]

    SUMMARIZE_PATTERNS = [
        r"^summarize(?:\s+(?:this\s+)?(?:file|code))?$",
        r"^summary\s+of\s+(?:this\s+)?(?:file|code)$",
    ]

    GENERATE_CODE_PATTERNS = [
        r"(?:generate|write|create|make)\s+(?:python\s+)?code\s+for\s+(.+)",
        r"i\s+want\s+(?:python\s+)?code\s+(?:for|to)\s+(.+)",
        r"(?:generate|write|create|make)\s+(?:python\s+)?code$",
    ]

    RENAME_SNIPPET_PATTERNS = [
        r"rename\s+snippet\s+([a-z0-9\-]+)\s+to\s+(.+)",
    ]

    SAVE_SNIPPET_NAMED_PATTERNS = [
        r"save\s+(?:snippet|code)\s+(?:as\s+|named?\s+)(.+)",
        r"save\s+(?:this\s+)?(?:as\s+|named?\s+)(.+)",
    ]

    # -----------------------------------------------------------------------
    # Voice code editing patterns
    # -----------------------------------------------------------------------

    INSERT_FUNCTION_PATTERNS = [
        r"insert\s+(?:a\s+)?function\s+(?:called\s+|named\s+)?(\w+)",
        r"add\s+(?:a\s+)?function\s+(?:called\s+|named\s+)?(\w+)",
        r"create\s+(?:a\s+)?function\s+(?:called\s+|named\s+)?(\w+)",
    ]

    INSERT_CLASS_PATTERNS = [
        r"insert\s+(?:a\s+)?class\s+(?:called\s+|named\s+)?(\w+)",
        r"add\s+(?:a\s+)?class\s+(?:called\s+|named\s+)?(\w+)",
        r"create\s+(?:a\s+)?class\s+(?:called\s+|named\s+)?(\w+)",
    ]

    INSERT_LINE_PATTERNS = [
        r"insert\s+(?:line\s+)?[\"']?(.+?)[\"']?\s+(?:at|on)\s+line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"add\s+(?:line\s+)?[\"']?(.+?)[\"']?\s+(?:at|on)\s+line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"insert\s+[\"'](.+?)[\"']\s+after\s+line\s+([\w\s]+?)(?:\s*$)",
    ]

    REPLACE_LINE_PATTERNS = [
        r"replace\s+line\s+([\w\s]+?)\s+with\s+[\"']?(.+?)[\"']?$",
        r"change\s+line\s+([\w\s]+?)\s+to\s+[\"']?(.+?)[\"']?$",
        r"set\s+line\s+([\w\s]+?)\s+to\s+[\"']?(.+?)[\"']?$",
    ]

    ADD_PARAMETER_PATTERNS = [
        r"add\s+(?:a\s+)?parameter\s+(?:called\s+|named\s+)?(\w+)(?:\s+to\s+(?:function\s+)?(\w+))?",
        r"add\s+(?:a\s+)?param\s+(?:called\s+|named\s+)?(\w+)(?:\s+to\s+(?:function\s+)?(\w+))?",
    ]

    INSERT_LOOP_PATTERNS = [
        r"insert\s+(?:a\s+)?(?:for\s+)?loop\s+(?:over\s+|for\s+)?(\w+)(?:\s+in\s+(\w+))?",
        r"add\s+(?:a\s+)?(?:for\s+)?loop\s+(?:over\s+|for\s+)?(\w+)(?:\s+in\s+(\w+))?",
        r"insert\s+(?:a\s+)?for\s+loop",
        r"add\s+(?:a\s+)?for\s+loop",
    ]

    INSERT_IF_PATTERNS = [
        r"insert\s+(?:an?\s+)?if\s+(?:statement\s+)?(?:for\s+|checking\s+)?(.+)",
        r"add\s+(?:an?\s+)?if\s+(?:statement\s+)?(?:for\s+|checking\s+)?(.+)",
    ]

    APPEND_LINE_PATTERNS = [
        r"append\s+[\"']?(.+?)[\"']?$",
        r"add\s+(?:a\s+)?(?:new\s+)?line\s+[\"']?(.+?)[\"']?$",
        r"write\s+[\"']?(.+?)[\"']?$",
        r"type\s+[\"']?(.+?)[\"']?$",
    ]

    # -----------------------------------------------------------------------
    # Semantic autocomplete patterns
    # -----------------------------------------------------------------------

    SUGGEST_NEXT_PATTERNS = [
        r"^suggest\s+(?:next\s+)?line$",
        r"^what\s+(?:comes|goes)\s+next$",
        r"^next\s+suggestion$",
        r"^complete\s+(?:this\s+)?line$",
        r"^what\s+should\s+i\s+(?:write|type)\s+next$",
    ]

    CHOOSE_SUGGESTION_PATTERNS = [
        r"^choose\s+(?:option\s+)?(\w+)$",
        r"^(?:select|pick|use)\s+(?:option\s+)?(\w+)$",
        r"^(?:option\s+)?(\w+)$",
    ]

    NEXT_STEP_PATTERNS = [
        r"^(?:next|forward)\s+step$",
        r"^step\s+(?:forward|next)$",
    ]

    PREVIOUS_STEP_PATTERNS = [
        r"^(?:previous|back|prev)\s+step$",
        r"^step\s+(?:back|backward|previous)$",
    ]

    WHAT_CHANGED_PATTERNS = [
        r"^what\s+changed(?:\s+here)?$",
        r"^(?:state\s+change|show\s+change)$",
    ]

    READ_OUTPUT_PATTERNS = [
        r"^(?:speak|read|say)\s+(?:the\s+)?output$",
    ]

    REPEAT_PATTERNS = [
        r"^repeat(?:\s+that)?$",
        r"^again$",
        r"^say\s+that\s+again$",
        r"^repeat\s+last$",
    ]

    HELP_PATTERNS = [
        r"^help$",
        r"^show\s+help$",
        r"^what\s+can\s+(?:i\s+)?(?:do|say)$",
        r"^list\s+commands$",
    ]

    # -----------------------------------------------------------------------
    # Execution story mode
    # -----------------------------------------------------------------------

    STORY_MODE_PATTERNS = [
        r"^(?:tell|narrate|explain|describe)\s+(?:the\s+)?(?:execution\s+)?story$",
        r"^(?:story|narrate)\s+(?:this\s+)?(?:execution|run|code)$",
        r"^what\s+(?:happened|did\s+the\s+code\s+do)\s+(?:when\s+it\s+ran)?$",
        r"^execution\s+story$",
        r"^(?:कहानी|narrate\s+करो|explain\s+करो)\s*(?:execution)?$",
    ]

    # -----------------------------------------------------------------------
    # Audio breakpoint debugger
    # -----------------------------------------------------------------------

    SET_BREAKPOINT_PATTERNS = [
        r"set\s+(?:a\s+)?breakpoint\s+(?:at\s+)?(?:line\s+)?([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"(?:pause|stop)\s+(?:at\s+)?(?:line\s+)?([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"break\s+(?:at\s+)?(?:line\s+)?([\w\s]+?)(?:\s*$|\s+(?:please|now))",
    ]

    CLEAR_BREAKPOINT_PATTERNS = [
        r"^(?:clear|remove|delete)\s+(?:all\s+)?breakpoints?$",
        r"^(?:breakpoints?\s+)?(?:clear|remove|delete)\s+(?:all\s+)?breakpoints?$",
    ]

    WATCH_VARIABLE_PATTERNS = [
        r"watch\s+(?:variable\s+)?(\w+)",
        r"monitor\s+(?:variable\s+)?(\w+)",
        r"keep\s+(?:an\s+)?eye\s+on\s+(?:variable\s+)?(\w+)",
    ]

    DEBUG_CONTINUE_PATTERNS = [
        r"^continue(?:\s+(?:debugging|execution))?$",
        r"^(?:resume|proceed)(?:\s+(?:debugging|execution))?$",
        r"^जारी\s+रखो$",
    ]

    DEBUG_STEP_IN_PATTERNS = [
        r"^step\s+in(?:to)?$",
        r"^(?:go\s+)?inside\s+(?:the\s+)?function$",
    ]

    DEBUG_STEP_OUT_PATTERNS = [
        r"^step\s+out(?:\s+of)?(?:\s+(?:the\s+)?function)?$",
        r"^(?:exit|leave)\s+(?:the\s+)?function$",
    ]

    # -----------------------------------------------------------------------
    # Learning / mentor mode
    # -----------------------------------------------------------------------

    MENTOR_MODE_PATTERNS = [
        r"^(?:start\s+)?(?:learning|mentor|tutor)\s+mode$",
        r"^(?:teach|tutor)\s+me$",
        r"^(?:मुझे\s+सिखाओ|learning\s+mode\s+शुरू\s+करो)$",
    ]

    QUIZ_ME_PATTERNS = [
        r"^quiz\s+me(?:\s+on\s+(.+))?$",
        r"^(?:test|challenge)\s+me(?:\s+on\s+(.+))?$",
        r"^give\s+me\s+(?:a\s+)?(?:quiz|challenge|test)(?:\s+on\s+(.+))?$",
        r"^(?:मुझे\s+quiz\s+करो|test\s+करो)(?:\s+(.+))?$",
    ]

    EXPLAIN_CONCEPT_PATTERNS = [
        r"explain\s+(.+?)\s+(?:like\s+i['\u2019]?m\s+new|for\s+(?:a\s+)?beginner|simply|in\s+simple\s+(?:terms|words))",
        r"what\s+(?:is|are)\s+(.+?)\s+in\s+simple\s+(?:terms|words)",
        r"(?:मुझे\s+)?(.+?)\s+(?:समझाओ|explain\s+करो)\s*(?:simply|simply\s+में)?",
    ]

    BUG_CHALLENGE_PATTERNS = [
        r"^(?:give\s+me\s+)?(?:a\s+)?bug\s+(?:fixing\s+)?challenge$",
        r"^(?:debug\s+)?challenge(?:\s+me)?$",
        r"^(?:एक\s+)?bug\s+(?:challenge|ढूंढो)$",
    ]

    # -----------------------------------------------------------------------
    # Intent map — order defines precedence (most specific first)
    # -----------------------------------------------------------------------

    def __init__(self) -> None:
        self.intent_map = self._build_intent_map()

    def _build_intent_map(self) -> Dict[str, List[str]]:
        """Return ordered intent → patterns mapping. More specific intents first."""
        return {
            # Line-level operations (specific verbs before generic navigation)
            "read_line":      self.READ_LINE_PATTERNS,
            "describe_line":  self.DESCRIBE_LINE_PATTERNS,
            "delete_line":    self.DELETE_LINE_PATTERNS,
            # General line navigation — tighter patterns, no bare \bline\b catch-all
            "goto_line":      self.GOTO_LINE_PATTERNS,
            # Function / class navigation
            "read_function":  self.READ_FUNCTION_PATTERNS,
            "find_function":  self.FIND_FUNCTION_PATTERNS,
            "sonify_function": self.SONIFY_FUNCTION_PATTERNS,
            "find_class":     self.FIND_CLASS_PATTERNS,
            "sonify_class":   self.SONIFY_CLASS_PATTERNS,
            # Execution and analysis
            "run":            self.RUN_PATTERNS,
            "analyze":        self.ANALYZE_PATTERNS,
            "fix":            self.FIX_PATTERNS,
            "advise":         self.ADVISE_PATTERNS,
            "summarize":      self.SUMMARIZE_PATTERNS,
            "generate_code":  self.GENERATE_CODE_PATTERNS,
            "rename_snippet":      self.RENAME_SNIPPET_PATTERNS,
            "save_snippet_named":  self.SAVE_SNIPPET_NAMED_PATTERNS,
            # Voice code editing
            "insert_function":     self.INSERT_FUNCTION_PATTERNS,
            "insert_class":        self.INSERT_CLASS_PATTERNS,
            "insert_line":         self.INSERT_LINE_PATTERNS,
            "replace_line":        self.REPLACE_LINE_PATTERNS,
            "add_parameter":       self.ADD_PARAMETER_PATTERNS,
            "insert_loop":         self.INSERT_LOOP_PATTERNS,
            "insert_if":           self.INSERT_IF_PATTERNS,
            "append_line":         self.APPEND_LINE_PATTERNS,
            # repeat MUST come before choose_suggestion — ^(\w+)$ matches "repeat" too
            "repeat":              self.REPEAT_PATTERNS,
            "help":                self.HELP_PATTERNS,
            # Semantic autocomplete
            "suggest_next":        self.SUGGEST_NEXT_PATTERNS,
            "choose_suggestion":   self.CHOOSE_SUGGESTION_PATTERNS,
            "clear_editor":   self.CLEAR_EDITOR_PATTERNS,
            "read_output":    self.READ_OUTPUT_PATTERNS,
            # Structure and playback
            "show_structure": self.SHOW_STRUCTURE_PATTERNS,
            "sonify_block":   self.SONIFY_BLOCK_PATTERNS,
            "locate_error":   self.ERROR_PATTERNS,
            "next_step":      self.NEXT_STEP_PATTERNS,
            "previous_step":  self.PREVIOUS_STEP_PATTERNS,
            "what_changed":   self.WHAT_CHANGED_PATTERNS,
            # Story mode
            "story_mode":          self.STORY_MODE_PATTERNS,
            # Breakpoint debugger
            "set_breakpoint":      self.SET_BREAKPOINT_PATTERNS,
            "clear_breakpoints":   self.CLEAR_BREAKPOINT_PATTERNS,
            "watch_variable":      self.WATCH_VARIABLE_PATTERNS,
            "debug_continue":      self.DEBUG_CONTINUE_PATTERNS,
            "debug_step_in":       self.DEBUG_STEP_IN_PATTERNS,
            "debug_step_out":      self.DEBUG_STEP_OUT_PATTERNS,
            # Mentor mode
            "mentor_mode":         self.MENTOR_MODE_PATTERNS,
            "quiz_me":             self.QUIZ_ME_PATTERNS,
            "explain_concept":     self.EXPLAIN_CONCEPT_PATTERNS,
            "bug_challenge":       self.BUG_CHALLENGE_PATTERNS,
        }

    # -----------------------------------------------------------------------
    # Number parsing
    # -----------------------------------------------------------------------

    def _word_to_number(self, word: str) -> Optional[int]:

        word = word.lower().strip()

        # Digit string: "5", "42", "100"
        try:
            return int(word)
        except ValueError:
            pass

        # Single word: "five", "twenty", "nineteen"
        if word in WORD_TO_NUMBER:
            return WORD_TO_NUMBER[word]

        # Two-word compound: "twenty five", "forty two"
        parts = word.split()
        if len(parts) == 2:
            tens_val = _TENS.get(parts[0])
            ones_val = _ONES.get(parts[1])
            if tens_val is not None and ones_val is not None and ones_val < 10:
                return tens_val + ones_val

        return None

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def parse(self, text: str) -> Dict:
        text = text.strip().lower()

        if not text:
            return {"intent": None, "slots": {}, "confidence": 0.0, "original": text}

        for intent, patterns in self.intent_map.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if not match:
                    continue

                slots = self._extract_slots(intent, match, text)

                # Slot-presence guards: if a required slot couldn't be parsed,
                # skip this match and continue trying other patterns.
                if intent in ("goto_line", "read_line") and "line_number" not in slots:
                    continue
                if intent in ("read_function", "find_function", "sonify_function") \
                        and "function_name" not in slots:
                    continue
                if intent in ("find_class", "sonify_class") \
                        and "class_name" not in slots:
                    continue
                if intent == "describe_line" and "line_number" not in slots:
                    continue
                if intent == "delete_line" and "line_number" not in slots:
                    continue
                if intent == "generate_code":
                    if "prompt" in slots and not slots["prompt"]:
                        del slots["prompt"]
                if intent == "save_snippet_named" and "name" not in slots:
                    continue
                if intent == "replace_line" and ("line_number" not in slots or "text" not in slots):
                    continue
                if intent == "insert_line" and ("line_number" not in slots or "text" not in slots):
                    continue
                if intent == "append_line" and "text" not in slots:
                    continue
                if intent == "insert_function" and "function_name" not in slots:
                    continue
                if intent == "insert_class" and "class_name" not in slots:
                    continue
                if intent == "set_breakpoint" and "line_number" not in slots:
                    continue
                if intent == "explain_concept" and "concept" not in slots:
                    continue

                return {
                    "intent": intent,
                    "slots": slots,
                    "confidence": 0.95,
                    "original": text,
                }

        return {
            "intent": None,
            "slots": {},
            "confidence": 0.0,
            "original": text,
        }

    def _extract_slots(self, intent: str, match: re.Match, text: str) -> Dict:
        """Extract and validate slots from a regex match."""
        slots: Dict = {}

        if intent in ("goto_line", "read_line", "describe_line", "delete_line"):
            if match.groups():
                raw = match.group(1).strip()
                num = self._word_to_number(raw)
                if num is not None:
                    slots["line_number"] = num

        elif intent in ("read_function", "find_function", "sonify_function"):
            if match.groups():
                slots["function_name"] = match.group(1).strip()

        elif intent in ("find_class", "sonify_class"):
            if match.groups():
                slots["class_name"] = match.group(1).strip()

        elif intent == "generate_code":
            if match.groups() and match.group(1):
                prompt = match.group(1).strip()
                if prompt:
                    slots["prompt"] = prompt


        elif intent == "rename_snippet":
            if match.groups() and len(match.groups()) >= 2:
                slots["id"] = match.group(1).strip()
                slots["new_name"] = match.group(2).strip()

        elif intent == "save_snippet_named":
            if match.groups() and match.group(1):
                slots["name"] = match.group(1).strip()

        elif intent == "insert_function":
            if match.groups() and match.group(1):
                slots["function_name"] = match.group(1).strip()

        elif intent == "insert_class":
            if match.groups() and match.group(1):
                slots["class_name"] = match.group(1).strip()

        elif intent == "insert_loop":
            if match.groups():
                slots["loop_var"]    = match.group(1).strip() if match.group(1) else "i"
                slots["iterable"]    = match.group(2).strip() if len(match.groups()) >= 2 and match.group(2) else "range(10)"

        elif intent == "insert_if":
            if match.groups() and match.group(1):
                slots["condition"] = match.group(1).strip()

        elif intent == "append_line":
            if match.groups() and match.group(1):
                slots["text"] = match.group(1).strip()

        elif intent == "replace_line":
            if match.groups() and len(match.groups()) >= 2:
                raw = match.group(1).strip()
                num = self._word_to_number(raw)
                if num is not None:
                    slots["line_number"] = num
                if match.group(2):
                    slots["text"] = match.group(2).strip()

        elif intent == "insert_line":
            if match.groups() and len(match.groups()) >= 2:
                slots["text"] = match.group(1).strip()
                raw = match.group(2).strip()
                num = self._word_to_number(raw)
                if num is not None:
                    slots["line_number"] = num

        elif intent == "add_parameter":
            if match.groups() and match.group(1):
                slots["param_name"]    = match.group(1).strip()
                slots["function_name"] = match.group(2).strip() if len(match.groups()) >= 2 and match.group(2) else None

        elif intent == "choose_suggestion":
            if match.groups() and match.group(1):
                raw = match.group(1).strip()
                num = self._word_to_number(raw)
                slots["choice"] = num if num is not None else raw

        elif intent == "set_breakpoint":
            if match.groups() and match.group(1):
                raw = match.group(1).strip()
                num = self._word_to_number(raw)
                if num is not None:
                    slots["line_number"] = num

        elif intent == "watch_variable":
            if match.groups() and match.group(1):
                slots["variable"] = match.group(1).strip()

        elif intent == "quiz_me":
            if match.groups() and match.group(1):
                slots["topic"] = match.group(1).strip()

        elif intent == "explain_concept":
            if match.groups() and match.group(1):
                slots["concept"] = match.group(1).strip()

        return slots

    def get_confidence(self, text: str) -> float:
        """Return confidence score (0.0–1.0) for the best-matching intent."""
        return self.parse(text).get("confidence", 0.0)

    def disambiguate(self, text: str, candidates: List[str]) -> Optional[str]:
       
        raise NotImplementedError(
            "disambiguate() is not yet implemented. "
            "Use parse() directly and inspect the confidence score."
        )


# ---------------------------------------------------------------------------
# Thread-safe lazy singleton
# ---------------------------------------------------------------------------

_parser: Optional[IntentParser] = None
_parser_lock = threading.Lock()


def get_parser() -> IntentParser:
    """Get or create the global IntentParser instance (thread-safe)."""
    global _parser
    if _parser is None:
        with _parser_lock:
            if _parser is None:
                _parser = IntentParser()
    return _parser


def parse_intent(text: str) -> Dict:
    """Convenience function: parse a voice command string into intent + slots."""
    return get_parser().parse(text)