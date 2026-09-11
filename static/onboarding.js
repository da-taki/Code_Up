'use strict';

/**
 * CodeUp minimal quick-start onboarding (Vision-Aid build).
 *
 * Deliberately NOT a Python lesson: this teaches the IDE only - find the
 * editor, type one line, run it, hear the output, know Ask CodeUp exists.
 * Five steps, no branching, no insert-command choreography, target
 * completion time about a minute or two.
 *
 * Kept fully separate from static/tutorial.js's TutorialController (which
 * teaches print/variables/if/for/while via a longer voice-command
 * walkthrough backed by the test-locked codeup.learning.tutorial_engine) -
 * that system is untouched and still reachable internally
 * (window.TutorialController.open()), just no longer what "start tutorial"
 * launches. See the action === 'start_tutorial' dispatch in app.js.
 */
(function () {
  if (typeof document === 'undefined' || typeof window === 'undefined') {
    return; // no browser context (e.g. required from a Node test) - nothing to do.
  }

  function _speak(text) {
    if (!text) return;
    try { if (typeof speak === 'function') { speak(text); return; } } catch (e) {}
    try { if (window.speak) window.speak(text); } catch (e) {}
  }
  function _cancelSpeech() {
    try { if (typeof SpeechManager !== 'undefined' && SpeechManager.cancelAll) { SpeechManager.cancelAll(); return; } } catch (e) {}
    try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) {}
  }
  function _setCode(code) {
    try { if (typeof setCode === 'function') return setCode(code, { preserveSpeech: true, source: 'onboarding', allowNonPython: true }); } catch (e) {}
    try { if (window.setCode) return window.setCode(code, { preserveSpeech: true, source: 'onboarding', allowNonPython: true }); } catch (e) {}
    return false;
  }
  function _getCode() {
    try { if (typeof getCode === 'function') return getCode(); } catch (e) {}
    try { if (window.getCode) return window.getCode(); } catch (e) {}
    return '';
  }
  function _focusEditor() {
    try { if (window.editor && window.editor.focus) { window.editor.focus(); return; } } catch (e) {}
    try { if (typeof editor !== 'undefined' && editor && editor.focus) { editor.focus(); return; } } catch (e) {}
  }
  function _focusCommandBox() {
    var el = document.getElementById('voiceText');
    if (el) { try { el.focus(); } catch (e) {} }
  }

  // Steps are deliberately linear (no branching, no per-topic content) -
  // this is IDE orientation, not a curriculum. `waitsForRun: true` means
  // the step only advances from a real Run result (see _onRun below), not
  // from "continue" - the point is to actually run code, not just hear
  // about running code.
  var STEPS = [
    {
      id: 'find_editor',
      status: 'Step 1 of 5 — Find the editor',
      text: 'Welcome to CodeUp. In about a minute you will run your first Python program. This box is the code editor. Say or type continue when you are ready.',
    },
    {
      id: 'type_and_run',
      status: 'Step 2 of 5 — Type and run',
      text: 'In the editor, type: print, open parenthesis, quote, Hello, quote, close parenthesis. Then press Control and Enter to run it. Say fill it in for me if you would rather CodeUp typed it.',
      waitsForRun: true,
    },
    {
      id: 'output',
      status: 'Step 3 of 5 — Program output',
      text: 'That is the Program output area, right below the editor. You just ran your first Python program. Say or type continue to keep going.',
    },
    {
      id: 'ask_codeup',
      status: 'Step 4 of 5 — Ask CodeUp',
      text: 'Whenever you are confused, type or say a question in the Ask CodeUp box - for example, "why is this line indented" or "explain this code". Say or type continue to finish.',
    },
    {
      id: 'finish',
      status: 'Step 5 of 5 — All set',
      text: 'That is everything you need to start: type code, run it, read the output, ask CodeUp when confused. Say or type start tutorial any time to hear this again. Now go write some Python.',
      isFinal: true,
    },
  ];

  function _showPanel() {
    var p = document.getElementById('onboardingOverlay');
    if (p) p.removeAttribute('hidden');
  }
  function _hidePanel() {
    var p = document.getElementById('onboardingOverlay');
    if (p) p.setAttribute('hidden', '');
  }
  function _setText(el, txt) { if (el) el.textContent = txt || ''; }

  var Controller = {
    active: false,
    index: 0,
    _boundRunSuccess: null,
    _boundRunError: null,

    open: function () {
      _cancelSpeech();
      this.active = true;
      this.index = 0;
      _showPanel();
      this._boundRunSuccess = this._onRun.bind(this, true);
      this._boundRunError = this._onRun.bind(this, false);
      window._tutorialOnRunSuccess = this._boundRunSuccess;
      window._tutorialOnRunError = this._boundRunError;
      this._enter();
    },

    _enter: function () {
      var step = STEPS[this.index];
      this._render(step);
      _speak(step.text);
      if (step.id === 'type_and_run') {
        _setCode('');
        _focusEditor();
      } else if (step.id === 'ask_codeup') {
        _focusCommandBox();
      }
    },

    _render: function (step) {
      _setText(document.getElementById('onboardingProgress'), 'Step ' + (this.index + 1) + ' of ' + STEPS.length);
      _setText(document.getElementById('onboardingStatus'), step.status);
      _setText(document.getElementById('onboardingText'), step.text);
      var nextBtn = document.getElementById('onboardingNextBtn');
      if (nextBtn) {
        nextBtn.hidden = !!step.waitsForRun;
        nextBtn.textContent = step.isFinal ? 'Done' : 'Continue';
      }
      var exampleBtn = document.getElementById('onboardingExampleBtn');
      if (exampleBtn) exampleBtn.hidden = step.id !== 'type_and_run';
    },

    next: function () {
      if (!this.active) return;
      var step = STEPS[this.index];
      if (step.waitsForRun) { _speak('Press Control and Enter to run your code first.'); return; }
      if (step.isFinal) { this.close(true); return; }
      this.index++;
      this._enter();
    },

    fillExample: function () {
      if (!this.active || STEPS[this.index].id !== 'type_and_run') return;
      _setCode('print("Hello")');
      _speak('Filled in for you: print, open parenthesis, quote, Hello, quote, close parenthesis. Press Control and Enter to run it.');
      _focusEditor();
    },

    repeat: function () {
      if (!this.active) return;
      _speak(STEPS[this.index].text);
    },

    _onRun: function (ok) {
      if (!this.active) return;
      var step = STEPS[this.index];
      if (!step.waitsForRun) return;
      if (!ok) { _speak('That did not run cleanly - check for a missing quote or parenthesis, then run again.'); return; }
      var code = _getCode();
      if (!/print\s*\(/.test(code)) { _speak('Almost - add a print statement, then run again.'); return; }
      this.index++;
      this._enter();
    },

    close: function (finished) {
      if (!this.active) return;
      this.active = false;
      _hidePanel();
      if (window._tutorialOnRunSuccess === this._boundRunSuccess) window._tutorialOnRunSuccess = null;
      if (window._tutorialOnRunError === this._boundRunError) window._tutorialOnRunError = null;
      _speak(finished ? 'Quick start finished. Happy coding.' : 'Quick start closed. Say start tutorial any time to open it again.');
      _focusEditor();
    },

    /**
     * Classifies a free-form utterance into a quick-start control intent,
     * mirroring TutorialModel.classifyDecision's phrase-matching style so
     * this integrates with the same handleUtterance interception points in
     * app.js. Returns true (consumed) or false (let it fall through to
     * normal IDE commands).
     */
    handleUtterance: function (text) {
      if (!this.active) return false;
      var t = String(text || '').toLowerCase().trim().replace(/[.!?]+$/, '').replace(/\s+/g, ' ');
      if (!t) return false;

      function has(list) {
        for (var i = 0; i < list.length; i++) {
          if (t === list[i] || t.indexOf(list[i]) !== -1) return true;
        }
        return false;
      }

      if (has(['exit tutorial', 'exit quick start', 'stop tutorial', 'skip tutorial',
               'close tutorial', 'quit tutorial', "i'm done", 'i am done']) || t === 'exit' || t === 'skip') {
        this.close(false); return true;
      }
      if (has(['repeat that', 'say that again', 'say it again', 'read it again']) || t === 'repeat') {
        this.repeat(); return true;
      }
      if (has(['fill it in for me', 'fill in an example', 'fill in the example', 'do it for me',
               'type it for me', 'write it for me']) || t === 'example') {
        this.fillExample(); return true;
      }
      if (has(['continue', 'next step', 'go on', 'keep going', 'move on', 'proceed']) ||
          t === 'next' || t === 'yes' || t === 'yeah' || t === 'yep' || t === 'ok' || t === 'okay') {
        this.next(); return true;
      }
      return false;
    },
  };

  window.MinimalOnboarding = Controller;

  function _bind() {
    function on(id, fn) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('click', function (e) { e.preventDefault(); fn(); });
    }
    on('onboardingNextBtn', function () { Controller.next(); });
    on('onboardingExampleBtn', function () { Controller.fillExample(); });
    on('onboardingExitBtn', function () { Controller.close(false); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _bind);
  } else {
    _bind();
  }
})();
