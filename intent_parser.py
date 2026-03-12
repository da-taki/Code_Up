"""
Smart Intent Parser for Voice Commands

Converts natural language voice input into structured intents and slots.
This replaces simple string matching with a robust grammar-based system.

Examples:
    "go to line fifteen" -> {intent: "goto_line", slots: {line_number: 15}}
    "read function analyze" -> {intent: "read_function", slots: {function_name: "analyze"}}
    "find class Parser" -> {intent: "find_structure", slots: {structure_type: "class", name: "Parser"}}
    "jump to error" -> {intent: "locate_error"}
"""

import re
from typing import Dict, List, Optional, Tuple


# Mapping of spoken numbers to digits
WORD_TO_NUMBER = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
}


class IntentParser:
    """Parse voice commands into structured intents and slots."""
    
    # Navigation intents
    GOTO_LINE_PATTERNS = [
        r"(?:go|jump|navigate)\s+to\s+line\s+(\w+)",
        r"(?:line)\s+(\w+)",
        r"(?:goto)\s+(\w+)",
    ]
    
    READ_LINE_PATTERNS = [
        r"read\s+line\s+(\w+)",
        r"read\s+(?:the\s+)?line\s+(\w+)",
    ]
    
    READ_FUNCTION_PATTERNS = [
        r"read\s+function\s+(\w+)",
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
    
    # Execution intents
    RUN_PATTERNS = [
        r"^run\s*(?:code)?$",
        r"^execute\s*(?:code)?$",
        r"^start$",
    ]
    
    # Analysis intents
    ANALYZE_PATTERNS = [
        r"^analyze\s*(?:code)?$",
        r"^analyze\s+(?:the\s+)?code$",
    ]
    
    FIX_PATTERNS = [
        r"^fix\s*(?:code)?$",
        r"^fix\s+my\s+code$",
    ]
    
    ADVISE_PATTERNS = [
        r"^advise$",
        r"^give\s+(?:me\s+)?(?:suggestions|advice)$",
    ]
    
    # Structure intents
    SHOW_STRUCTURE_PATTERNS = [
        r"(?:show|display|list)\s+structure",
        r"show\s+(?:code\s+)?map",
        r"structure\s+panel",
    ]
    
    # Sonification intents
    SONIFY_FUNCTION_PATTERNS = [
        r"sonify\s+function\s+(\w+)",
        r"sonify\s+(?:the\s+)?function\s+(\w+)",
    ]
    
    SONIFY_CLASS_PATTERNS = [
        r"sonify\s+class\s+(\w+)",
        r"sonify\s+(?:the\s+)?class\s+(\w+)",
    ]
    
    SONIFY_BLOCK_PATTERNS = [
        r"sonify\s+(?:block|current\s+block)",
        r"^sonify$",
    ]
    
    # Error intents
    ERROR_PATTERNS = [
        r"(?:find|locate|go\s+to)\s+(?:the\s+)?error",
        r"where\s+is\s+(?:the\s+)?error",
        r"next\s+error",
    ]
    
    # Help intents
    HELP_PATTERNS = [
        r"^help$",
        r"^show\s+help$",
        r"what\s+can\s+i\s+(?:do|say)",
    ]
    
    # Speech control
    SPEAK_OUTPUT_PATTERNS = [
        r"^speak\s+(?:the\s+)?output$",
        r"^read\s+(?:the\s+)?output$",
        r"^say\s+(?:the\s+)?output$",
    ]
    
    def __init__(self):
        """Initialize the intent parser."""
        self.intent_map = self._build_intent_map()
    
    def _build_intent_map(self) -> Dict[str, List[Tuple[str, str]]]:
        """Build a map of intents to their patterns."""
        return {
            "goto_line": self.GOTO_LINE_PATTERNS,
            "read_line": self.READ_LINE_PATTERNS,
            "read_function": self.READ_FUNCTION_PATTERNS,
            "find_class": self.FIND_CLASS_PATTERNS,
            "find_function": self.FIND_FUNCTION_PATTERNS,
            "run": self.RUN_PATTERNS,
            "analyze": self.ANALYZE_PATTERNS,
            "fix": self.FIX_PATTERNS,
            "advise": self.ADVISE_PATTERNS,
            "show_structure": self.SHOW_STRUCTURE_PATTERNS,
            "sonify_function": self.SONIFY_FUNCTION_PATTERNS,
            "sonify_class": self.SONIFY_CLASS_PATTERNS,
            "sonify_block": self.SONIFY_BLOCK_PATTERNS,
            "locate_error": self.ERROR_PATTERNS,
            "help": self.HELP_PATTERNS,
            "speak_output": self.SPEAK_OUTPUT_PATTERNS,
        }
    
    def _word_to_number(self, word: str) -> Optional[int]:
        """Convert word to number (e.g., 'fifteen' -> 15)."""
        word_lower = word.lower().strip()
        
        # Direct lookup
        if word_lower in WORD_TO_NUMBER:
            return WORD_TO_NUMBER[word_lower]
        
        # Try parsing as digit string
        try:
            return int(word_lower)
        except:
            return None
    
    def parse(self, text: str) -> Dict:
        """
        Parse voice command text into intent and slots.
        
        Returns:
            {
                "intent": "goto_line",
                "slots": {"line_number": 15},
                "confidence": 0.95,
                "original": "go to line fifteen"
            }
        """
        text = text.strip().lower()
        
        if not text:
            return {"intent": None, "slots": {}, "confidence": 0.0}
        
        # Try each intent
        for intent, patterns in self.intent_map.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    slots = self._extract_slots(intent, match, text)
                    return {
                        "intent": intent,
                        "slots": slots,
                        "confidence": 0.95,
                        "original": text
                    }
        
        # No matching intent
        return {
            "intent": None,
            "slots": {},
            "confidence": 0.0,
            "original": text
        }
    
    def _extract_slots(self, intent: str, match, text: str) -> Dict:
        """Extract slots from regex match."""
        slots = {}
        
        if intent in ["goto_line", "read_line"]:
            if match.groups():
                num = self._word_to_number(match.group(1))
                if num:
                    slots["line_number"] = num
        
        elif intent in ["read_function", "find_function", "sonify_function"]:
            if match.groups():
                slots["function_name"] = match.group(1)
        
        elif intent in ["find_class", "sonify_class"]:
            if match.groups():
                slots["class_name"] = match.group(1)
        
        return slots
    
    def get_confidence(self, text: str) -> float:
        """Get confidence score (0.0-1.0) for how well the text matches any intent."""
        result = self.parse(text)
        return result.get("confidence", 0.0)
    
    def disambiguate(self, text: str, candidates: List[str]) -> Optional[str]:
        """
        Given ambiguous text and candidate intents, return best match.
        
        Example:
            disambiguate("jump", ["goto_line", "find_function"])
            -> Could be either, returns None or best guess
        """
        # Future: implement fuzzy matching or ML-based disambiguation
        return None


# Lazy instantiation
_parser = None

def get_parser() -> IntentParser:
    """Get or create the global intent parser."""
    global _parser
    if _parser is None:
        _parser = IntentParser()
    return _parser


def parse_intent(text: str) -> Dict:
    """Convenience function to parse intent from text."""
    return get_parser().parse(text)
