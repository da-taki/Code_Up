'use strict';

let editor;
window._editorReady = false;
window._editorReadyQueue = [];
let audioCtx = null;
let snippetsCache = [];
let pendingConfirm = null;
let lastSpokenText = null;
let isListening = false;
let recognition = null;
let _restartTimer = null;
let _loadingSnippets = false;
let _apiKeyConfigured = false;
let _apiKeyPromptShown = false;

// Utility: calculate indentation level (spaces and tabs)
function getIndentLevel(line) {
  let indent = 0;
  for (let i = 0; i < line.length; i++) {
    if (line[i] === ' ')      indent += 0.25;
    else if (line[i] === '\t') indent += 1;
    else break;
  }
  return indent;
}

// Escape regex special characters in user-supplied strings
function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ---------- APP STATE ----------
const AppState = {
  isListening:  false,
  isSpeaking:   false,
  isExecuting:  false,
};

// Single helper to toggle visibility using the `hidden` attribute consistently.
// Using style.display would permanently override the `hidden` attribute from HTML.
function showEl(el) {
  if (!el) return;
  el.removeAttribute('hidden');
}
function hideEl(el) {
  if (!el) return;
  el.setAttribute('hidden', '');
}

// ---------- SPEECH MANAGER ----------
const SpeechManager = (function () {
  try {
    const queue = [];
    let currentUtterance = null;

    function dequeue() {
      if (currentUtterance || !queue.length) return;
      speakNow(queue.shift());
    }

    function speakNow(item) {
      if (!('speechSynthesis' in window) || !item || !item.text) {
        if (item && item.resolve) item.resolve();
        return;
      }
      AppState.isSpeaking = true;
      currentUtterance = new SpeechSynthesisUtterance(item.text);
      currentUtterance.rate  = item.rate  || 1;
      currentUtterance.pitch = item.pitch || 1;
      currentUtterance.lang  = (typeof getLanguage === 'function' && getLanguage() === 'hi') ? 'hi-IN' : 'en-US';
      let finished = false;
      const cleanup = () => {
        if (finished) return;
        finished = true;
        AppState.isSpeaking = false;
        currentUtterance = null;
        if (item.timeoutId) clearTimeout(item.timeoutId);
        if (item.resolve) item.resolve();
        dequeue();
      };

      currentUtterance.onend  = cleanup;
      currentUtterance.onerror = cleanup;
      item.timeoutId = setTimeout(cleanup, 30000);
      window.speechSynthesis.speak(currentUtterance);
    }

    function enqueue(text, opts = {}) {
      return new Promise(resolve => {
        queue.push({ text, ...opts, resolve });
        dequeue();
      });
    }

    function cancelAll() {
      queue.length = 0;
      try { window.speechSynthesis.cancel(); } catch (e) {}
      AppState.isSpeaking = false;
      currentUtterance = null;
    }

    return { enqueue, cancelAll };
  } catch (e) {
    return { enqueue: () => Promise.resolve(), cancelAll: () => {} };
  }
})();

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ---------- SONIFICATION MANAGER ----------
const SonificationManager = (function () {
  const jobs = new Map();
  const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function ensureAudio() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(e => console.warn('AudioContext resume failed:', e));
    }
    return audioCtx;
  }

  function playTone(freq, duration = 0.08, vol = 0.1) {
    if (REDUCED) return;
    try {
      const ctx = ensureAudio();
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value   = vol;
      osc.frequency.value = freq;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + duration);
      setTimeout(() => {
        try { osc.disconnect(); gain.disconnect(); } catch (e) {}
      }, (duration + 0.1) * 1000);
    } catch (e) {}
  }

  return {
    startJob(id)         { jobs.set(id, []); },
    pushTimer(id, t)     { const a = jobs.get(id); if (a) a.push(t); },
    cancelJob(id)        { (jobs.get(id) || []).forEach(t => clearTimeout(t)); jobs.delete(id); },
    clearAll()           { for (const [, a] of jobs) a.forEach(t => clearTimeout(t)); jobs.clear(); },
    playTone,
  };
})();

// ---------- ERROR BEACON MANAGER ----------
const ErrorBeaconManager = (function () {
  let intervalId = null;
  let currentLine = null;
  let currentSeverity = null;

  function start(line, severity = 'high') {
    stop();
    currentLine = line; currentSeverity = severity;
    const freq     = severity === 'high' ? 800 : severity === 'medium' ? 600 : 400;
    const interval = severity === 'high' ? 2000 : severity === 'medium' ? 3000 : 4000;
    intervalId = setInterval(() => SonificationManager.playTone(freq, 0.12, 0.05), interval);
  }

  function stop() {
    if (intervalId) { clearInterval(intervalId); intervalId = null; }
    currentLine = null; currentSeverity = null;
  }

  return {
    start, stop,
    downgrade(s) { if (currentLine) start(currentLine, s); },
    getState: () => ({ active: !!intervalId, line: currentLine, severity: currentSeverity }),
  };
})();

// ---------- AUDIO CUES ----------
function cueSuccess() { SonificationManager.playTone(900, 0.05, 0.08); }
function cueError()   { SonificationManager.playTone(200, 0.15, 0.08); }

const TONES = {
  indent0: 200, indent1: 300, indent2: 450, indent3: 600, indent4: 800,
  function: 900, class: 1000, loop: 400, conditional: 500, comment: 150, blank: 100,
};

function sonifyLine(lineContent, indentLevel) {
  const trimmed = lineContent.trim();
  SonificationManager.playTone(TONES[`indent${Math.min(indentLevel, 4)}`], 0.05, 0.08);
  setTimeout(() => {
    if (!trimmed)                                              SonificationManager.playTone(TONES.blank,        0.03, 0.05);
    else if (trimmed.startsWith('#'))                          SonificationManager.playTone(TONES.comment,      0.06, 0.06);
    else if (trimmed.startsWith('def '))                       SonificationManager.playTone(TONES.function,     0.08, 0.1);
    else if (trimmed.startsWith('class '))                     SonificationManager.playTone(TONES.class,        0.08, 0.12);
    else if (trimmed.startsWith('for ') || trimmed.startsWith('while ')) SonificationManager.playTone(TONES.loop, 0.08, 0.09);
    else if (trimmed.startsWith('if ') || trimmed.startsWith('elif ') || trimmed.startsWith('else')) SonificationManager.playTone(TONES.conditional, 0.08, 0.09);
  }, 60);
}

// ---------- LINE READING ----------
async function readLineEnhanced(line) {
  const model = getModel();
  if (!model) return;
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) {
    const msg = `Line ${line} is out of range. File has ${maxLine} lines.`;
    out(msg); speak(msg); return;
  }
  try {
    const res  = await fetch('/read-line-context', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), line }),
    });
    const data = await res.json();
    if (data.success) {
      sonifyLine(data.content, data.indent_level);
      setTimeout(() => { out(data.response); speak(data.response); }, 150);
    }
  } catch (e) {
    console.error(e);
    speak('Failed to read line context.');
  }
}

// ---------- BLOCK SONIFICATION ----------
async function sonifyCurrentBlock() {
  const model = getModel();
  if (!model) return;
  const pos         = editor.getPosition() || { lineNumber: 1 };
  const currentLine = pos.lineNumber;
  const lines       = getCode().split('\n');
  const currentIndent = getIndentLevel(lines[currentLine - 1]);

  let startLine = currentLine;
  let endLine   = currentLine;

  for (let i = currentLine - 2; i >= 0; i--) {
    if (getIndentLevel(lines[i]) < currentIndent && lines[i].trim()) { startLine = i + 1; break; }
  }
  for (let i = currentLine; i < lines.length; i++) {
    if (getIndentLevel(lines[i]) < currentIndent && lines[i].trim()) { endLine = i + 1; break; }
    if (i === lines.length - 1) endLine = i + 1;
  }

  SonificationManager.clearAll();
  speak(`Sonifying block from line ${startLine} to line ${endLine}`);

  const jobId = Date.now();
  SonificationManager.startJob(jobId);
  let delay = 0;
  for (let i = startLine - 1; i < endLine; i++) {
    const line   = lines[i];
    const indent = Math.floor(getIndentLevel(line));
    const t = setTimeout(() => { try { sonifyLine(line, indent); } catch (e) {} }, delay);
    SonificationManager.pushTimer(jobId, t);
    delay += 120;
  }
  const fin = setTimeout(() => { speak('Block sonification complete.'); SonificationManager.cancelJob(jobId); }, delay + 200);
  SonificationManager.pushTimer(jobId, fin);
}

// ---------- NAVIGATION HISTORY ----------
let navigationHistory = [];
let historyIndex = -1;
const MAX_HISTORY = 50;

function recordNavigation(line) {
  if (navigationHistory.length > 0 && navigationHistory[navigationHistory.length - 1] === line) return;
  navigationHistory.push(line);
  if (navigationHistory.length > MAX_HISTORY) navigationHistory.shift();
  historyIndex = navigationHistory.length - 1;
}

function navigateBack() {
  if (!ensureNotExecuting(() => navigateBack(), 'navigate back')) return;
  if (historyIndex <= 0) { speak('Already at the oldest position in history.'); return; }
  SpeechManager.cancelAll();
  historyIndex--;
  const line = navigationHistory[historyIndex];
  gotoLine(line, false);
  speak(`Navigated back to line ${line}.`);
}

function navigateForward() {
  if (!ensureNotExecuting(() => navigateForward(), 'navigate forward')) return;
  if (historyIndex >= navigationHistory.length - 1) { speak('Already at the newest position in history.'); return; }
  SpeechManager.cancelAll();
  historyIndex++;
  const line = navigationHistory[historyIndex];
  gotoLine(line, false);
  speak(`Navigated forward to line ${line}.`);
}

function showNavigationHistory() {
  if (navigationHistory.length === 0) { speak('Navigation history is empty.'); out('Navigation history is empty.'); return; }
  const history = navigationHistory.slice(-10).map((line, i) => `${i + 1}. Line ${line}`).join('\n');
  out(`Recent navigation history:\n${history}`);
  speak(`You have ${navigationHistory.length} positions in history. Showing last 10.`);
  speak(history);
}

// ---------- VARIABLE TRACKING ----------
async function listVariables() {
  if (!ensureNotExecuting(() => listVariables(), 'list variables')) return;
  const model = getModel();
  if (!model) return;
  const pos = editor.getPosition() || { lineNumber: 1 };
  showAI('Analyzing variables...');
  speak('Analyzing variables in scope.');
  try {
    const res  = await fetch('/track-variables', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), line: pos.lineNumber }),
    });
    const data = await res.json();
    if (data.success) {
      if (data.variables.length === 0) {
        out(`No variables found in ${data.current_scope}.`);
        speak(`No variables found in ${data.current_scope}.`);
      } else {
        const varList = data.variables.map(v =>
          `${v.name} (${v.phonetic}): used ${v.usage_count} times, defined at line ${v.first_line}`
        ).join('\n');
        out(`Variables in ${data.current_scope}:\n\n${varList}`);
        speak(`Found ${data.variables.length} variables in ${data.current_scope}.`);
        SpeechManager.cancelAll();
        data.variables.slice(0, 5).forEach(v => speak(`${v.phonetic}, used ${v.usage_count} times.`));
        if (data.variables.length > 5) speak(`And ${data.variables.length - 5} more. Check output for full list.`);
      }
    } else {
      const msg = data.message || 'Failed to analyze variables.';
      out(msg); speak(msg);
    }
  } catch (e) {
    console.error(e); out('Variable tracking failed.'); speak('Variable tracking failed.');
  } finally {
    hideAI();
  }
}

