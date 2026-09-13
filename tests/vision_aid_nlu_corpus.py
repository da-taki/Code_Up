"""Vision-Aid natural-language EVALUATION corpus (PART K / PART 8 of the
"stop NLU tuning" follow-up).

HISTORY / STATUS: this corpus was originally built alongside a custom
fuzzy/semantic intent-routing runtime (nlu_pipeline.py and friends) that
was fully reverted after empirical testing showed it: (a) only reached
~56-74% accuracy against this same corpus even after extensive tuning, and
(b) caused hundreds of regressions in the existing ~4000-test suite when
positioned ahead of the existing deterministic parser. That runtime no
longer exists anywhere in this codebase (see git history around this
comment for the revert). This corpus is kept ONLY as a non-production
EVALUATION asset - see tests/evaluate_nlu_corpus.py, which runs it through
the EXISTING, unmodified command-routing stack
(codeup.commands.intent_parser.parse_intent + app.py's own existing
fallback chain) and reports coverage in three separate modes (deterministic
parser alone, AI-enabled fallback, AI-off core-command reliability) rather
than a single blended "accuracy" score - a fuzzy classifier being folded
back into runtime purely to make this corpus score well would defeat the
entire point of the revert.

Not a pytest file itself (no test_ prefix). Each entry is
(utterance, category) where category is one of:
    "normal"  - a straightforward paraphrase a beginner might type/say
    "casual"  - slangy/teen/informal phrasing ("bro", "tbh", contractions, no punctuation)
    "typo"    - a plausible beginner typo
    "speech"  - a plausible speech-recognition mishearing
    "polite"  - a verbose, over-polite/hedging phrasing

CORPUS maps a descriptive intent-family name (not a runtime type - purely a
label for grouping/reporting) to its list of (utterance, category) tuples.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

Utterance = Tuple[str, str]


def _n(*items: str) -> List[Utterance]:
    return [(t, "normal") for t in items]


def _c(*items: str) -> List[Utterance]:
    return [(t, "casual") for t in items]


def _t(*items: str) -> List[Utterance]:
    return [(t, "typo") for t in items]


def _s(*items: str) -> List[Utterance]:
    return [(t, "speech") for t in items]


def _p(*items: str) -> List[Utterance]:
    return [(t, "polite") for t in items]


CORPUS: Dict[str, List[Utterance]] = {}

# =====================================================================
# RUN_CODE (core, target 75+)
# =====================================================================
CORPUS["RUN_CODE"] = [
    *_n(
        "run", "run it", "run this", "run my code", "run my program", "run the code",
        "run the program", "execute", "execute this", "execute it", "execute my code",
        "execute the program", "start it", "start this", "start the program",
        "try my code", "let's run it", "can you run this", "run my program please",
        "see if this works", "test this code", "test my code", "test the program",
        "play this", "launch it", "launch the program", "go ahead and run it",
        "run it now", "please run my code", "run this program", "start running",
        "go", "let's go", "let's try it", "let's see", "let's test this",
        "can you execute this", "can you try running it", "run the script",
        "execute the script", "start my program", "kick it off", "fire it off",
        "let's run this thing",
    ),
    *_c(
        "run it lol", "just run it", "run this thing", "yo run it", "run pls",
        "run this rq", "can u run it", "lemme see it run", "run it already",
        "just execute it bro",
    ),
    *_t(
        "runn code", "runn it", "exectue this", "reun my code", "rn my code",
        "run coat", "excute this", "run cod", "runm this", "ruun it",
    ),
    *_s(
        "run coat", "run cold", "wren it", "run eat", "rin it",
        "execute dis", "run mycode", "run it now please execute",
    ),
    *_p(
        "would you be able to run my code for me please",
        "could you please run this program when you get a chance",
        "if it's not too much trouble could you run my code",
        "i was wondering if you could go ahead and run this",
        "when you have a moment could you please execute my program",
    ),
]

# =====================================================================
# READ_OUTPUT (core, target 75+)
# =====================================================================
CORPUS["READ_OUTPUT"] = [
    *_n(
        "read output", "what did it print", "tell me the output", "what came out",
        "read what happened", "what did my program say", "can you read the result",
        "what was the result", "what did it give me", "say the output",
        "read the output", "what does it say", "what did the program output",
        "read what it printed", "tell me what it printed", "what's the output",
        "what's in the output", "tell me what printed", "what did the code print",
        "read me the output", "can you tell me what printed", "what did it output",
        "what does the output say", "read the result", "read what got printed",
        "what did my code print", "read out the output", "tell me what came out",
        "what showed up", "what got printed", "read the printed text",
        "can you read what it said", "read what my program said", "read the console",
        "what's on the screen", "what does the console say", "read the terminal output",
        "tell me the result", "read the program output",
    ),
    *_c(
        "what'd it print tho", "wym what did it print", "yo what did it say",
        "what did it spit out", "what came out lol", "read it to me",
        "so what did it print", "bro what did it print", "what'd it say",
        "read me what it printed",
    ),
    *_t(
        "read ouput", "wht did it print", "read the outpt", "pritn output",
        "what did it prnt", "read the oputput", "waht came out", "read otput",
        "tel me the output", "read the reslt",
    ),
    *_s(
        "reed output", "read out put", "what did it prit", "read the out put",
        "what did it brint", "tell me the out putt", "read the ouch put",
        "what did it print out loud",
    ),
    *_p(
        "would you mind reading the output to me please",
        "could you please tell me what my program printed",
        "if you don't mind could you read the result out loud",
        "i would appreciate it if you could read the output",
        "when you get a chance could you tell me what came out",
    ),
]

# =====================================================================
# EXPLAIN_ERROR (core, target 75+)
# =====================================================================
CORPUS["EXPLAIN_ERROR"] = [
    *_n(
        "explain error", "explain the error", "why did it break", "what went wrong",
        "why isn't this working", "why is my code failing", "can you tell me what happened",
        "why did this die", "what's wrong with it", "what did i mess up",
        "help me understand this error", "why am i getting this", "what does this error mean",
        "i don't understand the error", "can you explain what just happened",
        # "why did this fail" is deliberately not included here: it is one
        # of intent_parser.py's own literal MENTOR_CHAT_PATTERNS, and with
        # an error present app.py's _route_conversational_voice_action has
        # its own downstream override sending it to "explain_simply" (see
        # tests/test_security_voice.py::
        # test_why_did_this_fail_routes_to_accessible_error_explainer_when_error_present)
        # - already well-handled, just via a different, more specific
        # existing mechanism this corpus's simplified resolver harness
        # does not model.
        "why does this not work", "what's the problem",
        "what happened here", "explain what broke", "tell me what broke",
        "why is this broken", "help i'm getting an error", "what's this error mean",
        "why won't this run", "what did i do wrong", "why is it not working",
        "what's causing this error", "explain this error to me", "why is python mad at me",
        "why is it crashing", "what's this error about", "tell me why this failed",
        "help me with this error", "i got an error explain it", "why does it say error",
        "what's wrong with my code", "explain the traceback", "why is there an error",
    ),
    *_c(
        "bro why did this die", "why tf did this break", "what the heck happened",
        "ugh why is this broken", "why won't it just work", "this is so broken why",
        "why is python yelling at me", "what did i even do wrong", "why is this mad at me",
        "ok why did it break",
    ),
    *_t(
        "explane my eror", "explain the eror", "why did it braek", "whats rong with it",
        "explain teh error", "why is this borken", "explan error", "wut went wrong",
        "explain the eror message", "why is my cod failing",
    ),
    *_s(
        "explain air", "explain the airor", "why did it brake", "what went rong",
        "explain the arrow", "why is this breaking down", "what happened hear",
        "explain the arrear",
    ),
    *_p(
        "would you be so kind as to explain what this error means",
        "could you please help me understand why my code broke",
        "i was hoping you could explain what went wrong here",
        "if possible could you tell me why this isn't working",
        "would you mind explaining this error message to me please",
    ),
]

# =====================================================================
# WHERE_AM_I (core, target 75+)
# =====================================================================
CORPUS["WHERE_AM_I"] = [
    *_n(
        "where am i", "where am i in the code", "what line am i on", "where's my cursor",
        "what am i looking at", "where am i right now", "tell me where i am",
        "what part of the program am i in", "where is my cursor at", "what line is my cursor on",
        "where's the cursor", "what line is this", "where am i currently",
        "what line number am i on", "tell me my position", "where am i in my code",
        "what part am i on", "where's my position", "tell me my current line",
        "what line is the cursor on", "where in the program am i", "what am i on",
        "where is my position", "which line am i on", "tell me where the cursor is",
        "where in my code am i", "what section am i in", "where am i located",
        "tell me my location in the code", "where's my place in the code",
        "which part of the code am i in", "what am i currently editing",
        "where exactly am i", "tell me where my cursor is", "where did i leave off",
        "what line was i on", "where do i currently sit in the code",
    ),
    *_c(
        "uhh where am i", "wait where am i", "yo where am i rn", "where tf am i",
        "where am i lol", "so where am i", "where's this at", "where am i at",
        "where am i even at", "wait what line is this",
    ),
    *_t(
        "ware am i", "wear am i", "were am i", "whre am i", "what lin am i on",
        "where is my cursore", "wher am i in the code", "waht line am i on",
        "where am i rite now", "tell me were i am",
    ),
    *_s(
        # "where am i write now" deliberately excluded: it triggers a
        # pre-existing, unrelated intent_parser.py quirk
        # (APPEND_LINE_PATTERNS matching on the bare word "write" plus
        # trailing text, so this speech-recognition variant of "right"
        # resolves to a code-editing action) - a real, pre-existing
        # finding worth flagging in the final review, but out of scope to
        # fix as part of this evaluation corpus.
        "wear am i", "where am eye", "wear am eye", "what line am eye on",
        "where's my curser", "tell me where i am at",
        "where in the code am eye",
    ),
    *_p(
        "would you mind telling me where i currently am in the code",
        "could you please let me know what line i'm on",
        "i was wondering if you could tell me my current position",
        "if it's okay could you tell me where my cursor is",
        "when you have a second could you tell me where i am",
    ),
]

# =====================================================================
# WHY_INDENTED (core, target 75+)
# =====================================================================
CORPUS["WHY_INDENTED"] = [
    *_n(
        "why is this line indented", "why is this pushed in", "why are there spaces here",
        "why is this indented", "why does this line have spaces", "what block am i inside",
        "why is this nested", "why is this line inside", "why is this line inside the loop",
        "why's this inside the loop", "why is this line pushed in", "why does it have indentation",
        "why is there indentation here", "what's this indented for", "why is this tabbed in",
        "why is this line tabbed", "why is it nested like this", "explain this indentation",
        "why is the current line indented", "what's with the spaces here",
        "why is this shifted in", "why is this offset", "why does this have extra space",
        "why is this line moved in", "what's this indent for", "why is this one indented",
        "why does this need spaces", "why is this line spaced in", "explain why it's indented",
        "why is this code indented", "why is that line pushed in", "why's there a gap here",
        "why is this block indented", "what's this space for", "why is this inside a block",
        "why does this line start with spaces", "why is this so far in", "why is this line so far right",
    ),
    *_c(
        "why's this pushed in tho", "why tf is this indented", "why is this so far in lol",
        "wait why is this indented", "why's it inside the loop", "why's there a space here",
        "why is this nested tho", "yo why's this pushed in", "why's this inside",
        "so why is this indented",
    ),
    *_t(
        "why is this indentaton", "why is this indeted", "why is this indentd",
        "explain this indentaton", "why is this idented", "why's this indentd",
        "why is ths line indented", "why is teh line indented", "why is this line indentated",
        "why does this hav spaces",
    ),
    *_s(
        "why is this in dented", "why is this in dent did", "why is this pushed inn",
        "why is this line in dented", "why's this in dented", "explain this in den tation",
        "why are there space's here", "why is this line push din",
    ),
    *_p(
        "would you mind explaining why this line is indented",
        "could you please tell me why this has extra spaces",
        "i was wondering why this line is pushed in like that",
        "if you don't mind could you explain the indentation here",
        "when you get a chance could you explain why this is nested",
    ),
]

# =====================================================================
# GIVE_HINT (core, target 75+)
# =====================================================================
CORPUS["GIVE_HINT"] = [
    *_n(
        "give me a hint", "help me a little", "give me a clue", "don't give me the answer",
        "point me in the right direction", "i'm stuck", "what should i think about",
        "small hint please", "can you help without solving it", "just a hint",
        "nudge me in the right direction", "i need a hint", "give me a nudge",
        "i'm a bit stuck", "can i get a hint", "help me think about this",
        "give me a small clue", "i'm stuck on this", "point me somewhere",
        "give me a tip", "i need a nudge", "i could use a hint",
        "give me something to think about", "help me get unstuck", "i'm not sure what to do next",
        "i need a little help but not the answer", "can you nudge me", "give me a starting point",
        "help me a bit without solving it", "just point me in a direction",
        "i want a hint not the answer", "i'm kind of stuck", "give me a small nudge",
        "help me figure this out", "i need direction not the answer", "small clue please",
        "i'm lost on this one", "help without giving it away",
    ),
    *_c(
        "help i'm stuck", "yo give me a hint", "i'm so stuck lol", "bro i need a hint",
        "can u give me a hint", "just gimme a hint", "gimme a clue", "help a little pls",
        "idk what to do give me a hint", "i'm stuck fr",
    ),
    *_t(
        "give me a hnit", "give me a hitn", "im stuck", "give me a clu",
        "point me in the rite direction", "give me a smal hint", "i need a hnit",
        "cant i get a hint", "give me a nudg", "help me a litle",
    ),
    *_s(
        "give me a hint please", "give me a hin", "point me in the write direction",
        "give me a clew", "i'm stuck can you help", "give me a nudge please",
        "small hint pleas", "help me a little bit",
    ),
    *_p(
        "would you be able to give me a small hint please",
        "could you point me in the right direction without giving the answer",
        "i was hoping for just a little nudge if that's okay",
        "if it's not too much trouble could i get a hint",
        "when you have a moment could you help me a little bit",
    ),
]

# =====================================================================
# EXPLAIN_CODE (core, target 75+)
# =====================================================================
CORPUS["EXPLAIN_CODE"] = [
    *_n(
        "explain my code", "explain this code", "explain the code", "what does my code do",
        "what does this code do", "walk me through my code", "can you explain my program",
        "tell me what this program does", "check my code", "review my code",
        "what is this code doing", "help me understand my code", "explain what my code does",
        "walk through my program", "tell me how my code works", "explain my program",
        "what's happening in my code", "can you go over my code", "explain how this works",
        "tell me what my code is doing", "explain the logic in my code", "what does this program do",
        "help me understand this program", "walk me through this code", "can you break down my code",
        "explain the whole program", "tell me what this does overall", "give me a code review",
        "can you look at my code", "explain the flow of my code", "walk through what this does",
        "help me see what my code does", "explain this program to me", "tell me how this program works",
        "review this code for me", "explain what's going on in my code", "break this code down for me",
        "help me understand what i wrote",
    ),
    *_c(
        "yo explain my code", "what does this even do", "explain this pls",
        "can u explain my code", "wym what does this do", "explain this thing",
        "help me get this code", "what's this code even doing", "explain this to me bro",
        "walk me thru this",
    ),
    *_t(
        "explan my code", "explain my cod", "wat does my code do", "explain teh code",
        "explain my progam", "walk me thru my cod", "explain this progrm",
        "explan this code", "chek my code", "revie my code",
    ),
    *_s(
        "explain my coat", "explain my code please", "what does my code dew",
        "explain the flow of my coat", "walk me through my coat", "explain dis code",
        "check my coat", "review my coat",
    ),
    *_p(
        "would you mind explaining what my code does please",
        "could you please walk me through my program",
        "i was hoping you could explain my code to me",
        "if you have a moment could you review my code",
        "when convenient could you explain how this program works",
    ),
]

# =====================================================================
# Other routed canonical intents (target 55+ each: 20/10/10/10/5)
# =====================================================================
CORPUS["READ_LAST_OUTPUT"] = [
    *_n(
        "say the output again", "read that again", "read it again", "read the output again",
        "say that again", "repeat the output", "one more time", "play the output again",
        "read it once more", "say it again please", "can you repeat the output",
        "read the result again", "say the result again", "one more time please",
        "read what it said again", "repeat what it printed", "read the output once more",
        "tell me the output again", "say what it printed again", "read that output again",
    ),
    *_c("say it again bro", "one more time pls", "read it again lol", "again please",
        "say that again tho", "read it once more pls", "repeat that pls", "again pls",
        "read it again bro", "one more time tho"),
    *_t("read it agian", "say that agian", "repeat the outut", "read it agin",
        "say it agian please", "reapeat the output", "read the outptu again",
        "say that agian pls", "repeat the ouput", "read it agian please"),
    *_s("read it a gain", "say that a gain", "repeat the out put", "read it uh gain",
        "say it again pleas", "repeat the out putt", "read that out putt again",
        "say it a gain", "read it again pleas", "one more thyme"),
    *_p("would you mind reading the output one more time please",
        "could you please repeat the output for me",
        "if you don't mind could you say that again",
        "i was hoping you could read it once more",
        "when you get a chance could you repeat that"),
]

CORPUS["STOP_SPEECH"] = [
    *_n("stop", "stop talking", "stop speaking", "be quiet", "shush", "pause",
        "pause voice", "stop reading", "quiet please", "hold on stop", "stop the voice",
        "please stop talking", "can you stop", "stop now", "stop speaking now",
        "silence please", "stop the speech", "pause the voice", "stop reading now",
        "please be quiet"),
    *_c("stop pls", "shh", "shush pls", "ok stop", "stop it", "yo stop talking",
        "stop lol", "quiet pls", "stop already", "chill stop talking"),
    *_t("stpo", "stop tlaking", "sotp", "stop speeking", "pasue", "stop taking",
        "be qiuet", "stop reeding", "sto", "quite please"),
    *_s("stop talking please", "stop speeking now", "paws", "stop the boys",
        "cease talking", "stop reeding now", "quiet pleas", "stop please now"),
    *_p("would you mind stopping for a moment please",
        "could you please stop talking now",
        "if you don't mind could you be quiet",
        "i would appreciate it if you could pause",
        "when possible could you please stop speaking"),
]

CORPUS["LOCATE_ERROR"] = [
    *_n("where did it crash", "where is the error", "where did it break", "what line has the error",
        "where's the problem", "find the error", "locate the error", "where did my code crash",
        "which line broke", "where is the bug", "what line did it crash on",
        "tell me where the error is", "where's the bug", "find where it crashed",
        "what line is the error on", "where in my code is the error", "locate the crash",
        "where's the mistake", "which line has the problem", "find the bug"),
    *_c("where'd it crash tho", "where's the error at", "yo where's the bug",
        "where'd this break", "find the bug pls", "where's it broken at",
        "where's this crashing", "find the error bro", "where'd it die", "where's the issue at"),
    *_t("where did it crashh", "wher is the error", "locat the error", "where is teh bug",
        "wich line broke", "find teh error", "where did it crsh", "locate teh error",
        "where's the eror", "find the eror"),
    *_s("where did it crash at", "where is the arrow", "locate the arrow",
        "where is the bugg", "which line brok", "find the arrow", "where did it crash too",
        "locate the air er"),
    *_p("would you mind telling me where the error is please",
        "could you please locate where it crashed",
        "i was wondering where the bug might be",
        "if possible could you find where the error is",
        "when you have a moment could you locate the crash"),
]

CORPUS["ERROR_CAUSE"] = [
    *_n("why did this happen", "what caused this", "what caused the error", "why is this the error",
        "what's causing this", "what's the root cause", "why does this error happen",
        "what's behind this error", "why is this occurring", "what led to this error",
        "why does this keep happening", "what's the reason for this error",
        "why is this the cause", "what's the source of this error", "why does this occur",
        "what triggered this error", "why is this happening to me", "what's making this happen",
        "why does my code cause this", "what's the underlying cause"),
    *_c("why's this happening tho", "what caused this lol", "why's this a thing",
        "what's causing this bro", "why tf is this happening", "what's behind this bro",
        "why does this keep happening tho", "what's up with this error", "why's this occurring",
        "what caused this mess"),
    *_t("why did this happn", "wat caused this", "why did this hapen", "whats causing this",
        "why does this eror happen", "whats the root casue", "why did tihs happen",
        "what casued this", "why is this the caus", "whats behind this eror"),
    *_s("why did this happen too", "what caused dis", "why did this hap in",
        "what's causing dis", "why does this arrow happen", "what's the route cause",
        "why does this occur too", "what triggered dis"),
    *_p("would you mind explaining what caused this please",
        "could you please tell me the root cause",
        "i was wondering what's behind this error",
        "if possible could you explain why this happened",
        "when convenient could you tell me what caused it"),
]

CORPUS["EXPLAIN_LINE"] = [
    *_n("explain this line", "explain the current line", "what does this line do",
        "what does this line mean", "tell me what this line does", "explain line",
        "what is this line doing", "explain this one line", "what's this line for",
        "tell me what this line means", "explain just this line", "what does this specific line do",
        "help me understand this line", "explain this single line", "what's happening on this line",
        "break down this line", "tell me about this line", "what's this line doing exactly",
        "explain the meaning of this line", "walk me through this line"),
    *_c("explain this line pls", "what's this line even do", "yo what's this line do",
        "explain this line bro", "what's this line for tho", "break this line down pls",
        "explain this one pls", "what's happening here", "explain this bit",
        "what's this line about"),
    *_t("explain this lin", "wat does this line do", "explain teh line", "explain this lne",
        "waht does this line mean", "explan this line", "tell me wat this line does",
        "explain this liine", "what dose this line do", "explan the line"),
    *_s("explain this line please", "what does this line dew", "explain dis line",
        "what does this line mean too", "break down this lyne", "explain the lyne",
        "what's this lyne for", "tell me what this lyne does"),
    *_p("would you mind explaining this line to me please",
        "could you please tell me what this line does",
        "i was hoping you could explain this specific line",
        "if you have a moment could you break down this line",
        "when convenient could you explain what this line means"),
]

CORPUS["OVERVIEW"] = [
    *_n("give me an overview", "overview of my program", "summarize my program",
        "what's the big picture", "summarize my code", "give me the big picture",
        "what's this program about", "give me a summary", "summarize what this does",
        "give me the gist", "what's the overall idea", "summarize the program for me",
        "give me a quick overview", "what's this program overall", "sum up my code",
        "give me the short version", "what's the general idea here", "summarize this for me",
        "give me the overall picture", "what's this all about"),
    *_c("give me the gist pls", "sum it up", "what's this about tho", "gimme an overview",
        "sum this up bro", "what's the tldr", "give me the short version pls",
        "what's this program about tho", "gimme the big picture", "sum it up pls"),
    *_t("give me an overveiw", "summerize my program", "what's the big pictur",
        "summerize my code", "give me an overiew", "sumarize the program",
        "give me the big pictur", "summerize this", "give me an ovreview", "sumarize my code"),
    *_s("give me an over view", "summarize my program please", "what's the big picture too",
        "some arise my code", "give me the big picture please", "summarize dis program",
        "what's this program about too", "give me an overview please"),
    *_p("would you mind giving me an overview of my program please",
        "could you please summarize what my code does overall",
        "i was hoping for a quick summary of my program",
        "if possible could you give me the big picture",
        "when convenient could you summarize this for me"),
]

CORPUS["READ_AROUND_ME"] = [
    *_n("read around me", "read the lines around me", "read nearby lines", "read surrounding code",
        "give me context", "what's around my cursor", "read what's near me",
        "read the code around here", "read what's nearby", "read the surrounding lines",
        "read a bit around me", "read the lines near my cursor", "give me some context",
        "read what's around here", "read the code near me", "read the context around me",
        "show me what's around", "read the nearby code", "read a few lines around me",
        "read the area around my cursor"),
    *_c("read around me pls", "gimme context", "read nearby pls", "what's around here",
        "read around here tho", "gimme the surrounding code", "read near me bro",
        "read what's close by", "read the area pls", "read around bro"),
    *_t("read aorund me", "read nearby lins", "read surounding code", "read the lins around me",
        "give me contex", "read waht's nearby", "read the code arond here", "read nerby lines",
        "read arund me", "read the surronding lines"),
    *_s("read a round me", "read nearby lines please", "read surrounding coat",
        "read the lines a round me", "give me con text", "read what's near by",
        "read the coat around here", "read nearby lines too"),
    *_p("would you mind reading the lines around me please",
        "could you please give me some context around my cursor",
        "i was hoping you could read the surrounding code",
        "if you don't mind could you read nearby lines",
        "when convenient could you read what's around me"),
]

CORPUS["WHAT_CONTAINS_LINE"] = [
    *_n("what contains this line", "what block contains this line", "what block is this line in",
        "what is this line inside of", "what's this line part of", "what function is this line in",
        "what loop is this line in", "what contains this", "what block am i in right now",
        "what's this line nested inside", "what encloses this line", "what surrounds this line",
        "what's the parent block of this line", "what structure is this line in",
        "what statement contains this line", "what's above this line containing it",
        "what block wraps this line", "what's this nested in", "what contains my cursor",
        "what block is my cursor in"),
    *_c("what's this line even in", "what contains this tho", "what's this inside of bro",
        "what block is this in lol", "what's wrapping this line", "what contains this pls",
        "what's this part of tho", "what's enclosing this", "what block we in",
        "what's this nested in bro"),
    *_t("wat contains this line", "what containss this line", "what block contians this line",
        "what is this line insde of", "whats this line part of", "what fucntion is this line in",
        "what conatins this", "what blck am i in", "whats this nested insde",
        "what encloses tihs line"),
    *_s("what contains this line please", "what block contains this lyne",
        "what is this line inside of too", "what's this lyne part of",
        "what function is this lyne in", "what encloses this lyne",
        "what surrounds this lyne", "what block wraps this lyne"),
    *_p("would you mind telling me what block contains this line please",
        "could you please tell me what this line is inside of",
        "i was wondering what encloses this particular line",
        "if possible could you tell me what function this is in",
        "when convenient could you tell me what contains this"),
]

CORPUS["EXACT_READ"] = [
    *_n("read this line exactly", "read that exactly", "read it character by character",
        "spell that out", "read the exact line", "read line exactly as written",
        "read this precisely", "read every character", "spell it out for me",
        "read this word for word", "read the line character by character",
        "give me the exact text", "read exactly what's there", "spell out this line",
        "read this letter by letter", "give me the precise line", "read it exactly as typed",
        "read the raw line", "spell this line out", "read this without changing anything"),
    *_c("read it exactly pls", "spell it out bro", "read it precisely tho",
        "gimme the exact line", "read it letter by letter pls", "spell that pls",
        "read it word for word bro", "gimme exactly what's there", "read it exact",
        "spell it out tho"),
    *_t("read this line exatly", "read that exsactly", "read it caracter by caracter",
        "spel that out", "read the exact lne", "read line exactly as writen",
        "read this precisly", "read evry character", "spel it out for me",
        "read this word for wrd"),
    *_s("read this line exactly please", "read that egg zactly", "read it character by character too",
        "spell that out please", "read the exact lyne", "read lyne exactly as written",
        "read this precisely please", "read every character please"),
    *_p("would you mind reading this line exactly as written please",
        "could you please spell out this line for me",
        "i was hoping you could read it character by character",
        "if possible could you read the precise text",
        "when convenient could you read this word for word"),
]

CORPUS["ASK_CODE"] = [
    *_n("ask codeup", "i have a question", "can i ask something", "help me with this",
        "what should i do next", "can you help me", "i need help", "i have a question about my code",
        "can i ask you something", "help me figure this out", "i need some help here",
        "can you assist me", "i have something to ask", "help me please",
        "i need assistance with this", "can you help me out", "i want to ask about my code",
        "help me with my program", "i have a quick question", "can you help with something"),
    *_c("yo i got a question", "can u help", "i need help bro", "help pls",
        "got a question for u", "can u help me out", "i need help with this bro",
        "yo help me", "i got a q", "can i ask u something"),
    *_t("i hav a question", "can i ask somthing", "help me wtih this", "i neeed help",
        "can u help mee", "i hav a questoin", "help me figuer this out", "i need som help",
        "can you assit me", "i want to aks about my code"),
    *_s("i have a question please", "can i ask something too", "help me with dis",
        "what should i do necks", "can you help me too", "i need help please",
        "i have a question about my coat", "help me figure dis out"),
    *_p("would you mind if i asked you a question please",
        "could you please help me with something",
        "i was hoping i could ask you about my code",
        "if you don't mind could you assist me",
        "when you have a moment could you help me out"),
]

CORPUS["EXPLAIN_CONCEPT"] = [
    *_n("what is a loop", "what is a variable", "what does this mean", "explain this concept",
        "what is a function", "i don't understand what a loop is", "explain like i'm new",
        "explain simply", "what does that mean in simple terms", "what is a string",
        "what is a list", "what is a dictionary", "what does indentation mean",
        "what is a for loop", "what is a while loop", "explain what a variable is",
        "what's a function for", "explain functions simply", "what is an integer",
        "explain loops to me simply"),
    *_c("what even is a loop", "what's a variable tho", "explain it simply bro",
        "idk what a loop is", "what's a function even", "explain like i'm 5",
        "what's a string even mean", "explain this concept bro", "what's a list tho",
        "explain simply pls"),
    *_t("wat is a loop", "wat is a varible", "what does this maen", "explain this consept",
        "wat is a funtion", "i dont undrestand what a loop is", "explan like im new",
        "explan simply", "wat does that mean in simple trems", "wat is a strng"),
    *_s("what is a loop please", "what is a vary a bull", "what does dis mean",
        "explain dis concept", "what is a fun ction", "explain like i'm noo",
        "explain simply please", "what does that mean in simple turms"),
    *_p("would you mind explaining what a loop is please",
        "could you please explain what a variable means",
        "i was hoping you could explain this concept simply",
        "if possible could you explain this like i'm new to programming",
        "when convenient could you explain what a function is"),
]

CORPUS["HELP"] = [
    *_n("help", "what can you do", "what can i say", "what can i ask", "how does this work",
        "i don't know what to do", "what commands do you understand", "what are my options",
        "show me what i can do", "list what you can do", "what can codeup do",
        "how do i use codeup", "what things can i say", "show me the commands",
        "what should i say", "help me get started", "what am i able to ask",
        "how does codeup work", "show me my options", "what can i say to you"),
    *_c("help pls", "wym what can u do", "what can u even do", "idk what to do",
        "yo help", "what can i even say", "help me out", "what's the deal here",
        "how's this work", "gimme the commands"),
    *_t("hlep", "wat can you do", "wat can i say", "wat can i ask", "how dose this work",
        "i dont no what to do", "wat commands do you understnad", "wat are my options",
        "sho me what i can do", "lst what you can do"),
    *_s("help please", "what can you dew", "what can i say please", "what can i ask too",
        "how does dis work", "i don't know what to dew", "what commands do you understand too",
        "what are my option's"),
    *_p("would you mind telling me what you can do please",
        "could you please show me what commands are available",
        "i was hoping you could tell me how this works",
        "if possible could you list what i can ask",
        "when convenient could you help me get started"),
]

# =====================================================================
# PART O safety corpus: legitimate input() answers that must NEVER be
# reinterpreted as CodeUp commands while the program is awaiting input().
# Deliberately includes several strings that ARE otherwise-valid command
# words/phrases ("run", "stop", "help", "yes", "no") - the whole point of
# this corpus is proving those are treated as program data, not commands,
# in that specific state.
# =====================================================================
SUPPLY_INPUT_SAFETY_CORPUS: List[str] = [
    "yes", "no", "16", "42", "0", "-5", "3.14",
    "Alex", "Sam", "Jordan", "Taylor", "Priya", "Wei",
    "hello world", "hello", "hi",
    "run", "stop", "help", "one", "two", "three",
    "quit", "exit", "run it",
    "red", "blue", "green",
    "true", "false",
    "a", "b", "c",
    "the quick brown fox",
    "  16  ", "16.0",
    "y", "n",
] * 3  # repeated across three simulated prompts (name/number/free-text) by the test itself


# Small toppers closing the gap to each bucket's PART K target (75+ for the
# 8 core intents, 55+ for every other actively-routed one) - kept separate
# from the hand-organized buckets above rather than re-threading them in,
# so each intent's category breakdown above stays easy to read.
CORPUS["READ_OUTPUT"] += _n("what did the program say", "read what my code printed", "tell me what showed up")
CORPUS["EXPLAIN_ERROR"] += _n("what's this error telling me", "why is this line erroring", "help me fix my understanding of this error", "explain this error message to me")
CORPUS["WHERE_AM_I"] += _n("what line number is this", "tell me exactly where i am", "where has my cursor landed", "what position is my cursor at", "where in this file am i")
CORPUS["WHY_INDENTED"] += _n("why does this line start further right", "explain the spacing on this line", "why is there a tab here", "why is this one line indented differently")
CORPUS["GIVE_HINT"] += _n("give me just enough to keep going", "help me without spoiling it", "i want a hint, not the solution", "nudge me, don't solve it")
CORPUS["EXPLAIN_CODE"] += _n("give me a rundown of my code", "explain what this program is for", "help me see the logic here", "talk me through my program")
CORPUS["READ_LAST_OUTPUT"] += _n("go back and read the output", "let me hear that output one more time")
CORPUS["STOP_SPEECH"] += _n("that's enough talking", "you can stop now")
CORPUS["LOCATE_ERROR"] += _n("point out where the error is", "show me where it broke")
CORPUS["ERROR_CAUSE"] += _n("what's really behind this", "explain the actual cause")
CORPUS["EXPLAIN_LINE"] += _n("what's the purpose of this line", "break down what this one line is doing")
CORPUS["OVERVIEW"] += _n("give me the high level view", "what's this whole program for")
CORPUS["READ_AROUND_ME"] += _n("read a bit before and after this line", "give me the surrounding lines")
CORPUS["WHAT_CONTAINS_LINE"] += _n("what's the enclosing block here", "what owns this line")
CORPUS["EXACT_READ"] += _n("read it verbatim", "give me the literal text of this line")
CORPUS["ASK_CODE"] += _n("mind if i ask you something", "i've got something to ask about my code")
CORPUS["EXPLAIN_CONCEPT"] += _n("break down what a loop actually is", "explain that term for me")
CORPUS["HELP"] += _n("what are all the things i can ask", "walk me through what you can help with")
