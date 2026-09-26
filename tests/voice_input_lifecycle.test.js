'use strict';
// Behavioral lifecycle tests for VoiceEngine.VoiceInput (static/voice-engine.js),
// the single source of truth for microphone recognition. Runs the real file
// in a VM with a fake SpeechRecognition, fake timers, a controllable
// document.hidden and a shared sessionStorage (to simulate same-tab navigation).
//
// Each scenario reproduces a real instability found in the Vision-Aid pass:
//   * 'aborted' (another tab/window took the mic) used to leave voice dead
//     while the learner still believed it was on;
//   * switching to another window (tab hidden) used to trigger a refused
//     restart that permanently disabled voice;
//   * a second start() during startup created a second recognizer;
//   * a same-tab navigation (opening an assignment) silently dropped voice.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'static', 'voice-engine.js'), 'utf8');

function makeEnv(sharedSession) {
  let now = 1000;
  let seq = 1;
  let timers = [];
  const env = { instances: [], hidden: false, listeners: {}, refuseWhenHidden: true, refuseAll: false };
  const sessionStore = sharedSession || {};

  function setTimeoutFake(fn, delay) { const id = seq++; timers.push({ id, time: now + (Number(delay) || 0), fn }); return id; }
  function clearTimeoutFake(id) { timers = timers.filter(t => t.id !== id); }
  function setIntervalFake(fn, delay) { const id = seq++; timers.push({ id, time: now + delay, fn, interval: delay }); return id; }
  env.advance = function (ms) {
    const target = now + ms;
    for (let guard = 0; guard < 10000; guard++) {
      timers.sort((a, b) => a.time - b.time);
      if (!timers.length || timers[0].time > target) break;
      const t = timers.shift();
      now = t.time;
      if (t.interval) timers.push({ id: t.id, time: now + t.interval, fn: t.fn, interval: t.interval });
      t.fn();
    }
    now = target;
  };

  class FakeSR {
    constructor() { this.running = false; env.instances.push(this); }
    start() {
      if (this.running) throw new Error('InvalidStateError');
      if (env.refuseAll || (env.hidden && env.refuseWhenHidden)) {
        setTimeoutFake(() => { if (this.onerror) this.onerror({ error: 'not-allowed' }); if (this.onend) this.onend(); }, 10);
        return;
      }
      this.running = true;
      setTimeoutFake(() => { if (this.onstart) this.onstart(); }, 10);
    }
    stop() { if (!this.running) return; this.running = false; setTimeoutFake(() => { if (this.onend) this.onend(); }, 10); }
    abort() { if (!this.running) return; this.running = false; setTimeoutFake(() => { if (this.onerror) this.onerror({ error: 'aborted' }); if (this.onend) this.onend(); }, 10); }
    fail(error) { this.running = false; if (this.onerror) this.onerror({ error }); if (this.onend) this.onend(); }
    say(text) { if (this.onresult) this.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] }); }
  }

  const button = { textContent: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, classList: { add() {}, remove() {} } };
  const document = {
    readyState: 'complete',
    get hidden() { return env.hidden; },
    addEventListener(type, fn) { (env.listeners[type] = env.listeners[type] || []).push(fn); },
    getElementById(id) { return id === 'voiceButton' ? button : null; },
  };
  const window = {
    SpeechRecognition: FakeSR, webkitSpeechRecognition: undefined,
    addEventListener() {}, CODEUP_DEBUG: false,
  };
  const context = {
    window, document, console, Date: { now: () => now }, Math, JSON, Promise, Set, Object, String, Number, Array,
    setTimeout: setTimeoutFake, clearTimeout: clearTimeoutFake, setInterval: setIntervalFake, clearInterval: clearTimeoutFake,
    sessionStorage: {
      getItem: k => (k in sessionStore ? sessionStore[k] : null),
      setItem: (k, v) => { sessionStore[k] = String(v); },
      removeItem: k => { delete sessionStore[k]; },
    },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  vm.createContext(context);
  vm.runInContext(SRC + '\nthis.VoiceEngine = VoiceEngine;', context);
  env.VE = context.VoiceEngine;
  env.VI = context.VoiceEngine.VoiceInput;
  env.button = button;
  env.session = sessionStore;
  env.statuses = [];
  env.VI.onStatusChange((s, d) => env.statuses.push(s + (d && d.resumed ? ':resumed' : '')));
  env.live = () => env.instances.filter(r => r.running);
  env.setHidden = (h) => { env.hidden = h; (env.listeners.visibilitychange || []).forEach(fn => fn()); };
  return env;
}