async function findVariable(varName) {
  if (!varName) { speak('Please specify a variable name.'); return; }
  showAI(`Finding variable ${varName}...`);
  speak(`Finding all uses of ${varName}.`);
  try {
    const res  = await fetch('/find-variable-usage', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), variable: varName }),
    });
    const data = await res.json();
    if (data.success) {
      const usageList = data.usages.map(u => `Line ${u.line} (${u.type}): ${u.content}`).join('\n');
      out(`Variable '${varName}' (${data.phonetic}):\nFound ${data.count} usages:\n\n${usageList}`);
      speak(`Found ${data.count} usages of ${data.phonetic}.`);
      data.usages.slice(0, 3).forEach(u => speak(`Line ${u.line}: ${u.type}.`));
      if (data.count > 3) speak(`And ${data.count - 3} more. Check output for details.`);
      if (data.usages.length > 0) gotoLine(data.usages[0].line, false);
    } else {
      out(data.message); speak(data.message);
    }
  } catch (e) {
    console.error(e); out('Variable search failed.'); speak('Variable search failed.');
  } finally {
    hideAI();
  }
}

// ---------- ERROR BEACON ----------
async function checkSyntaxErrors() {
  showAI('Checking for errors...');
  speak('Checking code for errors.');
  try {
    const res  = await fetch('/check-syntax', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode() }),
    });
    const data = await res.json();
    if (data.success && !data.has_errors) {
      out('✓ No errors detected. Code looks good!');
      speak('No errors detected. Code looks good!');
      srAnnounce('No errors detected');
      stopErrorBeacon();
    } else if (data.success && data.has_errors) {
      const errorList = data.errors.map(e => `Line ${e.line || 'unknown'}: ${e.type} - ${e.message}`).join('\n');
      out(`⚠ Found ${data.error_count} error(s):\n\n${errorList}`);
      speak(`Found ${data.error_count} errors.`);
      srAnnounce('Found ' + data.error_count + ' error' + (data.error_count !== 1 ? 's' : ''));
      data.errors.forEach(e => speak(`${e.type} on line ${e.line || 'unknown'}.`));
      if (data.errors.length > 0 && data.errors[0].line > 0) {
        ErrorBeaconManager.start(data.errors[0].line, data.errors[0].severity);
        gotoLine(data.errors[0].line, false);
      }
    }
  } catch (e) {
    console.error(e); out('Syntax check failed.'); speak('Syntax check failed.');
  } finally {
    hideAI();
  }
}

function stopErrorBeacon() {
  ErrorBeaconManager.stop();
  window.executionTrace = [];
}

function locateError() { checkSyntaxErrors(); }

// ---------- HELP ----------
function showHelp() {
  if (!ensureNotExecuting(() => showHelp(), 'show help')) return;
  const helpText = `
CODEUP VOICE COMMANDS:

EXECUTION:
- "run" or "execute code"
- "analyze code"
- "fix code"

NAVIGATION:
- "go to line [number]"
- "read line [number]"
- "next line" / "previous line"
- "read current line"
- "go back" / "go forward" (history)
- "where am I?" (context)

VARIABLES:
- "what variables are available?"
- "find variable [name]"

ERRORS:
- "check for errors"
- "where is the error?"
- "stop error beacon"

CODE STRUCTURE:
- "sonify block" (Alt+S)
- "read line with context" (Alt+L)
- "summarize file"

SNIPPETS:
- "save snippet named [name]"
- "load snippet [number]"

EDITING:
- "clear editor"
- "delete line [number]"

GENERATION:
- "generate code for [task]"
- "advise on code"

UTILITIES:
- "repeat" (last action)
- "say that again" (last speech)
- "help" (this menu)

KEYBOARD SHORTCUTS:
- Escape: Stop speech immediately
- Ctrl+Enter: Run code
- Alt+S: Sonify block
- Alt+L: Read line with context
- Alt+V: List variables
- Alt+E: Check for errors
- Alt+H: Show this help
- Alt+Left/Right: Navigate history
- Alt+Home/End: Jump to top/bottom

NEW VOICE COMMANDS:
- "save snippet named [name]" — save with a specific name
- "restart tutorial" / "start over" — restart onboarding
- "insert function called [name]" — add a function
- "insert a for loop" — add a loop
- "replace line 5 with return x" — edit a line
- "suggest next line" → "choose 1/2/3" — autocomplete
- "tell the story" — narrate what your code did
- "set breakpoint at line 10" — audio debugger
- "watch variable x" — report x at breakpoints
- "continue" — run to next breakpoint
- "learning mode" — start mentor/quiz mode
- "quiz me on loops" — get a quiz question
- "explain variables" — concept explanation
- "bug challenge" — find and fix a bug
  `.trim();
  out(helpText);
  speak('Help menu displayed. Check the output panel for all commands.');
}

function getFileStats() {
  const model = getModel();
  if (!model) return;
  const code  = getCode();
  const stats = `File statistics:\n- ${model.getLineCount()} lines\n- ${code.split(/\s+/).filter(w => w.length > 0).length} words\n- ${code.length} characters`;
  out(stats);
  speak(`File has ${model.getLineCount()} lines.`);
}

function goToTop()    { gotoLine(1); speak('Jumped to top of file.'); }
function goToBottom() {
  const model = getModel();
  if (!model) return;
  gotoLine(model.getLineCount());
  speak('Jumped to bottom of file.');
}

function copyCode() {
  if (!ensureNotExecuting(() => copyCode(), 'copy code')) return;
  navigator.clipboard.writeText(getCode()).then(() => {
    speak('Code copied to clipboard.'); out('Code copied to clipboard.');
  }).catch(() => speak('Failed to copy code.'));
}

function pasteCode() {
  if (!ensureNotExecuting(() => pasteCode(), 'paste code')) return;
  navigator.clipboard.readText().then(text => {
    setCode(text); speak('Code pasted from clipboard.'); out('Code pasted from clipboard.');
  }).catch(() => speak('Failed to paste code.'));
}

// ---------- TTS ----------
function speak(text, opts = {}) {
  if (!text) return;
  lastSpokenText = text;
  SpeechManager.enqueue(text, opts).catch(() => {});
}
function speakOutput() {
  if (!ensureNotExecuting(() => speakOutput(), 'speak output')) return;
  const t = document.getElementById('output').textContent || 'No output available.';
  speak(t, { forceFull: true });
}
function repeatLastSpeech() {
  speak(lastSpokenText || 'There is nothing to repeat yet.', { forceFull: true });
}

// ---------- AI BUBBLE ----------
// Use showEl/hideEl (hidden attribute) to avoid inline style conflicting with HTML's `hidden` attr.
function showAI(msg) {
  const b = document.getElementById('aiBubble');
  if (!b) return;
  b.textContent = msg;
  showEl(b);
}
function hideAI() {
  hideEl(document.getElementById('aiBubble'));
}

// ---------- API KEY MODAL ----------
function openApiKeyModal() {
  const modal = document.getElementById('apiKeyModal');
  const input = document.getElementById('apiKeyInput');
  if (!modal) return;
  showEl(modal);
  // Focus the input so screen readers announce the dialog
  requestAnimationFrame(() => {
    if (input) input.focus();
  });
  speak('AI features need a Gemini API key. Get one for free at ai dot google dot dev. Paste it into the field and press Enter, or press Escape to cancel and continue without AI.');
  srAnnounce('Gemini API key required');
}

function closeApiKeyModal() {
  const modal = document.getElementById('apiKeyModal');
  if (modal) hideEl(modal);
  if (editor) editor.focus();
}

