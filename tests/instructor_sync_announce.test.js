'use strict';
// Behavioral test for static/instructor-sync.js announcements: runs the real
// script against a minimal fake DOM and a scripted live-summary endpoint, and
// drives polls through its own window "focus" listener.
//   * two events arriving in one poll are BOTH announced (the live region
//     used to be overwritten synchronously, so only the last was heard);
//   * events newer than the server-rendered watermark are announced on the
//     very first poll (they used to be swallowed as a "seed");
//   * nothing already reflected in the rendered page is announced.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'static', 'instructor-sync.js'), 'utf8');

function makeEnv(watermark, responses) {
  let now = 1e9;
  let timers = [];
  const listeners = { window: {}, document: {} };
  const announcer = {
    _t: '', heard: [],
    get textContent() { return this._t; },
    set textContent(v) { this._t = v; },
  };
  const document = {
    hidden: false,
    currentScript: { dataset: { cohortId: '7', lastEventId: watermark } },
    getElementById: id => (id === 'srAnnouncer' ? announcer : null),
    addEventListener(type, fn) { (listeners.document[type] = listeners.document[type] || []).push(fn); },
    querySelectorAll: () => [],
    activeElement: null,
  };
  let call = 0;
  const context = {
    document, console,
    window: { addEventListener(type, fn) { (listeners.window[type] = listeners.window[type] || []).push(fn); } },
    setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
    setInterval: () => 1, clearInterval() {},
    Date: { now: () => now },
    fetch: () => Promise.resolve({ json: () => Promise.resolve(responses[Math.min(call++, responses.length - 1)]) }),
  };
  vm.createContext(context);
  vm.runInContext(SRC, context);
  return {
    async poll() {
      now += 10000;
      (listeners.window.focus || []).forEach(fn => fn());
      for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
      const pending = timers; timers = [];
      pending.forEach(fn => fn());
      if (announcer.textContent) announcer.heard.push(announcer.textContent);
    },
    announcer,
  };
}

function summary(events) {
  return { success: true, learner_count: 0, learners: [], assignments: [], open_help_count: 0,
           help_requests: [], events };
}

(async function main() {
  let groups = 0;
  const ok = (name) => { groups++; console.log('ok -', name); };

  {
    const env = makeEnv('10', [summary([
      { id: 12, kind: 'help_requested', learner_name: 'Meera' },
      { id: 11, kind: 'help_requested', learner_name: 'Ravi' },
      { id: 5, kind: 'learner_joined', learner_name: 'Asha' },
    ])]);
    await env.poll();
    assert.deepStrictEqual(env.announcer.heard,
      ['Ravi requested instructor help. Meera requested instructor help.']);
    ok('two events in one poll are both announced, oldest first, and nothing from the rendered page');
  }

  {
    const env = makeEnv('10', [
      summary([{ id: 10, kind: 'learner_joined', learner_name: 'Asha' }]),
      summary([{ id: 13, kind: 'assignment_submitted', learner_name: 'Asha', assignment_title: 'Marks' },
               { id: 10, kind: 'learner_joined', learner_name: 'Asha' }]),
    ]);
    await env.poll();
    assert.deepStrictEqual(env.announcer.heard, []);
    await env.poll();
    assert.deepStrictEqual(env.announcer.heard, ['Asha submitted Marks.']);
    ok('first poll after render announces only what is newer than the page');
  }

  {
    const env = makeEnv('', [summary([{ id: 3, kind: 'learner_joined', learner_name: 'Old' }])]);
    await env.poll();
    assert.deepStrictEqual(env.announcer.heard, [], 'older markup without a watermark still seeds silently');
    ok('pages without a server watermark keep the old seeding behavior');
  }

  console.log(groups + ' groups passed');
})().catch(err => { console.error(err); process.exit(1); });