let groups = 0;
function group(name, fn) { fn(); groups++; console.log('ok -', name); }

group('start creates exactly one recognizer and remembers voice for this tab', () => {
  const e = makeEnv();
  assert.strictEqual(e.VI.start(true), true);
  e.advance(50);
  assert.strictEqual(e.live().length, 1);
  assert.strictEqual(e.VI.isActive(), true);
  assert.strictEqual(e.session.codeupVoiceWanted, '1');
  assert.ok(e.statuses.includes('listening'));
});

group('aborted (mic taken by another tab) restarts instead of dying silently', () => {
  const e = makeEnv();
  e.VI.start(true); e.advance(50);
  e.live()[0].fail('aborted');
  e.advance(2000);
  assert.strictEqual(e.live().length, 1, 'recognition must be running again');
  assert.strictEqual(e.VI.isActive(), true);
  assert.strictEqual(e.VI.isEnabledByUser(), true);
});

group('switching windows: no restart while hidden, resume on return, never permanently off', () => {
  const e = makeEnv();
  e.VI.start(true); e.advance(50);
  e.setHidden(true);
  e.live()[0].stop();                       // browser ends the session in the background
  e.advance(5000);
  assert.strictEqual(e.live().length, 0);
  assert.strictEqual(e.VI.isEnabledByUser(), true, 'the learner still wants voice on');
  assert.ok(!e.statuses.includes('blocked') && !e.statuses.includes('failed'));
  e.setHidden(false);
  e.advance(2000);
  assert.strictEqual(e.live().length, 1, 'listening again once the window is visible');
});

group('a refused restart in a background tab is suspended, not treated as permission denied', () => {
  const e = makeEnv();
  e.VI.start(true); e.advance(50);
  e.setHidden(true);
  e.refuseWhenHidden = true;
  e.VI.setLanguage('en');                   // forces stop -> onend -> (deferred) restart
  e.advance(5000);
  assert.strictEqual(e.VI.isEnabledByUser(), true);
  e.setHidden(false); e.advance(2000);
  assert.strictEqual(e.VI.isActive(), true);
});

group('user-initiated start refused while visible reports blocked and forgets the preference', () => {
  const e = makeEnv();
  e.refuseAll = true;
  e.VI.start(true); e.advance(100);
  assert.ok(e.statuses.includes('blocked'));
  assert.strictEqual(e.VI.isEnabledByUser(), false);
  assert.strictEqual(e.session.codeupVoiceWanted, undefined);
});

group('double start never produces two live recognizers', () => {
  const e = makeEnv();
  e.VI.start(true); e.VI.start(true); e.VI.start(false);
  e.advance(100);
  assert.strictEqual(e.live().length, 1);
});

group('a replaced recognizer cannot restart anything (stale handlers are detached)', () => {
  const e = makeEnv();
  e.VI.start(true); e.advance(50);
  const first = e.instances[0];
  first.fail('network');                    // onend -> scheduled restart creates instance #2
  e.advance(2000);
  assert.strictEqual(e.instances.length, 2);
  if (first.onend) first.onend();           // a late event from the old instance
  e.advance(2000);
  assert.strictEqual(e.live().length, 1);
});

