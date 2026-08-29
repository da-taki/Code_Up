'use strict';

// Pass 5C live check (rapid Run/Run/Explain/switch-file/Run/Walkthrough):
// runCode() used a raw, unguarded fetch('/run', ...), so an earlier run's
// late-arriving response was never checked for staleness before speaking or
// mutating output/trace state - confirmed live in the browser: a stale
// first run on file A spoke "Program output: FILE A output." *after* a
// walkthrough of file B had already narrated the correct, current state.
// Fixed by wiring runCode() into the same NarrationRequests 'run' scope
// every other guarded action already uses (analysis, mentor, walkthrough,
// ...), rather than inventing a one-off mechanism for this one call site.
// This pins the source-level wiring; the actual staleness behavior was
// verified live (no DOM/audio harness exists in this repo's JS suite).

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

function extractFunctionBody(src, name) {
  const start = src.indexOf(`async function ${name}(`);
  assert(start !== -1, `function ${name} not found in app.js`);
  let depth = 0;
  let i = src.indexOf('{', start);
  const bodyStart = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(bodyStart, i + 1);
    }
  }
  throw new Error(`unbalanced braces for function ${name}`);
}

let passed = 0;
function t(name, fn) { fn(); passed++; }

t('runCode() begins a NarrationRequests "run" guard', () => {
  const body = extractFunctionBody(source, 'runCode');
  assert(/NarrationRequests\.begin\(\s*'run'/.test(body), 'runCode must register itself with the centralized narration guard under a "run" scope');
});

t('runCode() aborts its own fetch via the guard signal', () => {
  const body = extractFunctionBody(source, 'runCode');
  const fetchIdx = body.indexOf("fetch('/run'");
  assert(fetchIdx !== -1, 'runCode must fetch /run');
  const fetchCallEnd = body.indexOf(');', fetchIdx);
  const fetchCall = body.slice(fetchIdx, fetchCallEnd);
  assert(/signal:\s*_runGuard\.signal/.test(fetchCall), 'the /run fetch must pass the guard\'s AbortSignal, so a superseded run\'s request is actually cancelled, not just ignored');
});

t('runCode() checks staleness before touching output/speech state', () => {
  const body = extractFunctionBody(source, 'runCode');
  const jsonIdx = body.indexOf('await res.json()');
  const guardCheckIdx = body.indexOf('_runGuard.active(');
  const firstSpeakOrOutIdx = body.indexOf('window.executionTrace');
  assert(jsonIdx !== -1 && guardCheckIdx !== -1 && firstSpeakOrOutIdx !== -1);
  assert(jsonIdx < guardCheckIdx && guardCheckIdx < firstSpeakOrOutIdx, 'the staleness check must run after parsing the response but before any state mutation or speech');
});

t('runCode() finishes the guard and treats an aborted fetch as a silent no-op', () => {
  const body = extractFunctionBody(source, 'runCode');
  assert(/_runGuard\.finish\(\)/.test(body), 'runCode must release its guard when done');
  assert(/e\.name === 'AbortError'/.test(body), 'an aborted (superseded) run must not surface a "System error" to the user');
});

console.log(passed + ' runCode narration guard tests passed');
