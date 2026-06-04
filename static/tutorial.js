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

  // Export the pure model for Node-based unit testing. Nothing below this point
  // runs under Node, because it all depends on document/window.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TutorialModel: TutorialModel };
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
  function _safeStorage(fn) { try { return fn(); } catch (e) { return null; } }

  // ─── FALLBACK CONTENT (used only if /tutorial/modules cannot be fetched) ──
  var FALLBACK_CONTENT = {
    order: ['print', 'variables', 'if', 'for', 'while'],
    count: 5,
    modules: {
      print: {
        id: 'print', order: 1, title: 'Print statements',
        concept: 'A print statement makes Python say something in the output. Write print, then brackets, with your message in quotes inside.',
        example_code: 'print("Hello world")',
        example_spoken: 'For example: print, open bracket, quote, Hello world, quote, close bracket.',
        task: 'Now you try. Write one line that prints any short message. Press Control and Enter to run. Say give me an example for help.',
        hints: ['Start with the word print and an opening bracket.', 'Put your message in double quotes.', 'Close the bracket at the end.'],
        success: 'Nicely done. You made Python speak with a print statement.',
        recap: 'Recap: print, then brackets, then your message in quotes.'
      },
      variables: {
        id: 'variables', order: 2, title: 'Variables',
        concept: 'A variable gives a name to information. Choose a name, an equals sign, then a value.',
        example_code: 'name = "Aman"\nprint(name)',
        example_spoken: 'For example: name equals quote Aman quote, then print bracket name bracket.',
        task: 'Store any word or number in a variable, then print that variable. Press Control and Enter to run.',
        hints: ['Line one: a name, equals, a value.', 'Line two: print your variable by name.', 'For example: score equals 10, then print bracket score bracket.'],
        success: 'Well done. You stored a value and printed it back.',
        recap: 'Recap: a variable is a name, equals, and a value.'
      },
      if: {
        id: 'if', order: 3, title: 'If statements',
        concept: 'An if statement makes a choice. It runs the indented lines underneath only when its condition is true.',
        example_code: 'age = 18\nif age >= 18:\n    print("You can vote")',
        example_spoken: 'For example: age equals 18, then if age greater-or-equal 18 colon, then an indented print.',
        task: 'Set a variable, then write an if statement with a colon, and an indented print underneath. Press Control and Enter to run.',
        hints: ['Set a variable first, like x equals 10.', 'The if line ends with a colon.', 'Indent the print under the if by four spaces.'],
        success: 'Great work. Your if statement made a decision.',
        recap: 'Recap: if checks a condition, ends with a colon, and indents what runs.'
      },
      for: {
        id: 'for', order: 4, title: 'For loops',
        concept: 'A for loop repeats an action a set number of times. The repeated lines are indented under the for line.',
        example_code: 'for number in range(3):\n    print(number)',
        example_spoken: 'For example: for number in range bracket 3 bracket colon, then an indented print.',
        task: 'Write a for loop that prints something a few times. Use range bracket 3 bracket to repeat three times. Press Control and Enter to run.',
        hints: ['Start with for, a name, in range, a number, then a colon.', 'Indent the repeated line four spaces.', 'For example: for i in range 3 colon, then print bracket i bracket.'],
        success: 'Excellent. Your for loop repeated and printed each time.',
        recap: 'Recap: a for loop with range repeats a set number of times.'
      },
      while: {
        id: 'while', order: 5, title: 'While loops',
        concept: 'A while loop repeats while a condition stays true. Use a counter that changes so it stops safely.',
        example_code: 'count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1',
        example_spoken: 'For example: count equals 1, while count less-or-equal 3 colon, an indented print, then count equals count plus 1.',
        task: 'Start a counter, write a while loop, print the counter inside, and increase it so the loop ends. Press Control and Enter to run.',
        hints: ['Set a counter first, like count equals 1.', 'The while line ends with a colon.', 'Most important: change the counter inside the loop, like count equals count plus 1.'],
        success: 'Brilliant. Your while loop counted up and stopped safely. That completes the last topic.',
        recap: 'Recap: a while loop repeats while true; always change the counter so it stops.'
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

    get active() { return this.model.active; },
    get step() { return this.model.moduleNumber(); },  // legacy compat

    _module: function () {
      if (!this.content) return null;
      return this.content.modules[this.model.moduleId] || null;
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
        _speak('Welcome to the CodeUp guided tutorial. I will help you write your first Python programs using speech and sound. We will begin with print statements. After each topic, you can stop, practise again, or continue. You are always in control.' + completedNote);
        _speak('At any time you can say, or type: repeat, to hear the instructions again. hint, for a clue. give me an example, to fill in code for you. run, to run your code. or exit tutorial, to stop. You can also press Tab to reach the tutorial buttons.');
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
      var m = this._module();
      if (!m) return;
      // Fresh editor for the activity so the learner starts clean.
      _setCode('');
      this._lastInstruction = m.task;
      this.render();
      this._setStatus('Activity: ' + m.title + '. Write your code, then Run.');
      _speak(m.task);
      _speak('The editor is ready. Type your code, then press Control and Enter to run.');
      _srAnnounce('Activity: ' + m.title + '. Editor ready.');
      _focusEditor();
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
          self._celebrateAndDecide(res.feedback);
        } else {
          if (res.feedback) _speak(res.feedback);
          if (res.hint) _speak('Here is a hint. ' + res.hint);
          _speak('Adjust your code and run again, or say give me an example.');
          self._setStatus('Keep trying — listen to the hint, then run again.');
          _srAnnounce('Not yet. Hint given.');
          _focusEditor();
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
        _speak(m.task);
      }
      _srAnnounce('Recap given.');
    },

    _repeatInstruction: function () {
      _speak(this._lastInstruction || 'There is nothing to repeat yet.');
      _srAnnounce('Repeating instructions.');
    },

    _giveHint: function () {
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
      _speak('I have filled in an example. Press Control and Enter to run it, or change it first.');
      _srAnnounce('Example loaded into the editor.');
      _focusEditor();
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

    // ── utterance interception (returns true if consumed) ────────────────────
    handleUtterance: function (text) {
      if (!this.model.active) return false;
      var kind = TutorialModel.classifyDecision(text);
      var low = String(text || '').toLowerCase().trim();

      // "skip" / "move on" during an activity -> offer the decision point.
      if (this.model.stage === 'activity' && (low === 'skip' || low === 'skip this' || low === 'skip this topic' || low === 'move on')) {
        this._skipToDecision();
        return true;
      }

      if (!kind) return false;

      switch (kind) {
        case 'exit':   this.exit(true); return true;
        case 'repeat': this._repeatInstruction(); return true;
        case 'hint':   this._giveHint(); return true;
        case 'example': this._loadExample(); return true;
        case 'recap':  this._recap(); return true;
        case 'again':  this._practiceAgain(); return true;
        case 'continue':
          if (this.model.stage === 'decision') { this._continue(); return true; }
          // "continue" said mid-activity: nudge them to run instead.
          _speak('Finish this activity first. Write your code, then press Control and Enter to run. Say give me an example if you would like help.');
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
      else if (this.model.stage === 'activity') stageText = m.task;
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
