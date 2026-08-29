'use strict';

// Pass 5C Scenario 4 (classroom AI change-review dialog): confirmed live in
// the browser through the real fixCode() -> reviewAiFix() -> FocusTrap flow
// that closing the dialog could strand focus - the original returnFocusTo
// hardcoded `document.getElementById('fixBtn') || document.activeElement`,
// and since getElementById never itself returns a falsy value, that `||`
// never fell through to the real opener; fixBtn also lives inside a
// collapsible "More tools" disclosure and is frequently not visible, so
// calling .focus() on it silently no-ops. The fix (a) captures the real
// opener dynamically in reviewAiFix(), and (b) re-validates it at *close*
// time in the shared FocusTrap.close(), falling back through
// editor -> fixBtn -> main heading (never <body>, never a hidden element)
// whenever the recorded opener has since become unusable.
//
// This runs the *actual* isUsable()/safeFallback() source from
// static/classroom.js (extracted and evaluated against a fake DOM), not a
// hand-rewritten reimplementation, so a future edit to the real logic is
// what gets tested here - not a copy that could drift out of sync.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'classroom.js'), 'utf8');

function extractFunctionSource(src, name) {
  const start = src.indexOf(`function ${name}(`);
  assert(start !== -1, `function ${name} not found in classroom.js`);
  let depth = 0;
  let i = src.indexOf('{', start);
  const bodyStart = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces for function ${name}`);
}

function makeElement(props) {
  return Object.assign({
    focus() { this._focused = true; },
    isConnected: true,
    offsetParent: {},
    disabled: false,
    hasAttribute() { return false; },
    setAttribute(name, value) { this._attrs = this._attrs || {}; this._attrs[name] = value; },
  }, props);
}

function buildSandbox({ editorInput, fixBtn, heading, body } = {}) {
  const fakeBody = body || { __isBody: true };
  const doc = {
    body: fakeBody,
    querySelector(sel) {
      if (sel === '.monaco-editor textarea') return editorInput || null;
      if (sel === 'h1') return heading || null;
      return null;
    },
    getElementById(id) {
      if (id === 'fixBtn') return fixBtn || null;
      if (id === 'pageTitle') return heading || null;
      return null;
    },
  };
  const sandbox = { document: doc, console };
  vm.createContext(sandbox);
  return sandbox;
}

function loadIsUsable(sandbox) {
  vm.runInContext(extractFunctionSource(source, 'isUsable'), sandbox);
  return sandbox.isUsable;
}

function loadSafeFallback(sandbox) {
  vm.runInContext(extractFunctionSource(source, 'isUsable'), sandbox);
  vm.runInContext(extractFunctionSource(source, 'safeFallback'), sandbox);
  return sandbox.safeFallback;
}

let passed = 0;
function t(name, fn) { fn(); passed++; }

// ---- isUsable() ------------------------------------------------------------

t('isUsable accepts a normal visible, connected, enabled element', () => {
  const sandbox = buildSandbox();
  const isUsable = loadIsUsable(sandbox);
  assert.strictEqual(isUsable(makeElement({})), true);
});

t('isUsable rejects document.body (the "focus landed nowhere useful" case)', () => {
  const body = { __isBody: true };
  const sandbox = buildSandbox({ body });
  const isUsable = loadIsUsable(sandbox);
  assert.strictEqual(isUsable(body), false);
});

t('isUsable rejects a hidden element (offsetParent null, e.g. inside a collapsed disclosure or display:none)', () => {
  const sandbox = buildSandbox();
  const isUsable = loadIsUsable(sandbox);
  assert.strictEqual(isUsable(makeElement({ offsetParent: null })), false);
});

t('isUsable rejects a detached (removed from the document) element', () => {
  const sandbox = buildSandbox();
  const isUsable = loadIsUsable(sandbox);
  assert.strictEqual(isUsable(makeElement({ isConnected: false })), false);
});

t('isUsable rejects a disabled control', () => {
  const sandbox = buildSandbox();
  const isUsable = loadIsUsable(sandbox);
  assert.strictEqual(isUsable(makeElement({ disabled: true })), false);
});

t('isUsable rejects null/undefined without throwing', () => {
  const sandbox = buildSandbox();
  const isUsable = loadIsUsable(sandbox);
  assert.strictEqual(isUsable(null), false);
  assert.strictEqual(isUsable(undefined), false);
});

// ---- safeFallback() ---------------------------------------------------------

t('safeFallback prefers the code editor when it is visible', () => {
  const editorInput = makeElement({ id: 'editor' });
  const fixBtn = makeElement({ id: 'fixBtn' });
  const sandbox = buildSandbox({ editorInput, fixBtn });
  const safeFallback = loadSafeFallback(sandbox);
  assert.strictEqual(safeFallback(), editorInput);
});

t('safeFallback skips a hidden editor and uses fixBtn if that is visible', () => {
  const editorInput = makeElement({ offsetParent: null });
  const fixBtn = makeElement({ id: 'fixBtn' });
  const sandbox = buildSandbox({ editorInput, fixBtn });
  const safeFallback = loadSafeFallback(sandbox);
  assert.strictEqual(safeFallback(), fixBtn);
});

t('safeFallback skips a hidden editor AND a hidden fixBtn (collapsed disclosure) and falls back to the main heading', () => {
  const editorInput = makeElement({ offsetParent: null });
  const fixBtn = makeElement({ offsetParent: null }); // inside a collapsed <details>
  const heading = makeElement({ tagName: 'H1' });
  const sandbox = buildSandbox({ editorInput, fixBtn, heading });
  const safeFallback = loadSafeFallback(sandbox);
  const result = safeFallback();
  assert.strictEqual(result, heading, 'must fall back to the heading, never stay on a hidden control');
  assert.strictEqual(heading._attrs && heading._attrs.tabindex, '-1', 'the heading must be made focusable via tabindex=-1');
});

t('safeFallback never returns document.body even when nothing else is available', () => {
  const sandbox = buildSandbox(); // no editor, no fixBtn, no heading
  const safeFallback = loadSafeFallback(sandbox);
  const result = safeFallback();
  assert.notStrictEqual(result, sandbox.document.body);
});

// ---- close() wiring: must re-validate at close time, not just at open time --

t('FocusTrap.close() re-validates the recorded opener before using it, falling back when it is no longer usable', () => {
  const closeSrc = extractFunctionSource(source, 'close');
  assert(closeSrc.includes('isUsable(returnFocusTo)'), 'close() must re-check the recorded opener\'s current usability, not trust the snapshot taken at open() time');
  assert(closeSrc.includes('safeFallback()'), 'close() must have a fallback path when the recorded opener is no longer usable');
});

t('reviewAiFix captures the opener dynamically rather than hardcoding fixBtn as the primary target', () => {
  const start = source.indexOf('function reviewAiFix(');
  assert(start !== -1, 'reviewAiFix not found');
  const snippet = source.slice(start, start + 1200);
  assert(
    !/returnFocusTo:\s*document\.getElementById\('fixBtn'\)\s*\|\|/.test(snippet),
    'must not regress to the old hardcoded-fixBtn-first fallback, which never actually fell through to the real opener'
  );
  assert(/document\.activeElement/.test(snippet), 'must consult the real active element when choosing the opener to restore');
});

console.log(passed + ' classroom review dialog focus tests passed');
