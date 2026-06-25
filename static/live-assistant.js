'use strict';
/**
 * Live Assistant Mode — a controlled, interruptible, non-visual assistant layer.
 *
 * This is a self-contained state machine. All side effects (speech, recognition,
 * status UI) are injected as `deps`, so it can be unit-tested in Node with mocks
 * and never assumes a DOM. app.js wires the real dependencies; index.html shows
 * the status panel.
 *
 * Design rules honoured here:
 *  - Hands-free listening is never on by default; it starts only on an explicit
 *    "start live assistant".
 *  - Commands are routed through the SAME /voice-command path as typed commands
 *    (app.js does that); only assistant *control/meta* commands are handled here.
 *  - The transcript is bounded.
 *  - It never undoes code. "cancel last command" only cancels pending speech.
 */
(function (root) {
  function createLiveAssistant(deps) {
    deps = deps || {};
    var MAX_TURNS = 10;

    var state = {
      assistantEnabled: false,
      listening: false,
      paused: false,
      processing: false,
      speaking: false,
      lastHeardCommand: '',
      lastAssistantResponse: '',
      lastAssistantAction: '',
      recentTurns: [],
      recognitionAvailable: !!deps.recognitionAvailable,
      speechAvailable: deps.speechAvailable !== false,
    };

    function _speak(text) {
      if (text && deps.speak) deps.speak(String(text));
    }

    function statusText() {
      if (!state.assistantEnabled) return 'off';
      if (state.speaking) return 'speaking';
      if (state.processing) return 'processing';
      if (state.paused) return 'paused';
      if (state.listening) return 'listening';
      return 'on';
    }

    function snapshot() {
      return {
        assistantEnabled: state.assistantEnabled,
        listening: state.listening,
        paused: state.paused,
        processing: state.processing,
        speaking: state.speaking,
        lastHeardCommand: state.lastHeardCommand,
        lastAssistantResponse: state.lastAssistantResponse,
        lastAssistantAction: state.lastAssistantAction,
        recentTurns: state.recentTurns.slice(),
        recognitionAvailable: state.recognitionAvailable,
        speechAvailable: state.speechAvailable,
        status: statusText(),
      };
    }

    function _emit() {
      if (deps.onStateChange) {
        try { deps.onStateChange(snapshot()); } catch (e) {}
      }
    }

    // ---- control -------------------------------------------------------

    function start() {
      state.assistantEnabled = true;
      state.paused = false;
      if (state.recognitionAvailable && deps.startListening) {
        deps.startListening();
        state.listening = true;
        _speak('Live assistant on and listening. Say what can I say here for commands.');
      } else {
        state.listening = false;
        _speak('Live assistant on. Speech recognition is not available in this browser, '
               + 'so you can still type commands.');
      }
      _emit();
    }

    function stop() {
      state.assistantEnabled = false;
      state.listening = false;
      state.paused = false;
      if (deps.stopListening) deps.stopListening();
      _speak('Live assistant off.');
      _emit();
    }

    function pauseListening() {
      if (!state.assistantEnabled) return;
      state.paused = true;
      state.listening = false;
      if (deps.stopListening) deps.stopListening();
      _speak('Listening paused. Say resume listening to continue.');
      _emit();
    }

    function resumeListening() {
      if (!state.assistantEnabled) return;
      state.paused = false;
      if (state.recognitionAvailable && deps.startListening) {
        deps.startListening();
        state.listening = true;
        _speak('Listening resumed.');
      } else {
        _speak('Speech recognition is not available. You can type commands.');
      }
      _emit();
    }

    function stopSpeaking() {
      if (deps.cancelSpeech) deps.cancelSpeech();
      state.speaking = false;
      _emit();
    }

    function repeat() {
      if (state.lastAssistantResponse) _speak(state.lastAssistantResponse);
      else _speak('I have not said anything to repeat yet.');
    }

    // Cancels pending speech/processing only. Never undoes code.
    function cancelLast() {
      state.processing = false;
      if (deps.cancelSpeech) deps.cancelSpeech();
      _speak('Cancelled. I did not change your code.');
      _emit();
    }

    // ---- transcript ----------------------------------------------------

    function recordTurn(heard, response, action) {
      if (heard) {
        state.lastHeardCommand = String(heard);
        state.recentTurns.push({ speaker: 'learner', text: String(heard).slice(0, 200), action: '' });
      }
      if (response || action) {
        if (response) state.lastAssistantResponse = String(response);
        if (action) state.lastAssistantAction = String(action);
        state.recentTurns.push({
          speaker: 'codeup',
          text: String(response || '').slice(0, 400),
          action: String(action || ''),
        });
      }
      // Keep at most MAX_TURNS exchanges (learner + codeup entries).
      while (state.recentTurns.length > MAX_TURNS * 2) state.recentTurns.shift();
      _emit();
    }

    function noteListening(on) { state.listening = !!on; _emit(); }
    function noteProcessing(on) { state.processing = !!on; _emit(); }
    function noteSpeaking(on) { state.speaking = !!on; _emit(); }

    // ---- context helpers ----------------------------------------------

    function modeName(mode) {
      return (mode === 'audio_blocks') ? 'Audio Blocks Mode' : 'Python Code Mode';
    }

    function helpText(mode) {
      if (mode === 'audio_blocks') {
        return 'In Audio Blocks Mode you can say: add print block, set message to hello, '
             + 'compile blocks, run, transfer blocks to Python mode, list blocks. '
             + 'Say what mode am I in to check your mode.';
      }
      return 'You can say: project map, show program state, what variables exist, '
           + 'step through this, explain error, what changed, read before and after, run, '
           + 'analyze. Say where am I or what mode am I in to get your bearings.';
    }

    function whereAmI(ctx) {
      ctx = ctx || {};
      var parts = ['You are in ' + modeName(ctx.mode) + '.'];
      if (ctx.file) parts.push('The current file is ' + ctx.file + '.');
      if (state.lastHeardCommand) parts.push('Your last command was ' + state.lastHeardCommand + '.');
      parts.push('Say what can I say here for commands.');
      return parts.join(' ');
    }

    // ---- meta-command interception ------------------------------------
    // Returns true if the text was an assistant control/meta command (handled
    // here, NOT sent to /voice-command). Everything else returns false and flows
    // through the normal command path.

    function handleMetaCommand(text) {
      var t = String(text || '').toLowerCase().trim().replace(/\s+/g, ' ');
      if (!t) return false;

      // "start" works even when the assistant is off (that is how you turn it on).
      if (/^(start|enable|turn on)( the)? live assistant$/.test(t) || t === 'start assistant') {
        start();
        return true;
      }
      // When the assistant is off, nothing else is intercepted: ordinary commands
      // keep their existing behaviour.
      if (!state.assistantEnabled) return false;

      if (/^(stop|disable|turn off)( the)? live assistant$/.test(t) || t === 'stop assistant') {
        stop(); return true;
      }
      if (t === 'pause listening' || t === 'pause') { pauseListening(); return true; }
      if (t === 'resume listening' || t === 'resume') { resumeListening(); return true; }
      if (t === 'stop speaking' || t === 'stop talking' || t === 'be quiet' || t === 'quiet') {
        stopSpeaking(); return true;
      }
      if (t === 'repeat that' || t === 'repeat last response' || t === 'say that again'
          || t === 'what did you just say') { repeat(); return true; }
      if (t === 'what did you hear' || t === 'what did i just ask' || t === 'what did i ask'
          || t === 'what did i say' || t === 'what did i just say') {
        _speak(state.lastHeardCommand
          ? ('You last asked: ' + state.lastHeardCommand + '.')
          : 'I have not heard a command yet.');
        return true;
      }
      if (t === 'what did you do' || t === 'what did you just do') {
        _speak(state.lastAssistantResponse
          ? ('I said: ' + state.lastAssistantResponse)
          : 'I have not done anything yet.');
        return true;
      }
      if (t === 'cancel last command' || t === 'cancel') { cancelLast(); return true; }
      if (t === 'what can i say here' || t === 'what can i say') {
        _speak(helpText(deps.getMode ? deps.getMode() : 'python')); return true;
      }
      if (t === 'where am i') {
        _speak(whereAmI({
          mode: deps.getMode ? deps.getMode() : 'python',
          file: deps.getFile ? deps.getFile() : '',
        }));
        return true;
      }
      if (t === 'what mode am i in') {
        _speak(modeName(deps.getMode ? deps.getMode() : 'python')); return true;
      }
      return false;
    }

    _emit();

    return {
      start: start, stop: stop,
      pauseListening: pauseListening, resumeListening: resumeListening,
      stopSpeaking: stopSpeaking, repeat: repeat, cancelLast: cancelLast,
      handleMetaCommand: handleMetaCommand, recordTurn: recordTurn,
      noteListening: noteListening, noteProcessing: noteProcessing, noteSpeaking: noteSpeaking,
      modeName: modeName, helpText: helpText, whereAmI: whereAmI,
      getState: snapshot,
    };
  }

  root.createLiveAssistant = createLiveAssistant;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { createLiveAssistant: createLiveAssistant };
  }
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
