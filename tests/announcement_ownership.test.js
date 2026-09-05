'use strict';
// Behavioral regression tests for the XRCVC announcement-deduplication pass.
//
// Rather than grepping for the literal string "{sr:false}" (which proves
// nothing about actual runtime behavior and would pass even if the logic
// were subtly wrong), this loads the *real* out(), updateCommandUnderstanding(),
// and showAI() function bodies straight out of static/app.js into a small
// mock-DOM sandbox and drives them like a browser would, asserting on what
// actually got announced - matching this repo's existing convention (see
// voice_speech_chunking.test.js) of testing real source against a minimal
// harness instead of reimplementing the logic as a second copy that could
// drift from the shipped code.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

function extract(startMarker, endMarker) {
  const start = APP_JS.indexOf(startMarker);
  assert(start !== -1, 'start marker not found: ' + startMarker);
  const end = APP_JS.indexOf(endMarker, start);
  assert(end !== -1, 'end marker not found: ' + endMarker);
  return APP_JS.slice(start, end);
}

const SHOW_AI_SRC = extract('let _aiBubbleAnnounceRestoreTimer = null;', 'function hideAI()');
const OUT_SRC = extract('function out(t, options = {})', 'let _cuAnnounceRestoreTimer = null;');
const UPDATE_CU_SRC = extract('let _cuAnnounceRestoreTimer = null;', 'window.updateTranscriptStatus');

// --- Fake timers (same pattern as voice_speech_chunking.test.js) ---------
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
  for (let guard = 0; guard < 100000; guard++) {
    _timers.sort((a, b) => a.time - b.time);
    if (!_timers.length || _timers[0].time > target) break;
    const t = _timers.shift();
    _now = t.time;
    try { t.fn(); } catch (e) {}
  }
  _now = target;
}

// --- Minimal mock DOM ------------------------------------------------------
function makeEl(initial) {
  return Object.assign({
    _text: '',
    _attrs: {},
    hidden: true,
    style: {},
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this._attrs, name) ? this._attrs[name] : null; },
    setAttribute(name, v) { this._attrs[name] = String(v); },
    removeAttribute(name) { delete this._attrs[name]; },
  }, initial || {});
}

// A single, shared element registry - every test mutates *this*, and
// getElementById always reads from it, so no test's setup can leak a
// stale override into a later, unrelated test (a real bug caught while
// writing this file: overriding getElementById per-test left later tests
// reading a null #aiBubble).
let elements = {};
let outputEl, aiBubbleEl;
let srAnnounceCalls;
let _browserSpeechEnabled;

