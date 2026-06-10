'use strict';

/**
 * CodeUp guided tutorial — a spoken, activity-based, opt-in tutorial for blind
 * beginners.
 *
 * Two layers live in this file:
 *
 *   1. TutorialModel — a pure, DOM-free state machine. It owns "where are we"
 *      (active module, stage, what is completed) and the transition rules. It
 *      has no side effects, so it is unit-testable in Node (see
 *      tests/tutorial_model.test.js) and cannot accidentally talk to the DOM.
 *
 *   2. TutorialController — the browser layer. It drives the model, speaks every
 *      essential event through the app's PROVEN audible path (the global
 *      speak() -> VoiceEngine), loads example code with {preserveSpeech:true}
 *      (so narration is never silently cancelled), manages an accessible panel
 *      with real focusable buttons, and intercepts tutorial utterances.
 *
 * Progression is always opt-in: finishing one module never starts the next.
 */
(function () {

  // ─── PURE STATE MACHINE (TutorialModel) ──────────────────────────────────
  function TutorialModel(order) {
    this.order = (order && order.length) ? order.slice() : ['print', 'variables', 'if', 'for', 'while'];
    this.active = false;
    this.moduleId = null;
    // idle | intro | activity | decision
    this.stage = 'idle';
    this.succeeded = false;
    this.completed = [];
  }

  /** Begin the tutorial. Always starts on the first module's intro. */
  TutorialModel.prototype.start = function () {
    this.active = true;
    this.moduleId = this.order[0];
    this.stage = 'intro';
    this.succeeded = false;
    return this.moduleId;
  };

  /** Move into the hands-on activity for the current module. */
  TutorialModel.prototype.beginActivity = function () {
    this.stage = 'activity';
    this.succeeded = false;
    return this.moduleId;
  };

  /**
   * Record a successful activity. Moves to the DECISION stage — never to the
   * next module. The learner must explicitly choose to continue.
   */
  TutorialModel.prototype.markSuccess = function () {
    this.succeeded = true;
    this.stage = 'decision';
    if (this.completed.indexOf(this.moduleId) === -1) {
      this.completed.push(this.moduleId);
    }
    return this.moduleId;
  };

  /** Move to the decision stage without recording success (used by "skip"). */
  TutorialModel.prototype.toDecision = function () {
    this.stage = 'decision';
    return this.moduleId;
  };

  /** The id of the module after the current one, or null if it is the last. */
  TutorialModel.prototype.nextModuleId = function () {
    var i = this.order.indexOf(this.moduleId);
    if (i === -1 || i + 1 >= this.order.length) return null;
    return this.order[i + 1];
  };

  /**
   * Opt-in advance from a decision point. Returns the new module id, or null
   * when there is no next module (tutorial finished).
   */
  TutorialModel.prototype.continueNext = function () {
    var n = this.nextModuleId();
    if (n) {
      this.moduleId = n;
      this.stage = 'intro';
      this.succeeded = false;
    }
    return n;
  };

  /** Practise the current module again (re-enter its activity). */
  TutorialModel.prototype.practiceAgain = function () {
    this.stage = 'activity';
    this.succeeded = false;
    return this.moduleId;
  };

  /** Jump straight to a specific module's intro. */
  TutorialModel.prototype.gotoModule = function (id) {
    if (this.order.indexOf(id) === -1) return false;
    this.active = true;
    this.moduleId = id;
    this.stage = 'intro';
    this.succeeded = false;
    return true;
  };

  /** Leave the tutorial cleanly. */
  TutorialModel.prototype.exit = function () {
    this.active = false;
    this.stage = 'idle';
    this.moduleId = null;
    this.succeeded = false;
  };

  TutorialModel.prototype.moduleNumber = function () {
    var i = this.order.indexOf(this.moduleId);
    return i === -1 ? 0 : i + 1;
  };

  /**
   * Classify a free-form utterance into a tutorial control intent.
   * Returns one of: continue | again | recap | repeat | hint | example | exit
   * or null when the text is not a tutorial-control phrase (so normal IDE
   * commands such as "run" or "read line 2" still flow through untouched).
   *
   * Bare "stop" / "help" are intentionally NOT consumed — they remain global
   * commands (stop speech / show help).
   */
  TutorialModel.classifyDecision = function (text) {
    var t = String(text || '').toLowerCase().trim().replace(/[.!?]+$/, '').replace(/\s+/g, ' ');
    if (!t) return null;

    function has(list) {
      for (var i = 0; i < list.length; i++) {
        if (t === list[i] || t.indexOf(list[i]) !== -1) return true;
      }
      return false;
    }

    // Exit — checked first so "exit tutorial" never reads as something else.
    if (has([
      'exit tutorial', 'close tutorial', 'stop tutorial', 'end tutorial',
      'leave tutorial', 'quit tutorial', 'exit the tutorial', 'stop the tutorial',
      'start coding', 'let me code', 'i want to code', "i'm done", 'i am done',
      "that's enough", 'thats enough', 'i am finished', "i'm finished", 'all done'
    ]) || t === 'exit' || t === 'quit' || t === 'done') {
      return 'exit';
    }

    // Repeat the instruction (distinct from "try again", which redoes the work).
    if (has([
      'repeat that', 'repeat the instruction', 'repeat instructions',
      'repeat the instructions', 'say that again', 'say it again', 'say again',
      'read it again', 'read that again', 'read the instructions again',
      'read instructions again', 'read instructions', 'instructions again',
      'what do i do', 'what should i do', 'what was that'
    ]) || t === 'repeat') {
      return 'repeat';
    }

    // Redo the activity.
    if (has([
      'try again', 'practise again', 'practice again', 'practise this again',
      'practice this again', 'do it again', 'one more time', 'let me try again',
      'i want to try again', 'redo', 'retry'
    ]) || t === 'again') {
      return 'again';
    }

    // Recap / summary of the current topic.
    if (has(['recap', 'summary', 'summarise', 'summarize', 'remind me', 'go over it again'])) {
      return 'recap';
    }

    // Fill in an example for me.
    if (has([
      'give me an example', 'show me an example', 'fill it in', 'fill in an example',
      'fill it in for me', 'do it for me', 'type it for me', 'write it for me',
      'show me how', 'an example', 'example please'
    ]) || t === 'example') {
      return 'example';
    }

    // Ask for a hint (bare "help" stays a global command).
    if (has(['give me a hint', 'i need a hint', 'need a hint', 'a hint', 'give me a clue', 'a clue', 'help me out']) || t === 'hint' || t === 'clue') {
      return 'hint';
    }

    // Continue to the next topic (decision stage).
    if (has([
      'continue', 'next topic', 'next lesson', 'next module', 'go on', 'keep going',
      'move on', 'proceed', 'go ahead', 'yes please', 'carry on'
    ]) || t === 'next' || t === 'yes' || t === 'yeah' || t === 'yep' || t === 'continue tutorial') {
      return 'continue';
    }

    return null;
  };

  // AI-tutor coach requests (friendlier explanations, adaptive hints,
  // encouragement). Mirrors tutorial_engine.classify_coach_request on the server
  // and is checked BEFORE classifyDecision so "say that again simpler" is coached
  // rather than read as a plain "repeat". Specific topics win over generic ones.
  TutorialModel.COACH_TRIGGERS = [
    ['why_quotes', ['why do we use quotes', 'why use quotes', 'why the quotes', 'why quotes', 'what are quotes for', 'what do quotes do', 'purpose of quotes']],
    ['why_indentation', ['why do we indent', 'why the indentation', 'why indentation', 'why indent', 'what is indentation', 'what does indentation do', 'why four spaces']],
    ['another_hint', ['give me another hint', 'another hint', 'one more hint', 'a different hint', 'different hint', 'more hints', 'next hint']],
    ['what_learning', ['what am i learning', 'what are we learning', 'what is this teaching', 'what am i doing', 'what topic is this', 'what is this topic', 'what is this about']],
    ['encourage', ['encourage me', 'i give up', 'this is too hard', 'this is hard', "i can't do this", 'i cannot do this', 'i am stuck', "i'm stuck", 'cheer me up']],
    ['explain_simpler', ['say that again simpler', 'say it simpler', 'explain it simpler', 'explain simpler', 'explain that simpler', 'make it simpler', 'simpler please', 'in simple words', 'in simpler words', 'simpler']],
    ['dont_understand', ["i don't understand", 'i do not understand', 'i dont understand', "i don't get it", 'i dont get it', "i'm confused", 'i am confused', "i don't follow", 'this is confusing', 'confused']]
  ];

  TutorialModel.classifyCoachRequest = function (text) {
    var t = String(text || '').toLowerCase().trim().replace(/[.!?]+$/, '').replace(/\s+/g, ' ');
    if (!t) return null;
    for (var i = 0; i < TutorialModel.COACH_TRIGGERS.length; i++) {
      var phrases = TutorialModel.COACH_TRIGGERS[i][1];
      for (var j = 0; j < phrases.length; j++) {
        if (t === phrases[j] || t.indexOf(phrases[j]) !== -1) return TutorialModel.COACH_TRIGGERS[i][0];
      }
    }
    return null;
  };

  // ─── STAGED ACTIVITY MODEL (declarative, pure) ───────────────────────────
  // Each module's hands-on activity is built line by line, by voice. A step is
  // satisfied when its `check(code)` predicate matches the live editor text.
  // Checks are cumulative and order-independent: the controller advances past
  // every satisfied step, so a learner can build pieces in any order and still
  // progress. These predicates are pure (regex over a code string) so they run
  // under Node and are unit-tested in tests/tutorial_model.test.js.
  function _hasAssign(code) {
    // A real assignment line (x = ...), not a comparison (x == ...).
    return /^[ \t]*[A-Za-z_]\w*\s*=(?!=)\s*\S/m.test(String(code || ''));
  }
  function _printsVariable(code) {
    // An assignment somewhere AND a print of a bare name (print(x)).
    return _hasAssign(code) && /\bprint\s*\(\s*[A-Za-z_]\w*\s*\)/.test(String(code || ''));
  }
  function _hasPrintArg(code) {
    return /\bprint\s*\(\s*\S/.test(String(code || ''));
  }
  function _hasIfHeader(code)    { return /^[ \t]*if\b.*:\s*$/m.test(String(code || '')); }
  function _hasForHeader(code)   { return /^[ \t]*for\b.*:\s*$/m.test(String(code || '')); }
  function _hasWhileHeader(code) { return /^[ \t]*while\b.*:\s*$/m.test(String(code || '')); }
  function _hasIndentedPrint(code) { return /^[ \t]+print\s*\(/m.test(String(code || '')); }
  function _hasIncrement(code) {
    // Indented "count = count + 1" or "count += 1".
    return /^[ \t]+[A-Za-z_]\w*\s*(?:=(?!=)\s*[A-Za-z_]\w*\s*[-+]|[-+]=)/m.test(String(code || ''));
  }

  var TUTORIAL_STEPS = {
    print: [
      {
        id: 'print',
        say: 'insert print hello world',
        prompt: 'Let us make Python say something out loud. Say, or type into the command box: insert print, hello world. You may use any short message.',
        hint: 'Say the word insert, then print, then your message. For example: insert print hello world.',
        readback: 'That print line will speak your message when the program runs.',
        check: _hasPrintArg,
      },
    ],
    variables: [
      {
        id: 'assign',
        say: 'insert a variable named name and give it the value Taknoor',
        prompt: 'A variable is a named box that stores information for later. Let us make a box called name and put a word inside it. Say: insert a variable named name and give it the value, then your own name. For example: insert a variable named name and give it the value Taknoor.',
        hint: 'Begin with: insert a variable named name. Then say: and give it the value, then a word such as Taknoor.',
        readback: 'You created a variable. The box called name now holds that text.',
        check: _hasAssign,
      },
      {
        id: 'print-var',
        say: 'insert print name',
        prompt: 'Now let us show what is inside the box. Say: insert print, then the same variable name. For example: insert print name.',
        hint: 'Say insert print, then the variable name you chose, with no quotes. For example: insert print name.',
        readback: 'You used the variable. When the program runs, it will read out the value stored inside it.',
        check: _printsVariable,
      },
    ],
    if: [
      {
        id: 'variable',
        say: 'insert a variable named age and give it the value 12',
        prompt: 'An if statement makes a decision. First we need something to decide about. Say: insert a variable named age and give it the value 12. You may pick any number.',
        hint: 'Say: insert a variable named age and give it the value 12.',
        readback: 'Good. Now we have a number to test.',
        check: _hasAssign,
      },
      {
        id: 'if-header',
        say: 'insert an if statement checking age is greater than 10',
        prompt: 'Now the decision itself. Say: insert an if statement checking age is greater than 10. CodeUp will add the colon at the end for you.',
        hint: 'Say: insert an if statement checking age is greater than 10.',
        readback: 'That is the question Python will check. The next line says what happens when it is true.',
        check: _hasIfHeader,
      },
      {
        id: 'indented-print',
        say: 'insert an indented print saying you can vote',
        prompt: 'The action belongs inside the decision, so it must be indented. Say: insert an indented print saying you can vote.',
        hint: 'Say: insert an indented print saying you can vote. The word indented adds the four spaces for you.',
        readback: 'That print is indented, so it only runs when the condition is true. Indentation is how Python knows it belongs to the if.',
        check: _hasIndentedPrint,
      },
    ],
    for: [
      {
        id: 'for-header',
        say: 'insert for i in range 3',
        prompt: 'A for loop repeats an action a set number of times. Say: insert for i in range 3. That sets up a loop that runs three times.',
        hint: 'Say: insert for i in range 3. CodeUp adds the brackets and the colon for you.',
        readback: 'That sets up the loop. The next line, indented, is the action it repeats.',
        check: _hasForHeader,
      },
      {
        id: 'indented-print',
        say: 'insert an indented print i',
        prompt: 'Now the action to repeat. Say: insert an indented print i. The i is the counter, so it will read out 0, then 1, then 2.',
        hint: 'Say: insert an indented print i. The word indented adds the four spaces that put it inside the loop.',
        readback: 'That print is indented, so the loop repeats it each time it runs.',
        check: _hasIndentedPrint,
      },
    ],
    while: [
      {
        id: 'counter',
        say: 'insert a variable named count and give it the value 1',
        prompt: 'A while loop repeats while a condition stays true. First we need a counter that will change over time. Say: insert a variable named count and give it the value 1.',
        hint: 'Say: insert a variable named count and give it the value 1.',
        readback: 'Good. Count starts at 1.',
        check: _hasAssign,
      },
      {
        id: 'while-header',
        say: 'insert while count is less than or equal to 3',
        prompt: 'Now the loop. Say: insert while count is less than or equal to 3. The loop keeps going while that stays true.',
        hint: 'Say: insert while count is less than or equal to 3.',
        readback: 'That is the condition the loop checks each time, before it repeats.',
        check: _hasWhileHeader,
      },
      {
        id: 'indented-print',
        say: 'insert an indented print count',
        prompt: 'Inside the loop, let us read the counter aloud. Say: insert an indented print count.',
        hint: 'Say: insert an indented print count. The word indented puts it inside the loop.',
        readback: 'That print is inside the loop, so it runs every repetition.',
        check: _hasIndentedPrint,
      },
      {
        id: 'increment',
        say: 'insert an indented count equals count plus 1',
        prompt: 'This is the most important line: we must change the counter so the loop can stop. Say: insert an indented count equals count plus 1.',
        hint: 'Say: insert an indented count equals count plus 1. Without this, the loop would run forever.',
        readback: 'That update grows count each time, so the condition eventually becomes false and the loop stops safely.',
        check: _hasIncrement,
      },
    ],
  };

  // Export the pure model + staged-activity definitions for Node-based unit
  // testing. Nothing below this point runs under Node (it needs document/window).
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TutorialModel: TutorialModel, TUTORIAL_STEPS: TUTORIAL_STEPS };
  }

  if (typeof document === 'undefined' || typeof window === 'undefined') {
    return; // Node / non-browser: model only.
  }

  // ─── BROWSER HELPERS (resolve the app's proven globals defensively) ───────
  function _speak(text, opts) {
    if (!text) return;
    try {
      if (typeof speak === 'function') { speak(text, opts || {}); return; }
    } catch (e) {}
    try { if (window.speak) window.speak(text, opts || {}); } catch (e) {}
  }
  function _cancelSpeech() {
    try { if (typeof VoiceEngine !== 'undefined' && VoiceEngine.cancelSpeech) { VoiceEngine.cancelSpeech(); return; } } catch (e) {}
    try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) {}
  }
  function _out(text) {
    try { if (typeof out === 'function') { out(text); return; } } catch (e) {}
    var el = document.getElementById('output');
    if (el) el.textContent = text;
  }
  function _srAnnounce(msg) {
    try { if (typeof srAnnounce === 'function') { srAnnounce(msg); return; } } catch (e) {}
  }
  function _setCode(code) {
    // ALWAYS preserve speech: setCode() otherwise calls SpeechManager.cancelAll(),
    // which is the classic "narration shown but not spoken" bug.
    try { if (typeof setCode === 'function') return setCode(code, { preserveSpeech: true, source: 'tutorial', allowNonPython: true }); } catch (e) {}
    try { if (window.setCode) return window.setCode(code, { preserveSpeech: true, source: 'tutorial', allowNonPython: true }); } catch (e) {}
    return false;
  }
  function _getCode() {
    try { if (typeof getCode === 'function') return getCode(); } catch (e) {}
    try { if (window.getCode) return window.getCode(); } catch (e) {}
    return '';
  }
  function _cueSuccess() {
    try { if (typeof cueSuccess === 'function') cueSuccess(); } catch (e) {}
  }
  function _focusEditor() {
    try { if (window.editor && window.editor.focus) { window.editor.focus(); return; } } catch (e) {}
    try { if (typeof editor !== 'undefined' && editor && editor.focus) { editor.focus(); return; } } catch (e) {}
    var ta = document.querySelector('#editor textarea');
    if (ta) { try { ta.focus(); } catch (e) {} }
  }
  // During tutorial activities the learner types/says insert COMMANDS, not raw
  // Python — so keyboard focus goes to the command box, never the code editor.
  function _focusCommandBox() {
    var el = document.getElementById('voiceText');
    if (el) { try { el.focus(); } catch (e) {} }
  }
  function _safeStorage(fn) { try { return fn(); } catch (e) { return null; } }

  // ─── FALLBACK CONTENT (used only if /tutorial/modules cannot be fetched) ──
  // Mirrors tutorial_engine.py. Concept + example are spoken in the intro; the
  // step-by-step "insert ..." prompts come from TUTORIAL_STEPS above.
  var FALLBACK_CONTENT = {
    order: ['print', 'variables', 'if', 'for', 'while'],
    count: 5,
    modules: {
      print: {
        id: 'print', order: 1, title: 'Print statements',
        concept: 'A print statement makes your program say or display a message when it runs. In CodeUp you create one by speaking an insert command.',
        example_code: 'print("Hello world")',
        example_spoken: 'For example, to make Python greet us, you would say: insert print hello world.',
        task: 'I will tell you a command to say. When you say it, I will place the code and read it back.',
        hints: ['Say: insert print hello world.', 'Start with the word insert, then print, then your message.', 'You can also type the command into the command box and press Enter.'],
        success: 'Nicely done. You made Python speak with a print statement.',
        recap: 'Recap: a print statement shows a message. You make one by saying: insert print, then your message.'
      },
      variables: {
        id: 'variables', order: 2, title: 'Variables',
        concept: 'A variable is a named box that stores information so your program can use it later. You give the box a name and put a value inside.',
        example_code: 'name = "Taknoor"\nprint(name)',
        example_spoken: 'For example, to store your name you would say: insert a variable named name and give it the value Taknoor. Then to show it: insert print name.',
        task: 'We will build it in two steps: first create the variable, then print what is inside it.',
        hints: ['First say: insert a variable named name and give it the value Taknoor.', 'Then say: insert print name.', 'You may choose any name and any value.'],
        success: 'Well done. You stored a value in a variable and printed it back.',
        recap: 'Recap: a variable is a named box. Say insert a variable named, a name, and give it the value, then a value. Print it by saying insert print and the name.'
      },
      if: {
        id: 'if', order: 3, title: 'If statements',
        concept: 'An if statement lets a program make a decision. Python checks whether something is true, and only then runs the indented action underneath it.',
        example_code: 'age = 12\nif age > 10:\n    print("you can vote")',
        example_spoken: 'For example: insert a variable named age and give it the value 12. Then: insert an if statement checking age is greater than 10. Then: insert an indented print saying you can vote.',
        task: 'We will build it line by line: a variable to test, the if decision, then an indented action.',
        hints: ['First the variable: insert a variable named age and give it the value 12.', 'Then the decision: insert an if statement checking age is greater than 10.', 'Then the action: insert an indented print saying you can vote.'],
        success: 'Great work. Your if statement made a decision and printed when the condition was true.',
        recap: 'Recap: an if statement checks a condition and ends with a colon. The line that runs when it is true is indented underneath.'
      },
      for: {
        id: 'for', order: 4, title: 'For loops',
        concept: 'A for loop repeats an action a known number of times. The repeated line is indented underneath the for line.',
        example_code: 'for i in range(3):\n    print(i)',
        example_spoken: 'For example: insert for i in range 3. Then: insert an indented print i.',
        task: 'We will build it in two steps: the loop line, then the indented action it repeats.',
        hints: ['First say: insert for i in range 3.', 'Then say: insert an indented print i.', 'The word indented adds the four spaces that put the line inside the loop.'],
        success: 'Excellent. Your for loop repeated the action and printed each time.',
        recap: 'Recap: a for loop with range repeats a set number of times. The repeated line is indented underneath.'
      },
      while: {
        id: 'while', order: 5, title: 'While loops',
        concept: 'A while loop repeats an action while a condition stays true. Because the condition could stay true forever, a while loop must change something each time so it can stop.',
        example_code: 'count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1',
        example_spoken: 'For example: insert a variable named count and give it the value 1. Then: insert while count is less than or equal to 3. Then: insert an indented print count. Then: insert an indented count equals count plus 1.',
        task: 'We will build a safe counter loop in four steps, ending with the line that lets it stop.',
        hints: ['Start the counter: insert a variable named count and give it the value 1.', 'The loop: insert while count is less than or equal to 3.', 'Most important, the update: insert an indented count equals count plus 1.'],
        success: 'Brilliant. Your while loop counted up and stopped safely. That completes the last topic.',
        recap: 'Recap: a while loop repeats while its condition is true. Always change the counter inside so it eventually stops.'
      }
    }
  };

  var PROGRESS_KEY = 'codeup_tutorial_progress';

  // ─── CONTROLLER ───────────────────────────────────────────────────────────
  var Controller = {
    model: new TutorialModel(FALLBACK_CONTENT.order),
    content: null,
    _contentPromise: null,
    _hintIndex: 0,
    _lastInstruction: '',
    _validating: false,
    _stepIndex: 0,  // which staged step of the current activity we are on

    get active() { return this.model.active; },
    get step() { return this.model.moduleNumber(); },  // legacy compat

    _module: function () {
      if (!this.content) return null;
      return this.content.modules[this.model.moduleId] || null;
    },

    // Ordered build steps for the current module's activity (may be empty).
    _steps: function () {
      return TUTORIAL_STEPS[this.model.moduleId] || [];
    },
    _currentStep: function () {
      var steps = this._steps();
      return (this._stepIndex >= 0 && this._stepIndex < steps.length) ? steps[this._stepIndex] : null;
    },

    _ensureContent: function () {
      var self = this;
      if (this.content) return Promise.resolve(this.content);
      if (this._contentPromise) return this._contentPromise;
      this._contentPromise = fetch('/tutorial/modules')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && data.modules && data.order) {
            self.content = data;
          } else {
            self.content = FALLBACK_CONTENT;
          }
          self.model = new TutorialModel(self.content.order);
          self._loadProgress();
          return self.content;
        })
        .catch(function () {
          self.content = FALLBACK_CONTENT;
          self.model = new TutorialModel(self.content.order);
          self._loadProgress();
          return self.content;
        });
      return this._contentPromise;
    },

    _loadProgress: function () {
      var raw = _safeStorage(function () { return localStorage.getItem(PROGRESS_KEY); });
      if (!raw) return;
      try {
        var arr = JSON.parse(raw);
        if (Array.isArray(arr)) this.model.completed = arr.filter(function (x) { return typeof x === 'string'; });
      } catch (e) {}
    },

    _saveProgress: function () {
      var done = this.model.completed;
      _safeStorage(function () { localStorage.setItem(PROGRESS_KEY, JSON.stringify(done)); });
    },

    // ── lifecycle ──────────────────────────────────────────────────────────
    open: function () {
      var self = this;
      this._ensureContent().then(function () {
        if (!self.content) { _speak('Sorry, the tutorial could not load right now.'); return; }
        _cancelSpeech();
        self.model.start();
        self._hintIndex = 0;
        self._showPanel();
        self.render();
        var completedNote = '';
        if (self.model.completed.length) {
          completedNote = ' You have already completed ' + self.model.completed.length + ' topic' + (self.model.completed.length === 1 ? '' : 's') + ', but we will start from the beginning. You can also say, practise, then a topic name, to jump straight to one.';
        }
        _speak('Welcome to the CodeUp guided tutorial. CodeUp lets you build Python programs by speaking instructions. In each lesson I will first explain a concept, then tell you an insert command to say. When you say it, I will place the code in the editor and read it back to you. You can also type the same command into the command box if voice is unavailable.' + completedNote);
        _speak('We will begin with print statements. After each topic you can stop, practise again, or continue. You are always in control.');
        _speak('At any time you can say, or type: repeat, to hear the instruction again. hint, for a clue. read my code, to hear what you have written. run code, to run it. or exit tutorial, to stop. You can also press Tab to reach the tutorial buttons.');
        _srAnnounce('Guided tutorial started. Topic 1 of ' + self.content.count + ', print statements.');
        self._enterModuleIntro(self.model.moduleId, { skipModelReset: true });
      });
    },

    // Jump straight to one topic ("practise for loops").
    practice: function (moduleId) {
      var self = this;
      this._ensureContent().then(function () {
        if (!self.content || !self.content.modules[moduleId]) {
          _speak('I could not find that topic. The topics are: print, variables, if statements, for loops, and while loops.');
          return;
        }
        _cancelSpeech();
        self.model.gotoModule(moduleId);
        self._hintIndex = 0;
        self._showPanel();
        self.render();
        _speak('Okay. Let us practise ' + self.content.modules[moduleId].title + '.');
        _srAnnounce('Practising ' + self.content.modules[moduleId].title);
        self._enterModuleIntro(moduleId, { skipModelReset: true });
      });
    },

    // close() / exit() — leave cleanly. close() kept for backward compatibility.
    close: function () { this.exit(false); },
    exit: function (startCoding) {
      if (this._pendingValidate) { clearTimeout(this._pendingValidate); this._pendingValidate = null; }
      this.model.exit();
      this._hidePanel();
      _cancelSpeech();
      if (startCoding) {
        _speak('Tutorial closed. The editor is yours now. Press Control and Enter to run your code, or say help to hear all commands. Say start tutorial any time to come back.');
      } else {
        _speak('Tutorial closed. Say start tutorial any time to open it again.');
      }
      _srAnnounce('Tutorial closed.');
      _focusEditor();
    },

    // next() — advance at a decision point (used by the "tutorial next" command).
    next: function () {
      if (this.model.stage === 'decision') { this._continue(); }
      else { this._repeatInstruction(); }
    },

    // ── module flow ──────────────────────────────────────────────────────────
    _enterModuleIntro: function (moduleId, opts) {
      opts = opts || {};
      if (!opts.skipModelReset) {
        this.model.moduleId = moduleId;
        this.model.stage = 'intro';
        this.model.succeeded = false;
      }
      this._hintIndex = 0;
      var m = this._module();
      if (!m) return;
      this.render();
      this._setStatus('Learning: ' + m.title);
      _speak('Topic ' + this.model.moduleNumber() + ' of ' + this.content.count + '. ' + m.title + '. ' + m.concept);
      _speak(m.example_spoken);
      this._enterActivity();
    },

    _enterActivity: function () {
      this.model.beginActivity();
      this._hintIndex = 0;
      this._stepIndex = 0;
      this._failCount = 0;
      this._coachHintIndex = 0;
      var m = this._module();
      if (!m) return;
      // Fresh editor for the activity so the learner builds from a clean slate.
      _setCode('');
      var first = this._currentStep();
      this._lastInstruction = first ? first.prompt : (m.task || '');
      this.render();
      this._setStatus(first ? ('Say: ' + first.say) : ('Activity: ' + m.title));
      _speak('Now let us build it together, one line at a time, with your voice.');
      if (first) _speak(first.prompt);
      _speak('Say the command, or type it into the command box and press Enter.');
      _srAnnounce('Activity: ' + m.title + '. Say the insert command.');
      _focusCommandBox();
    },

    // Observe a real insertion made through the normal voice/typed pipeline (the
    // tutorial never intercepts insert commands — it watches their result). The
    // insert helper has already read the new line back; here we confirm the
    // structure and prompt the next step. Speech is queued, so nothing overlaps.
    onInsert: function () {
      if (!this.model.active || this.model.stage !== 'activity') return;
      var steps = this._steps();
      if (!steps.length) return;
      var code = _getCode();
      var advanced = false;
      // Advance past every step the code now satisfies (order-independent).
      while (this._stepIndex < steps.length && steps[this._stepIndex].check(code)) {
        var done = steps[this._stepIndex];
        this._stepIndex++;
        advanced = true;
        if (done.readback) _speak(done.readback);
      }
      if (!advanced) return;  // the generic read-back already played; wait for more.
      if (this._stepIndex < steps.length) {
        var next = steps[this._stepIndex];
        this._lastInstruction = next.prompt;
        this.render();
        this._setStatus('Next, say: ' + next.say);
        _speak('Next. ' + next.prompt);
        _srAnnounce('Next step. ' + next.say);
      } else {
        this._lastInstruction = 'Say run code, or press Control and Enter, to run your program.';
        this.render();
        this._setStatus('All lines added. Say: run code.');
        _speak('Your program is complete. Now run it. Say: run code. Or press Control and Enter.');
        _srAnnounce('All lines added. Say run code.');
      }
    },

    // Called by window._tutorialOnRunSuccess / _tutorialOnRunError.
    onRunResult: function (ranOk) {
      // Only react during an active hands-on activity. Never auto-advance.
      if (!this.model.active || this.model.stage !== 'activity') return;
      if (this._validating) return;
      this._validating = true;
      var self = this;
      var code = _getCode();
      var output = (typeof window !== 'undefined' && window.lastRunOutput) ? window.lastRunOutput : '';

      this._validate(self.model.moduleId, code, ranOk, output).then(function (res) {
        self._validating = false;
        // Guard: the learner may have exited or moved on while we waited.
        if (!self.model.active || self.model.stage !== 'activity') return;
        if (res.passed) {
          self._failCount = 0;
          self._celebrateAndDecide(res.feedback);
        } else {
          // Repeated failure on the same activity: lead with warm encouragement
          // (deterministic, no AI needed) before the same factual hint.
          self._failCount = (self._failCount || 0) + 1;
          if (self._failCount >= 2) {
            _speak('You are doing fine. This part trips up everyone at first. Let us take it one small step.');
          }
          if (res.feedback) _speak(res.feedback);
          if (res.hint) _speak('Here is a hint. ' + res.hint);
          var cur = self._currentStep();
          if (cur) _speak('Try this command again. ' + cur.prompt);
          else _speak('Adjust your program and run again, or say give me an example.');
          self._setStatus('Not yet — listen to the hint, then say the command again.');
          _srAnnounce('Not yet. Hint given.');
          _focusCommandBox();
        }
      });
    },

    _validate: function (moduleId, code, ranOk, output) {
      var self = this;
      return fetch('/tutorial/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ module: moduleId, code: code, ran_ok: !!ranOk, output: output })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && typeof data.passed === 'boolean') return data;
          return self._localValidate(moduleId, code, ranOk);
        })
        .catch(function () { return self._localValidate(moduleId, code, ranOk); });
    },

    // Offline fallback validator (lightweight; backend AST check is primary).
    _localValidate: function (moduleId, code, ranOk) {
      var m = (this.content && this.content.modules[moduleId]) || {};
      var c = String(code || '');
      var ok = false;
      if (moduleId === 'print') ok = /\bprint\s*\(\s*[^)\s]/.test(c);
      else if (moduleId === 'variables') ok = /\b[A-Za-z_]\w*\s*=\s*\S/.test(c) && /\bprint\s*\(\s*[A-Za-z_]/.test(c);
      else if (moduleId === 'if') ok = /\bif\b.*:/.test(c) && /\bprint\s*\(/.test(c);
      else if (moduleId === 'for') ok = /\bfor\b.*:/.test(c) && /\bprint\s*\(/.test(c);
      else if (moduleId === 'while') {
        var unsafe = /\bwhile\s+(True|1)\s*:/.test(c) && !/\bbreak\b/.test(c);
        if (unsafe) return { passed: false, safe: false, feedback: 'That while loop would run forever. Add a counter that changes so it can stop.', hint: (m.hints || []).slice(-1)[0] };
        ok = /\bwhile\b.*:/.test(c) && /\bprint\s*\(/.test(c);
      }
      if (ok && ranOk) return { passed: true, safe: true, feedback: m.success || 'Well done.', hint: null };
      return { passed: false, safe: true, feedback: 'Not quite yet.', hint: (m.hints || [])[0] || null };
    },

    _celebrateAndDecide: function (successText) {
      this.model.markSuccess();
      this._saveProgress();
      _cueSuccess();
      var m = this._module();
      _speak(successText || (m && m.success) || 'Well done.');
      var nextId = this.model.nextModuleId();
      var prompt;
      if (nextId) {
        var nextTitle = this.content.modules[nextId].title;
        prompt = 'What would you like to do next? Say continue, to go on to ' + nextTitle + '. Say practise again, to repeat this topic. Say recap, to hear a summary. Or say exit tutorial, to stop and start coding.';
      } else {
        prompt = 'That was the final topic. Congratulations. Say practise again to repeat it, recap to hear a summary, or exit tutorial to start coding on your own.';
      }
      this._lastInstruction = prompt;
      this.render();
      _speak(prompt);
      this._setStatus(nextId ? 'Choose: continue, practise again, recap, or exit.' : 'All topics complete. Choose: practise again, recap, or exit.');
      _srAnnounce('Topic complete. Choose what to do next.');
      // Move keyboard focus to the most likely next action.
      var focusBtn = document.getElementById(nextId ? 'tutorialContinueBtn' : 'tutorialStopBtn');
      if (focusBtn) { try { focusBtn.focus(); } catch (e) {} }
    },

    _continue: function () {
      var nextId = this.model.nextModuleId();
      if (!nextId) { this.exit(true); return; }
      this.model.continueNext();
      this._enterModuleIntro(this.model.moduleId, { skipModelReset: true });
    },

    _practiceAgain: function () {
      this.model.practiceAgain();
      this._enterActivity();
    },

    _recap: function () {
      var m = this._module();
      if (!m) return;
      _speak(m.recap);
      if (this.model.stage === 'decision') {
        _speak(this._lastInstruction || 'Say continue, practise again, or exit tutorial.');
      } else {
        var cur = this._currentStep();
        if (cur) _speak('You are building it now. ' + cur.prompt);
        else _speak(this._lastInstruction || 'Say run code when you are ready.');
      }
      _srAnnounce('Recap given.');
    },

    _repeatInstruction: function () {
      _speak(this._lastInstruction || 'There is nothing to repeat yet.');
      _srAnnounce('Repeating the instruction.');
    },

    // Read the editor back, line by line, announcing indentation — so a blind
    // learner can hear the structure they have built so far.
    _readMyCode: function () {
      var trimmed = String(_getCode() || '').replace(/\s+$/, '');
      if (!trimmed.trim()) {
        _speak('Your program is empty so far. ' + (this._lastInstruction || ''));
        _srAnnounce('Program empty.');
        return;
      }
      var lines = trimmed.split('\n');
      _speak('Here is your program. ' + lines.length + ' line' + (lines.length === 1 ? '' : 's') + '.');
      for (var i = 0; i < lines.length; i++) {
        var lead = (lines[i].match(/^[ \t]*/) || [''])[0].replace(/\t/g, '    ');
        var note = lead.length >= 4 ? 'indented, ' : '';
        var body = lines[i].trim();
        _speak('Line ' + (i + 1) + '. ' + note + (body || 'blank') + '.');
      }
      _srAnnounce('Read your code.');
    },

    _giveHint: function () {
      // Prefer the hint for the exact build step the learner is on.
      if (this.model.stage === 'activity') {
        var cur = this._currentStep();
        if (cur && cur.hint) { _speak('Hint. ' + cur.hint); _srAnnounce('Hint given'); return; }
      }
      var m = this._module();
      if (!m || !m.hints || !m.hints.length) { _speak('Try saying: give me an example.'); return; }
      var idx = Math.min(this._hintIndex, m.hints.length - 1);
      _speak('Hint. ' + m.hints[idx]);
      _srAnnounce('Hint ' + (idx + 1) + ' of ' + m.hints.length);
      if (this._hintIndex < m.hints.length - 1) this._hintIndex++;
      else _speak('If you would like, say give me an example and I will fill it in for you.');
    },

    _loadExample: function () {
      var m = this._module();
      if (!m) return;
      _setCode(m.example_code);
      // A full example satisfies every build step; jump to the run prompt.
      this._stepIndex = this._steps().length;
      this._lastInstruction = 'Say run code, or press Control and Enter, to run it.';
      this.render();
      this._setStatus('Example loaded. Say: run code.');
      _speak('I have filled in a complete example for you. ' + (m.example_spoken || ''));
      _speak('When you are ready, say run code, or press Control and Enter. Or say practise again to build it yourself.');
      _srAnnounce('Example loaded into the editor.');
      _focusCommandBox();
    },

    _skipToDecision: function () {
      this.model.toDecision();
      var nextId = this.model.nextModuleId();
      var prompt = nextId
        ? 'Okay, we can move on. Say continue to go to the next topic, recap to hear this one again, or exit tutorial to stop.'
        : 'Okay. This was the last topic. Say recap to hear it again, or exit tutorial to start coding.';
      this._lastInstruction = prompt;
      this.render();
      _speak(prompt);
      this._setStatus('Choose: continue, recap, or exit.');
    },

    // ── AI tutor coach ───────────────────────────────────────────────────────
    // Asks the backend coach, which restates KNOWN lesson facts (Key 2 may
    // rephrase them more warmly). On any failure it falls back to a deterministic
    // local line, so the tutorial never depends on AI being reachable.
    _coach: function (requestType, text) {
      var self = this;
      var attempts = (requestType === 'another_hint') ? (this._coachHintIndex || 0) : (this._failCount || 0);
      fetch('/tutorial/coach', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ module: this.model.moduleId, request: requestType, text: text, attempts: attempts })
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (d && d.handled && d.text) { _speak(d.text); _srAnnounce('Coach replied.'); }
          else { self._coachLocalFallback(requestType); }
        })
        .catch(function () { self._coachLocalFallback(requestType); });
      if (requestType === 'another_hint') this._coachHintIndex = (this._coachHintIndex || 0) + 1;
    },

    _coachLocalFallback: function (requestType) {
      if (requestType === 'another_hint') { this._giveHint(); return; }
      if (requestType === 'why_quotes') { _speak('Quotes tell Python the words inside are text to show, not the name of a variable.'); return; }
      if (requestType === 'why_indentation') { _speak('Indentation, the four spaces, means that line belongs inside the block above it.'); return; }
      if (requestType === 'encourage') { _speak('You are doing well. This trips up everyone at first. Let us take it one small step.'); return; }
      var m = this._module();
      if (requestType === 'what_learning' && m) { _speak('Right now you are learning ' + m.title + '. ' + (m.concept || '')); return; }
      if (m && m.concept) { _speak(m.concept); } else { this._giveHint(); }
    },

    // ── utterance interception (returns true if consumed) ────────────────────
    handleUtterance: function (text) {
      if (!this.model.active) return false;
      var low = String(text || '').toLowerCase().trim().replace(/[.!?]+$/, '').replace(/\s+/g, ' ');

      // AI-tutor coach phrases are handled first so "say that again simpler" is
      // coached, not read as a plain repeat. Real coding commands (insert/run)
      // are never coach phrases, so they still flow through untouched.
      var coachReq = TutorialModel.classifyCoachRequest(text);
      if (coachReq) { this._coach(coachReq, text); return true; }

      // "read my code" / "read my program" — read the editor back. Line-specific
      // reads ("read line 2") are NOT consumed; they fall through to the IDE.
      if (low === 'read my code' || low === 'read my program' || low === 'read the code' ||
          low === 'read my program back' || low === 'read it back' || low === 'read back my code') {
        this._readMyCode();
        return true;
      }

      // "skip" / "move on" during an activity -> offer the decision point.
      if (this.model.stage === 'activity' && (low === 'skip' || low === 'skip this' || low === 'skip this topic' || low === 'move on')) {
        this._skipToDecision();
        return true;
      }

      var kind = TutorialModel.classifyDecision(text);
      if (!kind) return false;

      // Tutorial navigation (continue, next, hint, repeat, recap, try again,
      // exit tutorial, start coding) must not collide with narration: stop any
      // current speech through the shared engine BEFORE doing the action.
      if (typeof SpeechManager !== 'undefined' && SpeechManager.cancelAll) {
        SpeechManager.cancelAll();
      }

      switch (kind) {
        case 'exit':   this.exit(true); return true;
        case 'repeat': this._repeatInstruction(); return true;
        case 'hint':   this._giveHint(); return true;
        case 'example': this._loadExample(); return true;
        case 'recap':  this._recap(); return true;
        case 'again':  this._practiceAgain(); return true;
        case 'continue':
          if (this.model.stage === 'decision') { this._continue(); return true; }
          // "continue" said mid-activity: nudge them through the current step.
          var cur = this._currentStep();
          _speak('Let us finish this first. ' + (cur ? cur.prompt : 'Say give me an example if you would like help.'));
          return true;
        default: return false;
      }
    },

    // ── view / panel ─────────────────────────────────────────────────────────
    _showPanel: function () {
      var p = document.getElementById('tutorialOverlay');
      if (p) p.removeAttribute('hidden');
    },
    _hidePanel: function () {
      var p = document.getElementById('tutorialOverlay');
      if (p) p.setAttribute('hidden', '');
    },
    _setStatus: function (msg) {
      var el = document.getElementById('tutorialStatus');
      if (el) el.textContent = msg || '';
    },
    _setText: function (el, txt) { if (el) el.textContent = txt; },

    render: function () {
      if (!this.content || !this.model.active) return;
      var m = this._module();
      if (!m) return;
      this._setText(document.getElementById('tutorialTopic'), m.title);
      this._setText(document.getElementById('tutorialProgress'),
        'Topic ' + this.model.moduleNumber() + ' of ' + this.content.count);
      var stageText = '';
      if (this.model.stage === 'intro') stageText = m.concept;
      else if (this.model.stage === 'activity') stageText = this._lastInstruction || (this._currentStep() && this._currentStep().prompt) || m.task;
      else if (this.model.stage === 'decision') stageText = this._lastInstruction;
      this._setText(document.getElementById('tutorialText'), stageText);

      var inDecision = this.model.stage === 'decision';
      var activityControls = document.getElementById('tutorialActivityControls');
      var decisionControls = document.getElementById('tutorialDecisionControls');
      if (activityControls) { if (inDecision) activityControls.setAttribute('hidden', ''); else activityControls.removeAttribute('hidden'); }
      if (decisionControls) { if (inDecision) decisionControls.removeAttribute('hidden'); else decisionControls.setAttribute('hidden', ''); }

      // On the last topic there is no "next" — relabel the continue button.
      var continueBtn = document.getElementById('tutorialContinueBtn');
      if (continueBtn) {
        continueBtn.textContent = this.model.nextModuleId() ? 'Continue to next topic' : 'Finish tutorial';
      }
    },

    // ── DOM wiring ───────────────────────────────────────────────────────────
    _bind: function () {
      var self = this;
      function on(id, fn) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('click', function (e) { e.preventDefault(); fn(); });
      }
      on('tutorialBtn', function () { self.open(); });
      on('tutorialExitBtn', function () { self.exit(true); });
      on('tutorialRunBtn', function () { if (typeof runCode === 'function') runCode(); else if (window.runCode) window.runCode(); });
      on('tutorialExampleBtn', function () { self._loadExample(); });
      on('tutorialHintBtn', function () { self._giveHint(); });
      on('tutorialRepeatBtn', function () { self._repeatInstruction(); });
      on('tutorialContinueBtn', function () { self._continue(); });
      on('tutorialAgainBtn', function () { self._practiceAgain(); });
      on('tutorialRecapBtn', function () { self._recap(); });
      on('tutorialStopBtn', function () { self.exit(true); });

      // Run hooks from runCode(). Both fire ~1.5–2s after a run so program
      // output is spoken first; we then queue tutorial feedback after it.
      window._tutorialOnRunSuccess = function () { self.onRunResult(true); };
      window._tutorialOnRunError = function () { self.onRunResult(false); };
    }
  };

  // Expose for app.js (voice/typed command routing + restartTutorial).
  window.TutorialController = Controller;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { Controller._bind(); });
  } else {
    Controller._bind();
  }
})();