async function submitApiKey() {
  const input = document.getElementById('apiKeyInput');
  if (!input) return;
  const key = input.value.trim();
  if (!key) { speak('Please enter a key, or press Escape to cancel.'); return; }

  showAI('Testing API key...');
  speak('Testing your API key.');
  try {
    const res = await fetch('/api-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    });
    const data = await res.json();
    if (data.success) {
      _apiKeyConfigured = true;
      input.value = '';
      closeApiKeyModal();
      speak('API key configured. AI features are now available.');
      srAnnounce('API key configured');
    } else {
      speak('That key did not work. ' + (data.error || 'Please try again or press Escape to cancel.'));
    }
  } catch (e) {
    console.error(e);
    speak('Could not configure key. Please check your connection and try again.');
  } finally {
    hideAI();
  }
}

// Detect when the backend says AI is unconfigured and prompt the user once.
function maybePromptForApiKey(responseText) {
  if (_apiKeyConfigured || _apiKeyPromptShown) return false;
  if (!responseText) return false;
  const lower = String(responseText).toLowerCase();
  if (lower.includes('not configured') || lower.includes('insert_api_key_here')) {
    _apiKeyPromptShown = true;
    setTimeout(openApiKeyModal, 800);
    return true;
  }
  return false;
}

// ---------- MONACO SETUP ----------
window.MonacoEnvironment = {
  getWorkerUrl: function () { return '/static/python.worker.js'; },
};

require.config({ paths: { vs: '/static/vendor/monaco/min/vs' } });

require(['vs/editor/editor.main'], function () {
  if (editor) { console.warn('Editor already initialized, skipping'); return; }

  editor = monaco.editor.create(document.getElementById('editor'), {
    value:                'print("Hello CodeUp!")',
    language:             'python',
    theme:                document.body.classList.contains('theme-night') ? 'vs-dark' : 'vs',
    fontSize:             16,
    minimap:              { enabled: false },
    automaticLayout:      true,
    // Accessibility — critical for screen reader users
    accessibilitySupport: 'on',
    ariaLabel:            'Python code editor. Use arrow keys to navigate, type to edit. Press Escape to stop speech, Control Shift M to toggle voice control, F1 for editor commands.',
    // Slightly larger line height helps screen-reader sync with cursor
    lineHeight:           24,
    // Make sure tabs and tab-trapping work as users expect
    tabSize:              4,
    insertSpaces:         true,
    // Word wrap helps users who navigate by line
    wordWrap:             'on',
  });

  let _structureDebounce = null;
  editor.onDidChangeModelContent(() => {
    clearTimeout(_structureDebounce);
    _structureDebounce = setTimeout(updateStructurePanel, 600);
  });

  window._editorReady = true;
  try { window._resolveEditorReady(); } catch (e) {}
  while (window._editorReadyQueue && window._editorReadyQueue.length) {
    const fn = window._editorReadyQueue.shift();
    try { fn(); } catch (e) { console.warn('editor queued fn failed', e); }
  }

  registerPythonAutocomplete();
  registerEditorShortcuts();
  loadSnippets();
});

function getModel() { return editor && editor.getModel(); }
function getCode()  { return (editor && editor.getValue()) || ''; }
function getLanguage() { return (document.getElementById('languageSelector') || {}).value || 'en'; }

function setCode(v) {
  if (typeof ErrorBeaconManager !== 'undefined') ErrorBeaconManager.stop();
  SpeechManager.cancelAll();
  // Reset execution state but keep navigation history — a fix/generate should not erase history
  lastSpokenText = null;
  window.executionTrace = [];
  window.traceIndex = 0;
  if (editor) editor.setValue(v);
}

function out(t) { document.getElementById('output').textContent = t; }

// ---------- PENDING ACTIONS ----------
const pendingActions = [];
async function flushPendingActions() {
  while (pendingActions.length) {
    const a = pendingActions.shift();
    try { await a(); } catch (e) { console.error('Pending action failed', e); }
  }
}
function enqueueAfterExecution(fn) {
  pendingActions.push(fn);
  SpeechManager.enqueue('Queued your request; will run after execution completes.');
}
function ensureNotExecuting(actionFn, description) {
  if (AppState.isExecuting) { enqueueAfterExecution(actionFn); return false; }
  return true;
}

// ---------- RUN ----------
async function runCode() {
  SpeechManager.cancelAll();

  // Heads-up if the code uses input() — the sandbox will block it mid-run
  // but it's friendlier to warn the user before they wait for execution.
  const codeToCheck = getCode();
  if (/\binput\s*\(/.test(codeToCheck)) {
    speak('Heads up: your code uses input(), which is not supported in the sandbox. Replace input() with a hardcoded value before running. For example, replace name equals input quote your name quote with name equals quote Alice quote. Continuing anyway.');
    out('⚠ Warning: input() is not supported. Replace it with a hardcoded value.\nExample: name = "Alice"  instead of  name = input("Your name?")\n\nRunning anyway...');
  }

  AppState.isExecuting = true;
  cueSuccess();
  if (!/\binput\s*\(/.test(codeToCheck)) out('Running...');
  showAI('Running code...');
  speak('Running code.');
  srAnnounce('Running code');
  try {
    const res  = await fetch('/run', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    // Keep the full trace so debugContinue() can hit breakpoints in long programs.
    // Backend now caps at 5000 events, so this won't be unbounded.
    window.executionTrace = data.trace || [];
    window.traceIndex = 0;

    if (data.success) {
      out(data.output);
      cueSuccess();
      ErrorBeaconManager.stop();
      speak('Program output:');
      speak(data.output);
      if (data.semantic_issues && data.semantic_issues.length) {
        data.semantic_issues.forEach(e => speak(`${e.category}. ${e.message}`));
      }
      // Tutorial hook — single source of truth lives in index.html's TutorialState
      if (typeof window._tutorialOnRunSuccess === 'function') {
        setTimeout(function () { window._tutorialOnRunSuccess(); }, 2000);
      }
    } else {
      out('ERROR:\n' + (data.error || ''));
      cueError();
      speak('There was an error.');
      if (data.explanation) {
        speak('Analyzing the error, please wait.');
        setTimeout(function () { speak(data.explanation); }, 500);
      } else {
        speak('No explanation available. Check the output panel for details.');
      }
    }
  } catch (e) {
    out('System error.'); console.error(e); cueError(); speak('System error.');
  } finally {
    AppState.isExecuting = false;
    hideAI();
    await flushPendingActions();
  }
}

// ---------- ANALYZE ----------
async function analyzeCode() {
  cueSuccess(); out('Analyzing...'); showAI('Analyzing code with AI...'); speak('Analyzing code.');
  try {
    const res  = await fetch('/analyze', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    out(data.analysis || 'No analysis.');
    if (maybePromptForApiKey(data.analysis)) return;
    speak(data.analysis ? 'Analysis ready.' : 'No analysis available.');
    if (data.analysis) speak(data.analysis);
  } catch (e) {
    out('Analyze failed.'); console.error(e); cueError(); speak('Analyze failed.');
  } finally {
    hideAI();
  }
}

// ---------- SUMMARIZE ----------
async function summarizeFile() {
  cueSuccess(); out('Summarizing file...'); showAI('Summarizing this file...'); speak('Summarizing this file.');
  try {
    const res  = await fetch('/summarize', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    const summary = data.summary || 'No summary generated.';
    out(summary); speak(summary);
  } catch (e) {
    console.error(e); out('Summary failed.'); speak('Summary failed.');
  } finally {
    speak('Task completed.'); hideAI();
  }
}

// ---------- ADVISE ----------
async function adviseCode() {
  cueSuccess(); out('Advising on your code...'); showAI('Generating improvement suggestions...'); speak('Advising on your code.');
  try {
    const res  = await fetch('/advise', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    const advice = data.advice || 'No advice generated.';
    out(advice); speak('Here are some ways you can improve this code.'); speak(advice);
  } catch (e) {
    out('Advice failed.'); console.error(e); cueError(); speak('Advice failed.');
  } finally {
    speak('Task completed.'); hideAI();
  }
}

// ---------- FIX ----------
async function fixCode() {
  const before = getCode();
  cueSuccess(); out('Fixing...'); showAI('Fixing code with AI...'); speak('Fixing code.');
  try {
    const res  = await fetch('/fix', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: before, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) {
      setCode(data.code);
      out('Code fixed.'); speak('Code has been fixed.');
      const diffRes  = await fetch('/diff-explain', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ before, after: data.code, language: getLanguage() }),
      });
      const diffData = await diffRes.json();
      if (diffData.explanation) { out(diffData.explanation); speak(diffData.explanation); }
    } else {
      out('Fix failed.'); speak('Fix failed.');
    }
  } catch (e) {
    console.error(e); out('Fix failed.'); speak('Fix failed.');
  } finally {
    hideAI();
  }
}

// ---------- DESCRIBE LINE ----------
async function describeLine(line) {
  showAI('Describing line ' + line); speak('Describing line ' + line);
  try {
    const res  = await fetch('/describe', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), line, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) { out(data.description); speak(data.description); }
    else { const msg = data.message || 'Describe failed.'; out(msg); speak(msg); }
  } catch (e) {
    out('Describe failed.'); console.error(e); cueError(); speak('Describe failed.');
  } finally {
    hideAI();
  }
}

// ---------- GENERATE CODE ----------
async function generateCode(prompt) {
  if (!prompt) { speak('Please provide a description of what you want to generate.'); return; }
  showAI('Generating code for: ' + prompt); speak('Generating code for ' + prompt);
  if (getCode().trim()) speak('Warning. This will overwrite the current code.');
  try {
    const res  = await fetch('/generate-code', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ prompt, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success && data.code) {
      window.executionTrace = []; window.traceIndex = 0;
      setCode(data.code); out('Code generated and inserted into editor.'); cueSuccess(); speak('Code generated and inserted into the editor.');
    } else {
      out('Code generation failed.'); cueError(); speak('Code generation failed.');
    }
  } catch (e) {
    console.error(e); out('Code generation failed.'); cueError(); speak('Code generation failed.');
  } finally {
    hideAI();
  }
}

// ---------- NAVIGATION ----------
function gotoLine(line, record = true) {
  if (!ensureNotExecuting(() => gotoLine(line, record), 'go to line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) { const msg = `Line ${line} is out of range. File has ${maxLine} lines.`; out(msg); speak(msg); return; }
  if (record) recordNavigation(line);
  try {
    editor.setPosition({ lineNumber: line, column: 1 });
  } catch (e) { console.warn('gotoLine: setPosition failed', e); return; }
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  SpeechManager.cancelAll();
  speak(`Moved to line ${line}. ${text || 'Empty line.'}`);
}

function readLine(line) {
  if (!ensureNotExecuting(() => readLine(line), 'read line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) { const msg = `Line ${line} is out of range. File has ${maxLine} lines.`; out(msg); speak(msg); return; }
  SpeechManager.cancelAll();
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Line ${line}: ${text || 'Empty line.'}`);
}

function readCurrentLine() {
  if (!ensureNotExecuting(() => readCurrentLine(), 'read current line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const pos  = editor.getPosition() || { lineNumber: 1 };
  SpeechManager.cancelAll();
  const text = model.getLineContent(pos.lineNumber);
  out(`Line ${pos.lineNumber}: ${text}`);
  speak(`Current line ${pos.lineNumber}: ${text || 'Empty line.'}`);
}

function nextLine() {
  if (!ensureNotExecuting(() => nextLine(), 'next line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const pos  = editor.getPosition() || { lineNumber: 1 };
  const line = Math.min(model.getLineCount(), pos.lineNumber + 1);
  SpeechManager.cancelAll();
  editor.setPosition({ lineNumber: line, column: 1 });
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Line ${line}: ${text || 'Empty line.'}`);
}

function prevLine() {
  if (!ensureNotExecuting(() => prevLine(), 'previous line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const pos  = editor.getPosition() || { lineNumber: 1 };
  const line = Math.max(1, pos.lineNumber - 1);
  SpeechManager.cancelAll();
  editor.setPosition({ lineNumber: line, column: 1 });
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Line ${line}: ${text || 'Empty line.'}`);
}

// ---------- EDITING ----------
function clearEditor() {
  // Full reset including navigation history on explicit user clear
  navigationHistory = [];
  historyIndex = -1;
  setCode('');
  out('Editor cleared.');
  speak('Editor cleared. Previous code erased.');
}

function deleteLine(line) {
  const model = getModel();
  if (!model) return;
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) { const msg = `Line ${line} is out of range.`; out(msg); speak(msg); return; }
  const text = model.getLineContent(line);

  // Handle last line correctly: delete from end of previous line to end of last line
  let range;
  if (line === maxLine && maxLine > 1) {
    const prevLen = model.getLineContent(maxLine - 1).length;
    range = new monaco.Range(maxLine - 1, prevLen + 1, maxLine, text.length + 1);
  } else {
    range = new monaco.Range(line, 1, Math.min(line + 1, maxLine + 1), 1);
  }

  model.pushEditOperations([], [{ range, text: '' }], () => null);
  out(`Deleted line ${line}: ${text}`);
  speak(`Deleted line ${line}.`);
}

// ---------- SNIPPETS ----------
function srAnnounce(msg) {
  const el = document.getElementById('srAnnouncer');
  if (!el) return;
  // Clear then set forces screen readers to re-announce even if text is same
  el.textContent = '';
  setTimeout(function () { el.textContent = msg; }, 50);
}

async function saveSnippet() {
  // Keep old function as fallback but redirect to accessible version
  await saveSnippetAccessible();
}

async function saveSnippetAccessible(voiceName) {
  const input = document.getElementById('snippetNameInput');
  const name  = voiceName || (input && input.value.trim()) || 'Untitled';
  await saveSnippetWithName(name);
  if (input) input.value = '';
  srAnnounce('Snippet saved: ' + name);
}

async function saveSnippetWithName(name) {
  await fetch('/snippets', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, code: getCode() }),
  });
  await loadSnippets();
  speak(`Snippet saved as ${name}.`);
}

async function loadSnippets() {
  if (_loadingSnippets) return;
  _loadingSnippets = true;
  const list = document.getElementById('snippetList');
  if (!list) { _loadingSnippets = false; return; }
  try {
    const res  = await fetch('/snippets');
    const data = await res.json();
    snippetsCache = data.snippets || [];

    const fragment = document.createDocumentFragment();
    snippetsCache.forEach(sn => {
      const div = document.createElement('div');
      div.className  = 'snippet-item';
      div.textContent = sn.name || 'Untitled Snippet';
      div.dataset.id  = sn.id;
      // Keyboard accessibility: focusable and activatable via Enter/Space
      div.setAttribute('tabindex', '0');
      div.setAttribute('role', 'button');
      div.setAttribute('aria-label', `Load snippet: ${sn.name || 'Untitled'}`);
      div.addEventListener('click',   () => setCode(sn.code));
      div.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCode(sn.code); } });
      fragment.appendChild(div);
    });

    list.innerHTML = '';
    list.appendChild(fragment);
  } finally {
    _loadingSnippets = false;
  }
}

async function loadSnippetById(id) {
  const sn = snippetsCache.find(s => String(s.id) === String(id));
  if (!sn) { speak(`Snippet ${id} not found.`); out(`Snippet ${id} not found.`); return; }
  setCode(sn.code); speak(`Loaded snippet ${id}: ${sn.name}.`); out(`Loaded snippet ${id}: ${sn.name}.`);
}

async function deleteSnippetById(id) {
  await fetch(`/snippets/${id}`, { method: 'DELETE' });
  await loadSnippets();
  speak(`Deleted snippet ${id}.`); out(`Deleted snippet ${id}.`);
}

async function renameSnippetById(id, newName) {
  await fetch(`/snippets/${id}`, {
    method:  'PUT',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name: newName }),
  });
  await loadSnippets();
  speak(`Renamed snippet ${id} to ${newName}.`); out(`Renamed snippet ${id} to ${newName}.`);
}

// ---------- COMMAND HANDLER ----------
async function handleConfirmedAction(action, payload) {
  if (action === 'run')              await runCode();
  else if (action === 'analyze')     await analyzeCode();
  else if (action === 'fix')         await fixCode();
  else if (action === 'speak')       speakOutput();
  else if (action === 'read_output') speakOutput();
  else if (action === 'describe_line') await describeLine(payload && payload.line ? payload.line : 1);
  else if (action === 'next_step' || action === 'previous_step' || action === 'what_changed') {
    const text = (payload && (payload.speech || payload.message)) || 'No trace event available.';
    out(text); speak(text);
  }
  else if (action === 'generate_code') await generateCode(payload && payload.prompt ? payload.prompt : '');
  else if (action === 'save_snippet_named') await saveSnippetAccessible(payload && payload.name ? payload.name : 'Untitled');
  else if (action === 'load_snippet')    await loadSnippetById(payload && payload.id);
  else if (action === 'delete_snippet')  await deleteSnippetById(payload && payload.id);
  else if (action === 'rename_snippet')  await renameSnippetById(payload && payload.id, payload && payload.new_name ? payload.new_name : 'Renamed');
  else if (action === 'goto_line')       gotoLine(payload && payload.line ? payload.line : 1);
  else if (action === 'read_line')       readLine(payload && payload.line ? payload.line : 1);
  else if (action === 'read_current_line') readCurrentLine();
  else if (action === 'next_line')       nextLine();
  else if (action === 'prev_line')       prevLine();
  else if (action === 'clear_editor')    clearEditor();
  else if (action === 'delete_line')     deleteLine(payload && payload.line ? payload.line : 1);
  else if (action === 'summarize')       await summarizeFile();
  else if (action === 'advise')          await adviseCode();
  else if (action === 'read_line_enhanced') await readLineEnhanced(payload && payload.line ? payload.line : (editor && editor.getPosition() ? editor.getPosition().lineNumber : 1));
  else if (action === 'sonify_block')    await sonifyCurrentBlock();
  else if (action === 'sonify_function') await sonifyFunction(payload && payload.function_name ? payload.function_name : '');
  else if (action === 'sonify_class')    await sonifyClass(payload && payload.class_name ? payload.class_name : '');
  else if (action === 'find_function')   speak('Find function: ' + (payload && payload.function_name ? payload.function_name : ''));
  else if (action === 'find_class')      speak('Find class: ' + (payload && payload.class_name ? payload.class_name : ''));
  else if (action === 'show_structure')  toggleStructurePanel();
  else if (action === 'list_variables')  await listVariables();
  else if (action === 'find_variable')   await findVariable(payload && payload.variable ? payload.variable : '');
  else if (action === 'check_errors')    await checkSyntaxErrors();
  else if (action === 'locate_error')    locateError();
  else if (action === 'stop_beacon')     { stopErrorBeacon(); speak('Error beacon stopped.'); }
  else if (action === 'go_back')         navigateBack();
  else if (action === 'go_forward')      navigateForward();
  else if (action === 'show_history')    showNavigationHistory();
  else if (action === 'help')            showHelp();
  else if (action === 'file_stats')      getFileStats();
  else if (action === 'go_to_top')       goToTop();
  else if (action === 'go_to_bottom')    goToBottom();
  else if (action === 'copy_code')       copyCode();
  else if (action === 'paste_code')         pasteCode();
  else if (action === 'restart_tutorial')   restartTutorial();
  else if (action === 'start_tutorial')     { if (window.TutorialController) window.TutorialController.open(); }
  else if (action === 'skip_tutorial')      { if (window.TutorialController) window.TutorialController.close(); }
  else if (action === 'tutorial_next')      { if (window.TutorialController && window.TutorialController.active) window.TutorialController.next(); }
  else if (action === 'insert_function')    insertFunctionVoice(payload && payload.function_name);
  else if (action === 'insert_class')       insertClassVoice(payload && payload.class_name);
  else if (action === 'insert_loop')        insertLoopVoice(payload && payload.loop_var, payload && payload.iterable);
  else if (action === 'insert_if')          insertIfVoice(payload && payload.condition);
  else if (action === 'append_line')        appendLineVoice(payload && payload.text);
  else if (action === 'replace_line')       replaceLineVoice(payload && payload.line_number, payload && payload.text);
  else if (action === 'insert_line')        insertLineVoice(payload && payload.line_number, payload && payload.text);
  else if (action === 'add_parameter')      addParameterVoice(payload && payload.param_name, payload && payload.function_name);
  else if (action === 'suggest_next')       await suggestNextLine();
  else if (action === 'choose_suggestion')  chooseSuggestion(payload && payload.choice);
  else if (action === 'story_mode')         await tellExecutionStory();
  else if (action === 'set_breakpoint')     setBreakpoint(payload && payload.line_number);
  else if (action === 'clear_breakpoints')  clearBreakpoints();
  else if (action === 'watch_variable')     watchVariable(payload && payload.variable);
  else if (action === 'debug_continue')     debugContinue();
  else if (action === 'debug_step_in')      speak('Step in is not yet supported in sandbox mode.');
  else if (action === 'debug_step_out')     speak('Step out is not yet supported in sandbox mode.');
  else if (action === 'mentor_mode')        startMentorMode();
  else if (action === 'quiz_me')            await quizMe(payload && payload.topic);
  else if (action === 'explain_concept')    await explainConcept(payload && payload.concept);
  else if (action === 'bug_challenge')      await bugChallenge();
}

function tryResolveConfirmation(txt) {
  if (!pendingConfirm) return false;
  if (pendingConfirm.expiresAt && Date.now() > pendingConfirm.expiresAt) {
    pendingConfirm = null;
    speak('Confirmation timed out. Please repeat the command if you still want to proceed.');
    return true;
  }
  const options = pendingConfirm.options || [];
  const lower   = txt.toLowerCase();
  let chosen    = null;
  for (const opt of options) { if (lower.includes(opt)) chosen = opt; }
  if (!chosen) { speak('I did not understand your choice. Please say one of: ' + options.join(' or ')); return true; }
  pendingConfirm = null;
  speak('Confirmed ' + chosen + '.');
  handleConfirmedAction(chosen, {});
  return true;
}

async function handleCommandText(txt) {
  const field = document.getElementById('voiceText');
  if (field) field.value = txt;

  if (pendingConfirm) {
    tryResolveConfirmation(txt);
    return;
  }

  try {
    const res  = await fetch('/voice-command', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text: txt }),
    });
    const data = await res.json();

    if (!data.success) { speak(data.message || 'Command not recognized.'); return; }

    const action = data.action;

    if (action === 'unknown') {
      const heard = data.heard || txt;
      speak(`I heard "${heard}", but I could not match it to a command. Say help to hear available commands.`);
      out(`Unrecognized command: "${heard}"`);
      return;
    }

    if (action === 'confirm') {
      pendingConfirm = {
        options:   data.options || [],
        expiresAt: Date.now() + 15000,
        context:   { heard: data.heard || txt, raw: txt },
      };
      const opts  = (data.options || []).join(' or ');
      const heard = data.heard || txt;
      speak(`I am not fully sure what you meant. I heard "${heard}". Did you mean ${opts}?`);
      out(`Command ambiguous.\nHeard: "${heard}"\nPossible actions: ${opts}\nPlease say one option to confirm.`);
      return;
    }

    await handleConfirmedAction(action, data);
  } catch (err) {
    console.error(err); speak('Voice command failed.');
  }
}

async function submitCommand() {
  const field = document.getElementById('voiceText');
  if (!field) return;
  const txt = field.value.trim();
  if (!txt) return;
  await handleCommandText(txt);
  field.value = '';
}

// ---------- VOICE ----------
function toggleVoice() {
  if (isListening) stopListening(); else startListening();
}

function startListening() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    const isFirefox = navigator.userAgent.toLowerCase().includes('firefox');
    const msg = isFirefox
      ? 'Voice input is not supported in Firefox. Please open CodeUp in Chrome or Edge for voice control. Keyboard shortcuts and the typed command box still work in Firefox.'
      : 'Speech recognition is not supported in this browser. Please use Chrome or Edge for voice input. Keyboard shortcuts and the typed command box still work.';
    out(msg);
    speak(msg);
    srAnnounce('Speech recognition unavailable');
    return;
  }
  if (isListening) { speak('Already listening.'); return; }

  recognition = new SR();
  recognition.continuous      = true;
  recognition.interimResults  = false;
  recognition.lang            = getLanguage() === 'hi' ? 'hi-IN' : 'en-US';
  recognition._restartAttempts = 0;
  recognition._maxRestarts     = 5;
  recognition._backoffBase     = 300;

  recognition.onstart = () => {
    isListening = true;
    AppState.isListening = true;
    // Reset restart counter on clean session start
    recognition._restartAttempts = 0;
    const btn = document.getElementById('voiceButton');
    if (btn) {
      btn.textContent = '🎤 Voice (ON)';
      btn.setAttribute('aria-pressed', 'true');
      btn.classList.add('cu-button-voice--active');
    }
    cueSuccess();
    speak('Voice control activated. Say help at any time to hear available commands.');
    console.log('Voice: Listening started');
  };

  recognition.onresult = async (event) => {
    const transcript = event.results[event.results.length - 1][0].transcript;
    console.log('Voice heard:', transcript);
    await handleVoiceCommand(transcript);
  };

  recognition.onerror = (event) => {
    console.error('Voice error:', event.error);
    if (event.error === 'no-speech') { speak('No speech detected. Still listening.'); return; }
    if (event.error === 'aborted')   { isListening = false; AppState.isListening = false; return; }

    recognition._restartAttempts = (recognition._restartAttempts || 0) + 1;
    if (recognition._restartAttempts > recognition._maxRestarts) {
      speak('Voice recognition repeatedly failed. Voice control is paused.');
      isListening = false; AppState.isListening = false; return;
    }

    const delay = recognition._backoffBase * Math.pow(2, recognition._restartAttempts - 1);
    speak('Voice recognition error. Attempting to restart.');
    setTimeout(() => { try { if (isListening) recognition.start(); } catch (e) { console.error('Restart failed', e); } }, delay);
  };

  recognition.onend = () => {
    console.log('Voice: Session ended');
    if (!isListening) return;

    // Only increment restart counter on unexpected ends (not clean user stops)
    // _restartAttempts is already reset to 0 on onstart so this only accumulates during a session
    recognition._restartAttempts = (recognition._restartAttempts || 0) + 1;
    if (recognition._restartAttempts > recognition._maxRestarts) {
      speak('Voice recognition stopped after repeated failures.');
      isListening = false; AppState.isListening = false; return;
    }

    if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
    const delay = recognition._backoffBase * Math.pow(2, recognition._restartAttempts - 1);
    _restartTimer = setTimeout(() => {
      _restartTimer = null;
      try { if (isListening) recognition.start(); } catch (e) { console.error('Auto-restart failed', e); }
    }, delay);
  };

  try {
    recognition.start();
  } catch (e) {
    console.error('Failed to start recognition:', e);
    speak('Failed to start voice control.');
    isListening = false; AppState.isListening = false;
  }
}

function stopListening() {
  if (!recognition || !isListening) { speak('Voice control is not active.'); return; }
  if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
  isListening = false;
  AppState.isListening = false;
  const btn = document.getElementById('voiceButton');
  if (btn) {
    btn.textContent = '🎤 Voice (Off)';
    btn.setAttribute('aria-pressed', 'false');
    btn.classList.remove('cu-button-voice--active');
  }
  recognition.stop();
  speak('Voice control deactivated.');
  console.log('Voice: Listening stopped');
}

// ---------- VOICE COMMAND HANDLER ----------
async function handleVoiceCommand(rawText) {
  // Quiz answer intercept
  if (window._pendingQuizAnswer) {
    const t = rawText.toLowerCase().trim();
    const match = t.match(/(?:answer|option|choose)\s+([abc])|^([abc])$/);
    if (match) {
      const q = window._pendingQuizAnswer;
      window._pendingQuizAnswer = null;
      const chosen = (match[1] || match[2]).toUpperCase();
      if (chosen === q.answer) {
        SonificationManager.playTone(900, 0.1, 0.1);
        speak(`Correct! ${q.explanation}`);
        srAnnounce('Correct answer');
        out(`✓ CORRECT!\n\n${q.explanation}`);
      } else {
        SonificationManager.playTone(200, 0.15, 0.08);
        speak(`Not quite. The correct answer was ${q.answer}. ${q.explanation}`);
        srAnnounce('Wrong answer');
        out(`✗ The correct answer was ${q.answer}.\n\n${q.explanation}`);
      }
      return;
    }
  }

  // Bug challenge reveal intercept
  if (window._pendingBugChallenge) {
    const t = rawText.toLowerCase().trim();
    if (t.includes('show answer') || t.includes('give up') || t.includes('reveal') || t.includes('answer दिखाओ')) {
      const ch = window._pendingBugChallenge;
      window._pendingBugChallenge = null;
      out(`THE BUG:\n${ch.bug}\n\nFIXED CODE:\n${ch.fixed}`);
      speak(`The bug was: ${ch.bug}`);
      setTimeout(() => setCode(ch.fixed), 2000);
      srAnnounce('Answer revealed');
      return;
    }
  }

  if (pendingConfirm) {
    const handled = tryResolveConfirmation(rawText);
    if (handled) return;
    speak('Waiting for confirmation. Please respond to the pending question.');
    return;
  }

  const cleaned = rawText.toLowerCase().trim()
    .replace(/^(please|can you|could you|would you|hey|okay|ok)\s+/gi, '')
    .replace(/\s+(please|thanks|thank you)$/gi, '')
    .trim();

  console.log('Voice parsing:', cleaned);

  try {
    const res  = await fetch('/voice-command', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text: cleaned }),
    });
    const data = await res.json();

    // Handle confirm intent before the general success branch so it isn't
    // swallowed by handleConfirmedAction (which has no 'confirm' case)
    if (data.action === 'confirm') {
      pendingConfirm = {
        options:   data.options || [],
        expiresAt: Date.now() + 15000,
        context:   { heard: data.heard || rawText, raw: rawText },
      };
      const opts  = (data.options || []).join(' or ');
      const heard = data.heard || rawText;
      speak(`I am not fully sure what you meant. I heard "${heard}". Did you mean ${opts}?`);
      out(`Command ambiguous.\nHeard: "${heard}"\nPossible actions: ${opts}\nPlease say one option to confirm.`);
      return;
    }

    if (data.success && data.action && data.action !== 'unknown') {
      console.log('Backend action:', data.action);
      await handleConfirmedAction(data.action, data);
    } else {
      speak("I didn't understand that command. Say 'help' for available commands.");
      console.log('Command not recognized:', cleaned);
    }
  } catch (e) {
    console.error('Backend interpretation failed:', e);
    speak("Command not recognized. Say 'help' for available commands.");
  }
}

// ---------- KEYBOARD SHORTCUTS ----------
window.addEventListener('DOMContentLoaded', () => {
  const resumeAudio = () => {
    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  };
  document.addEventListener('click', resumeAudio);

  document.addEventListener('keydown', e => {
    resumeAudio();
    if (e.ctrlKey && e.shiftKey && e.key === 'M') { e.preventDefault(); toggleVoice(); }
    // Escape stops speech immediately. Ignored when command palette or input
    // dialog is open — those have their own Escape handlers.
    if (e.key === 'Escape') {
      const paletteOverlay = document.getElementById('commandPaletteOverlay');
      const paletteOpen = paletteOverlay && !paletteOverlay.hasAttribute('hidden');
      const dialogOpen  = !!document.getElementById('_cuInputDialog');
      if (paletteOpen || dialogOpen) return;
      if (AppState.isSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking)) {
        SpeechManager.cancelAll();
        ErrorBeaconManager.stop();
        srAnnounce('Speech stopped');
        SonificationManager.playTone(600, 0.05, 0.06);
        e.preventDefault();
      }
    }
  });

  // Command palette input — registered once here only (removed the duplicate at bottom of file)
  const paletteInput = document.getElementById('commandPaletteInput');
  if (paletteInput) {
    paletteInput.addEventListener('input', e => {
      commandPaletteSelectedIndex = 0;
      renderCommandPalette(e.target.value);
    });
    // Escape must be caught on the INPUT itself so Monaco never sees it
    paletteInput.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeCommandPalette(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); commandPaletteSelectedIndex++; renderCommandPalette(paletteInput.value); }
      else if (e.key === 'ArrowUp')   { e.preventDefault(); commandPaletteSelectedIndex--; renderCommandPalette(paletteInput.value); }
      else if (e.key === 'Enter')     { e.preventDefault(); executeCommandPaletteItem(commandPaletteSelectedIndex); }
    });
  }

  // Ctrl+Shift+P opens the palette — document-level is fine for this one
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.shiftKey && e.key === 'P') { e.preventDefault(); openCommandPalette(); }
  });

  // Click outside the container closes the palette
  const paletteOverlay = document.getElementById('commandPaletteOverlay');
  if (paletteOverlay) {
    paletteOverlay.addEventListener('click', e => {
      if (e.target === paletteOverlay) closeCommandPalette();
    });
  }
});

function registerEditorShortcuts() {
  if (window._editorShortcutsRegistered) return;
  if (!editor) return;
  window._editorShortcutsRegistered = true;

  // Ctrl+Enter: run code (advertised in UI — now actually registered)
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => { runCode(); });

  // Escape inside Monaco — stop speech immediately. Monaco normally swallows
  // Escape, so the document-level listener in DOMContentLoaded never fires
  // when focus is in the editor. Register it as an editor command too.
  editor.addCommand(monaco.KeyCode.Escape, () => {
    if (AppState.isSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking)) {
      SpeechManager.cancelAll();
      ErrorBeaconManager.stop();
      srAnnounce('Speech stopped');
      SonificationManager.playTone(600, 0.05, 0.06);
    }
  });

  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyS, () => { sonifyCurrentBlock(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyL, () => { const pos = editor.getPosition() || { lineNumber: 1 }; readLineEnhanced(pos.lineNumber); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyV, () => { listVariables(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyE, () => { checkSyntaxErrors(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyN, () => { speakNextStep(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyH, () => { showHelp(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.LeftArrow,  () => { navigateBack(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.RightArrow, () => { navigateForward(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.Home, () => { goToTop(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.End,  () => { goToBottom(); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyC, () => { copyCode(); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyV, () => { pasteCode(); });

  try { loadSnippets(); } catch (e) {}
  console.log('All accessibility features loaded.');
}

async function speakNextStep() {
  await handleCommandText('next step');
}

// ---------- STRUCTURE PANEL ----------
let lastStructureData = null;

async function updateStructurePanel() {
  if (!editor) return;
  const code    = editor.getValue();
  const panel   = document.getElementById('structurePanel');
  const content = document.getElementById('structureContent');
  if (!code.trim()) { hideEl(panel); return; }

  try {
    const res  = await fetch('/structure', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code }),
    });
    const data = await res.json();

    if (!data.success || !data.structure) {
      content.innerHTML = '<p class="structure-info">Unable to parse structure.</p>';
      showEl(panel);
      return;
    }

    lastStructureData = data.structure;
    const { imports, functions, classes, loops } = data.structure;
    let html = '';

    if (imports.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">📦 Imports</div>';
      imports.forEach(imp => {
        html += `<div class="structure-item" role="button" tabindex="0" data-line="1">
          <span class="structure-item-icon">📦</span>
          <span class="structure-item-label">${escapeHtml(imp)}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (classes.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">🏛️ Classes</div>';
      classes.forEach(cls => {
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${cls.line}" aria-label="Go to class ${escapeHtml(cls.name)} at line ${cls.line}">
          <span class="structure-item-icon">🏛️</span>
          <span class="structure-item-label">${escapeHtml(cls.name)}</span>
          <span class="structure-item-line">L${cls.line}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (functions.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">⚙️ Functions</div>';
      functions.forEach(fn => {
        const params = fn.params.map(p => p.name).join(', ');
        const asyncBadge = fn.is_async ? '<span style="color:#facc15;font-size:0.7rem;margin-right:4px;">async</span>' : '';
        const parentLabel = fn.parent_class ? `<span style="color:#64748b;font-size:0.75rem;">${escapeHtml(fn.parent_class)}.</span>` : '';
        const icon = fn.is_async ? '⚡' : '⚙️';
        const ariaLabel = `Go to ${fn.is_async ? 'async ' : ''}function ${fn.parent_class ? fn.parent_class + '.' : ''}${fn.name} at line ${fn.line}`;
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${fn.line}" aria-label="${escapeHtml(ariaLabel)}">
          <span class="structure-item-icon">${icon}</span>
          <span class="structure-item-label">${asyncBadge}${parentLabel}${escapeHtml(fn.name)}(${escapeHtml(params)})</span>
          <span class="structure-item-line">L${fn.line}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (loops.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">🔄 Loops</div>';
      loops.forEach((loop, idx) => {
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${loop.line}" aria-label="Go to loop at line ${loop.line}">
          <span class="structure-item-icon">🔄</span>
          <span class="structure-item-label">Loop #${idx + 1}</span>
          <span class="structure-item-line">L${loop.line}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (!imports.length && !functions.length && !classes.length && !loops.length) {
      html = '<p class="structure-info">No structures found.</p>';
    }

    content.innerHTML = html;
    showEl(panel);

    // Use event delegation — line numbers come from data attributes, not inline JS strings
    content.querySelectorAll('.structure-item').forEach(item => {
      const go = () => {
        const line = parseInt(item.dataset.line, 10);
        if (!isNaN(line)) gotoLine(line);
      };
      item.addEventListener('click',   go);
      item.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });

  } catch (e) {
    console.error('Structure parse error:', e);
    content.innerHTML = '<p class="structure-info">Error parsing structure.</p>';
    showEl(panel);
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ---------- AUTOCOMPLETE ----------
const PYTHON_KEYWORDS = [
  'False','None','True','and','as','assert','async','await','break','class',
  'continue','def','del','elif','else','except','finally','for','from','global',
  'if','import','in','is','lambda','nonlocal','not','or','pass','raise','return',
  'try','while','with','yield',
];

const PYTHON_BUILTINS = [
  'abs','all','any','ascii','bin','bool','bytearray','bytes','callable','chr',
  'classmethod','compile','complex','delattr','dict','dir','divmod','enumerate',
  'eval','exec','filter','float','format','frozenset','getattr','globals','hasattr',
  'hash','help','hex','id','input','int','isinstance','issubclass','iter','len',
  'list','locals','map','max','memoryview','min','next','object','oct','open','ord',
  'pow','print','property','range','repr','reversed','round','set','setattr','slice',
  'sorted','staticmethod','str','sum','super','tuple','type','vars','zip',
];

const PYTHON_SNIPPETS = {
  'if':       { label: 'if statement',         insertText: 'if ${1:condition}:\n\t${0:pass}',                                                                    kind: 'Snippet' },
  'for':      { label: 'for loop',             insertText: 'for ${1:item} in ${2:items}:\n\t${0:pass}',                                                           kind: 'Snippet' },
  'while':    { label: 'while loop',           insertText: 'while ${1:condition}:\n\t${0:pass}',                                                                  kind: 'Snippet' },
  'def':      { label: 'function',             insertText: 'def ${1:function_name}(${2:args}):\n\t"""${3:docstring}"""\n\t${0:pass}',                              kind: 'Snippet' },
  'class':    { label: 'class',                insertText: 'class ${1:ClassName}:\n\t"""${2:docstring}"""\n\tdef __init__(self):\n\t\t${0:pass}',                  kind: 'Snippet' },
  'try':      { label: 'try-except',           insertText: 'try:\n\t${1:pass}\nexcept ${2:Exception} as ${3:e}:\n\t${0:pass}',                                    kind: 'Snippet' },
  'with':     { label: 'with statement',       insertText: 'with ${1:context} as ${2:var}:\n\t${0:pass}',                                                         kind: 'Snippet' },
  'lambda':   { label: 'lambda function',      insertText: 'lambda ${1:x}: ${0:x}',                                                                              kind: 'Snippet' },
  'list-comp':{ label: 'list comprehension',   insertText: '[${1:x} for ${2:x} in ${3:items}]',                                                                  kind: 'Snippet' },
  'dict-comp':{ label: 'dict comprehension',   insertText: '{${1:k}: ${2:v} for ${3:k}, ${4:v} in ${5:items}}',                                                  kind: 'Snippet' },
  '__main__': { label: 'main block',           insertText: 'if __name__ == "__main__":\n\t${0:main()}',                                                           kind: 'Snippet' },
};

function registerPythonAutocomplete() {
  if (!monaco || !monaco.languages) return;
  monaco.languages.registerCompletionItemProvider('python', {
    provideCompletionItems(model, position) {
      const word  = model.getWordUntilPosition(position);
      const range = { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber, startColumn: word.startColumn, endColumn: word.endColumn };
      const suggestions = [];

      PYTHON_KEYWORDS.forEach(kw => suggestions.push({ label: kw, kind: monaco.languages.CompletionItemKind.Keyword, insertText: kw, range }));
      PYTHON_BUILTINS.forEach(fn => suggestions.push({ label: fn + '()', kind: monaco.languages.CompletionItemKind.Function, insertText: fn + '($0)', range, sortText: '1_' + fn }));
      Object.entries(PYTHON_SNIPPETS).forEach(([key, sn]) => suggestions.push({
        label: sn.label, kind: monaco.languages.CompletionItemKind.Snippet,
        insertText: sn.insertText, insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        range, sortText: '2_' + key,
      }));

      const codeText = model.getValue();
      const variables = [...new Set((codeText.match(/^[a-zA-Z_]\w*/gm) || []))];
      variables.forEach(v => {
        if (!PYTHON_KEYWORDS.includes(v) && !PYTHON_BUILTINS.includes(v)) {
          suggestions.push({ label: v, kind: monaco.languages.CompletionItemKind.Variable, insertText: v, range, sortText: '3_' + v });
        }
      });

      return { suggestions };
    },
    triggerCharacters: [],
  });
}

// ---------- FUNCTION / CLASS SONIFICATION ----------
async function sonifyFunction(functionName) {
  if (!functionName) { speak('Please specify a function name.'); return; }
  const lines = getCode().split('\n');
  // Escape regex metacharacters in the function name before building the pattern
  const pattern = new RegExp(`^\\s*def\\s+${escapeRegex(functionName)}\\s*\\(`, 'i');

  let startLine = -1, endLine = -1;
  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(lines[i])) {
      startLine = i + 1;
      const baseIndent = lines[i].search(/\S/);
      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].trim() === '') continue;
        if (lines[j].search(/\S/) <= baseIndent) { endLine = j; break; }
      }
      if (endLine === -1) endLine = lines.length;
      break;
    }
  }

  if (startLine === -1) { speak(`Function ${functionName} not found.`); return; }
  speak(`Sonifying function ${functionName} from line ${startLine} to ${endLine}.`);
  await sonifyRange(startLine, endLine, 'function');
}

async function sonifyClass(className) {
  if (!className) { speak('Please specify a class name.'); return; }
  const lines   = getCode().split('\n');
  const pattern = new RegExp(`^\\s*class\\s+${escapeRegex(className)}\\s*[:\\(]`, 'i');

  let startLine = -1, endLine = -1;
  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(lines[i])) {
      startLine = i + 1;
      const baseIndent = lines[i].search(/\S/);
      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].trim() === '') continue;
        if (lines[j].search(/\S/) <= baseIndent) { endLine = j; break; }
      }
      if (endLine === -1) endLine = lines.length;
      break;
    }
  }

  if (startLine === -1) { speak(`Class ${className} not found.`); return; }
  speak(`Sonifying class ${className} from line ${startLine} to ${endLine}.`);
  await sonifyRange(startLine, endLine, 'class');
}

async function sonifyRange(startLine, endLine, context = 'block') {
  const lines = getCode().split('\n');
  const contextFreqs = { function: 900, class: 1000, loop: 400, block: 600 };
  const baseFreq = contextFreqs[context] || 600;

  for (let i = startLine - 1; i < Math.min(endLine, lines.length); i++) {
    const indent = lines[i].search(/\S/);
    // Normalized volume consistent with all other sonification (0.08 not 0.3)
    SonificationManager.playTone(baseFreq + 50 * Math.min(indent / 2, 5), 0.05, 0.08);
    await sleep(100);
  }
  speak(`Finished sonifying ${context}.`);
}

// ---------- ERROR SONIFICATION ----------
const ERROR_SONIFICATION_MAP = {
  SyntaxError:       { freq: 200, duration: 300, label: 'syntax error' },
  IndentationError:  { freq: 250, duration: 250, label: 'indentation error' },
  NameError:         { freq: 400, duration: 200, label: 'name error' },
  TypeError:         { freq: 500, duration: 200, label: 'type error' },
  ValueError:        { freq: 550, duration: 200, label: 'value error' },
  AttributeError:    { freq: 600, duration: 150, label: 'attribute error' },
  KeyError:          { freq: 650, duration: 150, label: 'key error' },
  IndexError:        { freq: 700, duration: 150, label: 'index error' },
  ImportError:       { freq: 800, duration: 150, label: 'import error' },
  RuntimeError:      { freq: 900, duration: 150, label: 'runtime error' },
  ZeroDivisionError: { freq: 950, duration: 150, label: 'division by zero' },
  Warning:           { freq: 1100, duration: 100, label: 'warning' },
  Style:             { freq: 1200, duration: 80,  label: 'style issue' },
};

function classifyError(errorMessage) {
  const lower = errorMessage.toLowerCase();
  for (const [type] of Object.entries(ERROR_SONIFICATION_MAP)) {
    if (lower.includes(type.toLowerCase())) return type;
  }
  if (lower.includes('unexpected') || lower.includes('invalid')) return 'SyntaxError';
  if (lower.includes('not defined') || lower.includes('undefined')) return 'NameError';
  if (lower.includes('warning')) return 'Warning';
  return 'RuntimeError';
}

function sonifyError(errorMessage, errorLine = null) {
  const config = ERROR_SONIFICATION_MAP[classifyError(errorMessage)] || ERROR_SONIFICATION_MAP.RuntimeError;
  SonificationManager.playTone(config.freq, config.duration, 0.4);
  speak(`${config.label} on line ${errorLine || 'unknown'}. ${errorMessage.substring(0, 50)}`);
}

async function sonifyCodeIssues() {
  const lines  = getCode().split('\n');
  const issues = [];

  lines.forEach((line, idx) => {
    const indent = line.search(/\S/);
    // Inconsistent indentation check
    if (indent !== -1 && indent % 4 !== 0 && indent % 2 !== 0) {
      issues.push({ line: idx + 1, type: 'Style', message: 'Inconsistent indentation' });
    }
    // Detect bare assignment (=) inside if/while condition — heuristic
    const trimmed = line.trim();
    if (/^(if|elif|while)\s+/.test(trimmed) && /[^=!<>]=(?!=)/.test(trimmed)) {
      issues.push({ line: idx + 1, type: 'Warning', message: 'Possible assignment in condition' });
    }
  });

  if (issues.length === 0) { speak('No code issues detected.'); return; }
  speak(`Found ${issues.length} issue${issues.length !== 1 ? 's' : ''}.`);
  for (const issue of issues) { sonifyError(issue.message, issue.line); await sleep(300); }
}

// ---------- DEBUG SUGGESTIONS ----------
async function getDebugSuggestions() {
  const code = getCode();
  if (!code.trim()) { speak('Code is empty.'); return; }
  speak('Analyzing code for improvement suggestions...');
  try {
    const res  = await fetch('/debug-suggestions', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code, language: getLanguage() }),
    });
    const data = await res.json();
    if (!data.success || !data.suggestions) { out('No suggestions available.'); speak('Could not generate suggestions.'); return; }
    if (data.suggestions.length === 0) { out('Code looks good! No issues found.'); speak('Code looks good. No issues found.'); return; }

    let output = 'DEBUG SUGGESTIONS:\n\n';
    let speech = `Found ${data.suggestions.length} suggestion${data.suggestions.length !== 1 ? 's' : ''}. `;
    data.suggestions.forEach((sugg, idx) => { output += `${sugg.icon} ${sugg.text}\n\n`; speech += `Item ${idx + 1}: ${sugg.text}. `; });
    out(output); speak(speech);
    // Glyph decorations require a line number from the backend — skipping until backend provides it
  } catch (e) {
    console.error('Debug suggestions error:', e); speak('Error getting suggestions.');
  }
}

// ---------- COMMAND PALETTE ----------
const COMMAND_PALETTE_COMMANDS = [
  { id: 'run',              title: 'Run Code',           desc: 'Execute Python code',                icon: '▶',  keys: 'Ctrl+Enter',   action: () => runCode() },
  { id: 'analyze',          title: 'Analyze Code',        desc: 'AI analysis of code',               icon: '🔍', keys: 'Ctrl+Alt+A',   action: () => analyzeCode() },
  { id: 'fix',              title: 'Fix Code',            desc: 'Automatically fix errors',          icon: '🔧', keys: 'Ctrl+Alt+F',   action: () => fixCode() },
  { id: 'advise',           title: 'Get Advice',          desc: 'Suggestions for improvements',      icon: '💡', keys: 'Ctrl+Alt+I',   action: () => adviseCode() },
  { id: 'goto_line',        title: 'Go to Line',          desc: 'Jump to specific line',             icon: '➡️', keys: 'Ctrl+G',       action: () => showInputDialog('Enter line number:', gotoLine) },
  { id: 'read_line',        title: 'Read Line',           desc: 'Read current line with context',    icon: '📖', keys: '',             action: () => readCurrentLine() },
  { id: 'next_line',        title: 'Next Line',           desc: 'Move to next line',                 icon: '↓',  keys: 'Down',         action: () => nextLine() },
  { id: 'prev_line',        title: 'Previous Line',       desc: 'Move to previous line',             icon: '↑',  keys: 'Up',           action: () => prevLine() },
  { id: 'show_structure',   title: 'Show Structure',      desc: 'Display code navigation map',       icon: '🗺️', keys: 'Ctrl+Shift+S', action: () => toggleStructurePanel() },
  { id: 'sonify_block',     title: 'Sonify Block',        desc: 'Hear current code block',           icon: '🔊', keys: 'Alt+S',        action: () => sonifyCurrentBlock() },
  { id: 'next_step',        title: 'Next step',           desc: 'Step forward in execution trace',   icon: '⏭',  keys: 'Alt+N',        action: () => speakNextStep() },
  { id: 'prev_step',        title: 'Previous step',       desc: 'Step back in execution trace',      icon: '⏮',  keys: '',             action: () => handleCommandText('previous step') },
  { id: 'save_snippet',     title: 'Save Snippet',        desc: 'Save code as snippet',              icon: '💾', keys: 'Ctrl+S',       action: () => saveSnippet() },
  { id: 'list_variables',   title: 'List Variables',      desc: 'Show all variables in scope',       icon: '📊', keys: 'Ctrl+Alt+V',   action: () => listVariables() },
  { id: 'check_errors',     title: 'Check Errors',        desc: 'Find syntax errors',                icon: '⚠️', keys: '',             action: () => checkSyntaxErrors() },
  { id: 'locate_error',     title: 'Locate Error',        desc: 'Jump to first error',               icon: '🎯', keys: 'Ctrl+Alt+E',   action: () => locateError() },
  { id: 'clear_editor',     title: 'Clear Editor',        desc: 'Delete all code',                   icon: '🗑️', keys: '',             action: () => clearEditor() },
  { id: 'copy_code',        title: 'Copy Code',           desc: 'Copy to clipboard',                 icon: '📋', keys: 'Ctrl+C',       action: () => copyCode() },
  { id: 'paste_code',       title: 'Paste Code',          desc: 'Paste from clipboard',              icon: '📌', keys: 'Ctrl+V',       action: () => pasteCode() },
  { id: 'debug_suggestions',title: 'Debug Suggestions',   desc: 'Get AI debugging hints',            icon: '🔬', keys: '',             action: () => getDebugSuggestions() },
  { id: 'sonify_issues',    title: 'Sonify Issues',       desc: 'Hear code problems',                icon: '🔊', keys: '',             action: () => sonifyCodeIssues() },
  { id: 'help',             title: 'Show Help',           desc: 'Display all commands',              icon: '❓', keys: 'F1',           action: () => showHelp() },
];

let commandPaletteSelectedIndex = 0;

function openCommandPalette() {
  const overlay = document.getElementById('commandPaletteOverlay');
  const input   = document.getElementById('commandPaletteInput');
  showEl(overlay);
  commandPaletteSelectedIndex = 0;
  renderCommandPalette('');
  // Focus after a tick so Monaco doesn't reclaim focus immediately
  requestAnimationFrame(() => { if (input) { input.focus(); speak('Command palette open. Type to search, arrow keys to navigate, Enter to select, Escape to close.'); } });
}

function closeCommandPalette() {
  hideEl(document.getElementById('commandPaletteOverlay'));
  // Return focus to editor so keyboard users aren't stranded
  if (editor) editor.focus();
  speak('Command palette closed.');
}


function renderCommandPalette(filterText) {
  const resultsContainer = document.getElementById('commandPaletteResults');
  if (!resultsContainer) return;

  const query    = filterText.toLowerCase().trim();
  let   filtered = COMMAND_PALETTE_COMMANDS;
  if (query) {
    filtered = COMMAND_PALETTE_COMMANDS.filter(cmd =>
      cmd.title.toLowerCase().includes(query) ||
      cmd.desc.toLowerCase().includes(query)  ||
      cmd.id.toLowerCase().includes(query)
    );
  }

  if (filtered.length === 0) { resultsContainer.innerHTML = '<div class="command-palette-empty">No commands found</div>'; commandPaletteSelectedIndex = -1; return; }
  commandPaletteSelectedIndex = Math.max(0, Math.min(commandPaletteSelectedIndex, filtered.length - 1));

  resultsContainer.innerHTML = filtered.map((cmd, idx) => `
    <div class="command-palette-item ${idx === commandPaletteSelectedIndex ? 'selected' : ''}"
         role="option" aria-selected="${idx === commandPaletteSelectedIndex}" data-index="${idx}">
      <span class="command-palette-item-icon">${cmd.icon}</span>
      <div class="command-palette-item-main">
        <div class="command-palette-item-title">${escapeHtml(cmd.title)}</div>
        <div class="command-palette-item-desc">${escapeHtml(cmd.desc)}</div>
      </div>
      ${cmd.keys ? `<div class="command-palette-item-shortcut">${escapeHtml(cmd.keys)}</div>` : ''}
    </div>
  `).join('');

  resultsContainer.querySelectorAll('.command-palette-item').forEach((item, idx) => {
    item.addEventListener('click', () => executeCommandPaletteItem(idx));
  });
}

function executeCommandPaletteItem(index) {
  const input    = document.getElementById('commandPaletteInput');
  const query    = input ? input.value.toLowerCase().trim() : '';
  let   filtered = COMMAND_PALETTE_COMMANDS;
  if (query) filtered = COMMAND_PALETTE_COMMANDS.filter(cmd => cmd.title.toLowerCase().includes(query) || cmd.desc.toLowerCase().includes(query) || cmd.id.toLowerCase().includes(query));
  if (index < 0 || index >= filtered.length) return;
  closeCommandPalette();
  try { filtered[index].action(); } catch (e) { console.error('Command error:', e); speak('Error executing command.'); }
}

function toggleStructurePanel() {
  const panel = document.getElementById('structurePanel');
  if (!panel) return;
  // Use hidden attribute consistently (not style.display)
  if (panel.hasAttribute('hidden')) {
    showEl(panel);
    speak('Structure panel shown.');
  } else {
    hideEl(panel);
    speak('Structure panel hidden.');
  }
}

// ---------- VOICE CODE EDITING ----------

function insertAtCursor(text) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const pos = editor.getPosition() || { lineNumber: model.getLineCount(), column: 1 };
  const line = pos.lineNumber;
  const col  = model.getLineMaxColumn(line);
  model.pushEditOperations([], [{
    range: new monaco.Range(line, col, line, col),
    text:  '\n' + text,
  }], () => null);
  const newLine = line + text.split('\n').length;
  editor.setPosition({ lineNumber: newLine, column: 1 });
  editor.revealLineInCenter(newLine);
}

function insertFunctionVoice(functionName) {
  const name = functionName || 'my_function';
  const code = `def ${name}():\n    pass`;
  insertAtCursor(code);
  speak(`Inserted function ${name}. Cursor is on the pass line. Add your code there.`);
  srAnnounce(`Function ${name} inserted`);
}

function insertClassVoice(className) {
  const name = className || 'MyClass';
  const code = `class ${name}:\n    def __init__(self):\n        pass`;
  insertAtCursor(code);
  speak(`Inserted class ${name} with an init method.`);
  srAnnounce(`Class ${name} inserted`);
}

function insertLoopVoice(loopVar, iterable) {
  const v = loopVar  || 'i';
  const it = iterable || 'range(10)';
  const code = `for ${v} in ${it}:\n    pass`;
  insertAtCursor(code);
  speak(`Inserted for loop. Variable ${v} in ${it}. Replace pass with your loop body.`);
  srAnnounce('For loop inserted');
}

function insertIfVoice(condition) {
  const cond = condition || 'True';
  const code = `if ${cond}:\n    pass`;
  insertAtCursor(code);
  speak(`Inserted if statement checking ${cond}. Replace pass with your code.`);
  srAnnounce('If statement inserted');
}

function appendLineVoice(text) {
  if (!text) { speak('No text to append.'); return; }
  insertAtCursor(text);
  speak(`Appended: ${text}`);
  srAnnounce('Line appended');
}

function replaceLineVoice(lineNum, text) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (lineNum < 1 || lineNum > maxLine) { speak(`Line ${lineNum} is out of range.`); return; }
  const col = model.getLineMaxColumn(lineNum);
  model.pushEditOperations([], [{
    range: new monaco.Range(lineNum, 1, lineNum, col),
    text:  text,
  }], () => null);
  editor.setPosition({ lineNumber: lineNum, column: 1 });
  speak(`Replaced line ${lineNum} with: ${text}`);
  srAnnounce(`Line ${lineNum} replaced`);
}

function insertLineVoice(lineNum, text) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (lineNum < 1 || lineNum > maxLine + 1) { speak(`Line ${lineNum} is out of range.`); return; }
  model.pushEditOperations([], [{
    range: new monaco.Range(lineNum, 1, lineNum, 1),
    text:  text + '\n',
  }], () => null);
  editor.setPosition({ lineNumber: lineNum, column: 1 });
  speak(`Inserted at line ${lineNum}: ${text}`);
  srAnnounce(`Line inserted at ${lineNum}`);
}

function addParameterVoice(paramName, functionName) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const code  = getCode();
  const lines = code.split('\n');

  // Find the target function
  let targetLine = -1;
  const pattern = functionName
    ? new RegExp(`^\\s*def\\s+${escapeRegex(functionName)}\\s*\\(`)
    : /^\s*def\s+\w+\s*\(/;

  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(lines[i])) { targetLine = i + 1; break; }
  }

  if (targetLine === -1) {
    speak(functionName ? `Function ${functionName} not found.` : 'No function found to add parameter to.');
    return;
  }

  const lineContent = model.getLineContent(targetLine);
  const parenClose  = lineContent.lastIndexOf(')');
  if (parenClose === -1) { speak('Could not find function signature.'); return; }

  const parenOpen = lineContent.indexOf('(');
  const existing  = lineContent.slice(parenOpen + 1, parenClose).trim();
  const newParams = existing ? `${existing}, ${paramName}` : paramName;
  const newLine   = lineContent.slice(0, parenOpen + 1) + newParams + lineContent.slice(parenClose);

  model.pushEditOperations([], [{
    range: new monaco.Range(targetLine, 1, targetLine, model.getLineMaxColumn(targetLine)),
    text:  newLine,
  }], () => null);

  const fname = functionName || 'the function';
  speak(`Added parameter ${paramName} to ${fname}.`);
  srAnnounce(`Parameter ${paramName} added`);
}

