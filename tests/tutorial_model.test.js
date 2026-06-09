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
const { TutorialModel, TUTORIAL_STEPS } = require(path.join(__dirname, '..', 'static', 'tutorial.js'));

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

// CRITICAL: real coding commands must NOT be classified as tutorial controls,
// so they fall through to the normal voice-command pipeline while the tutorial
// is active. This is the root-cause guard for the Variables insert bug.
check('insert / run code commands are never swallowed', () => {
  const C = TutorialModel.classifyDecision;
  assert.strictEqual(C('insert a variable named name and give it the value Taknoor'), null);
  assert.strictEqual(C('insert a variable called name with value Aman'), null);
  assert.strictEqual(C('insert print name'), null);
  assert.strictEqual(C('insert print hello world'), null);
  assert.strictEqual(C('insert an if statement checking age is greater than 10'), null);
  assert.strictEqual(C('insert for i in range 3'), null);
  assert.strictEqual(C('insert while count is less than or equal to 3'), null);
  assert.strictEqual(C('insert an indented print count'), null);
  assert.strictEqual(C('run code'), null);
});

// Staged activities: each module exposes ordered, voice-built steps.
check('staged steps exist for every module', () => {
  assert.deepStrictEqual(Object.keys(TUTORIAL_STEPS).sort(),
    ['for', 'if', 'print', 'variables', 'while'].sort());
  assert.strictEqual(TUTORIAL_STEPS.print.length, 1);
  assert.strictEqual(TUTORIAL_STEPS.variables.length, 2);
  assert.strictEqual(TUTORIAL_STEPS.if.length, 3);
  assert.strictEqual(TUTORIAL_STEPS.for.length, 2);
  assert.strictEqual(TUTORIAL_STEPS.while.length, 4);
  // Every step names an exact "insert ..." command and a spoken prompt.
  Object.keys(TUTORIAL_STEPS).forEach(mid => {
    TUTORIAL_STEPS[mid].forEach(step => {
      assert.ok(/^insert\b/.test(step.say), mid + ' step.say should be an insert command: ' + step.say);
      assert.ok(step.prompt && step.prompt.length > 10, mid + ' step needs a prompt');
      assert.strictEqual(typeof step.check, 'function');
    });
  });
});

// Variables: requires assignment THEN a print of that variable (staged).
check('variables step checks gate assignment + print', () => {
  const S = TUTORIAL_STEPS.variables;
  assert.strictEqual(S[0].check('name = "Taknoor"'), true);   // assignment present
  assert.strictEqual(S[0].check('print(name)'), false);       // no assignment yet
  assert.strictEqual(S[1].check('name = "Taknoor"'), false);  // assigned but not printed
  assert.strictEqual(S[1].check('name = "Taknoor"\nprint(name)'), true);
  // Different names/values also work (not hardcoded).
  assert.strictEqual(S[1].check('student = "Aman"\nprint(student)'), true);
  assert.strictEqual(S[1].check('score = 7\nprint(score)'), true);
});

// While: counter, header, indented print, and a real increment.
check('while step checks gate the safe-loop structure', () => {
  const S = TUTORIAL_STEPS.while;
  const full = 'count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1';
  assert.strictEqual(S[0].check(full), true);   // counter
  assert.strictEqual(S[1].check(full), true);   // while header
  assert.strictEqual(S[2].check(full), true);   // indented print
  assert.strictEqual(S[3].check(full), true);   // increment
  // Missing increment fails the last step.
  assert.strictEqual(S[3].check('count = 1\nwhile count <= 3:\n    print(count)'), false);
  // "count += 1" also counts as an increment.
  assert.strictEqual(S[3].check('count = 1\nwhile count <= 3:\n    print(count)\n    count += 1'), true);
});

// If / for: header + indented body, condition is not mistaken for assignment.
check('if and for step checks gate header + indented body', () => {
  assert.strictEqual(TUTORIAL_STEPS.if[1].check('age = 12\nif age > 10:'), true);
  assert.strictEqual(TUTORIAL_STEPS.if[0].check('if age > 10:'), false);   // "==" guard: header is not an assignment
  assert.strictEqual(TUTORIAL_STEPS.if[2].check('age = 12\nif age > 10:\n    print("you can vote")'), true);
  assert.strictEqual(TUTORIAL_STEPS.for[0].check('for i in range(3):'), true);
  assert.strictEqual(TUTORIAL_STEPS.for[1].check('for i in range(3):\n    print(i)'), true);
});

// AI-tutor coach: new phrases classify, and they are checked BEFORE the plain
// control words (so "say that again simpler" coaches, not repeats), while the
// existing control words and real coding commands are untouched.
check('classifyCoachRequest recognises coach phrases', () => {
  const K = TutorialModel.classifyCoachRequest;
  assert.strictEqual(K('explain simpler'), 'explain_simpler');
  assert.strictEqual(K('say that again simpler'), 'explain_simpler');
  assert.strictEqual(K("I don't understand"), 'dont_understand');
  assert.strictEqual(K('why do we use quotes'), 'why_quotes');
  assert.strictEqual(K('why indentation'), 'why_indentation');
  assert.strictEqual(K('give me another hint'), 'another_hint');
  assert.strictEqual(K('encourage me'), 'encourage');
  assert.strictEqual(K('what am I learning'), 'what_learning');
});

check('coach classifier never swallows control or coding commands', () => {
  const K = TutorialModel.classifyCoachRequest;
  // Existing control words are NOT coach phrases (so classifyDecision handles them).
  ['continue', 'try again', 'recap', 'hint', 'repeat', 'exit tutorial',
   'read my code', 'say that again', 'give me a hint'].forEach((w) => {
    assert.strictEqual(K(w), null, 'should not coach: ' + w);
  });
  // Real coding commands are never coach phrases.
  ['run code', 'run', 'insert print hello world',
   'insert a variable named name and give it the value Taknoor'].forEach((w) => {
    assert.strictEqual(K(w), null, 'should not coach: ' + w);
  });
  // And the existing control words still classify as before.
  assert.strictEqual(TutorialModel.classifyDecision('say that again'), 'repeat');
  assert.strictEqual(TutorialModel.classifyDecision('continue'), 'continue');
  assert.strictEqual(TutorialModel.classifyDecision('hint'), 'hint');
  assert.strictEqual(TutorialModel.classifyDecision('exit tutorial'), 'exit');
});

console.log('tutorial_model.test.js: ' + groups + ' groups passed');
