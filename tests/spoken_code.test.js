'use strict';
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
  '\nthis.spokenConditionPhrase = spokenConditionPhrase;' +
  '\nthis.formatRunOutputSpeech = formatRunOutputSpeech;' +
  '\nthis.formatFullOutputSpeech = formatFullOutputSpeech;',
  sandbox
);
const N = sandbox;

const ALIAS_START = '// ==== PROJECT-FILE-ALIASES-START';
const ALIAS_END = '// ==== PROJECT-FILE-ALIASES-END';
const as = src.indexOf(ALIAS_START);
const ae = src.indexOf(ALIAS_END);
assert(as !== -1 && ae !== -1 && ae > as, 'project file alias markers not found in static/app.js');

const aliasSandbox = {};
vm.createContext(aliasSandbox);
vm.runInContext(
  src.slice(as, ae) +
  '\nthis.normalizeProjectPath = normalizeProjectPath;' +
  '\nthis.resolveProjectFileAlias = resolveProjectFileAlias;',
  aliasSandbox
);
const A = aliasSandbox;

let groups = 0;
function check(name, fn) {
  try { fn(); groups++; }
  catch (err) { console.error('FAILED: ' + name); console.error(err && err.message ? err.message : err); process.exit(1); }
}

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
check('a mis-heard print keyword is safely corrected', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('prent hello world'), 'print("hello world")');
  assert.strictEqual(N.normalizeSpokenCodeText('prnt hello world'), 'print("hello world")');
  assert.strictEqual(N.normalizeSpokenCodeText('print hello world'), 'print("hello world")');
});
check('"saying" forces a quoted message even for one word', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('an indented print statement saying ready'), '    print("ready")');
  assert.strictEqual(N.normalizeSpokenCodeText('print saying you can vote'), 'print("you can vote")');
});
check('indented counter increment', () => {
  assert.strictEqual(N.normalizeSpokenCodeText('indented count equals count plus 1'), '    count = count + 1');
});

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

check('project file aliases resolve friendly spoken names', () => {
  const files = [
    'README.md', 'main.py', 'questions.py', 'requirements.txt', 'score.py',
    'data/marks.csv', 'data_loader.py', 'stats_utils.py', 'tests/test_main.py',
  ];
  assert.strictEqual(A.resolveProjectFileAlias('main', files).path, 'main.py');
  assert.strictEqual(A.resolveProjectFileAlias('main dot py', files).path, 'main.py');
  assert.strictEqual(A.resolveProjectFileAlias('questions', files).path, 'questions.py');
  assert.strictEqual(A.resolveProjectFileAlias('score', files).path, 'score.py');
  assert.strictEqual(A.resolveProjectFileAlias('data loader', files).path, 'data_loader.py');
  assert.strictEqual(A.resolveProjectFileAlias('marks', files).path, 'data/marks.csv');
  assert.strictEqual(A.resolveProjectFileAlias('test main', files).path, 'tests/test_main.py');
  assert.strictEqual(A.resolveProjectFileAlias('tests slash test main dot py', files).path, 'tests/test_main.py');
});

check('project file aliases refuse ambiguous matches', () => {
  const files = ['main.py', 'folder/main.py'];
  assert.strictEqual(
    A.resolveProjectFileAlias('main', files).error,
    'I found multiple matching files. Please say the full file name.'
  );
});

check('project file aliases keep safe fallback for missing files', () => {
  assert.strictEqual(A.resolveProjectFileAlias('missing file', ['main.py']).path, 'missing_file');
});

check('single-line output is spoken with the value', () => {
  assert.strictEqual(N.formatRunOutputSpeech('Taki\n'), 'Program output: Taki.');
});
check('multi-line output is read as a comma list, not raw newlines', () => {
  assert.strictEqual(N.formatRunOutputSpeech('0\n1\n2\n'), 'Program output: 0, 1, 2.');
});
check('empty output is stated plainly, never a dangling label', () => {
  assert.strictEqual(N.formatRunOutputSpeech(''), 'Program ran successfully with no printed output.');
  assert.strictEqual(N.formatRunOutputSpeech('   \n  \n'), 'Program ran successfully with no printed output.');
  assert.strictEqual(N.formatRunOutputSpeech('Program finished with no output.'),
    'Program ran successfully with no printed output.');
  assert.strictEqual(N.formatFullOutputSpeech('Program finished with no output.'),
    'The program finished with no printed output.');
});
check('reasonable multi-line output is spoken completely', () => {
  const many = Array.from({ length: 40 }, (_, i) => String(i)).join('\n');
  const spoken = N.formatRunOutputSpeech(many);
  assert.ok(spoken.startsWith('Program output: 0, 1, 2,'), spoken);
  assert.ok(spoken.includes('39'), 'reasonable output must include the final line');
});
check('unicode output is spoken without crashing', () => {
  assert.strictEqual(N.formatRunOutputSpeech('hello नमस्ते'),
    'Program output: hello नमस्ते.');
});
check('extremely long output is shortened with a clear limit notice', () => {
  const spoken = N.formatRunOutputSpeech('x'.repeat(5000));
  assert.ok(/shortened for speech after 4000 characters/.test(spoken), spoken);
  assert.ok(spoken.length < 4200, 'speech limit should prevent an unbounded queue');
});
check('120-line output under the speech cap is not mislabeled as shortened', () => {
  const many = Array.from({ length: 120 }, (_, i) => `line ${i}`).join('\n');
  const spoken = N.formatRunOutputSpeech(many);
  assert.ok(spoken.startsWith('Program output: line 0, line 1,'), spoken);
  assert.ok(spoken.includes('line 119'), 'automatic narration should include the final line under the cap');
  assert.ok(!/shortened for speech after 4000 characters/.test(spoken), spoken);
});

check('prime output through 47 is included in run speech', () => {
  const primes = ['2','3','5','7','11','13','17','19','23','29','31','37','41','43','47'].join('\n') + '\n';
  const spoken = N.formatRunOutputSpeech(primes);
  assert.ok(spoken.includes('47'), spoken);
});
check('read full output reads everything, no summarizing', () => {
  const many = Array.from({ length: 40 }, (_, i) => String(i)).join('\n');
  const full = N.formatFullOutputSpeech(many);
  assert.ok(full.startsWith('Complete program output: 0, 1, 2,'), full);
  assert.ok(full.indexOf('39') !== -1, 'full readback must include the last line');
  assert.strictEqual(N.formatFullOutputSpeech(''), 'No output available.');
});
check('explicit output replay is not capped at the automatic speech limit', () => {
  const many = Array.from({ length: 700 }, (_, i) => `line ${i}`).join('\n');
  const automatic = N.formatRunOutputSpeech(many);
  const full = N.formatFullOutputSpeech(many);
  assert.ok(/shortened for speech after 4000 characters/.test(automatic), automatic);
  assert.ok(full.startsWith('Complete program output: line 0, line 1,'), full);
  assert.ok(full.includes('line 699'), 'explicit replay must include the final line');
  assert.ok(full.length > 4200, 'explicit replay should not reuse the automatic cap');
});

console.log('spoken_code.test.js: ' + groups + ' groups passed');