// ---------- SEMANTIC AUTOCOMPLETE ----------

let _lastSuggestions = [];
let _suggestionsLang = 'en';

async function suggestNextLine() {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const pos  = editor.getPosition() || { lineNumber: model.getLineCount() };
  showAI('Thinking of next lines...');
  speak('Analyzing context. Suggesting next lines.');

  try {
    const res  = await fetch('/suggest-next', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), line: pos.lineNumber, language: getLanguage() }),
    });
    const data = await res.json();

    if (!data.success || !data.suggestions || data.suggestions.length === 0) {
      speak('Could not generate suggestions. Try writing a bit more code first.');
      hideAI(); return;
    }

    _lastSuggestions = data.suggestions;
    _suggestionsLang = getLanguage();

    const numbered = data.suggestions.map((s, i) => `${i + 1}: ${s}`).join('\n');
    out(`Suggested next lines:\n${numbered}\n\nSay "choose 1", "choose 2", or "choose 3" to insert.`);
    srAnnounce(`${data.suggestions.length} suggestions ready`);

    speak(`Here are ${data.suggestions.length} suggestions.`);
    data.suggestions.forEach((s, i) => speak(`Option ${i + 1}: ${s}`));
    speak('Say choose 1, choose 2, or choose 3 to insert your choice.');

  } catch (e) {
    console.error(e);
    speak('Suggestion failed. Please try again.');
  } finally {
    hideAI();
  }
}

