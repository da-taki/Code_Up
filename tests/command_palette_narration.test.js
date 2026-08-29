'use strict';

// Pass 5C live check (speech -> palette -> run -> input dialog): opening the
// command palette used to speak its own "Command palette open..." message
// without first cancelling whatever narration was already playing, so it
// silently queued behind the old speech instead of announcing immediately -
// confirmed live in the browser (speechSynthesis.speak() call order) before
// being fixed with the same SpeechManager.cancelAll() call every other
// context-switching action in app.js already makes (runCode, setCode,
// analyzeCode, etc). This pins the source-level invariant: no full DOM/audio
// harness exists in this repo's JS suite, so this is a regression guard
// against the exact line being removed again, not a substitute for the live
// browser verification already performed.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

function extractFunctionBody(src, name) {
  const start = src.indexOf(`function ${name}(`);
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

t('openCommandPalette cancels prior narration before speaking its own announcement', () => {
  const body = extractFunctionBody(source, 'openCommandPalette');
  const cancelIdx = body.indexOf('SpeechManager.cancelAll()');
  const speakIdx = body.indexOf("speak('Command palette open");
  assert(cancelIdx !== -1, 'openCommandPalette must call SpeechManager.cancelAll()');
  assert(speakIdx !== -1, 'openCommandPalette must announce itself');
  assert(cancelIdx < speakIdx, 'cancelAll() must run before the palette announces itself, or the announcement queues behind stale narration instead of interrupting it');
});

console.log(passed + ' command palette narration tests passed');
