'use strict';

const assert = require('assert');
const path = require('path');
const { createNarrationGuard } = require(path.join(__dirname, '..', 'static', 'narration-guard.js'));

let passed = 0;
async function t(name, fn) { await fn(); passed++; }
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

(async () => {
  await t('slow old response is stale after newer fast response in same scope', async () => {
    const guard = createNarrationGuard();
    const slow = deferred();
    const fast = deferred();
    const first = guard.guardedFetch('analysis', () => slow.promise, { code: 'a=1', file: 'main.py' }, () => ({ code: 'a=1', file: 'main.py' }));
    const second = guard.guardedFetch('analysis', () => fast.promise, { code: 'b=2', file: 'main.py' }, () => ({ code: 'b=2', file: 'main.py' }));
    fast.resolve('fast');
    assert.deepStrictEqual(await second, { stale: false, value: 'fast', guard: (await second).guard });
    slow.resolve('slow');
    const old = await first;
    assert.strictEqual(old.stale, true);
    assert.strictEqual(old.value, undefined);
  });

  await t('old response is stale when code changes before it returns', async () => {
    const guard = createNarrationGuard();
    const slow = deferred();
    let current = { code: 'print(1)', file: 'main.py' };
    const resultPromise = guard.guardedFetch('walkthrough', () => slow.promise, current, () => current);
    current = { code: 'print(2)', file: 'main.py' };
    slow.resolve('old explanation');
    const result = await resultPromise;
    assert.strictEqual(result.stale, true);
  });

  await t('global invalidation makes old narration stale', async () => {
    const guard = createNarrationGuard();
    const slow = deferred();
    const resultPromise = guard.guardedFetch('mentor', () => slow.promise, { code: 'x=1', file: 'main.py' }, () => ({ code: 'x=1', file: 'main.py' }));
    guard.invalidate();
    slow.resolve('mentor reply');
    const result = await resultPromise;
    assert.strictEqual(result.stale, true);
  });

  await t('unrelated scopes can coexist', async () => {
    const guard = createNarrationGuard();
    const a = deferred();
    const b = deferred();
    const analysis = guard.guardedFetch('analysis', () => a.promise, { code: 'x=1', file: 'main.py' }, () => ({ code: 'x=1', file: 'main.py' }));
    const learning = guard.guardedFetch('learning', () => b.promise, { code: 'x=1', file: 'main.py' }, () => ({ code: 'x=1', file: 'main.py' }));
    a.resolve('analysis');
    b.resolve('learning');
    assert.strictEqual((await analysis).stale, false);
    assert.strictEqual((await learning).stale, false);
  });

  await t('new request aborts previous request in same scope', async () => {
    const guard = createNarrationGuard();
    let aborted = false;
    const first = guard.begin('voice-command', { code: 'x=1', file: 'main.py' });
    if (first.signal) first.signal.addEventListener('abort', () => { aborted = true; });
    const second = guard.begin('voice-command', { code: 'x=2', file: 'main.py' });
    assert.strictEqual(aborted, true);
    assert.strictEqual(first.active({ code: 'x=1', file: 'main.py' }), false);
    assert.strictEqual(second.active({ code: 'x=2', file: 'main.py' }), true);
  });

  console.log(passed + ' narration guard tests passed');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