function chooseSuggestion(choice) {
  if (!_lastSuggestions || _lastSuggestions.length === 0) {
    speak('No suggestions available. Say suggest next line first.');
    return;
  }

  let idx;
  if (typeof choice === 'number') {
    idx = choice - 1;
  } else {
    const words = { 'one': 0, 'two': 1, 'three': 2, 'first': 0, 'second': 1, 'third': 2 };
    idx = words[String(choice).toLowerCase()] !== undefined
      ? words[String(choice).toLowerCase()]
      : parseInt(choice, 10) - 1;
  }

  if (isNaN(idx) || idx < 0 || idx >= _lastSuggestions.length) {
    speak(`Invalid choice. Please say choose 1, 2, or 3.`);
    return;
  }

  const chosen = _lastSuggestions[idx];
  insertAtCursor(chosen);
  _lastSuggestions = [];
  speak(`Inserted: ${chosen}`);
  srAnnounce('Suggestion inserted');
}

// ---------- EXECUTION STORY MODE ----------

async function tellExecutionStory() {
  showAI('Narrating your execution...');
  speak('Narrating what happened when your code ran.');
  try {
    const res  = await fetch('/execution-story', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) {
      out(data.story);
      speak(data.story);
      srAnnounce('Execution story ready');
    } else {
      out(data.story || 'No story available.');
      speak(data.story || 'Run your code first, then ask for the story.');
    }
  } catch (e) {
    console.error(e);
    speak('Could not narrate execution. Please try again.');
  } finally {
    hideAI();
  }
}

