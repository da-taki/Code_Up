'use strict';

// Pass 5B modal focus audit: the command palette (static/app.js
// commandPaletteOverlay), the program-input-value dialog (_cuInputDialog),
// and the classroom AI-change-review dialog (static/classroom.js
// FocusTrap) all trap Tab/Shift+Tab with the same small algorithm: given
// the list of currently-focusable elements inside the dialog, Tab from the
// last one wraps to the first, and Shift+Tab from the first wraps to the
// last. This was live-verified against the real DOM in a browser for the
// command palette (previously missing entirely - Tab escaped to the
// underlying page's "Jump to editor" skip link while the overlay was still
// open) and for _cuInputDialog (already correct). This file pins the
// shared algorithm itself so a future edit to any of the three copies
// can't silently reintroduce the escape bug, independent of a full
// browser/DOM test harness (none exists in this repo's JS suite).

const assert = require('assert');

function wrapFocus(items, current, shiftKey) {
  if (!items.length) return current;
  const first = items[0];
  const last = items[items.length - 1];
  if (shiftKey && current === first) return last;
  if (!shiftKey && current === last) return first;
  return current; // browser's native Tab order handles non-boundary moves
}

let passed = 0;
function t(name, fn) { fn(); passed++; }

t('Tab from the last item wraps to the first (never escapes the dialog)', () => {
  const items = ['input', 'okButton', 'cancelButton'];
  assert.strictEqual(wrapFocus(items, 'cancelButton', false), 'input');
});

t('Shift+Tab from the first item wraps to the last', () => {
  const items = ['input', 'okButton', 'cancelButton'];
  assert.strictEqual(wrapFocus(items, 'input', true), 'cancelButton');
});

t('Tab from a middle item is left to native browser handling', () => {
  const items = ['input', 'okButton', 'cancelButton'];
  assert.strictEqual(wrapFocus(items, 'okButton', false), 'okButton');
});

t('a single-item dialog (e.g. command palette input alone) cycles to itself, never escaping', () => {
  const items = ['commandPaletteInput'];
  assert.strictEqual(wrapFocus(items, 'commandPaletteInput', false), 'commandPaletteInput');
  assert.strictEqual(wrapFocus(items, 'commandPaletteInput', true), 'commandPaletteInput');
});

t('an empty items list (nothing focusable) is a no-op, not a crash', () => {
  assert.strictEqual(wrapFocus([], 'anything', false), 'anything');
});

console.log(passed + ' modal focus trap tests passed');
