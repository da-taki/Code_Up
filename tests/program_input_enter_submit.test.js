'use strict';

// XRCVC full re-audit, item 2: Enter must submit #programInputValue exactly
// like the Submit button, without duplicating on key-repeat or hijacking an
// IME composition's own Enter. This extracts the *real* keydown listener
// callback straight out of static/app.js (not a reimplementation of it) and
// drives it with fake KeyboardEvent-shaped objects, asserting on which real
// handler function actually got called - matching this repo's existing
// convention (announcement_ownership.test.js, run_code_narration_guard.test.js)
// of testing shipped source instead of a copy that could drift from it.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

const START = "programInput.addEventListener('keydown', ";
const startIdx = APP_JS.indexOf(START);
assert(startIdx !== -1, 'program input keydown listener not found in app.js');
const bodyStart = startIdx + START.length;
const nextAnchor = APP_JS.indexOf("const programSubmit = document.getElementById", bodyStart);
assert(nextAnchor !== -1, 'end anchor (programSubmit declaration) not found');
const closeIdx = APP_JS.lastIndexOf('});', nextAnchor);
assert(closeIdx !== -1 && closeIdx > bodyStart, 'end of program input keydown listener not found');
const LISTENER_SRC = APP_JS.slice(bodyStart, closeIdx + '}'.length);

let submitCalls = 0;
let cancelCalls = 0;
const sandbox = {
  submitProgramInputValue() { submitCalls++; },
  cancelProgramInputRequest() { cancelCalls++; },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
const listener = vm.runInContext('(' + LISTENER_SRC + ')', sandbox);

function fakeEvent(overrides) {
  let prevented = false;
  return Object.assign({
    key: 'Enter',
    isComposing: false,
    repeat: false,
    preventDefault() { prevented = true; },
    get defaultPrevented() { return prevented; },
  }, overrides);
}

function reset() { submitCalls = 0; cancelCalls = 0; }

let passed = 0;
function t(name, fn) { reset(); fn(); passed++; }

t('a plain Enter keydown submits exactly once, via the same function the Submit button calls', () => {
  const evt = fakeEvent({ key: 'Enter' });
  listener(evt);
  assert.strictEqual(submitCalls, 1);
  assert.strictEqual(evt.defaultPrevented, true, 'must preventDefault so Enter does not also insert a newline/submit a form');
});

t('Escape still cancels, unaffected by the Enter changes', () => {
  const evt = fakeEvent({ key: 'Escape' });
  listener(evt);
  assert.strictEqual(cancelCalls, 1);
  assert.strictEqual(submitCalls, 0);
});

t('an IME composition Enter (isComposing:true) does not submit - it is confirming a composed character, not the field', () => {
  const evt = fakeEvent({ key: 'Enter', isComposing: true });
  listener(evt);
  assert.strictEqual(submitCalls, 0, 'submitting here would eat the IME candidate confirmation instead of letting it compose');
});

t('a key-repeat Enter (repeat:true, held key) does not create a duplicate submission', () => {
  const first = fakeEvent({ key: 'Enter' });
  listener(first);
  const repeated = fakeEvent({ key: 'Enter', repeat: true });
  listener(repeated);
  assert.strictEqual(submitCalls, 1, 'holding Enter down must submit once, not once per auto-repeated keydown');
});

t('other keys (e.g. Tab) are left alone - Tab navigation must keep working', () => {
  const evt = fakeEvent({ key: 'Tab' });
  listener(evt);
  assert.strictEqual(submitCalls, 0);
  assert.strictEqual(cancelCalls, 0);
  assert.strictEqual(evt.defaultPrevented, false, 'Tab must not be preventDefault-ed here, or focus could not leave the field');
});

console.log(passed + ' program input Enter-to-submit tests passed');
