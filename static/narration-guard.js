(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.CodeUpNarrationGuard = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function createNarrationGuard(deps) {
    const options = deps || {};
    const controllers = new Map();
    const generations = new Map();
    let globalGeneration = 0;

    function nextGeneration(scope) {
      const next = (generations.get(scope) || 0) + 1;
      generations.set(scope, next);
      return next;
    }

    function abortScope(scope) {
      const existing = controllers.get(scope);
      if (existing && typeof existing.abort === 'function') {
        try { existing.abort(); } catch (e) {}
      }
      controllers.delete(scope);
    }

    function invalidate(scope) {
      if (scope) {
        abortScope(scope);
        nextGeneration(scope);
        return;
      }
      globalGeneration += 1;
      for (const key of Array.from(controllers.keys())) abortScope(key);
    }

    function begin(scope, context) {
      const name = scope || 'default';
      abortScope(name);
      const generation = nextGeneration(name);
      const globalAtStart = globalGeneration;
      const Controller = options.AbortController || (typeof AbortController !== 'undefined' ? AbortController : null);
      const controller = Controller ? new Controller() : null;
      if (controller) controllers.set(name, controller);
      const snapshot = Object.assign({}, context || {});

      function active(currentContext) {
        if (globalAtStart !== globalGeneration) return false;
        if (generations.get(name) !== generation) return false;
        if (controller && controller.signal && controller.signal.aborted) return false;
        const now = currentContext || {};
        if (Object.prototype.hasOwnProperty.call(snapshot, 'code') && now.code !== snapshot.code) return false;
        if (Object.prototype.hasOwnProperty.call(snapshot, 'file') && now.file !== snapshot.file) return false;
        if (Object.prototype.hasOwnProperty.call(snapshot, 'runId') && now.runId !== snapshot.runId) return false;
        return true;
      }

      function finish() {
        if (generations.get(name) === generation) controllers.delete(name);
      }

      return {
        scope: name,
        generation,
        signal: controller ? controller.signal : undefined,
        active,
        finish,
      };
    }

    async function guardedFetch(scope, fetcher, context, currentContext) {
      const guard = begin(scope, context);
      try {
        const value = await fetcher(guard);
        if (!guard.active(typeof currentContext === 'function' ? currentContext() : currentContext)) {
          return { stale: true, value: undefined, guard };
        }
        return { stale: false, value, guard };
      } catch (error) {
        if (error && error.name === 'AbortError') return { stale: true, value: undefined, guard };
        throw error;
      } finally {
        guard.finish();
      }
    }

    return { begin, invalidate, guardedFetch, _debugGenerations: generations };
  }

  return { createNarrationGuard };
});
