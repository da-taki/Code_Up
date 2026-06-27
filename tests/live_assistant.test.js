'use strict';
// Unit tests for the Live Assistant state machine (static/live-assistant.js).
// Pure: recognition and speech are mocked. No DOM, no real microphone.

const assert = require('assert');
const path = require('path');
const { createLiveAssistant } = require(path.join(__dirname, '..', 'static', 'live-assistant.js'));

function makeDeps(overrides) {
  const log = { spoken: [], started: 0, stopped: 0, cancelled: 0, snapshots: [] };
  const deps = Object.assign({
    speak: t => log.spoken.push(t),
    cancelSpeech: () => log.cancelled++,
    startListening: () => log.started++,
    stopListening: () => log.stopped++,
    recognitionAvailable: true,
    speechAvailable: true,
    getMode: () => 'python',
    getFile: () => 'main.py',
    onStateChange: snap => log.snapshots.push(snap),
  }, overrides || {});
  return { deps, log };
}

let passed = 0;
function t(name, fn) { fn(); passed++; }

// 1. starts off, start enables listening and triggers recognition
t('start enables assistant and listening', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  assert.strictEqual(la.getState().status, 'off');
  la.start();
  const s = la.getState();
  assert.strictEqual(s.assistantEnabled, true);
  assert.strictEqual(s.listening, true);
  assert.strictEqual(s.status, 'listening');
  assert.strictEqual(log.started, 1);
});

// 2. stop disables and stops listening
t('stop disables assistant and stops listening', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  la.stop();
  assert.strictEqual(la.getState().assistantEnabled, false);
  assert.strictEqual(la.getState().status, 'off');
  assert.ok(log.stopped >= 1);
});

// 3. pause/resume listening
t('pause and resume listening', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  assert.strictEqual(la.handleMetaCommand('pause live assistant'), true);
  assert.strictEqual(la.getState().status, 'paused');
  assert.strictEqual(la.getState().listening, false);
  assert.strictEqual(la.handleMetaCommand('resume live assistant'), true);
  assert.strictEqual(la.getState().status, 'listening');
  assert.strictEqual(la.getState().listening, true);
});

// 4. stop speaking calls speech cancel
t('stop speaking cancels speech', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  la.handleMetaCommand('stop speaking');
  assert.strictEqual(log.cancelled, 1);
});

// 5 & 6. last heard / last response storage + repeat
t('records turns and repeats last response', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  la.recordTurn('project map', 'Project map: 3 files.', 'deterministic_message');
  assert.strictEqual(la.getState().lastHeardCommand, 'project map');
  assert.strictEqual(la.getState().lastAssistantResponse, 'Project map: 3 files.');
  assert.strictEqual(la.getState().lastAssistantAction, 'deterministic_message');
  log.spoken.length = 0;
  la.repeat();
  assert.strictEqual(log.spoken[0], 'Project map: 3 files.');
});

// 7. recent turns bounded
t('recent turns are bounded', () => {
  const { deps } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  for (let i = 0; i < 40; i++) la.recordTurn('cmd ' + i, 'reply ' + i, 'deterministic_message');
  assert.ok(la.getState().recentTurns.length <= 20, 'transcript should be bounded');
});

// 8. empty command does not get intercepted; empty turn is a no-op
t('empty recognition result does not execute', () => {
  const { deps } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  assert.strictEqual(la.handleMetaCommand(''), false);
  assert.strictEqual(la.handleMetaCommand('   '), false);
  const before = la.getState().recentTurns.length;
  la.recordTurn('', '', '');
  assert.strictEqual(la.getState().recentTurns.length, before);
});

// 9. unsupported recognition fallback
t('unsupported speech recognition falls back to typed mode', () => {
  const { deps, log } = makeDeps({ recognitionAvailable: false });
  const la = createLiveAssistant(deps);
  la.start();
  assert.strictEqual(la.getState().assistantEnabled, true);
  assert.strictEqual(la.getState().listening, false);
  assert.strictEqual(log.started, 0, 'should not start recognition when unavailable');
  assert.ok(log.spoken.some(s => /not available/i.test(s)), 'should announce fallback');
});

// 10. cockpit + proposal commands are NOT intercepted (route normally)
t('cockpit and proposal commands route normally', () => {
  const { deps } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  ['project map', 'show program state', 'what variables exist', 'step through this',
   'explain error', 'what changed', 'read before and after', 'open audio blocks',
   'transfer blocks to python mode', 'apply', 'reject', 'explain', 'run', 'next step'
  ].forEach(cmd => assert.strictEqual(la.handleMetaCommand(cmd), false, cmd + ' must route normally'));
});

// 11. when off, only "start live assistant" is intercepted
t('when off only start is intercepted', () => {
  const { deps } = makeDeps();
  const la = createLiveAssistant(deps);
  assert.strictEqual(la.handleMetaCommand('pause listening'), false);
  assert.strictEqual(la.handleMetaCommand('where am i'), false);
  assert.strictEqual(la.handleMetaCommand('start live assistant'), true);
  assert.strictEqual(la.getState().assistantEnabled, true);
});

// 12. recall + context commands
t('recall and context commands speak the right thing', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  la.recordTurn('explain error', 'The program crashed at line 2.', 'deterministic_message');
  log.spoken.length = 0;
  la.handleMetaCommand('what did you hear');
  assert.ok(/explain error/.test(log.spoken[0]));
  log.spoken.length = 0;
  la.handleMetaCommand('what did you do');
  assert.ok(/crashed at line 2/.test(log.spoken[0]));
  log.spoken.length = 0;
  la.handleMetaCommand('what mode am i in');
  assert.strictEqual(log.spoken[0], 'Python Code Mode');
  log.spoken.length = 0;
  la.handleMetaCommand('where am i');
  assert.ok(/Python Code Mode/.test(log.spoken[0]) && /main\.py/.test(log.spoken[0]));
  log.spoken.length = 0;
  la.handleMetaCommand('what can i say here');
  assert.ok(/project map/.test(log.spoken[0]));
});

// 13. cancel last command never undoes code
t('cancel last command does not undo code', () => {
  const { deps, log } = makeDeps();
  const la = createLiveAssistant(deps);
  la.start();
  log.spoken.length = 0;
  la.handleMetaCommand('cancel last command');
  assert.ok(log.spoken.some(s => /did not change your code/i.test(s)));
});

// 14. status text updates through the lifecycle
t('status text reflects lifecycle', () => {
  const { deps } = makeDeps();
  const la = createLiveAssistant(deps);
  assert.strictEqual(la.getState().status, 'off');
  la.start(); assert.strictEqual(la.getState().status, 'listening');
  la.noteProcessing(true); assert.strictEqual(la.getState().status, 'processing');
  la.noteProcessing(false); la.noteSpeaking(true); assert.strictEqual(la.getState().status, 'speaking');
  la.noteSpeaking(false); la.pauseListening(); assert.strictEqual(la.getState().status, 'paused');
});

console.log(passed + ' live assistant tests passed');
