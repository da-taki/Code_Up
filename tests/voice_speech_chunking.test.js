'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

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
function fakeSetInterval(fn, delay) {
  const id = _timerSeq++;
  _timers.push({ id, time: _now + (Number(delay) || 0), fn, interval: Number(delay) || 0 });
  return id;
}
function fakeClearInterval(id) {
  _timers = _timers.filter(t => t.id !== id);
}
function advance(ms) {
  const target = _now + ms;
  for (let guard = 0; guard < 100000; guard++) {
    _timers.sort((a, b) => a.time - b.time);
    if (!_timers.length || _timers[0].time > target) break;
    const t = _timers.shift();
    _now = t.time;
    if (t.interval) _timers.push({ id: t.id, time: _now + t.interval, fn: t.fn, interval: t.interval });
    try { t.fn(); } catch (e) {  }
  }
  _now = target;
}

const SPEECH_MS = 1000;
let _utteranceDurationMs = SPEECH_MS; // how long the mock takes to "finish" an utterance
let spokenLog;          // every text handed to speechSynthesis.speak()
let completedLog;       // utterances that finished naturally (onend)
let cancelledLog;
let pauseResumeLog;     // timestamps of pause()+resume() "kick" pairs while speaking
let _current = null;

function resetLogs() {
  spokenLog = [];
  completedLog = [];
  cancelledLog = [];
  pauseResumeLog = [];
  _current = null;
  _utteranceDurationMs = SPEECH_MS;
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
    }, _utteranceDurationMs);
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
  pause() { this._pausedAt = _now; },
  resume() {
    if (this._pausedAt === _now) pauseResumeLog.push(_now);
    this._pausedAt = null;
  },
  getVoices() { return []; },
  addEventListener() {},
};
function SpeechSynthesisUtterance(text) {
  this.text = text;
  this.lang = ''; this.rate = 1; this.pitch = 1; this.voice = null;
  this.onstart = null; this.onend = null; this.onerror = null;
}

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
  setInterval: fakeSetInterval,
  clearInterval: fakeClearInterval,
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
  setInterval: fakeSetInterval,
  clearInterval: fakeClearInterval,
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

const HELP = 'You can build Python by speaking or typing. Try commands like generate code ' +
  'to print even numbers, insert print hello, or put a loop in the editor. Then run code, ' +
  'say explain it for an explanation, debug errors, or start tutorial for a guided ' +
  'walkthrough. Say more examples for a longer list.';

const REPORT = 'Project report ready. This is a single-file Python program. It uses a for loop ' +
  'to repeat a print statement three times. The loop is controlled by range three, so the values ' +
  'are zero, one, and two. The indented print line is the action the loop repeats. The last ' +
  'successful output was zero, one, two. Concepts used include loops and print output. Run it by ' +
  'pressing Control Enter. Say more to hear next steps.';

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
  assert(!(completedLog.length === 1 && completedLog[0].startsWith('Say more examples')),
    'only the final sentence was spoken');
  assert(completedLog[0].startsWith('You can build Python'), 'first completed chunk was not the opening');
  assert(completedLog.join(' ').includes('Say more examples for a longer list'), 'ending was dropped');
});

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

check('cancelSpeech with no follow-up still stops in-flight speech', () => {
  resetLogs();
  VoiceEngine.speak(REPORT);
  advance(10); // let the first chunk start
  assert.strictEqual(spokenLog.length, 1, 'expected one chunk to have started');
  VoiceEngine.cancelSpeech();
  advance(60000);
  assert(cancelledLog.length >= 1, 'cancelSpeech failed to stop the in-flight chunk');
  assert.strictEqual(spokenLog.length, 1, 'a queued chunk resumed after a real stop');
});

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
  const reportChunks = completedLog.filter(t => /report|loop|range|output|loops|next steps/i.test(t));
  assert(reportChunks.length >= 2, 'second response was not fully spoken');
  assert(completedLog.some(t => t.includes('Say more to hear next steps')),
    'second response ending was dropped');
});

