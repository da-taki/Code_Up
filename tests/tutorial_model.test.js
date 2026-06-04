'use strict';
/*
 * Pure-logic tests for the tutorial state machine (TutorialModel), run with
 * plain Node — no test framework, no DOM. Exits non-zero on the first failure.
 * Bridged into pytest by tests/test_tutorial_frontend.py (skipped if node is
 * unavailable). Covers: starts at print, NO auto-advance after success,
 * continue -> variables, clean exit, and utterance classification.
 */
const assert = require('assert');
const path = require('path');
const { TutorialModel } = require(path.join(__dirname, '..', 'static', 'tutorial.js'));

let groups = 0;
function check(name, fn) {
  try {
    fn();
    groups++;
  } catch (e) {
    console.error('FAILED: ' + name);
    console.error(e && e.message ? e.message : e);
    process.exit(1);
  }
}

// 1. Starting the tutorial begins at print statements.
check('starts at print', () => {
  const m = new TutorialModel();
  assert.strictEqual(m.start(), 'print');
  assert.strictEqual(m.stage, 'intro');
  assert.strictEqual(m.active, true);
});

// 2. Completing print does NOT automatically start variables.
check('no auto-advance after success', () => {
  const m = new TutorialModel();
  m.start();
  m.beginActivity();
  m.markSuccess();
  assert.strictEqual(m.moduleId, 'print', 'should still be on print');
  assert.strictEqual(m.stage, 'decision');
  assert.deepStrictEqual(m.completed, ['print']);
});

// 3. Choosing continue after print begins variables.
check('continue advances to variables', () => {
  const m = new TutorialModel();
  m.start();
  m.beginActivity();
  m.markSuccess();
  assert.strictEqual(m.continueNext(), 'variables');
  assert.strictEqual(m.moduleId, 'variables');
  assert.strictEqual(m.stage, 'intro');
});

// 4. Choosing exit exits cleanly.
check('exit clears active state', () => {
  const m = new TutorialModel();
  m.start();
  m.exit();
  assert.strictEqual(m.active, false);
  assert.strictEqual(m.stage, 'idle');
  assert.strictEqual(m.moduleId, null);
});

// Finishing the last module reports "no next".
check('continue past while finishes', () => {
  const m = new TutorialModel();
  m.gotoModule('while');
  m.beginActivity();
  m.markSuccess();
  assert.strictEqual(m.nextModuleId(), null);
  assert.strictEqual(m.continueNext(), null);
});

// Practise-again re-enters the same activity.
check('practice again stays on module', () => {
  const m = new TutorialModel();
  m.start();
  m.beginActivity();
  m.markSuccess();
  assert.strictEqual(m.practiceAgain(), 'print');
  assert.strictEqual(m.stage, 'activity');
});

// gotoModule jumps to a topic; rejects unknown.
check('gotoModule jumps and validates', () => {
  const m = new TutorialModel();
  assert.strictEqual(m.gotoModule('for'), true);
  assert.strictEqual(m.moduleId, 'for');
  assert.strictEqual(m.gotoModule('nope'), false);
});

// 5. Decision/utterance classification: tutorial words mapped...
check('classifyDecision maps tutorial words', () => {
  const C = TutorialModel.classifyDecision;
  assert.strictEqual(C('continue'), 'continue');
  assert.strictEqual(C('next'), 'continue');
  assert.strictEqual(C('yes'), 'continue');
  assert.strictEqual(C('try again'), 'again');
  assert.strictEqual(C('practise again'), 'again');
  assert.strictEqual(C('recap'), 'recap');
  assert.strictEqual(C('give me a recap'), 'recap');
  assert.strictEqual(C('repeat'), 'repeat');
  assert.strictEqual(C('say that again'), 'repeat');
  assert.strictEqual(C('read the instructions again'), 'repeat');
  assert.strictEqual(C('hint'), 'hint');
  assert.strictEqual(C('give me a hint'), 'hint');
  assert.strictEqual(C('give me an example'), 'example');
  assert.strictEqual(C('exit tutorial'), 'exit');
  assert.strictEqual(C('stop tutorial'), 'exit');
  assert.strictEqual(C('start coding'), 'exit');
});

// ...and global commands are NOT consumed (so the IDE still works).
check('global commands pass through untouched', () => {
  const C = TutorialModel.classifyDecision;
  assert.strictEqual(C('stop'), null);
  assert.strictEqual(C('help'), null);
  assert.strictEqual(C('run'), null);
  assert.strictEqual(C('read line 2'), null);
  assert.strictEqual(C('go to line 5'), null);
  assert.strictEqual(C('analyze'), null);
  assert.strictEqual(C(''), null);
});

console.log('tutorial_model.test.js: ' + groups + ' groups passed');
