'use strict';
/*
 * Browser-speech regression test for the "only speaks the end of the output"
 * accessibility bug.
 *
 * It loads the REAL static/voice-engine.js inside a vm sandbox with a mocked
 * speechSynthesis + a controllable clock, then reproduces the exact sequence the
 * command handler performs for every learning response:
 *
 *     VoiceEngine.cancelSpeech();   // handleConfirmedAction -> SpeechManager.cancelAll()
 *     VoiceEngine.speak(response);  // the handler then speaks the full response
 *
 * The bug: cancelSpeech() schedules deferred speechSynthesis.cancel() calls
 * (Chrome quirk) at 50/150/300 ms. Those used to fire into the NEW utterance and
 * cut off its first chunks, so the learner only heard the final sentence. We
 * assert on the ACTUAL text handed to speechSynthesis.speak(): the first
 * utterance must start at the BEGINNING of the response, and no started chunk may
 * be cancelled by a stale timer.
 *
 * Run with plain Node (no framework). Bridged into pytest by
 * tests/test_voice_speech_frontend.py (skipped if node is unavailable).
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ── Controllable clock ───────────────────────────────────────────────────────
let _now = 1000000;
let _timerSeq = 1;
let _timers = [];
function fakeSetTimeout(fn, delay) {
  const id = _timerSeq++;
  _timers.push({ id, time: _now + (Number(delay) || 0), fn });
  return id;
}
function fakeClearTimeout(id) {
  _timers = _timers.filter(t => t.id !== id);
}
function advance(ms) {
  const target = _now + ms;
  // Execute due timers in chronological order; timers may schedule more timers.
  // Guard against runaway loops.
  for (let guard = 0; guard < 100000; guard++) {
    _timers.sort((a, b) => a.time - b.time);
    if (!_timers.length || _timers[0].time > target) break;
    const t = _timers.shift();
    _now = t.time;
    try { t.fn(); } catch (e) { /* swallow, mirrors browser */ }
  }
  _now = target;
}

// ── Mocked Web Speech API ────────────────────────────────────────────────────
const SPEECH_MS = 1000; // each utterance "takes" 1s of natural speech (> 300ms)
let spokenLog;          // every text handed to speechSynthesis.speak()
let completedLog;       // utterances that finished naturally (onend)
let cancelledLog;       // utterances cut off mid-flight by cancel()
let _current = null;

function resetLogs() {
  spokenLog = [];
  completedLog = [];
  cancelledLog = [];
  _current = null;
}

const speechSynthesis = {
  speaking: false,
  pending: false,
  paused: false,
  speak(utt) {
    spokenLog.push(utt.text);
    _current = utt;
    this.speaking = true;
    utt._endTimer = fakeSetTimeout(() => {
      if (_current === utt) {
        _current = null;
        speechSynthesis.speaking = false;
        completedLog.push(utt.text);
        if (typeof utt.onend === 'function') utt.onend();
      }
    }, SPEECH_MS);
  },
  cancel() {
    if (_current) {
      const utt = _current;
      fakeClearTimeout(utt._endTimer);
      _current = null;
      this.speaking = false;
      cancelledLog.push(utt.text);
      if (typeof utt.onerror === 'function') utt.onerror({ error: 'canceled' });
    }
  },
  pause() {}, resume() {},
  getVoices() { return []; },
  addEventListener() {},
};
function SpeechSynthesisUtterance(text) {
  this.text = text;
  this.lang = ''; this.rate = 1; this.pitch = 1; this.voice = null;
  this.onstart = null; this.onend = null; this.onerror = null;
}

// ── Sandbox window/document ──────────────────────────────────────────────────
const _store = {};
const localStorage = {
  getItem: k => (k in _store ? _store[k] : null),
  setItem: (k, v) => { _store[k] = String(v); },
  removeItem: k => { delete _store[k]; },
};
const documentMock = {
  readyState: 'complete',
  getElementById: () => null,
  addEventListener: () => {},
};
const windowMock = {
  speechSynthesis,
  SpeechSynthesisUtterance,
  localStorage,
  document: documentMock,
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  addEventListener: () => {},
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
};

const sandbox = {
  window: windowMock,
  document: documentMock,
  localStorage,
  speechSynthesis,
  SpeechSynthesisUtterance,
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  console: { log() {}, warn() {}, error() {} },
  Date,
  Math,
  Promise,
  Array,
  Object,
  String,
  JSON,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'voice-engine.js'), 'utf8');
vm.runInContext(src + '\nthis.VoiceEngine = VoiceEngine;', sandbox);
const VoiceEngine = sandbox.VoiceEngine;
assert(VoiceEngine && typeof VoiceEngine.speak === 'function', 'VoiceEngine not exposed');

let groups = 0;
function check(name, fn) {
  try { fn(); groups++; }
  catch (err) { console.error('FAILED: ' + name); console.error(err && err.message ? err.message : err); process.exit(1); }
}

