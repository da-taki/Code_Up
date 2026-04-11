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
      stopErrorBeacon();
    } else if (data.success && data.has_errors) {
      const errorList = data.errors.map(e => `Line ${e.line || 'unknown'}: ${e.type} - ${e.message}`).join('\n');
      out(`⚠ Found ${data.error_count} error(s):\n\n${errorList}`);
      speak(`Found ${data.error_count} errors.`);
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
- Ctrl+Enter: Run code
- Alt+S: Sonify block
- Alt+L: Read line with context
- Alt+V: List variables
- Alt+E: Check for errors
- Alt+H: Show this help
- Alt+Left/Right: Navigate history
- Alt+Home/End: Jump to top/bottom
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

// ---------- MONACO SETUP ----------
window.MonacoEnvironment = {
  getWorkerUrl: function () { return '/static/python.worker.js'; },
};

require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' } });

require(['vs/editor/editor.main'], function () {
  if (editor) { console.warn('Editor already initialized, skipping'); return; }

  editor = monaco.editor.create(document.getElementById('editor'), {
    value:            'print("Hello CodeUp!")',
    language:         'python',
    theme:            'vs-dark',
    fontSize:         16,
    minimap:          { enabled: false },
    automaticLayout:  true,
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
  AppState.isExecuting = true;
  cueSuccess();
  out('Running...');
  showAI('Running code...');
  speak('Running code.');
  try {
    const res  = await fetch('/run', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    window.executionTrace = (data.trace || []).slice(0, 1000);
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
      // Tutorial hook — show choice step after first successful run
      if (window._tutorialAwaitingRun) {
        window._tutorialAwaitingRun = false;
        setTimeout(function () {
          if (typeof showChoiceStep === 'function') {
            showChoiceStep(window._tutorialLang, window._tutorialStep1);
          }
        }, 2000);
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
    speak(data.analysis ? 'Analysis ready.' : 'No analysis available.');
    if (data.analysis) speak(data.analysis);
  } catch (e) {
    out('Analyze failed.'); console.error(e); cueError(); speak('Analyze failed.');
  } finally {
    speak('Task completed.'); hideAI();
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
async function saveSnippet() {
  const name = prompt('Snippet name:') || 'Untitled';
  await saveSnippetWithName(name);
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
  else if (action === 'save_snippet_named') await saveSnippetWithName(payload && payload.name ? payload.name : 'Untitled');
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
  else if (action === 'paste_code')      pasteCode();
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
  if (!SR) { alert('Speech recognition not supported in this browser.'); speak('Speech recognition not supported.'); return; }
  if (isListening) { speak('Already listening.'); return; }

  recognition = new SR();
  recognition.continuous      = true;
  recognition.interimResults  = false;
  recognition.lang            = 'en-US';
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
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${fn.line}" aria-label="Go to function ${escapeHtml(fn.name)} at line ${fn.line}">
          <span class="structure-item-icon">⚙️</span>
          <span class="structure-item-label">${escapeHtml(fn.name)}(${escapeHtml(params)})</span>
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

function showInputDialog(promptText, callback) {
  const value = window.prompt(promptText);
  if (value !== null && value !== '') callback(parseInt(value, 10) || 1);
}