// ---------- AUDIO BREAKPOINT DEBUGGER ----------

let _breakpoints = new Set();
let _watchedVars = new Set();
let _breakpointDecorations = [];

function setBreakpoint(lineNum) {
  if (!lineNum) { speak('Please specify a line number for the breakpoint.'); return; }
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (lineNum < 1 || lineNum > maxLine) { speak(`Line ${lineNum} is out of range.`); return; }

  _breakpoints.add(lineNum);

  // Visual decoration — red dot in gutter
  _breakpointDecorations = editor.deltaDecorations(_breakpointDecorations, [
    ...Array.from(_breakpoints).map(l => ({
      range: new monaco.Range(l, 1, l, 1),
      options: {
        isWholeLine: true,
        className:   'bp-line',
        glyphMarginClassName: 'bp-glyph',
        glyphMarginHoverMessage: { value: `Breakpoint at line ${l}` },
      }
    }))
  ]);

  SonificationManager.playTone(600, 0.1, 0.1);
  speak(`Breakpoint set at line ${lineNum}.`);
  srAnnounce(`Breakpoint line ${lineNum}`);
  out(`Breakpoints active: ${Array.from(_breakpoints).sort((a,b)=>a-b).join(', ')}`);
}

function clearBreakpoints() {
  _breakpoints.clear();
  _watchedVars.clear();
  _breakpointDecorations = editor.deltaDecorations(_breakpointDecorations, []);
  speak('All breakpoints cleared.');
  srAnnounce('Breakpoints cleared');
  out('All breakpoints removed.');
}

