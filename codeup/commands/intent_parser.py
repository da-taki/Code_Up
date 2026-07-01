
import re
import threading
from typing import Dict, List, Optional



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

_HINDI_NUMBERS: Dict[str, int] = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20,
    "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24, "पच्चीस": 25,
    "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "उनतीस": 29, "तीस": 30,
    "इकतीस": 31, "बत्तीस": 32, "तैंतीस": 33, "चौंतीस": 34, "पैंतीस": 35,
    "छत्तीस": 36, "सैंतीस": 37, "अड़तीस": 38, "उनतालीस": 39, "चालीस": 40,
    "इकतालीस": 41, "बयालीस": 42, "तैंतालीस": 43, "चौवालीस": 44, "पैंतालीस": 45,
    "छियालीस": 46, "सैंतालीस": 47, "अड़तालीस": 48, "उनचास": 49, "पचास": 50,
    "इक्यावन": 51, "बावन": 52, "तिरपन": 53, "चौवन": 54, "पचपन": 55,
    "छप्पन": 56, "सत्तावन": 57, "अट्ठावन": 58, "उनसठ": 59, "साठ": 60,
    "इकसठ": 61, "बासठ": 62, "तिरसठ": 63, "चौंसठ": 64, "पैंसठ": 65,
    "छियासठ": 66, "सड़सठ": 67, "अड़सठ": 68, "उनहत्तर": 69, "सत्तर": 70,
    "इकहत्तर": 71, "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74, "पचहत्तर": 75,
    "छिहत्तर": 76, "सतहत्तर": 77, "अठहत्तर": 78, "उनासी": 79, "अस्सी": 80,
    "इक्यासी": 81, "बयासी": 82, "तिरासी": 83, "चौरासी": 84, "पचासी": 85,
    "छियासी": 86, "सतासी": 87, "अट्ठासी": 88, "नवासी": 89, "नब्बे": 90,
    "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94, "पचानवे": 95,
    "छियानवे": 96, "सत्तानवे": 97, "अट्ठानवे": 98, "निन्यानवे": 99, "सौ": 100,
}

WORD_TO_NUMBER: Dict[str, int] = {
    **_ONES, **_TENS, **_HINDI_NUMBERS,
    "hundred": 100, "thousand": 1000,
}