const sandbox = {
  document: {
    getElementById(id) { return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null; },
  },
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  srAnnounce(text, priority) { srAnnounceCalls.push({ text, priority }); },
  showEl(el) { if (el) el.hidden = false; },
  get _browserSpeechEnabled() { return _browserSpeechEnabled; },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(OUT_SRC + '\n' + UPDATE_CU_SRC + '\n' + SHOW_AI_SRC, sandbox);

function reset(browserSpeechEnabled) {
  outputEl = makeEl({ _attrs: { 'aria-live': browserSpeechEnabled ? 'off' : 'polite' } });
  aiBubbleEl = makeEl({ _attrs: { 'aria-live': 'polite' } });
  elements = { output: outputEl, aiBubble: aiBubbleEl };
  srAnnounceCalls = [];
  _browserSpeechEnabled = browserSpeechEnabled;
}

let groups = 0;
function check(name, fn) {
  try { fn(); groups++; }
  catch (err) { console.error('FAILED: ' + name); console.error(err && err.stack ? err.stack : err); process.exit(1); }
}

// ---------------------------------------------------------------------------
// out()
// ---------------------------------------------------------------------------

check('out() in Screen Reader Safe mode relies on #output\'s own aria-live, not a manual announce', () => {
  reset(false);
  sandbox.out('Hello from a plain call');
  assert.strictEqual(outputEl.textContent, 'Hello from a plain call', 'visible text must still be written');
  assert.strictEqual(srAnnounceCalls.length, 0,
    'out() manually announced on top of #output\'s own aria-live - that is the exact duplicate this pass fixed');
});

check('out() in CodeUp Voice mode still manually announces (its aria-live is off there)', () => {
  reset(true);
  sandbox.out('Hello in CodeUp Voice mode');
  assert.strictEqual(srAnnounceCalls.length, 1,
    '#output\'s aria-live is off in this mode, so out()\'s manual announce is the only signal - must not be removed');
  assert.strictEqual(srAnnounceCalls[0].text, 'Hello in CodeUp Voice mode');
});

check('out(text, {sr:false}) never manually announces in either mode', () => {
  reset(false);
  sandbox.out('quiet one', { sr: false });
  reset(true);
  sandbox.out('quiet two', { sr: false });
  assert.strictEqual(srAnnounceCalls.length, 0);
});

check('out() still escalates to assertive priority for error text in CodeUp Voice mode', () => {
  reset(true);
  sandbox.out('Fix failed.');
  assert.strictEqual(srAnnounceCalls.length, 1);
  assert.strictEqual(srAnnounceCalls[0].priority, 'assertive');
});

// ---------------------------------------------------------------------------
// updateCommandUnderstanding({..., announce: false})
// ---------------------------------------------------------------------------

function withCommandUnderstanding() {
  const container = makeEl({ _attrs: { 'aria-live': 'polite' } });
  const nextEl = makeEl({});
  elements.commandUnderstanding = container;
  elements.nextCommandAction = nextEl;
  return container;
}

check('announce:false silences the native aria-live for that write, then restores it', () => {
  reset(false);
  const container = withCommandUnderstanding();
  sandbox.updateCommandUnderstanding({ nextAction: 'Action: deterministic message.', announce: false });
  assert.strictEqual(container.getAttribute('aria-live'), 'off',
    'a real screen reader must not announce this generic label - the richer companion out()/speak() owns this event');
  advance(60);
  assert.strictEqual(container.getAttribute('aria-live'), 'polite',
    'must self-heal back to polite so the *next* (non-suppressed) update is still announced');
});

check('omitting announce (the default) never touches aria-live - the failure-catch-block path stays the sole owner', () => {
  reset(false);
  const container = withCommandUnderstanding();
  sandbox.updateCommandUnderstanding({ nextAction: 'Command failed. Try again.', isError: true });
  assert.strictEqual(container.getAttribute('aria-live'), 'polite',
    'a failure update has no richer companion announcement - suppressing it here would be a silent failure');
});

check('two announce:false updates close together do not get stuck "off" (the real bug found and fixed this pass)', () => {
  reset(false);
  const container = withCommandUnderstanding();
  sandbox.updateCommandUnderstanding({ nextAction: 'first', announce: false });
  advance(10); // well before the first restore (50ms) fires
  sandbox.updateCommandUnderstanding({ nextAction: 'second', announce: false });
  advance(100); // past both restores
  assert.strictEqual(container.getAttribute('aria-live'), 'polite',
    'stuck at "off" would silence every future command status update, forever, for this whole session');
});

// ---------------------------------------------------------------------------
// showAI({announce:false})
// ---------------------------------------------------------------------------

check('showAI(msg, {announce:false}) silences #aiBubble\'s own aria-live for that call, then restores it', () => {
  reset(false);
  sandbox.showAI('Fixing code with AI...', { announce: false });
  assert.strictEqual(aiBubbleEl.textContent, 'Fixing code with AI...', 'visible bubble text must still update');
  assert.strictEqual(aiBubbleEl.hidden, false, 'bubble must still be shown');
  assert.strictEqual(aiBubbleEl.getAttribute('aria-live'), 'off');
  advance(60);
  assert.strictEqual(aiBubbleEl.getAttribute('aria-live'), 'polite');
});

check('showAI(msg) without announce:false (the default) is unaffected - most callers have no companion speak()', () => {
  reset(false);
  sandbox.showAI('Packaging your project...');
  assert.strictEqual(aiBubbleEl.getAttribute('aria-live'), 'polite',
    'default behavior must be untouched: many showAI() callers rely on this being the only signal they emit');
});

console.log(groups + ' groups passed');