function watchVariable(varName) {
  if (!varName) { speak('Please specify a variable name to watch.'); return; }
  _watchedVars.add(varName);
  speak(`Now watching variable ${varName}. I will report its value at each breakpoint.`);
  srAnnounce(`Watching ${varName}`);
  out(`Watched variables: ${Array.from(_watchedVars).join(', ')}`);
}

function debugContinue() {
  // Walk the trace forward until we hit a breakpoint line
  const storage = window._sessionStorage || {};
  const trace   = window.executionTrace || [];
  if (!trace.length) { speak('No trace available. Run your code first.'); return; }

  let idx = window.traceIndex || 0;
  let hitBreakpoint = false;

  while (idx < trace.length) {
    const event = trace[idx];
    idx++;
    if (event.type === 'line_exec' && _breakpoints.has(event.line)) {
      hitBreakpoint = true;
      window.traceIndex = idx;

      // Report watched variables at this breakpoint
      const stateEvents = trace.slice(0, idx).filter(e => e.type === 'state_change' && e.line === event.line);
      let varReport = '';
      if (_watchedVars.size > 0 && stateEvents.length > 0) {
        const lastState = stateEvents[stateEvents.length - 1];
        const relevant  = (lastState.changes || []).filter(c =>
          Array.from(_watchedVars).some(v => c.startsWith(v))
        );
        if (relevant.length) varReport = ' ' + relevant.join(', ');
      }

      SonificationManager.playTone(800, 0.15, 0.12);
      gotoLine(event.line, false);
      speak(`Hit breakpoint at line ${event.line}.${varReport || ''}`);
      srAnnounce(`Breakpoint hit line ${event.line}`);
      return;
    }
  }

  if (!hitBreakpoint) {
    window.traceIndex = 0;
    speak('No more breakpoints hit. Execution complete.');
    srAnnounce('Execution complete');
  }
}