function firstWords(text, n) {
  return String(text).split(/\s+/).slice(0, n).join(' ');
}

// The real onboarding/help response. Output box shows all of it; the learner used
// to only hear the final "Say more examples..." sentence.
const HELP = 'You can build Python by speaking or typing. Try commands like generate code ' +
  'to print even numbers, insert print hello, or put a loop in the editor. Then run code, ' +
  'say explain it for an explanation, debug errors, or start tutorial for a guided ' +
  'walkthrough. Say more examples for a longer list.';

// A long, project-report style response (many chunks).
const REPORT = 'Project report ready. This is a single-file Python program. It uses a for loop ' +
  'to repeat a print statement three times. The loop is controlled by range three, so the values ' +
  'are zero, one, and two. The indented print line is the action the loop repeats. The last ' +
  'successful output was zero, one, two. Concepts used include loops and print output. Run it by ' +
  'pressing Control Enter. Say more to hear next steps.';

// 1. The reproduction: cancel (as the handler does) then speak the full response.
//    First spoken utterance must START at the beginning; nothing cancelled.
check('help: first utterance starts at the beginning, not the end', () => {
  resetLogs();
  VoiceEngine.cancelSpeech();
  VoiceEngine.speak(HELP);
  advance(60000);
  assert(spokenLog.length > 0, 'nothing was spoken');
  assert(spokenLog[0].startsWith('You can build Python'),
    'first utterance did not start at the beginning: ' + JSON.stringify(spokenLog[0]));
  assert.strictEqual(cancelledLog.length, 0,
    'a started chunk was cancelled by a stale timer: ' + JSON.stringify(cancelledLog));
  // The learner must NOT only hear the final sentence.
  assert(!(completedLog.length === 1 && completedLog[0].startsWith('Say more examples')),
    'only the final sentence was spoken');
  // First audible chunk begins the response; full text covered start..end.
  assert(completedLog[0].startsWith('You can build Python'), 'first completed chunk was not the opening');
  assert(completedLog.join(' ').includes('Say more examples for a longer list'), 'ending was dropped');
});

// 2. Long report: chunked, but still starts at the beginning with no early cut-off.
check('report: long response speaks from the beginning through to the end', () => {
  resetLogs();
  VoiceEngine.cancelSpeech();
  VoiceEngine.speak(REPORT);
  advance(120000);
  assert(spokenLog[0].startsWith('Project report ready'),
    'first utterance was not the report opening: ' + JSON.stringify(spokenLog[0]));
  assert.strictEqual(cancelledLog.length, 0,
    'a started chunk was cancelled mid-flight: ' + JSON.stringify(cancelledLog));
  assert(completedLog.length >= 2, 'expected the long report to be chunked');
  assert(completedLog[0].startsWith('Project report ready'), 'first audible chunk was not the opening');
  assert(completedLog[completedLog.length - 1].includes('Say more to hear next steps'),
    'final chunk (next steps) was dropped');
});

// 3. A genuine stop with no follow-up speak must still silence everything: the
//    deferred cancels are only neutralised when new narration actually starts.
check('cancelSpeech with no follow-up still stops in-flight speech', () => {
  resetLogs();
  VoiceEngine.speak(REPORT);
  advance(10); // let the first chunk start
  assert.strictEqual(spokenLog.length, 1, 'expected one chunk to have started');
  VoiceEngine.cancelSpeech();
  advance(60000);
  assert(cancelledLog.length >= 1, 'cancelSpeech failed to stop the in-flight chunk');
  // No further chunks resumed after the cancel.
  assert.strictEqual(spokenLog.length, 1, 'a queued chunk resumed after a real stop');
});

// 4. Rapid re-speak (barge-in style): two cancel+speak cycles back to back. The
//    SECOND response must start at its beginning and not be cut off by the first
//    cycle's stale cancels.
check('back-to-back cancel+speak keeps the latest response intact from the start', () => {
  resetLogs();
  VoiceEngine.cancelSpeech();
  VoiceEngine.speak(HELP);
  advance(20);
  VoiceEngine.cancelSpeech();
  VoiceEngine.speak(REPORT);
  advance(120000);
  const reportStartIdx = spokenLog.findIndex(t => t.startsWith('Project report ready'));
  assert(reportStartIdx !== -1, 'second response never started at its beginning');
  // Everything spoken at/after the report's first chunk belongs to the report and
  // completes without being cancelled.
  const reportChunks = completedLog.filter(t => /report|loop|range|output|loops|next steps/i.test(t));
  assert(reportChunks.length >= 2, 'second response was not fully spoken');
  assert(completedLog.some(t => t.includes('Say more to hear next steps')),
    'second response ending was dropped');
});

console.log(groups + ' groups passed');