group('same-tab navigation resumes voice once, and says so', () => {
  const shared = {};
  const page1 = makeEnv(shared);
  page1.VI.start(true); page1.advance(50);
  const page2 = makeEnv(shared);            // new page, same tab session
  assert.strictEqual(page2.VI.resumeIfWanted(), true);
  page2.advance(50);
  assert.strictEqual(page2.VI.isActive(), true);
  assert.ok(page2.statuses.includes('listening:resumed'));
  assert.strictEqual(page2.VI.resumeIfWanted(), false, 'resume is a one-time action');
});

group('resume refused on the new page reports resume_failed instead of looping', () => {
  const shared = {};
  const page1 = makeEnv(shared);
  page1.VI.start(true); page1.advance(50);
  const page2 = makeEnv(shared);
  page2.refuseAll = true;
  page2.VI.resumeIfWanted(); page2.advance(200);
  assert.ok(page2.statuses.includes('resume_failed'));
  assert.strictEqual(page2.VI.isEnabledByUser(), false);
});

group('stop() turns voice off and it stays off after navigation', () => {
  const shared = {};
  const page1 = makeEnv(shared);
  page1.VI.start(true); page1.advance(50);
  page1.VI.stop(); page1.advance(50);
  assert.strictEqual(page1.live().length, 0);
  const page2 = makeEnv(shared);
  assert.strictEqual(page2.VI.resumeIfWanted(), false);
});

group('stop() during a recognition transition clears intent and prevents restart', () => {
  const e = makeEnv();
  e.VI.start(true);
  e.VI.stop();
  e.advance(5000);
  assert.strictEqual(e.live().length, 0);
  assert.strictEqual(e.VI.isEnabledByUser(), false);
  assert.strictEqual(e.session.codeupVoiceWanted, undefined);
});

group('a restart storm gives up with one clear status instead of looping forever', () => {
  const e = makeEnv();
  e.VI.start(true); e.advance(50);
  for (let i = 0; i < 12; i++) {
    const r = e.live()[0];
    if (!r) { e.advance(5000); continue; }
    r.fail('network');
    e.advance(5000);
  }
  // Sessions that die immediately count as rapid restarts; either recovery or
  // a single 'failed' status is acceptable - never a silent dead state.
  assert.ok(e.VI.isActive() || e.statuses.includes('failed'));
});

group('self-echo of CodeUp speech is ignored, real commands are not', () => {
  const e = makeEnv();
  const heard = [];
  e.VE.setCommandHandler(async t => { heard.push(t); });
  e.VI.start(true); e.advance(50);
  e.VE.noteSpoken('I added a print statement. Say run to see the output, or say check my work.');
  e.live()[0].say('say run to see the output');
  e.advance(100);
  e.live()[0].say('check my work');
  e.advance(100);
  assert.deepStrictEqual(heard, ['check my work']);
  assert.strictEqual(e.VE.isLikelyEcho('stop'), false, 'barge-in words always get through');
  e.advance(20000);
  assert.strictEqual(e.VE.isLikelyEcho('say run to see the output'), false, 'echo window expires');
});

group('control words CodeUp is saying right now are echo; the same words from the learner are not', () => {
  const e = makeEnv();
  const heard = [];
  e.VE.setCommandHandler(async t => { heard.push(t); });
  e.VI.start(true); e.advance(50);
  e.VE.noteSpoken('Say stop to interrupt me, or say stop listening to turn voice off.');
  e.live()[0].say('stop listening');
  e.live()[0].say('stop');
  e.advance(100);
  assert.deepStrictEqual(heard, [], 'CodeUp hearing itself must not stop speech or switch voice off');
  assert.strictEqual(e.VI.isEnabledByUser(), true);
  e.VE.noteSpoken('Here is your output: 10.');
  assert.strictEqual(e.VE.isLikelyEcho('stop'), true, 'still inside the first sentence window');
  e.advance(20000);
  e.VE.noteSpoken('Here is your output: 10.');
  assert.strictEqual(e.VE.isLikelyEcho('stop'), false, 'a learner saying stop over unrelated speech is barge-in');
  assert.strictEqual(e.VE.isLikelyEcho('stop listening'), false);
});

console.log(groups + ' groups passed');