// ---------- MENTOR / LEARNING MODE ----------

let _mentorActive = false;
let _currentQuiz  = null;

function startMentorMode() {
  _mentorActive = true;
  const lang = getLanguage();
  if (lang === 'hi') {
    speak('Learning mode शुरू हुआ। आप कह सकते हैं: quiz करो, bug challenge दो, या कोई concept समझाओ जैसे variables समझाओ।');
  } else {
    speak('Learning mode started. You can say: quiz me, bug challenge, or explain a concept — for example, explain loops.');
  }
  srAnnounce('Learning mode active');
  out('LEARNING MODE ACTIVE\n\nCommands:\n- "quiz me on loops"\n- "explain variables"\n- "bug challenge"\n- "explain conditionals"\n- "quiz me on functions"');
}

async function quizMe(topic) {
  const t = topic || 'Python basics';
  showAI(`Creating quiz on ${t}...`);
  speak(`Creating a quiz question on ${t}.`);
  try {
    const res  = await fetch('/mentor/quiz', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ topic: t, language: getLanguage() }),
    });
    const data = await res.json();
    if (!data.success) { speak('Could not generate quiz. Try again.'); hideAI(); return; }

    const q = data.quiz;
    _currentQuiz = q;

    const display = `QUIZ: ${q.question}\n\n${q.options.join('\n')}\n\nSay "answer A", "answer B", or "answer C".`;
    out(display);
    srAnnounce('Quiz question ready');
    speak(q.question);
    q.options.forEach(o => speak(o));
    speak('Say answer A, answer B, or answer C.');

    // Store expected answer — handleVoiceCommand checks this cleanly
    window._pendingQuizAnswer = {
      answer: q.answer,
      explanation: q.explanation,
    };

  } catch (e) {
    console.error(e);
    speak('Quiz failed. Please try again.');
  } finally {
    hideAI();
  }
}

async function explainConcept(concept) {
  const c = concept || 'variables';
  showAI(`Explaining ${c}...`);
  speak(`Explaining ${c}.`);
  try {
    const res  = await fetch('/mentor/explain', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ concept: c, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) {
      out(data.explanation);
      speak(data.explanation);
      srAnnounce(`${c} explained`);
    } else {
      speak('Could not explain that concept. Try again.');
    }
  } catch (e) {
    console.error(e);
    speak('Explanation failed. Please try again.');
  } finally {
    hideAI();
  }
}

async function bugChallenge() {
  showAI('Generating bug challenge...');
  speak('Generating a bug fixing challenge. Get ready.');
  try {
    const res  = await fetch('/mentor/bug-challenge', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ language: getLanguage() }),
    });
    const data = await res.json();
    if (!data.success) { speak('Could not generate challenge. Try again.'); hideAI(); return; }

    const ch = data.challenge;
    setCode(ch.code);
    out(`BUG CHALLENGE\n\nFind and fix the bug in the editor.\nHint: ${ch.hint}\n\nSay "show answer" when ready to reveal.`);
    srAnnounce('Bug challenge loaded');
    speak(`Bug challenge loaded into editor. ${ch.hint}. Say show answer when you are ready.`);

    // Store pending bug challenge — handleVoiceCommand checks this cleanly
    window._pendingBugChallenge = {
      bug: ch.bug,
      fixed: ch.fixed,
    };

  } catch (e) {
    console.error(e);
    speak('Challenge failed. Please try again.');
  } finally {
    hideAI();
  }
}

function restartTutorial() {
  if (window.TutorialController) {
    window.TutorialController.open();
    speak('Tutorial restarted from the beginning.');
  }
}

function showInputDialog(promptText, callback) {
  // window.prompt is inaccessible to screen readers — use an inline modal instead
  const existing = document.getElementById('_cuInputDialog');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.id = '_cuInputDialog';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', promptText);
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:30000;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';

  overlay.innerHTML = `
    <div style="background:var(--bg-panel);border:1px solid var(--border-strong);border-radius:12px;padding:24px;min-width:280px;max-width:400px;width:90%;color:var(--text-main);">
      <label id="_cuDialogLabel" style="display:block;color:var(--text-main);margin-bottom:12px;font-family:inherit;font-size:0.9rem;">${promptText}</label>
      <input id="_cuDialogInput" type="text" aria-labelledby="_cuDialogLabel"
             style="width:100%;padding:8px 12px;border-radius:6px;border:1px solid var(--border-soft);background:var(--bg-soft);color:var(--text-main);font-family:inherit;font-size:1rem;margin-bottom:12px;"
      />
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button id="_cuDialogCancel" style="padding:8px 16px;border-radius:6px;border:1px solid var(--border-soft);background:var(--bg-soft);color:var(--text-main);cursor:pointer;font-family:inherit;">Cancel</button>
        <button id="_cuDialogOk"     style="padding:8px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-family:inherit;">Go</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  const input  = document.getElementById('_cuDialogInput');
  const ok     = document.getElementById('_cuDialogOk');
  const cancel = document.getElementById('_cuDialogCancel');

  function confirm() {
    const val = input.value.trim();
    overlay.remove();
    if (editor) editor.focus();
    if (val) callback(parseInt(val, 10) || 1);
  }
  function dismiss() {
    overlay.remove();
    if (editor) editor.focus();
    speak('Cancelled.');
  }

  ok.addEventListener('click', confirm);
  cancel.addEventListener('click', dismiss);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); confirm(); }
    if (e.key === 'Escape') { e.preventDefault(); dismiss(); }
  });
  overlay.addEventListener('click', e => { if (e.target === overlay) dismiss(); });

  requestAnimationFrame(() => {
    input.focus();
    speak(promptText + '. Type a number and press Enter, or press Escape to cancel.');
  });
}