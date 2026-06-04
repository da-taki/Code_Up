'use strict';
/*
 * Pure-logic tests for the spoken-code normalizers in static/app.js — the layer
 * that turns a learner's spoken "insert ..." command into real Python. These
 * functions are the crux of the voice-driven tutorial (string vs number
 * quoting, condition normalization, indentation), so they get direct coverage.
 *
 * The functions live inside app.js (which needs a browser to load fully), so we
 * extract just the marked, side-effect-free block between
 *   // ==== SPOKEN-CODE-NORMALIZERS-START
 *   // ==== SPOKEN-CODE-NORMALIZERS-END
 * and evaluate it in an isolated vm context. Bridged into pytest by
 * tests/test_tutorial_frontend.py (skipped if node is unavailable).
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const START = '// ==== SPOKEN-CODE-NORMALIZERS-START';
const END = '// ==== SPOKEN-CODE-NORMALIZERS-END';
const s = src.indexOf(START);
const e = src.indexOf(END);
assert(s !== -1 && e !== -1 && e > s, 'normalizer markers not found in static/app.js');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  src.slice(s, e) +
  '\nthis.normalizeSpokenValue = normalizeSpokenValue;' +
  '\nthis.normalizeSpokenCondition = normalizeSpokenCondition;' +
  '\nthis.normalizeSpokenCodeText = normalizeSpokenCodeText;' +
  '\nthis.normalizeSpokenPrintArgument = normalizeSpokenPrintArgument;' +
  '\nthis.spokenConditionPhrase = spokenConditionPhrase;',
  sandbox
);
const N = sandbox;

let groups = 0;
function check(name, fn) {
  try { fn(); groups++; }
  catch (err) { console.error('FAILED: ' + name); console.error(err && err.message ? err.message : err); process.exit(1); }
}

// --- Variable values: text is quoted, numbers/booleans stay literal ----------
check('string variable values are quoted', () => {
  assert.strictEqual(N.normalizeSpokenValue('Taknoor'), '"Taknoor"');
  assert.strictEqual(N.normalizeSpokenValue('Aman'), '"Aman"');
  assert.strictEqual(N.normalizeSpokenValue('Patiala'), '"Patiala"');
});
check('numeric variable values stay numbers', () => {
  assert.strictEqual(N.normalizeSpokenValue('7'), '7');
  assert.strictEqual(N.normalizeSpokenValue('12'), '12');
  assert.strictEqual(N.normalizeSpokenValue('seven'), '7');     // spoken digit word
});
check('boolean and none values', () => {
  assert.strictEqual(N.normalizeSpokenValue('true'), 'True');
  assert.strictEqual(N.normalizeSpokenValue('false'), 'False');
});

// --- Conditions: spoken comparisons become real Python operators -------------
check('comparison conditions normalize', () => {
  assert.strictEqual(N.normalizeSpokenCondition('age is greater than 10'), 'age > 10');
  assert.strictEqual(N.normalizeSpokenCondition('whether age is greater than 10'), 'age > 10');
  assert.strictEqual(N.normalizeSpokenCondition('count is less than or equal to 3'), 'count <= 3');
  assert.strictEqual(N.normalizeSpokenCondition('x is greater than or equal to 5'), 'x >= 5');
});
check('condition read back in words', () => {
  assert.strictEqual(N.spokenConditionPhrase('age > 10'), 'age is greater than 10');
  assert.strictEqual(N.spokenConditionPhrase('count <= 3'), 'count is less than or equal to 3');
});

// --- Lines: for-headers, indentation, prints, increments ---------------------
check('for-loop header', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('for i in range 3'), 'for i in range(3):');
});
check('indented print of a variable', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('indented print count'), '    print(count)');
  assert.strictEqual(N.normalizeSpokenCodeText('indented print i'), '    print(i)');
});
check('print message vs print variable', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('print hello world'), 'print("hello world")');
  assert.strictEqual(N.normalizeSpokenCodeText('print name'), 'print(name)');
});
check('"saying" forces a quoted message even for one word', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('an indented print statement saying ready'), '    print("ready")');
  assert.strictEqual(N.normalizeSpokenCodeText('print saying you can vote'), 'print("you can vote")');
});
check('indented counter increment', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('indented count equals count plus 1'), '    count = count + 1');
});

// End-to-end: the exact "insert ..." commands the tutorial tells the learner to
// say must build the exact example programs the backend validators accept (see
// tests/test_tutorial_insert_pipeline.py::test_rewritten_examples_still_validate).
// Here we mirror the insert helpers' simple templates with the pure normalizers.
check('canonical tutorial commands build the example programs', () => {
  const variable = (name, value) => name + ' = ' + N.normalizeSpokenValue(value);
  const ifHeader = (c) => 'if ' + N.normalizeSpokenCondition(c) + ':';
  const whileHeader = (c) => 'while ' + N.normalizeSpokenCondition(c) + ':';
  const line = (t) => N.normalizeSpokenCodeText(t);

  assert.strictEqual(line('print hello world'), 'print("hello world")');

  assert.strictEqual(
    [variable('name', 'Taknoor'), line('print name')].join('\n'),
    'name = "Taknoor"\nprint(name)');

  assert.strictEqual(
    [variable('age', '12'), ifHeader('age is greater than 10'),
     line('an indented print saying you can vote')].join('\n'),
    'age = 12\nif age > 10:\n    print("you can vote")');

  assert.strictEqual(
    [line('for i in range 3'), line('an indented print i')].join('\n'),
    'for i in range(3):\n    print(i)');

  assert.strictEqual(
    [variable('count', '1'), whileHeader('count is less than or equal to 3'),
     line('an indented print count'), line('an indented count equals count plus 1')].join('\n'),
    'count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1');
});

console.log('spoken_code.test.js: ' + groups + ' groups passed');