// ---------------------------------------------------------------------------
// XRCVC "speech stops mid-output" (Version 2, reproduced with a prime-number
// list read aloud stopping around 29): Chrome's speechSynthesis silently
// stops producing audio - `speaking` stays true, neither onend nor onerror
// ever fires - once a single utterance has been playing for roughly 15
// seconds. VoiceEngine now runs a pause()+resume() "keep-alive" every 10s
// for as long as an utterance is in flight, which resets Chrome's internal
// timer without audibly interrupting playback.
// ---------------------------------------------------------------------------

const LONG_SINGLE_CHUNK = 'Program output: ' +
  'this single utterance stays under the two hundred sixty character chunk ' +
  'limit so it is spoken as one chunk, but a slow speech rate can still make ' +
  'it take thirty or more seconds to finish playing out loud';
assert(LONG_SINGLE_CHUNK.length <= 260, 'test fixture must stay a single chunk');
// A slower rate (accessibility settings allow 0.5x-2.0x, see applySpeechRate())
// widens VoiceEngine's own rate-aware "stuck utterance" safety timeout far
// past the mock's chosen completion time, so these tests exercise only the
// keep-alive interval and never race against that other safety net.
const SLOW_RATE = 0.5;

check('a long single chunk gets periodic keep-alive kicks while it plays', () => {
  resetLogs();
  _utteranceDurationMs = 40000; // slower than Chrome's ~15s silent-stop bug
  VoiceEngine.speak(LONG_SINGLE_CHUNK, { rate: SLOW_RATE });
  advance(39000); // just before the mock's onend fires
  assert.strictEqual(spokenLog.length, 1, 'expected exactly one chunk to have started');
  assert.strictEqual(completedLog.length, 0, 'utterance finished earlier than the mock allows');
  assert(pauseResumeLog.length >= 3,
    'expected at least 3 keep-alive kicks (~10s/20s/30s) during a 39s utterance, got ' + pauseResumeLog.length);
  advance(2000); // let onend fire
  assert.strictEqual(completedLog.length, 1, 'the long chunk never completed');
  assert.strictEqual(completedLog[0], LONG_SINGLE_CHUNK, 'completed text does not match what was spoken');
});

check('keep-alive kicks stop once the utterance finishes (no lingering interval)', () => {
  resetLogs();
  _utteranceDurationMs = 40000;
  VoiceEngine.speak(LONG_SINGLE_CHUNK, { rate: SLOW_RATE });
  advance(41000); // let it finish naturally
  const kicksAtFinish = pauseResumeLog.length;
  advance(60000); // nothing queued afterwards - no utterance should be in flight
  assert.strictEqual(pauseResumeLog.length, kicksAtFinish,
    'keep-alive kept firing after speech finished - the interval was not cleared');
});

check('keep-alive kicks stop once speech is cancelled mid-utterance', () => {
  resetLogs();
  _utteranceDurationMs = 40000;
  VoiceEngine.speak(LONG_SINGLE_CHUNK, { rate: SLOW_RATE });
  advance(12000); // let one kick happen
  assert(pauseResumeLog.length >= 1, 'expected at least one kick before cancelling');
  VoiceEngine.cancelSpeech();
  const kicksAtCancel = pauseResumeLog.length;
  advance(60000);
  assert.strictEqual(pauseResumeLog.length, kicksAtCancel,
    'keep-alive kept firing after cancelSpeech() - the interval was not cleared');
});

check("a slow speech rate does not make VoiceEngine's own stuck-utterance timeout cut speech off early", () => {
  // Regression for the second half of the same XRCVC bug: the safety
  // timeout used to be a fixed `text.length * 100`ms estimate that ignored
  // the utterance's own rate, so a user who slowed CodeUp's speech down for
  // accessibility could have VoiceEngine itself cancel speech that was
  // still legitimately playing, before Chrome's real bug ever entered the
  // picture.
  resetLogs();
  _utteranceDurationMs = 40000; // realistic time to actually speak this text at 0.5x
  VoiceEngine.speak(LONG_SINGLE_CHUNK, { rate: SLOW_RATE });
  advance(39999);
  assert.strictEqual(cancelledLog.length, 0,
    "VoiceEngine's own safety timeout cancelled a slow-rate utterance before it could finish playing");
  advance(2);
  assert.strictEqual(completedLog.length, 1, 'the slow-rate utterance never completed naturally');
});

console.log(groups + ' groups passed');