class IntentParser:


    GOTO_LINE_PATTERNS = [
        r"(?:go|jump|navigate|move)\s+to\s+line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"(?:go|jump|navigate|move)\s+to\s+line\s+(\w+)",
        r"(?:लाइन|line)\s+(\S+)\s+(?:पर\s+)?(?:जाओ|जाइए|चलो)",
    ]

    READ_LINE_PATTERNS = [
        r"read\s+(?:the\s+)?line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"read\s+(?:the\s+)?line\s+(\w+)",
        r"(?:लाइन|line)\s+(\S+)\s+(?:को\s+)?(?:पढ़ो|पढ़िए|बोलो|सुनाओ)",
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

    GOTO_DEFINITION_PATTERNS = [
        r"^go\s+to\s+definition\s+of\s+([A-Za-z_]\w*)$",
        r"^go\s+to\s+(?:the\s+)?(?:function|class)\s+([A-Za-z_]\w*)$",
        r"^find\s+definition\s+of\s+([A-Za-z_]\w*)$",
        r"^where\s+is\s+([A-Za-z_]\w*)\s+defined$",
    ]

    FIND_REFERENCES_PATTERNS = [
        r"^where\s+is\s+([A-Za-z_]\w*)\s+used$",
        r"^find\s+uses\s+of\s+([A-Za-z_]\w*)$",
        r"^find\s+references\s+to\s+([A-Za-z_]\w*)$",
        r"^where\s+do\s+i\s+use\s+([A-Za-z_]\w*)$",
    ]

    FILE_OUTLINE_PATTERNS = [
        r"^outline\s+this\s+file$", r"^summarize\s+file\s+structure$", r"^read\s+file\s+outline$",
    ]

    SAFE_RENAME_PATTERNS = [
        r"^rename\s+(?:variable\s+)?([A-Za-z_]\w*)\s+to\s+([A-Za-z_]\w*)$",
        r"^change\s+name\s+from\s+([A-Za-z_]\w*)\s+to\s+([A-Za-z_]\w*)$",
    ]

    NAME_CONFLICT_PATTERNS = [
        r"^check\s+names$", r"^find\s+name\s+problems$", r"^check\s+for\s+shadowing$",
    ]

    CURRENT_BLOCK_PATTERNS = [
        r"^read\s+current\s+block$", r"^read\s+this\s+block$", r"^describe\s+current\s+block$",
    ]
    ADJACENT_SYMBOL_PATTERNS = [
        r"^(?:go\s+to\s+)?(next|previous)\s+(function|class)$",
    ]
    NEXT_ERROR_PATTERNS = [
        r"^go\s+to\s+next\s+error$", r"^jump\s+to\s+error$", r"^where\s+is\s+the\s+error$",
    ]
    CHECK_BRACKETS_PATTERNS = [
        r"^check\s+brackets$", r"^check\s+parentheses$", r"^are\s+my\s+brackets\s+balanced$",
    ]
    CHECK_STRINGS_PATTERNS = [
        r"^check\s+strings$", r"^check\s+quotes$", r"^are\s+my\s+strings\s+closed$",
    ]
    CHECK_LONG_LINES_PATTERNS = [
        r"^check\s+long\s+lines$", r"^find\s+long\s+lines$", r"^readability\s+check$",
    ]
    COMMENT_LINE_PATTERNS = [r"^comment\s+(?:this|current)\s+line$"]
    UNCOMMENT_LINE_PATTERNS = [r"^uncomment\s+(?:this|current)\s+line$"]
    DUPLICATE_LINE_PATTERNS = [
        r"^duplicate\s+(?:this|current)\s+line$", r"^copy\s+this\s+line\s+below$",
    ]
    DELETE_BLANK_LINES_PATTERNS = [
        r"^delete\s+blank\s+lines$", r"^remove\s+blank\s+lines$", r"^clean\s+blank\s+lines$",
    ]
    EXPECTED_OUTPUT_PATTERNS = [
        r"^expect(?:ed)?\s+output\s+(.+)$", r"^compare\s+output\s+to\s+(.+)$",
        r"^should\s+print\s+(.+)$",
    ]
    RUN_HISTORY_PATTERNS = [
        r"^show\s+run\s+history$", r"^what\s+have\s+i\s+run$", r"^run\s+summary$",
    ]
    RESET_RUN_STATE_PATTERNS = [
        r"^reset\s+run\s+state$", r"^clear\s+last\s+output$", r"^clear\s+run\s+history$",
    ]
    CODE_STATS_PATTERNS = [
        r"^show\s+code\s+stats$", r"^code\s+statistics$", r"^summarize\s+code\s+numbers$",
    ]
    CODE_NESTING_PATTERNS = [
        r"^show\s+nesting\s+depth$", r"^how\s+nested\s+is\s+this\s+code$", r"^check\s+nesting$",
    ]
    SHOW_TODOS_PATTERNS = [
        r"^show\s+todos$", r"^list\s+todos$", r"^find\s+todo\s+comments$",
    ]
    SHOW_REQUIREMENTS_PATTERNS = [
        r"^show\s+requirements$", r"^list\s+requirements$",
        r"^what\s+packages\s+does\s+this\s+project\s+need$",
    ]
    MISSING_PROJECT_FILES_PATTERNS = [
        r"^check\s+missing\s+files$", r"^check\s+project\s+imports$", r"^find\s+missing\s+files$",
    ]
    CSV_PREVIEW_PATTERNS = [
        r"^preview\s+csv(?:\s+file)?(?:\s+([A-Za-z0-9_./-]+\.csv))?$",
        r"^read\s+csv\s+preview(?:\s+([A-Za-z0-9_./-]+\.csv))?$",
    ]
    ACCESSIBLE_LEARNING_PATTERNS = [
        r"^(?:start|continue|reset) (?:learning|python) path$",
        r"^(?:next|previous|repeat|skip) lesson$", r"^where am i in the learning path$",
        r"^(?:list lessons|check lesson|give lesson hint|show lesson goal)$",
        r"^(?:start (?:block|parsons) practice(?: \d+)?|read block order|read block \d+|move block \d+ (?:up|down)|(?:indent|outdent) block \d+|check block order|convert blocks to code|reset block practice|exit block practice)$",
        r"^(?:show|practice) keyboard shortcuts$", r"^(?:enter|exit) navigation mode$",
        r"^navigation mode (?:on|off)$", r"^what navigation mode am i in$",
        r"^(?:next|previous) (?:symbol|loop|error|todo)$", r"^read current scope$",
        r"^(?:give me (?:a small|a bigger|the next) hint|repeat hint|hide hints|why is this hint useful|show solution steps|stop hints)$",
        r"^(?:summarize|describe) csv$", r"^list csv columns$", r"^read csv row \d+$",
        r"^(?:find )?(?:highest|lowest)(?: value)? in [\w -]+$", r"^average(?: of)? [\w -]+$",
        r"^compare columns [\w -]+ and [\w -]+$", r"^(?:describe chart|make chart accessible|read chart as text)$",
        r"^(?:sonify data|sonify column [\w -]+|stop sonification)$",
        r"^teacher mode (?:on|off)$", r"^(?:generate (?:lesson|student|mistakes) report|show common mistakes|export teacher report|reset teacher report)$",
        r"^(?:include|exclude) code (?:in|from) teacher report$",
        r"^(?:check beginner style|check readable names|check function length|check too much nesting|check confusing names|explain style issues|show more style issues)$",
        r"^(?:start error practice|practice (?:indentation|name|type|syntax) errors|read error challenge|check error fix|give error hint|show error solution|next error challenge|exit error practice)$",
        r"^(?:open|show) accessible coding tools$", r"^explain quorum$",
        r"^how is codeup different from quorum$", r"^explain vs code handoff$",
        r"^show accessible coding pathway$",
    ]
    # Canonical command ownership (one owner per phrase; aliases route to that
    # owner). When phrases overlap, the earlier entry in _build_intent_map wins,
    # so precedence is encoded by registration order there:
    #   tutor_mode           -> hints-before-fixes        (start tutor mode, give me a hint, show fix)
    #   codex_handoff        -> bridge to coding agents   (make codex handoff)
    #   understanding_check  -> session/code questions    (check my understanding, quiz me on this code)
    #   programming_literacy -> structured lessons        (start literacy mode, list lessons, check lesson understanding)
    #   accessible_learning  -> older learning-path/block-practice surface
    # programming_literacy is registered ahead of accessible_learning, so shared
    # lesson phrases ("list lessons", "next lesson") canonically belong to
    # Programming Literacy Mode. Teacher reports split by owner too: the
    # cockpit "make a teacher report" (full session) is handled in app.py, while
    # "teacher lesson report" stays inside programming_literacy.
    TUTOR_MODE_PATTERNS = [
        r"^(?:start|turn on) tutor mode$",
        r"^(?:stop|turn off) tutor mode$",
        r"^tutor mode status$",
        r"^(?:hint only|give me a hint|explain first|let me try again|show fix|fix with teaching)$",
    ]
    CODEX_HANDOFF_PATTERNS = [
        r"^(?:make|create|prepare) codex handoff$",
        r"^make handoff pack$",
        r"^copy handoff for codex$",
    ]
    UNDERSTANDING_CHECK_PATTERNS = [
        r"^(?:check my understanding|quiz me on this code|ask me a question)$",
        r"^what mistake did i make$",
        r"^(?:give me a similar exercise|make practice question)$",
        r"^grade my attempt$",
    ]
    PROGRAMMING_LITERACY_PATTERNS = [
        r"^(?:start programming literacy mode|start literacy mode)$",
        r"^(?:start lesson|start first lesson|start lesson [a-z0-9 ]+|start [a-z ]+ lesson)$",
        r"^(?:list lessons|show lessons)$",
        r"^(?:next lesson|previous lesson|lesson status)$",
        r"^(?:what am i learning|what should i do next|give me lesson starter code)$",
        r"^(?:practice the mistake|check lesson understanding|complete lesson)$",
        r"^(?:teacher lesson report|graduation report)$",
        r"^am i ready for (?:codex|vs code|vscode)$",
    ]
    AUDIO_BLOCKS_PATTERNS = [
        r"^(?:enter|open|switch to) block mode$", r"^(?:exit block mode|switch to code mode|switch to python mode|open python mode)$",
        r"^(?:open|enter|switch to) audio blocks$", r"^start audio blocks mode$",
        r"^what mode am i in$", r"^(?:list block categories|what blocks can i add|help with blocks)$",
        r"^list (?:import|output|variable|math|condition|loop|list|function|exception|input|comment) blocks$",
        r"^(?:read block workspace|read block order|read block \d+|read selected block|select block (?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)|first block|last block|where am i in blocks|summarize blocks|read nested blocks|read children of block \d+)$",
        r"^(?:move block \d+ (?:up|down|before block \d+|after block \d+)|(?:indent|outdent|delete) block \d+|put block \d+ inside (?:else of )?block \d+|remove block \d+ from loop|undo|redo|undo block change|redo block change|clear block workspace|edit selected block|delete selected block|duplicate selected block|move selected block (?:up|down))$",
        r"^(?:add .+(?:block|import|function)|add print text .+|add print variable \w+|append .+ to \w+|set variable \w+ to .+|edit block \d+|set block \d+ (?:text|variable|condition) to .+|set (?:selected block )?(?:message|variable name|variable value|import library|import alias|condition|loop variable|range start|range stop|function name|parameter|return value) to .+|rename block variable \w+ to \w+|clear block \d+ value)$",
        r"^(?:compile blocks|compile blocks to python|convert blocks to code|send blocks to editor|transfer blocks to python mode|copy blocks to python mode|preview generated code|run blocks|explain generated code|compare blocks and code)$",
        r"^(?:convert code to blocks|import code into blocks|explain why code cannot become blocks)$",
        r"^(?:start block lesson|next block lesson|check block lesson|give block lesson hint|show block lesson solution|exit block lesson)$",
        r"^(?:export block project|download block project|export blocks and python)$",
    ]
    IMPORT_POLICY_PATTERNS = [
        r"^explain\s+blocked\s+import$", r"^why\s+is\s+([A-Za-z_]\w*)\s+blocked$",
        r"^show\s+safe\s+imports$", r"^what\s+imports\s+are\s+allowed$",
    ]

    RUN_PATTERNS = [
        r"^run\s*(?:code|program|it|this|that)?$",
        r"^execute\s*(?:code|program|it|this|that)?$",
        r"^start\s*(?:code|program|it|this|that)?$",
        r"^launch\s*(?:code|program|it|this|that)?$",
        r"^play\s*(?:code|program|it|this|that)?$",
        r"^go\s*(?:now)?$",
        r"^(?:code|program)\s+run\s+karo$",
        r"^program\s+chalao$",
        r"^let'?s?\s+(?:run|go|try)\s*(?:it|this|that)?\.?$",
        r"^try\s+(?:it|this|that)\.?$",
        r"^do\s+it\.?$",
        r"^make\s+it\s+(?:go|run)\.?$",
        r"^see\s+(?:what|if)\s+(?:happens|it\s+does|it\s+works)\.?$",
        r"^(?:कोड\s+)?(?:चलाओ|चलाइए|चलाइये)$",
        r"^रन\s*(?:करो|कीजिए|कीजिये)?$",
        r"^शुरू\s+करो$",
        r"^देखो\s+क्या\s+होता\s+है$",
    ]


    ANALYZE_DEEP_PATTERNS = [
        r"^analyze\s+deeper$",
        r"^(?:go\s+)?deeper$",
        r"^more\s+detail(?:s)?$",
        r"^line\s+by\s+line$",
        r"^explain\s+(?:in\s+)?more\s+detail$",
        r"^गहराई\s+से\s+(?:analyze|समझाओ)$",
        r"^और\s+detail$",
    ]

    ANALYZE_PATTERNS = [
        r"^analyze\s*(?:(?:the\s+)?code)?$",
        r"^analyse\s*(?:(?:the\s+)?code)?$",
        r"^(?:check|review|explain)\s+(?:the\s+)?code$",
        r"^(?:कोड\s+)?(?:का\s+)?विश्लेषण\s*(?:करो|कीजिए)?$",
        r"^कोड\s+(?:को\s+)?(?:समझाओ|समझाइए|जांचो)$",
    ]

    FIX_PATTERNS = [
        r"^fix\s*(?:(?:my\s+|this\s+|the\s+)?(?:code|it|bug|error))?$",
        r"^auto\s*fix$",
        r"^repair\s+(?:my\s+|this\s+|the\s+)?code$",
        r"^correct\s+(?:my\s+|this\s+|the\s+)?code$",
        r"^debug\s+(?:my\s+|this\s+|the\s+)?code$",
        r"^make\s+it\s+work$",
        r"^what'?s?\s+wrong$",
        r"^why\s+(?:doesn'?t|isn'?t)\s+(?:it|this)\s+work(?:ing)?$",
        r"^(?:कोड|गलती|error|bug)\s+(?:को\s+)?ठीक\s*(?:करो|कीजिए|कीजिये)?$",
        r"^सही\s+करो$",
        r"^क्या\s+गलत\s+है$",
        r"^काम\s+क्यों\s+नहीं\s+कर\s+रहा$",
    ]

    ADVISE_PATTERNS = [
        r"^advise(?:\s+on\s+(?:the\s+)?code)?$",
        r"^(?:give\s+)?advice(?:\s+on\s+(?:the\s+)?code)?$",
        r"how\s+can\s+i\s+improve\s+(?:this\s+)?code",
        r"what\s+features\s+can\s+i\s+add",
        r"^improve\s+(?:my\s+)?code$",
    ]

    SHOW_STRUCTURE_PATTERNS = [
        r"^(?:show|display|list)\s+(?:code\s+)?structure$",
        r"^show\s+(?:code\s+)?map$",
        r"^structure\s+panel$",
    ]


    SONIFY_FUNCTION_PATTERNS = [
        r"sonify\s+(?:the\s+)?function\s+(\w+)",
    ]

    SONIFY_CLASS_PATTERNS = [
        r"sonify\s+(?:the\s+)?class\s+(\w+)",
    ]

    SONIFY_BLOCK_PATTERNS = [
        r"^sonify(?:\s+(?:block|current\s+block|this|this\s+block|code))?$",
        r"^block\s+sonify\s+karo$",
        r"^is\s+block\s+ka\s+audio\s+structure\s+sunao$",
        r"^(?:audio|hear|play|sound\s+out)\s+(?:code\s+)?(?:structure|block)?$",
        r"^play\s+(?:the\s+)?code$",
        r"^make\s+(?:it\s+)?sing$",
        r"^read\s+(?:current\s+)?line$",
        r"^read\s+this\s+line$",
        r"^what\s+(?:does\s+)?this\s+line\s+(?:say|do)$",
    ]


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

    # Deterministic accessibility commands that act on the current cursor line.
    EXPLAIN_CURRENT_LINE_PATTERNS = [
        r"^explain\s+(?:this|the\s+current|current)\s+line$",
        r"^what\s+does\s+(?:this|the\s+current|current)\s+line\s+do$",
    ]

    READ_AROUND_PATTERNS = [
        r"^read\s+around\s+(?:me|here|the\s+cursor)$",
        r"^read\s+nearby\s+lines$",
        r"^read\s+surrounding\s+code$",
        r"^read\s+the\s+lines\s+around\s+me$",
        r"^give\s+me\s+context$",
    ]

    LIST_VARIABLES_PATTERNS = [
        r"^list\s+(?:my\s+)?variables$",
        r"^what\s+variables\s+do\s+i\s+have$",
        r"^show\s+(?:my\s+)?variables$",
    ]

    READ_ERROR_SUMMARY_PATTERNS = [
        r"^read\s+(?:the\s+)?errors?\s+only$",
        r"^just\s+tell\s+me\s+the\s+error$",
        r"^summari[sz]e\s+(?:the\s+)?error$",
    ]

    INTEL_TOOLKIT_STATUS_PATTERNS = [
        r"^intel\s+toolkit\s+status$",
        r"^show\s+intel\s+toolkit\s+status$",
        r"^intel\s+status$",
        r"^show\s+intel\s+optimization\s+report$",
        r"^what\s+intel\s+tools\s+are\s+used$",
    ]

    CLEAR_EDITOR_PATTERNS = [
        r"^(?:clear|reset)\s+(?:editor|code|file|the\s+editor)$",
        r"^(?:editor|code)\s+clear\s+karo$",
        r"^code\s+hata\s+do$",
        r"^naya\s+code\s+shuru\s+karo$",
        r"^(?:एडिटर|editor|कोड|code)\s+(?:को\s+)?(?:साफ|खाली|reset)\s*(?:करो|कीजिए)?$",
        r"^(?:कोड|code)\s+(?:को\s+)?(?:हटाओ|मिटाओ)$",
    ]

    SET_COLOR_MODE_PATTERNS = [
        r"^(?:turn\s+on|enable|activate|use|set|toggle)\s+(protanopia|deuteranopia|tritanopia|high\s*contrast|red\s*blind|green\s*blind|blue\s*blind|normal|default|standard)\s*(?:mode|colors?)?$",
        r"^(?:switch\s+to|change\s+to)\s+(protanopia|deuteranopia|tritanopia|high\s*contrast|red\s*blind|green\s*blind|blue\s*blind|normal|default|standard)\s*(?:mode|colors?)?$",
        r"^(protanopia|deuteranopia|tritanopia|high\s*contrast|red\s*blind|green\s*blind|blue\s*blind)\s+mode$",
        r"^(?:turn\s+off|disable|reset)\s+(?:color\s+(?:blind\s+)?|colour\s+(?:blind\s+)?)?mode$",
    ]

    DELETE_LINE_PATTERNS = [
        r"delete\s+(?:the\s+)?line\s+([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"delete\s+(?:the\s+)?line\s+(\w+)",
    ]

    SUMMARIZE_PATTERNS = [
        r"^summarize(?:\s+(?:this\s+)?(?:file|code))?$",
        r"^summary\s+of\s+(?:this\s+)?(?:file|code)$",
        r"^(?:कोड\s+(?:का\s+)?)?सारांश\s*(?:दो|बताओ|दीजिए)?$",
    ]
    READ_CODE_PATTERNS = [
        r"^read\s+(?:me\s+)?my\s+(?:whole\s+|entire\s+|full\s+|complete\s+)?(?:code|program|script)(?:\s+back)?(?:\s+to\s+me)?(?:\s+(?:out\s+loud|aloud))?$",
        r"^read\s+(?:all\s+(?:of\s+)?)?my\s+(?:code|program|script)$",
        r"^read\s+back\s+(?:my\s+|the\s+)?(?:code|program|script)$",
        r"^read\s+(?:me\s+)?(?:my\s+|the\s+)?(?:code|program|script)\s+line\s+by\s+line$",
        r"^read\s+line\s+by\s+line$",
    ]
    NARRATE_FILE_PATTERNS = [
        r"^narrate(?:\s+(?:the\s+)?(?:file|code|whole\s+file))?$",
        r"^read\s+(?:me\s+)?(?:the\s+)?(?:file|code)$",
        r"^read\s+(?:the\s+)?(?:whole|entire|full)\s+(?:file|code)$",
        r"^read\s+(?:me\s+)?(?:the\s+)?(?:file|code)\s+(?:from\s+)?(?:start\s+to\s+(?:end|finish))?$",
        r"^(?:पूरा|पूरी)\s+(?:file|कोड|code)\s+(?:पढ़ो|सुनाओ|narrate\s*करो)$",
        r"^(?:कोड|file)\s+(?:को\s+)?(?:शुरू\s+से\s+अंत\s+तक\s+)?(?:पढ़ो|सुनाओ)$",
    ]

    WALK_THROUGH_PATTERNS = [
        r"^(?:walk|talk|take)\s+(?:me\s+)?through\s+(?:this\s+|the\s+)?(?:code|program)$",
        r"^(?:walk|take)\s+(?:me\s+)?through\s+(?:this|it)$",
        r"^explain\s+what\s+(?:this\s+|the\s+)?(?:code|program)\s+does$",
        r"^explain\s+(?:this\s+|the\s+)?(?:code|program)(?:\s+step\s+by\s+step)?$",
        r"^step\s+by\s+step\s+explanation(?:\s+of\s+(?:this\s+|the\s+)?(?:code|program))?$",
        r"^(?:is|ye|yeh)\s+(?:program|code)\s+(?:ko\s+)?samjhao$",
        r"^(?:ye|yeh|is)\s+(?:code|program)\s+kya\s+karta\s+hai(?:\s+batao)?$",
        r"^(?:code|program)\s+(?:ko\s+)?step\s+by\s+step\s+samjhao$",
    ]

    DEMO_LIST_PATTERNS = [
        r"^(?:show|list)\s+(?:demos|examples|presets)$",
        r"^what\s+demos?\s+(?:are\s+)?(?:available|there)$",
        r"^demos?$",
        r"^(?:कौन\s+से\s+|क्या\s+)?(?:demos?|examples?)\s*(?:हैं|दिखाओ)?$",
    ]

    DEMO_RUN_PATTERNS = [
        r"^(?:run|load|play|start)\s+demo\s+(.+)$",
        r"^demo\s+(.+?)\s+(?:run|load|चलाओ)$",
        r"^(?:run|load|play|start)\s+(?:the\s+)?(\w+)\s+demo$",
        r"^demo\s+(\w+)$",
    ]

    PAUSE_VOICE_PATTERNS = [
        r"^pause$",
        r"^pause\s+voice(?:\s+(?:recognition|control|input))?$",
        r"^pause\s+please$",
        r"^(?:turn|switch)\s+voice\s+off$",
        r"^voice\s+off$",
        r"^(?:stop|halt)\s+listening$",
        r"^(?:mute|silence)\s+(?:the\s+)?(?:mic|microphone)?$",
        r"^voice\s+pause$",
        r"^voice\s+band\s+karo$",
        r"^sunna\s+band\s+karo$",
        r"^go\s+(?:to\s+)?silent$",
        r"^(?:रुको\s+थोड़ा|pause\s*करो)$",
        r"^(?:आवाज़|voice)\s+(?:को\s+)?(?:रोको|बंद\s+करो|pause\s*करो)$",
        r"^सुनना\s+बंद\s+करो$",
    ]

    RESUME_VOICE_PATTERNS = [
        r"^resume$",
        r"^resume\s+voice(?:\s+(?:recognition|control|input))?$",
        r"^resume\s+please$",
        r"^(?:turn|switch)\s+voice\s+on$",
        r"^voice\s+on$",
        r"^(?:start|continue)\s+listening$",
        r"^(?:unmute|wake\s+up)$",
        r"^voice\s+resume$",
        r"^voice\s+on\s+karo$",
        r"^dobara\s+sunna\s+shuru\s+karo$",
        r"^(?:are\s+you\s+)?(?:listening)\??$",
        r"^come\s+back$",
        r"^(?:आवाज़|voice)\s+(?:को\s+)?(?:चालू\s+करो|शुरू\s+करो|resume\s*करो)$",
        r"^(?:फिर\s+से\s+)?सुनो$",
    ]

    GENERATE_CODE_PATTERNS = [
        r"^(?:please\s+)?(?:generate|write|create|make|build)\s+(?:a\s+|some\s+)?(?:python\s+)?code\s+(?:that|which|to|for)\s+(\S+(?:\s+\S+)*)",
        r"^(?:please\s+)?(?:generate|write|create|make|build)\s+(?:a\s+|an\s+|some\s+)?(?:python\s+)?(?:\w+\s+){0,2}(?:program|script|function|class|method)\s+(?:that|which|to|for)\s+(\S+(?:\s+\S+)*)",
        r"^(?:please\s+)?(?:generate|write|create|make|build)\s+(?:a\s+|some\s+)?(?:python\s+)?code\s+for\s+(\S+(?:\s+\S+)*)",
        r"^i\s+want\s+(?:python\s+)?code\s+(?:for|to|that)\s+(\S+(?:\s+\S+)*)",
        r"^(?:please\s+)?(?:generate|write|create|make|build)\s+(?:a\s+|an\s+|some\s+)?(.+\b(?:game|project|app|application|program|script|tool|tests?|pandas|numpy|multiple\s+files|multi-file|split\s+into|csv)\b.*)$",
        r"^(?:generate|write|create|make)\s+(?:python\s+)?code$",
        r"^(\S+(?:\s+\S+){1,})\s+(?:के\s+लिए|का|की)\s+(?:कोड|code)\s+(?:बनाओ|लिखो|बनाइए|बनाइये)$",
        r"^(?:कोड|code)\s+(?:बनाओ|लिखो)\s+(\S+(?:\s+\S+){2,})$",
    ]

    RENAME_SNIPPET_PATTERNS = [
        r"rename\s+snippet\s+([a-z0-9\-]+)\s+to\s+(.+)",
    ]

    SAVE_SNIPPET_AUTO_PATTERNS = [
        r"^save\s+(?:this\s+)?(?:code|program)?\s+as\s+(?:a\s+)?snippet$",
        r"^save\s+(?:this\s+)?(?:code|program)\s+as\s+(?:a\s+)?snippet$",
        r"^is\s+code\s+ko\s+snippet\s+save\s+karo$",
    ]

    SAVE_SNIPPET_NAMED_PATTERNS = [
        r"^save\s+this\s+as\s+(?:a\s+)?snippet\s+(?:called|named)\s+(.+)$",
        r"^save\s+(?:this\s+)?(?:code|program)?\s+as\s+(?:a\s+)?snippet\s+(?:called|named)\s+(.+)$",
        r"^save\s+(?:this\s+)?program\s+as\s+(.+)$",
        r"^is\s+code\s+ko\s+(.+?)\s+naam\s+se\s+snippet\s+save\s+karo$",
        r"^is\s+code\s+ko\s+(.+?)\s+naam\s+se\s+save\s+karo$",
        r"save\s+(?:snippet|code)\s+(?:as\s+|named?\s+)(.+)",
        r"save\s+(?:this\s+)?(?:as\s+|named?\s+)(.+)",
        r"(?:snippet|कोड)\s+(?:को\s+)?(.+?)\s+(?:नाम\s+से\s+)?(?:सेव|save)\s*(?:करो|कीजिए)?",
    ]

    LIST_SNIPPETS_PATTERNS = [
        r"^(?:show|list|read)\s+(?:my\s+)?snippets$",
        r"^what\s+snippets\s+(?:do\s+i\s+have|are\s+saved)$",
        r"^mere\s+snippets\s+dikhao$",
    ]

    LOAD_SNIPPET_PATTERNS = [
        r"^load\s+(?:the\s+)?snippet\s+(?:called|named)\s+(.+)$",
        r"^load\s+snippet\s+(.+)$",
        r"^(.+?)\s+wala\s+snippet\s+load\s+karo$",
        r"^(.+?)\s+snippet\s+load\s+karo$",
    ]

    PREVIEW_SNIPPET_PATTERNS = [
        r"^preview\s+snippet\s+(\w+)$",
        r"^(?:peek|read)\s+(?:at\s+)?snippet\s+(\w+)$",
        r"^snippet\s+preview\s+(\w+)$",
        r"^snippet\s+(\w+)\s+(?:झलक|preview)$",
    ]

    READ_PROJECT_FILES_PATTERNS = [
        r"^(?:read|list|show)\s+(?:the\s+)?project\s+files$",
        r"^(?:what|which)\s+files\s+(?:are\s+)?(?:in\s+)?(?:this\s+)?project\??$",
        r"^file\s+(?:map|tree|list)$",
    ]

    OPEN_PROJECT_FILE_PATTERNS = [
        r"^open\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
        r"^switch\s+to\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
        r"^open\s+(?!(?:the\s+)?tutorial$)(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+)$",
        r"^switch\s+to\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+)$",
    ]

    CREATE_PROJECT_FILE_PATTERNS = [
        r"^create\s+(?:a\s+)?(?:new\s+)?file\s+([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
        r"^add\s+(?:a\s+)?(?:new\s+)?file\s+([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
    ]

    RENAME_PROJECT_FILE_PATTERNS = [
        r"^rename\s+(?:this\s+|current\s+)?file\s+to\s+([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
        r"^rename\s+(?:file\s+)?([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))\s+to\s+([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
    ]

    DELETE_PROJECT_FILE_PATTERNS = [
        r"^delete\s+(?:this\s+|current\s+)?file$",
        r"^delete\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
    ]

    RUN_PROJECT_FILE_PATTERNS = [
        r"^run\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
        r"^execute\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+(?:\s+dot\s+[a-zA-Z0-9_]+|\.[a-zA-Z0-9_]+))$",
        r"^run\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+)$",
        r"^execute\s+(?:the\s+)?(?:file\s+)?([a-zA-Z0-9_./\-\s]+)$",
    ]

    EXPLAIN_PROJECT_STRUCTURE_PATTERNS = [
        r"^explain\s+(?:the\s+)?project\s+structure$",
        r"^describe\s+(?:the\s+)?project\s+structure$",
        r"^read\s+(?:the\s+)?project\s+structure$",
    ]

    EXPLAIN_REQUIREMENTS_PATTERNS = [
        r"^explain\s+(?:the\s+)?requirements$",
        r"^what\s+requirements\s+(?:does\s+this\s+project\s+need|are\s+needed)\??$",
    ]


    INSERT_FUNCTION_PATTERNS = [
        r"insert\s+(?:a\s+)?function\s+(?:called\s+|named\s+)?(\w+)",
        r"add\s+(?:a\s+)?function\s+(?:called\s+|named\s+)?(\w+)",
        r"create\s+(?:a\s+)?function\s+(?:called\s+|named\s+)?(\w+)",
        r"function\s+(\w+)\s+(?:जोड़ो|बनाओ|डालो)",
        r"(?:एक\s+)?function\s+(?:जोड़ो|बनाओ|डालो)\s+(?:नाम\s+)?(\w+)",
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
        r"insert\s+(?:a\s+)?(?:for\s+)?loop\s+(?:over|for)\s+(\w+)(?:\s+in\s+(\w+))?",
        r"add\s+(?:a\s+)?(?:for\s+)?loop\s+(?:over|for)\s+(\w+)(?:\s+in\s+(\w+))?",
        r"insert\s+(?:a\s+)?for\s+loop",
        r"add\s+(?:a\s+)?for\s+loop",
    ]

    INSERT_IF_PATTERNS = [
        r"insert\s+(?:an?\s+)?if\s+(?:statement\s+)?(?:for\s+|checking\s+)?(.+)",
        r"add\s+(?:an?\s+)?if\s+(?:statement\s+)?(?:for\s+|checking\s+)?(.+)",
    ]

    INSERT_WHILE_PATTERNS = [
        r"(?:insert|add)\s+(?:a\s+)?while\s+loop\s+(?:that\s+runs\s+)?"
        r"(?:while\s+|until\s+|when\s+|for\s+(?:as\s+long\s+as\s+)?|"
        r"checking\s+(?:whether\s+|that\s+)?)?(.+)",
        r"(?:insert|add)\s+(?:a\s+)?while\s+(.+)",
    ]

    INSERT_VARIABLE_PATTERNS = [
        r"(?:insert|add|create|make|declare)\s+(?:a\s+|an\s+|new\s+)*variable\s+"
        r"(?:called\s+|named\s+)?([A-Za-z_]\w*)\s+(?:and\s+)?"
        r"(?:give\s+it\s+the\s+value|giving\s+it\s+the\s+value|give\s+the\s+value|"
        r"with\s+(?:the\s+)?value|with\s+value|set\s+to|equal\s+to|equals|"
        r"holding|storing|that\s+(?:holds|stores|is|equals))\s+(.+)",
        r"(?:insert|add|create|make|declare)\s+(?:a\s+|an\s+|new\s+)*variable\s+"
        r"(?:called\s+|named\s+)?([A-Za-z_]\w*)\s*=\s*(.+)",
    ]

    APPEND_LINE_PATTERNS = [
        r"(?:insert|add)\s+(print\s+.+?)$",
        r"append\s+[\"']?(.+?)[\"']?$",
        r"add\s+(?:a\s+)?(?:new\s+)?line\s+[\"']?(.+?)[\"']?$",
        r"write\s+[\"']?(.+?)[\"']?$",
        r"type\s+[\"']?(.+?)[\"']?$",
        r"insert\s+(.+)$",
    ]


    SUGGEST_NEXT_PATTERNS = [
        r"^suggest\s+(?:next\s+)?line$",
        r"^what\s+(?:comes|goes)\s+next$",
        r"^next\s+suggestion$",
        r"^complete\s+(?:this\s+)?line$",
        r"^what\s+should\s+i\s+(?:write|type)\s+next$",
        r"^अगली\s+(?:लाइन|line)\s+(?:सुझाओ|बताओ|suggest\s*करो)$",
        r"^(?:आगे\s+)?क्या\s+लिखूं$",
    ]

    CHOOSE_SUGGESTION_PATTERNS = [
        r"^choose\s+(?:option\s+)?(\w+)$",
        r"^(?:select|pick|use)\s+(?:option\s+)?(\w+)$",
        r"^(?:option\s+)?(\w+)$",
    ]

    NEXT_STEP_PATTERNS = [
        r"^(?:next|forward)\s+step$",
        r"^step\s+(?:forward|next)$",
        r"^अगला\s+(?:कदम|step)$",
        r"^आगे\s+(?:बढ़ो|जाओ)$",
    ]

    PREVIOUS_STEP_PATTERNS = [
        r"^(?:previous|back|prev)\s+step$",
        r"^step\s+(?:back|backward|previous)$",
        r"^पिछला\s+(?:कदम|step)$",
        r"^पीछे\s+(?:जाओ|बढ़ो)$",
    ]

    WHAT_CHANGED_PATTERNS = [
        r"^what\s+changed(?:\s+here)?$",
        r"^(?:state\s+change|show\s+change)$",
    ]

    READ_OUTPUT_PATTERNS = [
        r"^(?:speak|read|say)\s+(?:the\s+)?output$",
        r"^(?:speak|read|say)\s+(?:the\s+)?(?:full|whole|entire|last|complete|all(?:\s+of)?(?:\s+the)?)\s+output$",
        r"^(?:speak|read|say)\s+(?:the\s+)?output\s+(?:again|in\s+full|out\s+loud|aloud)$",
        r"^read\s+(?:me\s+)?(?:the\s+)?output\s+again$",
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
        r"^show\s+commands$",
        r"^what\s+can\s+(?:i\s+)?(?:do|say)$",
        r"^what\s+can\s+(?:i\s+)?(?:do|say)\s+here$",
        r"^what\s+commands\s+can\s+i\s+try$",
        r"^how\s+do\s+i\s+use\s+this$",
        r"^what\s+should\s+i\s+say$",
        r"^guide\s+me$",
        r"^(?:i'?m\s+)?(?:lost|stuck|confused)$",
        r"^how\s+(?:do\s+i|does\s+this)\s+work$",
        r"^what\s+now$",
        r"^(?:i\s+)?(?:don'?t|do\s+not)\s+know\s+what\s+to\s+(?:do|say)$",
        r"^(?:tell|show)\s+me\s+(?:the\s+)?commands$",
        r"^(?:मदद|सहायता|help)\s*(?:चाहिए|करो|दो|कीजिए)?$",
        r"^क्या\s+कर\s+सकते\s+हो$",
        r"^मैं\s+(?:क्या|कैसे)\s+करूं$",
        r"^समझ\s+नहीं\s+आ\s+रहा$",
        r"^madad$",
        r"^madat$",
        r"^madad\s+karo$",
        r"^help\s+me$",
    ]

    MORE_HELP_PATTERNS = [
        r"^more\s+help$",
        r"^full\s+help$",
        r"^all\s+commands$",
        r"^list\s+commands$",
        r"^पूरी\s+(?:मदद|help)$",
        r"^सभी\s+(?:कमांड|commands)$",
        r"^कमांड\s+(?:की\s+)?सूची$",
    ]


    STORY_MODE_PATTERNS = [
        r"^(?:tell|narrate|explain|describe)\s+(?:the\s+)?(?:execution\s+)?story$",
        r"^(?:story|narrate)\s+(?:this\s+)?(?:execution|run|code)$",
        r"^what\s+(?:happened|did\s+the\s+code\s+do)\s+(?:when\s+it\s+ran)?$",
        r"^execution\s+story$",
        r"^(?:कहानी|narrate\s+करो|explain\s+करो)\s*(?:execution)?$",
    ]


    SET_BREAKPOINT_PATTERNS = [
        r"set\s+(?:a\s+)?breakpoint\s+(?:at\s+)?(?:line\s+)?([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"(?:pause|stop)\s+(?:at\s+)?(?:line\s+)?([\w\s]+?)(?:\s*$|\s+(?:please|now))",
        r"break\s+(?:at\s+)?(?:line\s+)?([\w\s]+?)(?:\s*$|\s+(?:please|now))",
    ]

    SET_AUDIO_BREAKPOINT_PATTERNS = [
        r"^(?:pause|stop|break)(?:\s+execution)?\s+when\s+(.+)$",
        r"^set\s+(?:a\s+)?conditional\s+(?:audio\s+)?breakpoint\s+when\s+(.+)$",
    ]

    LIST_AUDIO_BREAKPOINT_PATTERNS = [
        r"^(?:list|show|read)\s+(?:conditional\s+)?(?:audio\s+)?breakpoints?$",
    ]

    WHY_AUDIO_BREAKPOINT_PATTERNS = [
        r"^why\s+did\s+(?:it|execution|the\s+program)\s+pause$",
        r"^why\s+am\s+i\s+paused$",
        r"^explain\s+(?:the\s+)?(?:current\s+)?pause$",
    ]

    CLEAR_BREAKPOINT_PATTERNS = [
        r"^(?:clear|remove|delete)\s+(?:all\s+)?breakpoints?$",
        r"^(?:breakpoints?\s+)?(?:clear|remove|delete)\s+(?:all\s+)?breakpoints?$",
    ]

    REMOVE_BREAKPOINT_PATTERNS = [
        r"^remove\s+(?:the\s+)?breakpoint\s+(?:on\s+)?line\s+([\w\s]+)$",
    ]
    DISABLE_BREAKPOINTS_PATTERNS = [r"^disable\s+breakpoints$"]
    ENABLE_BREAKPOINTS_PATTERNS = [r"^enable\s+breakpoints$"]
    REPEAT_STEP_PATTERNS = [r"^repeat\s+step$"]
    FIRST_STEP_PATTERNS = [r"^go\s+to\s+(?:the\s+)?first\s+step$"]
    LAST_STEP_PATTERNS = [r"^go\s+to\s+(?:the\s+)?last\s+step$"]

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

    CONCEPT_QUESTION_PATTERNS = [
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*what(?:'?s| is| are)\s+(?:a|an|the|this|that)?\s*[\w\s\-\"']*?\b(?:loop|print|range|colon|indent\w*|variable|function|string|list|dictionary|integer|float|boolean|for\s+line|while\s+line|space|line\s+\w+)\b",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*what\s+does\s+.+?\b(?:mean|do|doing|represent|stand\s+for)\b",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*why\s+(?:is|are)\s+(?:there\s+)?.+",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*(?:can|could)\s+you\s+explain\s+.+",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*explain\s+(?:line|this|that|the|why|how)\b.+",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*what\s+happens\s+if\s+.+",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*what(?:'?s| is)\s+wrong\s+with\s+.+",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*show\s+me\s+(?:a|an|some)?\s*\w*\s*example\b(?!.*\bin\s+(?:the|my)\s+(?:editor|code)\b)",
        r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*how\s+(?:do|does|can)\s+.+\bwork\b",
        r"\b(?:kya\s+karta\s+hai|ka\s+matlab|kyun\s+hai|kya\s+hota\s+hai|kya\s+represent\s+karta\s+hai)\b",
    ]

    BUG_CHALLENGE_PATTERNS = [
        r"^(?:give\s+me\s+)?(?:a\s+)?bug\s+(?:fixing\s+)?challenge$",
        r"^(?:debug\s+)?challenge(?:\s+me)?$",
        r"^(?:एक\s+)?bug\s+(?:challenge|ढूंढो)$",
    ]

    READ_OUTLINE_PATTERNS = [
        r"^(?:read|speak|say)\s+(?:the\s+)?(?:outline|structure)$",
        r"^(?:explain|describe)\s+(?:the\s+)?(?:code\s+)?structure$",
        r"^(?:file|code)\s+outline$",
        r"^(?:à¤°à¥‚à¤ªà¤°à¥‡à¤–à¤¾|outline)\s+(?:à¤ªà¤¢à¤¼à¥‹|à¤¬à¥‹à¤²à¥‹)$",
    ]

    SONIFY_FILE_PATTERNS = [
        r"^sonify\s+(?:the\s+)?(?:whole\s+)?(?:file|code)$",
        r"^(?:play|hear)\s+(?:the\s+)?(?:whole\s+)?(?:file|code)\s+shape$",
        r"^sonify\s+(?:the\s+)?indent\s+profile$",
    ]

    EXPLAIN_DIFF_PATTERNS = [
        r"^why\s+(?:is|was)\s+(?:the\s+)?output\s+different$",
        r"^why\s+did\s+this\s+run\s+differently$",
        r"^explain\s+(?:the\s+)?output\s+diff(?:erence)?$",
    ]

    PRODUCT_POSITIONING_PATTERNS = [
        r"^why\s+not\s+use\s+vs\s+code\??$",
        r"^how\s+is\s+codeup\s+different\s+from\s+vs\s+code\??$",
        r"^is\s+codeup\s+better\s+than\s+vs\s+code\??$",
        r"^how\s+is\s+codeup\s+different\s+from\s+codex\??$",
        r"^how\s+is\s+codeup\s+different\s+from\s+copilot\??$",
    ]

    PROFESSIONAL_TRANSITION_PATTERNS = [
        r"^what\s+happens\s+after\s+codeup\??$",
        r"^how\s+do\s+students\s+move\s+to\s+vs\s+code\??$",
        r"^when\s+do\s+students\s+use\s+professional\s+ides\??$",
    ]

    MENTOR_CHAT_PATTERNS = [
        r"^(?:ask\s+mentor|codeup\s+mentor|mentor\s+(?!mode$))\s+(.+)$",
        r"^why\s+did\s+(?:this|my\s+code|it)\s+fail\??$",
        r"^what\s+should\s+i\s+do\s+next\??$",
        r"^check\s+my\s+code$",
        r"^what\s+does\s+(.+?)\s+do\s+here\??$",
        r"^explain\s+line\s+([\w\s]+?)\s+again$",
    ]

    MENTOR_HINT_PATTERNS = [
        r"^(?:give\s+me\s+)?(?:a\s+)?tiny\s+hint$",
        r"^don'?t\s+give\s+me\s+the\s+answer$",
        r"^(?:give\s+me\s+)?(?:a\s+)?bigger\s+hint$",
        r"^(?:show\s+me\s+)?(?:the\s+)?exact\s+fix$",
        r"^exact\s+fix$",
    ]

    MENTOR_WALKTHROUGH_PATTERNS = [
        r"^slow\s+walkthrough$",
        r"^walk\s+me\s+through\s+(?:this\s+)?slowly$",
        r"^explain\s+like\s+i\s+am\s+new$",
        r"^explain\s+like\s+i'?m\s+new$",
    ]

    MENTOR_PROGRESS_PATTERNS = [
        r"^did\s+i\s+fix\s+it\??$",
        r"^is\s+it\s+better\s+now\??$",
        r"^what\s+changed$",
    ]

    MENTOR_TRANSFORM_PATTERNS = [
        r"^shorter$",
        r"^say\s+that\s+shorter$",
        r"^say\s+that\s+simpler$",
        r"^explain\s+simpler$",
    ]

    MENTOR_PREFERENCE_PATTERNS = [
        r"^set\s+me\s+to\s+beginner\s+mode$",
        r"^beginner\s+mode$",
        r"^intermediate\s+mode$",
        r"^don'?t\s+give\s+direct\s+answers$",
        r"^explain\s+in\s+hinglish$",
        r"^use\s+shorter\s+answers$",
    ]

    MENTOR_CODE_MAP_PATTERNS = [
        r"^(?:give\s+me\s+)?(?:a\s+)?map\s+of\s+my\s+code$",
        r"^summarize\s+my\s+code\s+structure$",
    ]


    CODE_MAP_PATTERNS = [
        r"^(?:give\s+me\s+)?(?:a\s+)?code\s+map$",
        r"^code\s+map$",
        r"^code\s+map\s+batao$",
        r"^code\s+ka\s+structure\s+samjhao$",
        r"^read\s+(?:the\s+)?structure$",
        r"^what\s+is\s+(?:the\s+)?structure$",
        r"^(?:show|describe)\s+(?:the\s+)?(?:code\s+)?structure$",
        r"^(?:audio\s+)?code\s+map$",
        r"^map\s+(?:the\s+|my\s+|this\s+)?code$",
        r"^show\s+(?:me\s+)?(?:the\s+)?code\s+map$",
        r"^give\s+me\s+(?:a\s+)?map\s+of\s+(?:the|this)\s+code$",
        r"^(?:explain|show|describe)\s+(?:me\s+)?(?:the\s+)?structure\s+of\s+(?:my|this|the)\s+(?:code|program)$",
        r"^mere\s+code\s+ka\s+(?:map|structure)\s+(?:batao|dikhao|samjhao)$",
    ]

    INSIDE_LOOP_PATTERNS = [
        r"^what\s+is\s+inside\s+(?:the\s+)?loop$",
        r"^what'?s?\s+inside\s+(?:the\s+)?loop$",
        r"^inside\s+(?:the\s+)?loop$",
    ]

    AFTER_LOOP_PATTERNS = [
        r"^what\s+comes?\s+after\s+(?:the\s+)?loop$",
        r"^what'?s?\s+after\s+(?:the\s+)?loop$",
        r"^after\s+(?:the\s+)?loop$",
    ]

    NESTING_DEPTH_PATTERNS = [
        r"^how\s+(?:deeply\s+)?nested\s+am\s+i$",
        r"^(?:what\s+is\s+)?(?:the\s+)?(?:deepest\s+)?nesting\s+(?:depth|level)$",
        r"^how\s+deep\s+(?:is\s+(?:the\s+)?(?:nesting|code))$",
    ]

    LIST_FUNCTIONS_PATTERNS = [
        r"^list\s+(?:my\s+)?functions$",
        r"^what\s+functions\s+(?:do\s+i\s+have|are\s+(?:there|defined|here))$",
        r"^show\s+(?:my\s+)?functions$",
        r"^read\s+(?:my\s+|the\s+)?functions$",
    ]

    PREFLIGHT_CHECK_PATTERNS = [
        r"^check\s+my\s+code\s+before\s+running$", r"^preflight\s+check$", r"^will\s+this\s+run$",
    ]
    CHECK_INDENTATION_PATTERNS = [
        r"^explain\s+indentation$", r"^check\s+indentation$", r"^where\s+is\s+indentation\s+wrong$",
    ]
    LIST_IMPORTS_PATTERNS = [
        r"^list\s+imports$", r"^what\s+imports\s+am\s+i\s+using$", r"^check\s+imports$",
        r"^read\s+(?:my\s+|the\s+)?imports$",
    ]
    SANDBOX_CHECK_PATTERNS = [
        r"^find\s+risky\s+code$", r"^check\s+for\s+unsafe\s+code$", r"^sandbox\s+check$",
    ]
    REPEAT_LAST_OUTPUT_PATTERNS = [
        r"^read\s+last\s+output$", r"^repeat\s+output$", r"^what\s+did\s+it\s+print$",
    ]
    REPEAT_LAST_ERROR_PATTERNS = [
        r"^read\s+last\s+error$", r"^repeat\s+error$", r"^what\s+was\s+the\s+error$",
    ]
    PROJECT_HEALTH_PATTERNS = [
        r"^check\s+project$", r"^project\s+health\s+check$", r"^is\s+my\s+project\s+ready$",
    ]
    PROJECT_FILE_TREE_PATTERNS = [
        r"^read\s+file\s+tree$", r"^summarize\s+project\s+files$",
        r"^what\s+files\s+are\s+in\s+this\s+project$",
    ]
    EXPLAIN_ERROR_PATTERNS = [
        r"^explain\s+(?:the\s+)?error$",
        r"^trace\s+(?:the\s+)?error$",
        r"^explain\s+(?:the\s+)?traceback$",
        r"^narrate\s+(?:the\s+)?error$",
    ]
    CRASH_LOCATION_PATTERNS = [
        r"^where\s+did\s+it\s+crash$",
        r"^where\s+(?:did\s+)?(?:the\s+|my\s+)?(?:program|code)\s+crash$",
        r"^where\s+is\s+the\s+crash$",
    ]
    ERROR_CAUSE_PATTERNS = [
        r"^what\s+caused\s+(?:this|the\s+error|the\s+crash|it)$",
        r"^why\s+did\s+it\s+crash$",
    ]
    ERROR_VALUE_PATTERNS = [
        r"^what\s+value\s+caused\s+(?:this|the\s+error|it|the\s+crash)$",
        r"^which\s+value\s+caused\s+(?:this|the\s+error)$",
    ]
    READ_FULL_TRACEBACK_PATTERNS = [
        r"^read\s+(?:the\s+)?full\s+traceback$",
        r"^show\s+(?:me\s+)?(?:the\s+)?full\s+traceback$",
        r"^read\s+(?:the\s+)?(?:whole|entire)\s+traceback$",
    ]
    TEST_NEXT_PATTERNS = [
        r"^what\s+should\s+i\s+test\s+next$",
        r"^what\s+do\s+i\s+test\s+next$",
        r"^what\s+next\s+to\s+test$",
    ]
    FIX_WITH_EXPLANATION_PATTERNS = [
        r"^fix\s+with\s+explanation$",
        r"^explain\s+and\s+fix$",
        r"^propose\s+a\s+fix$",
    ]
    DIFF_REVIEW_PATTERNS = [
        r"^what\s+changed$", r"^review\s+(?:the\s+)?changes$", r"^audio\s+diff$",
        r"^read\s+the\s+diff$", r"^read\s+(?:the\s+)?changes$", r"^review\s+the\s+diff$",
        r"^show\s+(?:me\s+)?the\s+diff$",
    ]
    DIFF_BEFORE_AFTER_PATTERNS = [
        r"^read\s+before\s+and\s+after$", r"^before\s+and\s+after$",
        r"^read\s+(?:the\s+)?(?:old|previous)\s+version$",
        r"^read\s+(?:the\s+)?new\s+version$",
    ]
    DIFF_EXPLAIN_PATTERNS = [
        r"^explain\s+this\s+change$", r"^explain\s+the\s+change$",
        r"^what\s+does\s+this\s+change\s+mean$",
    ]
    DIFF_RISK_PATTERNS = [
        r"^is\s+this\s+risky$", r"^is\s+this\s+change\s+risky$",
        r"^what\s+is\s+risky\s+about\s+this\s+change$",
        r"^how\s+risky\s+is\s+(?:this|this\s+change)$",
    ]
    DIFF_NEXT_PATTERNS = [r"^next\s+change$"]
    DIFF_PREV_PATTERNS = [r"^previous\s+change$", r"^prev\s+change$"]
    DIFF_ACCEPT_PATTERNS = [
        r"^accept\s+this\s+change$", r"^accept\s+the\s+change$", r"^keep\s+this\s+change$",
    ]
    DIFF_ACCEPT_ALL_PATTERNS = [r"^accept\s+all\s+changes$", r"^accept\s+all$"]
    DIFF_REJECT_ALL_PATTERNS = [r"^reject\s+all\s+changes$", r"^reject\s+all$"]
    UNDO_CHANGE_PATTERNS = [
        r"^undo\s+last\s+change$", r"^undo\s+(?:the\s+)?last\s+(?:change|edit)$",
        r"^undo\s+my\s+last\s+(?:change|edit)$", r"^undo\s+this\s+change$",
        r"^undo\s+(?:the\s+)?change$", r"^reject\s+this\s+change$",
    ]
    CHANGE_APPLY_PATTERNS = [
        r"^apply\s+this\s+change$", r"^apply\s+the\s+(?:change|fix)$", r"^apply\s+all$",
    ]
    PROGRAM_STATE_PATTERNS = [
        r"^show\s+(?:me\s+)?(?:the\s+)?program\s+state$",
        r"^show\s+(?:me\s+)?(?:the\s+)?state$",
        r"^what\s+is\s+the\s+program\s+state$",
    ]
    SUMMARIZE_VARIABLES_PATTERNS = [
        r"^summari[sz]e\s+(?:my\s+|the\s+)?variables$",
        r"^what\s+variables\s+exist$",
        r"^what\s+variables\s+are\s+there$",
    ]
    VARIABLE_NOW_PATTERNS = [
        r"^what\s+is\s+(\w+)\s+now$",
        r"^what'?s\s+(\w+)\s+now$",
        r"^what\s+is\s+(?:the\s+value\s+of\s+)(\w+)$",
        r"^what\s+is\s+(\w+)\s+right\s+now$",
    ]
    READ_WATCHED_PATTERNS = [
        r"^read\s+watched\s+variables$",
        r"^read\s+(?:my\s+)?watched\s+variables$",
        r"^what\s+are\s+(?:my\s+)?watched\s+variables$",
    ]
    STEP_THROUGH_PATTERNS = [
        r"^step\s+through\s+(?:this|the\s+code|my\s+code|it)$",
        r"^start\s+stepping$", r"^step\s+through$", r"^trace\s+(?:this|the\s+code)$",
    ]
    # "next step" / "previous step" reuse the existing next_step/previous_step
    # intents (context-aware in app.py), so they are not redefined here.
    EXPLAIN_STEP_PATTERNS = [r"^explain\s+(?:the\s+)?current\s+step$", r"^explain\s+this\s+step$"]
    LOOP_STATE_PATTERNS = [
        r"^explain\s+loop\s+state$", r"^loop\s+state$", r"^what\s+is\s+the\s+loop\s+doing$",
    ]
    # Note: the bare "why did it pass/fail" forms are intentionally NOT matched
    # here; "why did it fail" is an existing error follow-up. Use the explicit
    # "condition" phrasings for State Watch.
    CONDITION_PASS_PATTERNS = [
        r"^why\s+did\s+(?:this\s+|the\s+)?condition\s+pass$",
        r"^why\s+was\s+(?:the\s+)?condition\s+true$",
    ]
    CONDITION_FAIL_PATTERNS = [
        r"^why\s+did\s+(?:this\s+|the\s+)?condition\s+fail$",
        r"^why\s+was\s+(?:the\s+)?condition\s+false$",
    ]
    PROGRAM_OUTPUT_PATTERNS = [
        r"^what\s+did\s+the\s+program\s+print$", r"^what\s+was\s+printed$",
        r"^read\s+(?:the\s+)?printed\s+output$",
    ]
    PROJECT_MAP_PATTERNS = [
        r"^project\s+map$",
        r"^(?:give|show)\s+me\s+(?:a|the)\s+project\s+map$",
        r"^map\s+(?:this|the)\s+project$",
        r"^summarize\s+(?:this\s+|the\s+)?project$",
        r"^what\s+does\s+each\s+file\s+do$",
        r"^where\s+does\s+(?:the\s+|this\s+)?program\s+(?:start|begin)$",
        r"^where\s+does\s+it\s+start$",
        r"^what\s+is\s+(?:the\s+)?entry\s+point$",
        r"^what\s+imports\s+what$",
        r"^show\s+(?:me\s+)?(?:the\s+)?import\s+graph$",
        r"^what\s+functions\s+are\s+in\s+(?:this\s+|the\s+)?project$",
    ]
    LOOP_SUMMARY_PATTERNS = [
        r"^explain\s+loops$", r"^how\s+many\s+times\s+does\s+this\s+loop\s+run$", r"^check\s+loops$",
    ]

    WHERE_IN_PROGRAM_PATTERNS = [
        r"^where\s+am\s+i\s+in\s+(?:the\s+)?program$",
        r"^what\s+part\s+(?:of\s+(?:the\s+)?program\s+)?am\s+i\s+(?:in|at)$",
    ]


    WATCH_VAR_PATTERNS = [
        r"^track\s+(\w+)$",
        r"^track\s+(?:variable\s+)?(\w+)$",
    ]

    STOP_WATCHING_PATTERNS = [
        r"^stop\s+watching\s+(\w+)$",
        r"^unwatch\s+(\w+)$",
        r"^untrack\s+(\w+)$",
    ]

    CLEAR_WATCHED_PATTERNS = [
        r"^clear\s+(?:watched\s+)?variables$",
        r"^stop\s+watching\s+(?:all\s+)?variables$",
        r"^clear\s+(?:all\s+)?watches$",
    ]

    STEP_NARRATION_PATTERNS = [
        r"^run\s+with\s+(?:step\s+)?narration$",
        r"^step\s+narration$",
        r"^step\s+by\s+step\s+run\s+karke\s+samjhao$",
        r"^har\s+step\s+narrate\s+karo$",
        r"^narrate\s+(?:the\s+)?(?:execution|run|steps?)$",
        r"^run\s+and\s+narrate$",
        r"^trace\s+(?:the\s+)?(?:execution|run)$",
    ]

    READ_VARIABLE_VALUES_PATTERNS = [
        r"^read\s+variable\s+values$",
        r"^(?:what\s+are\s+)?(?:the\s+)?variable\s+values$",
        r"^show\s+(?:variable\s+)?values$",
    ]

    WHAT_CHANGED_STEP_PATTERNS = [
        r"^what\s+changed\s+in\s+this\s+step$",
        r"^what\s+changed\s+(?:at\s+)?this\s+(?:point|step)$",
    ]

    ONLY_ANNOUNCE_CHANGES_PATTERNS = [
        r"^only\s+announce\s+changes$",
        r"^announce\s+(?:only\s+)?changes$",
        r"^changes\s+only$",
    ]


    COMPARE_BEFORE_AFTER_PATTERNS = [
        r"^compare\s+before\s+and\s+after$",
        r"^before\s+(?:and|vs\.?)\s+after$",
        r"^(?:show\s+)?(?:the\s+)?(?:before\s+and\s+after|diff)$",
    ]

    REPLAY_MISTAKE_PATTERNS = [
        r"^replay\s+(?:my\s+)?mistake$",
        r"^meri\s+mistake\s+samjhao$",
        r"^maine\s+kya\s+galti\s+ki\s+thi\s+batao$",
        r"^(?:show|explain)\s+(?:my\s+)?(?:last\s+)?mistake$",
        r"^what\s+(?:was\s+)?(?:my\s+)?mistake$",
    ]

    WHY_FIXED_WORKS_PATTERNS = [
        r"^why\s+does\s+(?:the\s+)?fixed\s+version\s+work$",
        r"^why\s+(?:does\s+)?(?:the\s+)?(?:corrected|new)\s+(?:version|code)\s+work$",
        r"^why\s+(?:did\s+(?:it|the\s+fix)\s+)?work$",
    ]

    SHOW_CHANGED_LINES_PATTERNS = [
        r"^show\s+(?:only\s+)?changed\s+lines$",
        r"^(?:what|which)\s+lines?\s+(?:changed|are\s+different)$",
    ]

    MENTOR_STOP_PATTERNS = [
        r"^mentor\s+stop$",
        r"^stop\s+mentor$",
    ]

    SET_INPUTS_PATTERNS = [
        r"^set\s+inputs?\s+to\s+(.+)$",
        r"^use\s+inputs?\s+(.+)$",
        r"^inputs?\s+are\s+(.+)$",
        r"^(?:पहले\s+से\s+|preflight\s+)?inputs?\s+(.+?)\s+(?:set|डालो|दो)$",
    ]
    CLEAR_INPUTS_PATTERNS = [
        r"^clear\s+inputs?$",
        r"^remove\s+(?:all\s+)?inputs?$",
        r"^reset\s+inputs?$",
        r"^inputs?\s+(?:साफ|खाली)\s*करो$",
    ]
    LIST_INPUTS_PATTERNS = [
        r"^(?:list|show|read)\s+(?:my\s+)?inputs?$",
        r"^what\s+(?:are\s+my\s+)?inputs?$",
        r"^inputs?\s+(?:सुनाओ|बताओ)$",
    ]
    LIVE_INPUT_MODE_PATTERNS = [
        r"^(?:switch\s+to\s+)?live\s+input(?:\s+mode)?$",
        r"^interactive\s+(?:mode|input)$",
        r"^live\s+(?:run|mode)$",
        r"^(?:लाइव|interactive)\s+(?:मोड|mode)$",
    ]
    PREFLIGHT_INPUT_MODE_PATTERNS = [
        r"^(?:switch\s+to\s+)?(?:pre-?flight|preflight)\s+(?:input\s+)?mode$",
        r"^(?:pre-?flight|preflight)\s+inputs?$",
        r"^batch\s+(?:input\s+)?mode$",
    ]

    SAVE_MACRO_PATTERNS = [
        r"^remember\s+(?:this\s+)?as\s+(.+)$",
        r"^save\s+(?:this\s+)?(?:as\s+)?macro\s+(.+)$",
        r"^macro\s+save\s+(.+)$",
        r"^(?:इसे\s+)?(.+?)\s+(?:नाम\s+से\s+)?(?:याद|macro)\s*(?:रखो|करो)$",
    ]
    USE_MACRO_PATTERNS = [
        r"^use\s+macro\s+(.+)$",
        r"^(?:run|load)\s+macro\s+(.+)$",
        r"^macro\s+(.+?)\s+(?:use|run|load)$",
        r"^macro\s+(.+)$",  # last because greediest
        r"^(.+?)\s+macro\s+(?:चलाओ|use\s*करो)$",
    ]
    LIST_MACROS_PATTERNS = [
        r"^list\s+macros?$",
        r"^show\s+(?:my\s+)?macros?$",
        r"^what\s+macros?\s+(?:do\s+i\s+have|are\s+saved)$",
        r"^macros?\s+(?:की\s+सूची|बताओ)$",
    ]

    SHARE_MACRO_PATTERNS = [
        r"^share\s+(?:this|current\s+code)(?:\s+as\s+(.+))?$",
        r"^share\s+macro(?:\s+(.+))?$",
    ]
    USE_SHARED_MACRO_PATTERNS = [
        r"^use\s+shared\s+macro\s+([a-z0-9]{4})$",
        r"^load\s+shared\s+macro\s+([a-z0-9]{4})$",
    ]

    BOOKMARK_OUTPUT_PATTERNS = [
        r"^bookmark\s+(?:this|here)?(?:\s+as\s+(.+))?$",
        r"^mark\s+(?:this|here)?(?:\s+as\s+(.+))?$",
        r"^(?:यहां|यह)\s+bookmark\s*(?:करो)?(?:\s+(?:नाम\s+)?(.+))?$",
    ]
    READ_BOOKMARK_PATTERNS = [
        r"^read\s+from\s+bookmark(?:\s+(.+))?$",
        r"^(?:go\s+to|jump\s+to)\s+bookmark(?:\s+(.+))?$",
        r"^bookmark(?:\s+(.+))?\s+(?:से|से\s+पढ़ो|पढ़ो)$",
    ]
    LIST_BOOKMARKS_PATTERNS = [
        r"^list\s+bookmarks?$",
        r"^show\s+(?:my\s+)?bookmarks?$",
        r"^bookmarks?$",
        r"^bookmarks?\s+(?:बताओ|सूची)$",
    ]

    NAV_WHAT_FILE_PATTERNS = [
        r"^what\s+file\s+am\s+i\s+in$", r"^which\s+file\s+am\s+i\s+in$",
        r"^what\s+file\s+is\s+this$",
    ]
    NAV_READ_COMMENTS_PATTERNS = [
        r"^read\s+(?:the\s+|all\s+)?comments$", r"^list\s+(?:the\s+)?comments$",
    ]
    NAV_CHANGED_LINE_PATTERNS = [
        r"^jump\s+to\s+(?:the\s+)?changed\s+line$", r"^go\s+to\s+(?:the\s+)?changed\s+line$",
        r"^read\s+(?:the\s+)?changed\s+line$", r"^where\s+was\s+the\s+last\s+change$",
        r"^jump\s+to\s+(?:the\s+)?change$",
    ]
    NAV_GO_MAIN_PATTERNS = [
        r"^go\s+to\s+(?:the\s+)?main\s+function$", r"^jump\s+to\s+(?:the\s+)?main\s+function$",
        r"^find\s+(?:the\s+)?main\s+function$", r"^go\s+to\s+main$",
    ]
    NAV_OPEN_FILE_PATTERNS = [
        r"^open\s+(?:the\s+)?file\s+(?:with|that\s+has|containing)\s+(?:the\s+)?main(?:\s+function)?$",
        r"^open\s+(?:the\s+)?file\s+that\s+handles\s+(?:the\s+)?\w+$",
        r"^open\s+(?:the\s+)?file\s+with\s+\w+$",
    ]
    NAV_WHAT_FILE_DOES_PATTERNS = [
        r"^what\s+does\s+this\s+file\s+do$", r"^what\s+does\s+the\s+file\s+do$",
        r"^what\s+is\s+this\s+file\s+for$",
    ]
    WHERE_AM_I_PATTERNS = [
        r"^where\s+am\s+i(?:\s+in\s+execution)?$",
        r"^what\s+block\s+am\s+i\s+in$",
        r"^where\s+is\s+my\s+cursor$",
        r"^(?:current\s+)?(?:execution\s+)?position$",
        r"^what\s+line\s+(?:is\s+running|am\s+i\s+on)$",
        r"^(?:मैं\s+)?कहां\s+हूं$",
        r"^कौन\s+सी\s+line$",
    ]

    EXPLAIN_SIMPLY_PATTERNS = [
        r"^(?:debug|explain)\s+(?:this|the|my)\s+error$",
        r"^explain\s+(?:like\s+i'?m\s+(?:five|new|a\s+beginner)|simpler|simply|in\s+plain\s+(?:words|english))$",
        r"^(?:simpler|too\s+complicated|i\s+don'?t\s+understand)$",
        r"^(?:और\s+आसान|simple\s+में|बच्चे\s+की\s+तरह)\s*(?:समझाओ)?$",
    ]

    NARRATE_DIFF_PATTERNS = [
        r"^(?:what'?s|whats)\s+different$",
        r"^(?:narrate|tell\s+me)\s+(?:the\s+)?diff(?:erence)?$",
        r"^(?:what\s+)?changed\s+(?:in|from)\s+(?:the\s+)?(?:last\s+)?(?:run|output)$",
        r"^output\s+diff$",
        r"^(?:पिछले\s+से\s+)?क्या\s+अलग\s+है$",
    ]

    START_TUTORIAL_PATTERNS = [
        r"^(?:(?:hey|ok|okay|please|let'?s|lets|can\s+you|could\s+you|"
        r"will\s+you|i\s+(?:want|would\s+like|wanna)\s+to|i'?d\s+like\s+to)\s+){0,3}"
        r"(?:start|open|begin|launch|take\s+me\s+to)\s+(?:the\s+|a\s+)?tutorial"
        r"(?:\s+please)?$",
        r"^(?:go\s+to\s+)?(?:the\s+)?tutorial$",
        r"^tutorial\s+(?:शुरू\s+करो|खोलो)$",
    ]

    RESTART_TUTORIAL_PATTERNS = [
        r"^restart\s+tutorial$",
        r"^tutorial\s+restart$",
        r"^start\s+tutorial\s+again$",
    ]

    SKIP_TUTORIAL_PATTERNS = [
        r"^skip\s+tutorial$",
        r"^(?:close|exit|stop)\s+tutorial$",
        r"^tutorial\s+(?:बंद\s+करो|छोड़ो)$",
    ]

    TUTORIAL_NEXT_PATTERNS = [
        r"^tutorial\s+next$",
    ]

    TUTORIAL_PRACTICE_PATTERNS = [
        r"^(?:let\s+me\s+|i\s+want\s+to\s+)?practi[sc]e\s+(?:the\s+)?"
        r"(print(?:ing)?(?:\s+statements?)?|variables?|if(?:\s+statements?)?|"
        r"conditionals?|for(?:\s+loops?)?|while(?:\s+loops?)?)"
        r"(?:\s+(?:statements?|loops?))?(?:\s+(?:again|module|topic|lesson))?$",
        r"^practi[sc]e\s+(?:the\s+)?(print(?:ing)?|variables?|if|for|while)"
        r"\s+(?:module|topic|lesson)$",
    ]


    def __init__(self) -> None:
        self.intent_map = self._build_intent_map()

    def _build_intent_map(self) -> Dict[str, List[str]]:
        return {
            "audio_blocks": self.AUDIO_BLOCKS_PATTERNS,
            "tutor_mode": self.TUTOR_MODE_PATTERNS,
            "codex_handoff": self.CODEX_HANDOFF_PATTERNS,
            "understanding_check": self.UNDERSTANDING_CHECK_PATTERNS,
            "programming_literacy": self.PROGRAMMING_LITERACY_PATTERNS,
            "accessible_learning": self.ACCESSIBLE_LEARNING_PATTERNS,
            "read_line":      self.READ_LINE_PATTERNS,
            "describe_line":  self.DESCRIBE_LINE_PATTERNS,
            "explain_current_line": self.EXPLAIN_CURRENT_LINE_PATTERNS,
            "read_around_cursor":   self.READ_AROUND_PATTERNS,
            "list_variables":       self.LIST_VARIABLES_PATTERNS,
            "read_error_summary":   self.READ_ERROR_SUMMARY_PATTERNS,
            "intel_toolkit_status":  self.INTEL_TOOLKIT_STATUS_PATTERNS,
            "delete_line":    self.DELETE_LINE_PATTERNS,
            "goto_line":      self.GOTO_LINE_PATTERNS,
            "read_function":  self.READ_FUNCTION_PATTERNS,
            "goto_definition": self.GOTO_DEFINITION_PATTERNS,
            "find_references": self.FIND_REFERENCES_PATTERNS,
            "file_outline": self.FILE_OUTLINE_PATTERNS,
            "safe_rename": self.SAFE_RENAME_PATTERNS,
            "name_conflicts": self.NAME_CONFLICT_PATTERNS,
            "current_block": self.CURRENT_BLOCK_PATTERNS,
            "adjacent_symbol": self.ADJACENT_SYMBOL_PATTERNS,
            "next_error": self.NEXT_ERROR_PATTERNS,
            "check_brackets": self.CHECK_BRACKETS_PATTERNS,
            "check_strings": self.CHECK_STRINGS_PATTERNS,
            "check_long_lines": self.CHECK_LONG_LINES_PATTERNS,
            "comment_line": self.COMMENT_LINE_PATTERNS,
            "uncomment_line": self.UNCOMMENT_LINE_PATTERNS,
            "duplicate_line": self.DUPLICATE_LINE_PATTERNS,
            "delete_blank_lines": self.DELETE_BLANK_LINES_PATTERNS,
            "expected_output": self.EXPECTED_OUTPUT_PATTERNS,
            "run_history": self.RUN_HISTORY_PATTERNS,
            "reset_run_state": self.RESET_RUN_STATE_PATTERNS,
            "code_stats": self.CODE_STATS_PATTERNS,
            "code_nesting": self.CODE_NESTING_PATTERNS,
            "show_todos": self.SHOW_TODOS_PATTERNS,
            "show_requirements": self.SHOW_REQUIREMENTS_PATTERNS,
            "missing_project_files": self.MISSING_PROJECT_FILES_PATTERNS,
            "csv_preview": self.CSV_PREVIEW_PATTERNS,
            "import_policy": self.IMPORT_POLICY_PATTERNS,
            "find_function":  self.FIND_FUNCTION_PATTERNS,
            "sonify_function": self.SONIFY_FUNCTION_PATTERNS,
            "find_class":     self.FIND_CLASS_PATTERNS,
            "sonify_class":   self.SONIFY_CLASS_PATTERNS,
            "run":            self.RUN_PATTERNS,
            "mentor_stop":     self.MENTOR_STOP_PATTERNS,
            "mentor_code_map": self.MENTOR_CODE_MAP_PATTERNS,
            "code_map":        self.CODE_MAP_PATTERNS,
            "inside_loop":     self.INSIDE_LOOP_PATTERNS,
            "after_loop":      self.AFTER_LOOP_PATTERNS,
            "nesting_depth":   self.NESTING_DEPTH_PATTERNS,
            "list_functions":  self.LIST_FUNCTIONS_PATTERNS,
            "preflight_check": self.PREFLIGHT_CHECK_PATTERNS,
            "check_indentation": self.CHECK_INDENTATION_PATTERNS,
            "list_imports": self.LIST_IMPORTS_PATTERNS,
            "sandbox_check": self.SANDBOX_CHECK_PATTERNS,
            "repeat_last_output": self.REPEAT_LAST_OUTPUT_PATTERNS,
            "repeat_last_error": self.REPEAT_LAST_ERROR_PATTERNS,
            "project_health": self.PROJECT_HEALTH_PATTERNS,
            "project_file_tree": self.PROJECT_FILE_TREE_PATTERNS,
            "project_map": self.PROJECT_MAP_PATTERNS,
            "explain_error_trace": self.EXPLAIN_ERROR_PATTERNS,
            "crash_location": self.CRASH_LOCATION_PATTERNS,
            "error_cause": self.ERROR_CAUSE_PATTERNS,
            "error_value": self.ERROR_VALUE_PATTERNS,
            "read_full_traceback": self.READ_FULL_TRACEBACK_PATTERNS,
            "test_next": self.TEST_NEXT_PATTERNS,
            "fix_with_explanation": self.FIX_WITH_EXPLANATION_PATTERNS,
            "diff_review": self.DIFF_REVIEW_PATTERNS,
            "diff_before_after": self.DIFF_BEFORE_AFTER_PATTERNS,
            "diff_explain": self.DIFF_EXPLAIN_PATTERNS,
            "diff_risk": self.DIFF_RISK_PATTERNS,
            "diff_next": self.DIFF_NEXT_PATTERNS,
            "diff_prev": self.DIFF_PREV_PATTERNS,
            "accept_change": self.DIFF_ACCEPT_PATTERNS,
            "accept_all_changes": self.DIFF_ACCEPT_ALL_PATTERNS,
            "reject_all_changes": self.DIFF_REJECT_ALL_PATTERNS,
            "undo_last_change": self.UNDO_CHANGE_PATTERNS,
            "change_apply": self.CHANGE_APPLY_PATTERNS,
            "program_state": self.PROGRAM_STATE_PATTERNS,
            "summarize_variables": self.SUMMARIZE_VARIABLES_PATTERNS,
            "variable_now": self.VARIABLE_NOW_PATTERNS,
            "read_watched": self.READ_WATCHED_PATTERNS,
            "step_through": self.STEP_THROUGH_PATTERNS,
            "explain_step": self.EXPLAIN_STEP_PATTERNS,
            "loop_state": self.LOOP_STATE_PATTERNS,
            "condition_pass": self.CONDITION_PASS_PATTERNS,
            "condition_fail": self.CONDITION_FAIL_PATTERNS,
            "program_output": self.PROGRAM_OUTPUT_PATTERNS,
            "loop_summary": self.LOOP_SUMMARY_PATTERNS,
            "where_in_program": self.WHERE_IN_PROGRAM_PATTERNS,
            "watch_var":       self.WATCH_VAR_PATTERNS,
            "stop_watching":   self.STOP_WATCHING_PATTERNS,
            "clear_watched":   self.CLEAR_WATCHED_PATTERNS,
            "step_narration":  self.STEP_NARRATION_PATTERNS,
            "read_var_values": self.READ_VARIABLE_VALUES_PATTERNS,
            "what_changed_step": self.WHAT_CHANGED_STEP_PATTERNS,
            "only_announce_changes": self.ONLY_ANNOUNCE_CHANGES_PATTERNS,
            "compare_before_after": self.COMPARE_BEFORE_AFTER_PATTERNS,
            "replay_mistake":  self.REPLAY_MISTAKE_PATTERNS,
            "why_fixed_works": self.WHY_FIXED_WORKS_PATTERNS,
            "show_changed_lines": self.SHOW_CHANGED_LINES_PATTERNS,
            "mentor_progress": self.MENTOR_PROGRESS_PATTERNS,
            "mentor_hint":     self.MENTOR_HINT_PATTERNS,
            "mentor_walkthrough": self.MENTOR_WALKTHROUGH_PATTERNS,
            "mentor_transform": self.MENTOR_TRANSFORM_PATTERNS,
            "mentor_preference": self.MENTOR_PREFERENCE_PATTERNS,
            "product_positioning": self.PRODUCT_POSITIONING_PATTERNS,
            "professional_transition": self.PROFESSIONAL_TRANSITION_PATTERNS,
            "mentor_chat":     self.MENTOR_CHAT_PATTERNS,
            "analyze_deep":   self.ANALYZE_DEEP_PATTERNS,
            "analyze":        self.ANALYZE_PATTERNS,
            "fix":            self.FIX_PATTERNS,
            "advise":         self.ADVISE_PATTERNS,
            "summarize":      self.SUMMARIZE_PATTERNS,
            "walk_through":   self.WALK_THROUGH_PATTERNS,
            "read_code":      self.READ_CODE_PATTERNS,
            "narrate_file":   self.NARRATE_FILE_PATTERNS,
            "demo_run":       self.DEMO_RUN_PATTERNS,
            "demo_list":      self.DEMO_LIST_PATTERNS,
            "pause_voice":    self.PAUSE_VOICE_PATTERNS,
            "resume_voice":   self.RESUME_VOICE_PATTERNS,
            "read_project_files": self.READ_PROJECT_FILES_PATTERNS,
            "nav_open_file":       self.NAV_OPEN_FILE_PATTERNS,
            "open_project_file": self.OPEN_PROJECT_FILE_PATTERNS,
            "create_project_file": self.CREATE_PROJECT_FILE_PATTERNS,
            "rename_project_file": self.RENAME_PROJECT_FILE_PATTERNS,
            "delete_project_file": self.DELETE_PROJECT_FILE_PATTERNS,
            "run_project_file": self.RUN_PROJECT_FILE_PATTERNS,
            "explain_project_structure": self.EXPLAIN_PROJECT_STRUCTURE_PATTERNS,
            "explain_requirements": self.EXPLAIN_REQUIREMENTS_PATTERNS,
            "generate_code":  self.GENERATE_CODE_PATTERNS,
            "rename_snippet":      self.RENAME_SNIPPET_PATTERNS,
            "save_snippet_auto":   self.SAVE_SNIPPET_AUTO_PATTERNS,
            "save_snippet_named":  self.SAVE_SNIPPET_NAMED_PATTERNS,
            "list_snippets":       self.LIST_SNIPPETS_PATTERNS,
            "load_snippet":        self.LOAD_SNIPPET_PATTERNS,
            "preview_snippet":     self.PREVIEW_SNIPPET_PATTERNS,
            "insert_function":     self.INSERT_FUNCTION_PATTERNS,
            "insert_class":        self.INSERT_CLASS_PATTERNS,
            "insert_line":         self.INSERT_LINE_PATTERNS,
            "replace_line":        self.REPLACE_LINE_PATTERNS,
            "add_parameter":       self.ADD_PARAMETER_PATTERNS,
            "insert_loop":         self.INSERT_LOOP_PATTERNS,
            "insert_if":           self.INSERT_IF_PATTERNS,
            "insert_while":        self.INSERT_WHILE_PATTERNS,
            "insert_variable":     self.INSERT_VARIABLE_PATTERNS,
            "append_line":         self.APPEND_LINE_PATTERNS,
            "tutorial_practice":   self.TUTORIAL_PRACTICE_PATTERNS,
            "restart_tutorial":    self.RESTART_TUTORIAL_PATTERNS,
            "start_tutorial":      self.START_TUTORIAL_PATTERNS,
            "skip_tutorial":       self.SKIP_TUTORIAL_PATTERNS,
            "tutorial_next":       self.TUTORIAL_NEXT_PATTERNS,
            "repeat":              self.REPEAT_PATTERNS,
            "help":                self.HELP_PATTERNS,
            "more_help":           self.MORE_HELP_PATTERNS,
            "suggest_next":        self.SUGGEST_NEXT_PATTERNS,
            "choose_suggestion":   self.CHOOSE_SUGGESTION_PATTERNS,
            "clear_editor":   self.CLEAR_EDITOR_PATTERNS,
            "set_color_mode": self.SET_COLOR_MODE_PATTERNS,
            "read_output":    self.READ_OUTPUT_PATTERNS,
            "show_structure": self.SHOW_STRUCTURE_PATTERNS,
            "read_outline":   self.READ_OUTLINE_PATTERNS,
            "sonify_block":   self.SONIFY_BLOCK_PATTERNS,
            "sonify_file":    self.SONIFY_FILE_PATTERNS,
            "locate_error":   self.ERROR_PATTERNS,
            "next_step":      self.NEXT_STEP_PATTERNS,
            "previous_step":  self.PREVIOUS_STEP_PATTERNS,
            "repeat_step": self.REPEAT_STEP_PATTERNS,
            "first_step": self.FIRST_STEP_PATTERNS,
            "last_step": self.LAST_STEP_PATTERNS,
            "what_changed":   self.WHAT_CHANGED_PATTERNS,
            "story_mode":          self.STORY_MODE_PATTERNS,
            "set_audio_breakpoint": self.SET_AUDIO_BREAKPOINT_PATTERNS,
            "list_audio_breakpoints": self.LIST_AUDIO_BREAKPOINT_PATTERNS,
            "why_audio_breakpoint": self.WHY_AUDIO_BREAKPOINT_PATTERNS,
            "set_breakpoint":      self.SET_BREAKPOINT_PATTERNS,
            "clear_breakpoints":   self.CLEAR_BREAKPOINT_PATTERNS,
            "remove_breakpoint": self.REMOVE_BREAKPOINT_PATTERNS,
            "disable_breakpoints": self.DISABLE_BREAKPOINTS_PATTERNS,
            "enable_breakpoints": self.ENABLE_BREAKPOINTS_PATTERNS,
            "watch_variable":      self.WATCH_VARIABLE_PATTERNS,
            "debug_continue":      self.DEBUG_CONTINUE_PATTERNS,
            "debug_step_in":       self.DEBUG_STEP_IN_PATTERNS,
            "debug_step_out":      self.DEBUG_STEP_OUT_PATTERNS,
            "mentor_mode":         self.MENTOR_MODE_PATTERNS,
            "quiz_me":             self.QUIZ_ME_PATTERNS,
            "explain_concept":     self.EXPLAIN_CONCEPT_PATTERNS,
            "bug_challenge":       self.BUG_CHALLENGE_PATTERNS,
            "explain_diff":        self.EXPLAIN_DIFF_PATTERNS,
            "set_inputs":          self.SET_INPUTS_PATTERNS,
            "clear_inputs":        self.CLEAR_INPUTS_PATTERNS,
            "list_inputs":         self.LIST_INPUTS_PATTERNS,
            "live_input_mode":     self.LIVE_INPUT_MODE_PATTERNS,
            "preflight_input_mode": self.PREFLIGHT_INPUT_MODE_PATTERNS,
            "save_macro":          self.SAVE_MACRO_PATTERNS,
            "use_macro":           self.USE_MACRO_PATTERNS,
            "list_macros":         self.LIST_MACROS_PATTERNS,
            "share_macro":         self.SHARE_MACRO_PATTERNS,
            "use_shared_macro":    self.USE_SHARED_MACRO_PATTERNS,
            "bookmark_output":     self.BOOKMARK_OUTPUT_PATTERNS,
            "read_bookmark":       self.READ_BOOKMARK_PATTERNS,
            "list_bookmarks":      self.LIST_BOOKMARKS_PATTERNS,
            "where_am_i":          self.WHERE_AM_I_PATTERNS,
            "nav_what_file":       self.NAV_WHAT_FILE_PATTERNS,
            "nav_read_comments":   self.NAV_READ_COMMENTS_PATTERNS,
            "nav_changed_line":    self.NAV_CHANGED_LINE_PATTERNS,
            "nav_go_main":         self.NAV_GO_MAIN_PATTERNS,
            "nav_what_file_does":  self.NAV_WHAT_FILE_DOES_PATTERNS,
            "explain_simply":      self.EXPLAIN_SIMPLY_PATTERNS,
            "narrate_diff":        self.NARRATE_DIFF_PATTERNS,
            "concept_question":    self.CONCEPT_QUESTION_PATTERNS,
        }


    def _normalize_tutorial_module(self, raw: str) -> Optional[str]:
        text = (raw or "").lower().strip()
        if text.startswith("print"):
            return "print"
        if text.startswith("variable"):
            return "variables"
        if text.startswith("if") or text.startswith("conditional"):
            return "if"
        if text.startswith("for"):
            return "for"
        if text.startswith("while"):
            return "while"
        return None

    def _word_to_number(self, word: str) -> Optional[int]:
        word = word.lower().strip()

        try:
            return int(word)
        except ValueError:
            pass

        if word in WORD_TO_NUMBER:
            return WORD_TO_NUMBER[word]

        parts = word.split()

        if len(parts) == 2:
            tens_val = _TENS.get(parts[0])
            ones_val = _ONES.get(parts[1])
            if tens_val is not None and ones_val is not None and ones_val < 10:
                return tens_val + ones_val

        if len(parts) == 3 and parts[1] == "hundred":
            hundreds = _ONES.get(parts[0])
            rest = WORD_TO_NUMBER.get(parts[2])
            if hundreds is not None and 1 <= hundreds <= 9 and rest is not None and rest < 100:
                return hundreds * 100 + rest

        if len(parts) == 4 and parts[1] == "hundred":
            hundreds = _ONES.get(parts[0])
            tens_val = _TENS.get(parts[2])
            ones_val = _ONES.get(parts[3])
            if hundreds is not None and 1 <= hundreds <= 9 and tens_val is not None and ones_val is not None and ones_val < 10:
                return hundreds * 100 + tens_val + ones_val

        if len(parts) == 2 and parts[1] == "hundred":
            hundreds = _ONES.get(parts[0])
            if hundreds is not None and 1 <= hundreds <= 9:
                return hundreds * 100

        return None


    def parse(self, text: str) -> Dict:
        text = text.strip()

        if not text:
            return {"intent": None, "slots": {}, "confidence": 0.0, "original": text}

        for intent, patterns in self.intent_map.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if not match:
                    continue

                slots = self._extract_slots(intent, match, text)

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
                if intent == "load_snippet" and "id" not in slots:
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
                if intent == "insert_while" and "condition" not in slots:
                    continue
                if intent == "insert_variable" and ("name" not in slots or "value" not in slots):
                    continue
                if intent in ("set_breakpoint", "remove_breakpoint") and "line_number" not in slots:
                    continue
                if intent == "set_audio_breakpoint" and "condition" not in slots:
                    continue
                if intent == "explain_concept" and "concept" not in slots:
                    continue
                if intent == "tutorial_practice" and "module" not in slots:
                    continue
                if intent == "set_inputs" and "values" not in slots:
                    continue
                if intent == "save_macro" and "name" not in slots:
                    continue
                if intent == "use_macro" and "name" not in slots:
                    continue
                if intent == "set_color_mode" and "mode" not in slots:
                    continue
                if intent in ("watch_var", "stop_watching") and "variable" not in slots:
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

        elif intent in ("goto_definition", "find_references"):
            if match.groups() and match.group(1):
                slots["name"] = match.group(1).strip()

        elif intent == "safe_rename":
            if len(match.groups()) >= 2 and match.group(1) and match.group(2):
                slots["old_name"] = match.group(1).strip()
                slots["new_name"] = match.group(2).strip()

        elif intent == "adjacent_symbol":
            if len(match.groups()) >= 2:
                slots["direction"] = match.group(1).strip().lower()
                slots["kind"] = match.group(2).strip().lower()

        elif intent == "expected_output":
            if match.groups() and match.group(1):
                slots["expected"] = match.group(1).strip()

        elif intent == "csv_preview":
            if match.groups() and match.group(1):
                slots["path"] = match.group(1).strip()

        elif intent == "import_policy":
            if match.groups() and match.group(1):
                slots["module"] = match.group(1).strip()

        elif intent in ("find_class", "sonify_class"):
            if match.groups():
                slots["class_name"] = match.group(1).strip()

        elif intent in ("open_project_file", "create_project_file", "run_project_file"):
            groups = [g for g in match.groups() if g]
            if groups:
                slots["path"] = groups[-1].strip()

        elif intent == "rename_project_file":
            groups = [g for g in match.groups() if g]
            if len(groups) >= 2:
                slots["old_path"] = groups[0].strip()
                slots["path"] = groups[1].strip()
            elif groups:
                slots["path"] = groups[0].strip()

        elif intent == "delete_project_file":
            groups = [g for g in match.groups() if g]
            if groups:
                slots["path"] = groups[0].strip()

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

        elif intent == "load_snippet":
            if match.groups() and match.group(1):
                slots["id"] = match.group(1).strip()

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

        elif intent == "insert_while":
            if match.groups() and match.group(1):
                slots["condition"] = match.group(1).strip()

        elif intent == "insert_variable":
            if match.groups() and len(match.groups()) >= 2 and match.group(1) and match.group(2):
                slots["name"] = match.group(1).strip()
                slots["value"] = match.group(2).strip()

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

        elif intent == "remove_breakpoint":
            if match.groups() and match.group(1):
                raw = match.group(1).strip()
                num = self._word_to_number(raw)
                if num is not None:
                    slots["line_number"] = num

        elif intent == "set_audio_breakpoint":
            if match.groups() and match.group(1):
                condition = match.group(1).strip()
                if condition:
                    slots["condition"] = condition

        elif intent == "watch_variable":
            if match.groups() and match.group(1):
                slots["variable"] = match.group(1).strip()

        elif intent in ("watch_var", "stop_watching", "variable_now"):
            if match.groups() and match.group(1):
                slots["variable"] = match.group(1).strip()

        elif intent == "tutorial_practice":
            if match.groups() and match.group(1):
                module = self._normalize_tutorial_module(match.group(1))
                if module:
                    slots["module"] = module

        elif intent == "quiz_me":
            if match.groups() and match.group(1):
                slots["topic"] = match.group(1).strip()

        elif intent == "explain_concept":
            if match.groups() and match.group(1):
                slots["concept"] = match.group(1).strip()

        elif intent == "concept_question":
            cleaned = re.sub(r"^(?:hey|ok|okay|so|um|hmm)[,\s]+", "", text, flags=re.IGNORECASE).strip()
            slots["message"] = cleaned or text.strip()

        elif intent == "mentor_chat":
            original = text.strip()
            message = original
            if match.groups() and match.group(1):
                captured = match.group(1).strip()
                if original.lower().startswith(("ask mentor ", "codeup mentor ", "mentor ")):
                    message = captured
                elif original.lower().startswith("what does "):
                    message = f"What does {captured} do here?"
                elif original.lower().startswith("explain line "):
                    message = f"Explain line {captured} again."
            slots["message"] = message
            slots["mode"] = "concept" if original.lower().startswith("what does ") else "general"

        elif intent == "mentor_hint":
            low = text.lower().strip()
            if "bigger" in low:
                slots["mode"] = "bigger_hint"
            elif "exact" in low or "fix" in low:
                slots["mode"] = "exact_fix"
            else:
                slots["mode"] = "tiny_hint"
            slots["message"] = text.strip()

        elif intent == "mentor_walkthrough":
            slots["mode"] = "slow_walkthrough"
            slots["message"] = text.strip()

        elif intent == "mentor_transform":
            low = text.lower().strip()
            if "repeat" in low or "again" in low:
                slots["mode"] = "repeat"
            elif "simpler" in low:
                slots["mode"] = "simpler"
            else:
                slots["mode"] = "shorter"
            slots["message"] = text.strip()

        elif intent == "mentor_preference":
            low = text.lower().strip()
            if "intermediate" in low:
                slots["key"] = "level"
                slots["value"] = "intermediate"
            elif "beginner" in low:
                slots["key"] = "level"
                slots["value"] = "beginner"
            elif "direct" in low:
                slots["key"] = "answerStyle"
                slots["value"] = "hints_first"
            elif "hinglish" in low:
                slots["key"] = "languageStyle"
                slots["value"] = "hinglish"
            elif "shorter" in low:
                slots["key"] = "answerStyle"
                slots["value"] = "hints_first"

        elif intent == "demo_run":
            if match.groups() and match.group(1):
                slots["preset"] = match.group(1).strip().lower()

        elif intent == "preview_snippet":
            if match.groups() and match.group(1):
                slots["snippet_id"] = match.group(1).strip()

        elif intent == "set_color_mode":
            if match.groups() and match.group(1):
                raw = match.group(1).strip().lower().replace(' ', '')
                aliases = {
                    'redblind': 'protanopia',
                    'greenblind': 'deuteranopia',
                    'blueblind': 'tritanopia',
                    'highcontrast': 'high-contrast',
                    'normal': 'default',
                    'standard': 'default',
                }
                slots["mode"] = aliases.get(raw, raw)
            elif "off" in text or "disable" in text or "reset" in text:
                slots["mode"] = "default"

        elif intent == "set_inputs":
            if match.groups() and match.group(1):
                raw = match.group(1).strip()
                parts = re.split(r'\s*(?:,|;|\band\b)\s*', raw, flags=re.IGNORECASE)
                values = [p.strip() for p in parts if p.strip()]
                if values:
                    slots["values"] = values[:50]

        elif intent == "save_macro":
            if match.groups() and match.group(1):
                name = match.group(1).strip().lower()
                name = re.sub(r'[^a-z0-9 _\u0900-\u097f-]', '', name)[:64].strip()
                if name:
                    slots["name"] = name

        elif intent == "use_macro":
            if match.groups() and match.group(1):
                name = match.group(1).strip().lower()
                name = re.sub(r'[^a-z0-9 _\u0900-\u097f-]', '', name)[:64].strip()
                if name:
                    slots["name"] = name

        elif intent == "share_macro":
            if match.groups() and match.group(1):
                name = match.group(1).strip().lower()
                name = re.sub(r'[^a-z0-9 _\u0900-\u097f-]', '', name)[:64].strip()
                if name:
                    slots["name"] = name

        elif intent == "use_shared_macro":
            if match.groups() and match.group(1):
                slots["share_code"] = match.group(1).strip().upper()

        elif intent == "bookmark_output":
            if match.groups() and len(match.groups()) >= 1 and match.group(1):
                label = match.group(1).strip().lower()[:64]
                if label:
                    slots["label"] = label

        elif intent == "read_bookmark":
            if match.groups() and len(match.groups()) >= 1 and match.group(1):
                slots["label"] = match.group(1).strip().lower()[:64]

        return slots


_parser: Optional[IntentParser] = None
_parser_lock = threading.Lock()


def get_parser() -> IntentParser:
    global _parser
    if _parser is None:
        with _parser_lock:
            if _parser is None:
                _parser = IntentParser()
    return _parser


def parse_intent(text: str) -> Dict:
    return get_parser().parse("" if text is None else str(text))
