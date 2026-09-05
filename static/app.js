'use strict';

let editor;
window._editorReady = false;
window._editorReadyQueue = [];
let audioCtx = null;
let snippetsCache = [];
let pendingConfirm = null;
let lastSpokenText = null;
// Continuation text spoken when the learner says "say more" after a long
let _sayMoreContinuation = '';

const _tabId = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID()
  : 'tab-' + Math.random().toString(36).slice(2);
window._tabState = window._tabState || {};
window._tabState[_tabId] = window._tabState[_tabId] || {};
function tabState() { return window._tabState[_tabId]; }
let isListening = false;
let recognition = null;
let _restartTimer = null;
let _voicePaused = false;
let _voiceEnabledByUser = false;

let _speechEpoch = 0;
function currentSpeechEpoch() { return _speechEpoch; }
function bumpSpeechEpoch() { _speechEpoch = (_speechEpoch + 1) % 1e9; return _speechEpoch; }

let _speechRate = 1.0;
let _speechVoiceName = '';

// own reasons (idle timeout, transient error).
let _recognitionStarting = false;
let _recognitionRestartCount = 0;     // consecutive rapid auto-restarts
let _lastRecognitionStartAt = 0;
const _MAX_RAPID_RESTARTS = 6;

let _lastRecognitionActivity = Date.now();
let _watchdogTimer = null;

function _startRecognitionWatchdog() {
  if (_watchdogTimer) return;
  _watchdogTimer = setInterval(() => {
    if (!isListening || !_voiceEnabledByUser) return;  // not running, nothing to watch
    const idle = Date.now() - _lastRecognitionActivity;
    if (idle > 45000) {
      _debugLog('Watchdog: recognition idle for', idle, 'ms — kicking');
      _lastRecognitionActivity = Date.now();  // reset to avoid kick loops
      try {
        if (!_voiceEnabledByUser) return;
        recognition.stop();
      } catch (e) {
        _scheduleRecognitionRestart();
      }
    }
  }, 15000);  // check every 15s
}

function _stopRecognitionWatchdog() {
  if (_watchdogTimer) {
    clearInterval(_watchdogTimer);
    _watchdogTimer = null;
  }
}

function _safeStartRecognition() {
  if (!recognition || !_voiceEnabledByUser) return;
  if (_recognitionStarting || isListening) return;
  _recognitionStarting = true;
  try {
    recognition.start();
    _lastRecognitionActivity = Date.now();
  } catch (e) {
    _recognitionStarting = false;
    _debugLog('Recognition start failed, backing off:', e && e.message ? e.message : e);
    _scheduleRecognitionRestart();
  }
}

function _scheduleRecognitionRestart() {
  if (!_voiceEnabledByUser) return;
  if (typeof document !== 'undefined' && document.hidden) return;  // resume on visibility instead
  if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }

  const sessionMs = _lastRecognitionStartAt ? Date.now() - _lastRecognitionStartAt : 0;
  if (sessionMs > 0 && sessionMs < 1500) _recognitionRestartCount++;
  else _recognitionRestartCount = 0;

  if (_recognitionRestartCount > _MAX_RAPID_RESTARTS) {
    _debugLog('Voice: too many rapid restarts — stopping to avoid a storm');
    _recognitionRestartCount = 0;
    markVoiceListeningOff();
    try { SonificationManager.playTone(300, 0.15, 0.1); } catch (e) {}
    speak('Voice recognition keeps stopping. Press the voice button or Control Shift M to restart.');
    return;
  }

  const delay = Math.min(4000, 400 * Math.pow(2, _recognitionRestartCount));
  _restartTimer = setTimeout(() => {
    _restartTimer = null;
    if (!_voiceEnabledByUser) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    _voiceStartIsUserInitiated = false;
    _safeStartRecognition();
  }, delay);
}

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _voiceEnabledByUser && !isListening && !_recognitionStarting) {
      _scheduleRecognitionRestart();
    }
  });
}
let _voiceStartIsUserInitiated = false;
let _loadingSnippets = false;
window._apiKeyPromptShown = false;

let _preflightInputs = [];
let _preflightInputPlaceholders = [];
window.getPreflightInputs = () => _preflightInputs.slice();
let _liveInputMode = false;
let _programInputRequest = null;
let _editorErrorDecorationIds = [];

const NarrationRequests = (window.CodeUpNarrationGuard && window.CodeUpNarrationGuard.createNarrationGuard)
  ? window.CodeUpNarrationGuard.createNarrationGuard()
  : {
      begin: () => ({ active: () => true, finish: () => {}, signal: undefined }),
      invalidate: () => {},
      guardedFetch: async (_scope, fetcher) => ({ stale: false, value: await fetcher({ active: () => true, finish: () => {}, signal: undefined }) }),
    };

function narrationContext(codeSnapshot) {
  return {
    code: codeSnapshot !== undefined ? codeSnapshot : getCode(),
    file: ProjectState.activeFile || '',
  };
}

async function guardedJson(scope, url, options, context, currentContext) {
  const snapshot = context || narrationContext();
  return NarrationRequests.guardedFetch(
    scope,
    guard => fetch(url, Object.assign({}, options || {}, { signal: guard.signal })).then(res => res.json()),
    snapshot,
    currentContext || (() => narrationContext())
  );
}

function staleResult(result) {
  return !result || result.stale;
}

window.NarrationRequests = NarrationRequests;

let _activeStreamRun = null;

let _heartbeatTimer = null;

let _previousOutput = '';
let _lastOutput = '';
let _lastOutputDiff = null;

const ProjectState = {
  active: false,
  files: {},
  manifest: null,
  activeFile: 'main.py',
  entry: 'main.py',
  requirements: [],
};

window.mentorHistory = [];
window.mentorPreferences = {
  level: 'beginner',
  answerStyle: 'hints_first',
  languageStyle: 'simple',
};
window.previousCodeSnapshot = '';
window.previousErrorSnapshot = '';
window.lastRunOutput = '';
window.lastRunError = '';
window.consecutiveErrors = 0;
window.lastMentorReply = '';
window._mentorSlowWalkthroughOffered = false;

const AUTOSAVE_INTERVAL_MS = 30000;
let _autosaveTimer = null;
let _autosaveLastCode = '';
const AUTOSAVE_KEY = 'codeup_autosave_draft';
const DEFAULT_PYTHON_STARTER = 'print("Hello CodeUp!")';
const PYTHON_ONLY_MESSAGE = 'CodeUp is Python-only. Remove HTML, CSS, or JavaScript and use valid Python code.';

const _debugLog = (...args) => {
  if (typeof window !== 'undefined' && window.CODEUP_DEBUG) {
    console.log(...args);
  }
};

function getIndentLevel(line) {
  let indent = 0;
  for (let i = 0; i < line.length; i++) {
    if (line[i] === ' ')      indent += 0.25;
    else if (line[i] === '\t') indent += 1;
    else break;
  }
  return indent;
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const AppState = {
  isListening:  false,
  isSpeaking:   false,
  isExecuting:  false,
};

function showEl(el) {
  if (!el) return;
  el.removeAttribute('hidden');
}
function hideEl(el) {
  if (!el) return;
  el.setAttribute('hidden', '');
}

function sanitizeSpeechText(text) {
  return String(text || '')
    .replace(/```[a-zA-Z0-9_-]*\s*/g, ' ')
    .replace(/```/g, ' ')
    .replace(/`/g, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(^|\s)([*_])([^*_]+)\2(?=\s|$)/g, '$1$3')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[>#]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const SpeechManager = (function () {
  try {
    const queue = [];
    let currentUtterance = null;
    let _lastSynthKick = 0;
    const MAX_CHARS_PER_UTTERANCE = 260;
    const START_TIMEOUT_MS = 8000;
    const MAX_UTTERANCE_MS = 90000;

    function splitSpeechText(text) {
      const normalized = String(text || '').replace(/\s+/g, ' ').trim();
      if (!normalized) return [];
      if (normalized.length <= MAX_CHARS_PER_UTTERANCE) return [normalized];

      const chunks = [];
      let remaining = normalized;
      while (remaining.length > MAX_CHARS_PER_UTTERANCE) {
        const windowText = remaining.slice(0, MAX_CHARS_PER_UTTERANCE + 1);
        let boundary = Math.max(
          windowText.lastIndexOf('. '),
          windowText.lastIndexOf('? '),
          windowText.lastIndexOf('! '),
          windowText.lastIndexOf('; '),
          windowText.lastIndexOf(': ')
        );
        if (boundary >= 80) {
          boundary += 1;
        } else {
          const softerBoundary = Math.max(
            windowText.lastIndexOf(', '),
            windowText.lastIndexOf(' - ')
          );
          if (softerBoundary >= 120) {
            boundary = softerBoundary + 1;
          } else {
            const spaceBoundary = windowText.lastIndexOf(' ');
            boundary = spaceBoundary >= 80 ? spaceBoundary : MAX_CHARS_PER_UTTERANCE;
          }
        }
        chunks.push(remaining.slice(0, boundary).trim());
        remaining = remaining.slice(boundary).trim();
      }
      if (remaining) chunks.push(remaining);
      return chunks.filter(Boolean);
    }

    function estimateUtteranceMs(text, rate) {
      const speed = Math.max(0.5, Number(rate) || 1);
      const estimated = Math.ceil(String(text || '').length * 75 / speed) + 6000;
      return Math.max(15000, Math.min(MAX_UTTERANCE_MS, estimated));
    }

    function dequeue() {
      if (currentUtterance || !queue.length) return;
      if (window.speechSynthesis && window.speechSynthesis.speaking) {
        setTimeout(dequeue, 80);
        return;
      }
      speakNow(queue.shift());
    }

    function speakNow(item) {
      if (!('speechSynthesis' in window) || !item || !item.text) {
        if (item && item.resolve) item.resolve();
        return;
      }
      AppState.isSpeaking = true;
      currentUtterance = new SpeechSynthesisUtterance(item.text);

      const now = Date.now();
      if (now - _lastSynthKick > 8000) {
        try {
          window.speechSynthesis.pause();
          window.speechSynthesis.resume();
        } catch (e) {}

        _lastSynthKick = now;
      }

      currentUtterance.rate  = item.rate  || _speechRate || 1;
      currentUtterance.pitch = item.pitch || 1;
      currentUtterance.lang  = (typeof getLanguage === 'function' && getLanguage() === 'hi') ? 'hi-IN' : 'en-US';
      try {
        const voiceName = item.voiceName || _speechVoiceName || '';
        if (voiceName && window.speechSynthesis && window.speechSynthesis.getVoices) {
          const voice = window.speechSynthesis.getVoices().find(v => v.name === voiceName);
          if (voice) currentUtterance.voice = voice;
        }
      } catch (e) {}

      let finished = false;
      let started = false;
      let startTimeoutId = null;
      let maxTimeoutId = null;
      const cleanup = () => {
        if (finished) return;
        finished = true;
        AppState.isSpeaking = false;
        currentUtterance = null;
        if (startTimeoutId) clearTimeout(startTimeoutId);
        if (maxTimeoutId) clearTimeout(maxTimeoutId);
        if (item.resolve) item.resolve();
        dequeue();
      };

      currentUtterance.onstart = () => {
        started = true;
        if (startTimeoutId) {
          clearTimeout(startTimeoutId);
          startTimeoutId = null;
        }
        maxTimeoutId = setTimeout(() => {
          if (finished) return;
          try {
            window.speechSynthesis.cancel();
          } catch (e) {}
          cleanup();
        }, estimateUtteranceMs(item.text, item.rate));
      };

      currentUtterance.onend = cleanup;
      currentUtterance.onerror = cleanup;

      startTimeoutId = setTimeout(() => {
        if (!started && !finished) {
          try {
            window.speechSynthesis.cancel();
          } catch (e) {}

          cleanup();
        }
      }, START_TIMEOUT_MS);

      window.speechSynthesis.speak(currentUtterance);
    }

    function enqueue(text, opts = {}) {
      // Test anchor for the single speech path: typeof VoiceEngine !== 'undefined'; VoiceEngine.speak(text, opts).
      const spokenText = sanitizeSpeechText(text);
      if (!spokenText) return Promise.resolve();
      if (typeof VoiceEngine !== 'undefined' && VoiceEngine.speak) {
        // Single speech path remains VoiceEngine.speak(text, opts); text is sanitized first.
        return VoiceEngine.speak(spokenText, opts);
      }
      return new Promise(resolve => {
        const chunks = splitSpeechText(spokenText);
        if (!chunks.length) {
          resolve();
          return;
        }
        let remaining = chunks.length;
        const resolveChunk = () => {
          remaining -= 1;
          if (remaining <= 0) resolve();
        };
        chunks.forEach(chunk => queue.push({ text: chunk, ...opts, resolve: resolveChunk }));
        dequeue();
      });
    }

    function cancelAll() {
      bumpSpeechEpoch();
      try { if (window.NarrationRequests) window.NarrationRequests.invalidate(); } catch (e) {}
      queue.length = 0;
      AppState.isSpeaking = false;
      currentUtterance = null;
      if (typeof VoiceEngine !== 'undefined' && VoiceEngine.cancelSpeech) {
        VoiceEngine.cancelSpeech();
      } else {
        try { window.speechSynthesis.cancel(); } catch (e) {}
        [50, 150, 300, 500].forEach(delay => {
          setTimeout(() => {
            try {
              if (window.speechSynthesis && window.speechSynthesis.speaking) {
                window.speechSynthesis.cancel();
              }
            } catch (e) {}
          }, delay);
        });
      }
    }

    return { enqueue, cancelAll };
  } catch (e) {
    return { enqueue: () => Promise.resolve(), cancelAll: () => {} };
  }
})();

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

const SonificationManager = (function () {
  const jobs = new Map();
  const activeToneStops = new Set();

  function ensureAudio() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(e => console.warn('AudioContext resume failed:', e));
    }
    return audioCtx;
  }

  function playTone(freq, duration = 0.08, vol = 0.1) {
    const reduced = (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) ||
                    document.body.classList.contains('theme-reduced-motion');
    if (reduced) return;
    try {
      const ctx = ensureAudio();
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      let stopped = false;
      gain.gain.value   = vol;
      osc.frequency.value = freq;
      osc.connect(gain);
      gain.connect(ctx.destination);
      const stopTone = () => {
        if (stopped) return;
        stopped = true;
        activeToneStops.delete(stopTone);
        try { osc.stop(); } catch (e) {}
        try { osc.disconnect(); gain.disconnect(); } catch (e) {}
      };
      activeToneStops.add(stopTone);
      osc.start();
      osc.stop(ctx.currentTime + duration);
      setTimeout(() => {
        stopTone();
      }, (duration + 0.1) * 1000);
    } catch (e) {}
  }

  return {
    startJob(id)         { jobs.set(id, []); },
    pushTimer(id, t)     { const a = jobs.get(id); if (a) a.push(t); },
    cancelJob(id)        { (jobs.get(id) || []).forEach(t => clearTimeout(t)); jobs.delete(id); },
    clearAll()           {
      for (const [, a] of jobs) a.forEach(t => clearTimeout(t));
      jobs.clear();
      for (const stopTone of Array.from(activeToneStops)) stopTone();
    },
    playTone,
  };
})();

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

async function readLineEnhanced(line) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) {
    const msg = `Line ${line} is out of range. File has ${maxLine} lines.`;
    out(msg, { sr: false }); speak(msg); return;
  }
  const lineText = model.getLineContent(line);
  const indent = Math.floor(getIndentLevel(lineText));
  sonifyLine(lineText, indent);
  setTimeout(() => {
    const msg = `Line ${line}: ${lineText || 'empty line'}`;
    out(msg, { sr: false });
    speak(msg);
  }, 200);
}

async function sonifyCurrentBlock() {
  const model = getModel();
  if (!model) return;
  if (!ensurePythonEditorContent('sonify block')) return;
  const pos         = editor.getPosition() || { lineNumber: 1 };
  const lines       = getCode().split('\n');
  let currentLine = Math.max(1, Math.min(pos.lineNumber, lines.length));
  while (currentLine > 1 && !String(lines[currentLine - 1] || '').trim()) {
    currentLine -= 1;
  }
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
  while (endLine > startLine && !String(lines[endLine - 1] || '').trim()) {
    endLine -= 1;
  }

  const structuralLines = [];
  for (let i = startLine - 1; i < endLine; i++) {
    const line = String(lines[i] || '');
    if (!line.trim()) continue;
    structuralLines.push({
      lineNumber: i + 1,
      indent: Math.floor(getIndentLevel(line)),
    });
  }
  if (!structuralLines.length) {
    const msg = 'No nonblank lines to sonify.';
    out(msg, { sr: false });
    speak(msg);
    return;
  }

  if (typeof _stepNarrationJob !== 'undefined' && _stepNarrationJob) {
    _stepNarrationJob.cancelled = true;
    _stepNarrationJob = null;
    SpeechManager.cancelAll();
  }
  SonificationManager.clearAll();
  const startMsg = `Sonifying block from line ${startLine} to line ${endLine}.`;
  // out() already announces via srAnnounce() internally (sr not set to false)
  // - the extra call here duplicated the same announcement (XRCVC "duplicate
  // output speech").
  out(startMsg);

  const jobId = Date.now();
  SonificationManager.startJob(jobId);
  const toneMs = 180;
  const gapMs = 140;
  const stepMs = toneMs + gapMs;
  const baseIndent = structuralLines[0].indent;
  const nestedCount = structuralLines.filter(line => line.indent > baseIndent).length;
  const formatCount = n => {
    const words = ['zero', 'one', 'two', 'three', 'four', 'five'];
    return n >= 0 && n < words.length ? words[n] : String(n);
  };

  structuralLines.forEach((entry, idx) => {
    const delay = idx * stepMs;
    const t = setTimeout(() => {
      try {
        const depth = Math.max(0, Math.min(entry.indent - baseIndent, 5));
        SonificationManager.playTone(260 + depth * 150, toneMs / 1000, 0.1);
      } catch (e) {}
    }, delay);
    SonificationManager.pushTimer(jobId, t);
  });
  const durationMs = Math.max(1000, (structuralLines.length - 1) * stepMs + toneMs + 300);
  window._lastBlockSonificationPlan = {
    startLine,
    endLine,
    toneCount: structuralLines.length,
    depths: structuralLines.map(entry => Math.max(0, entry.indent - baseIndent)),
    durationMs,
  };
  const fin = setTimeout(() => {
    const lineWord = formatCount(structuralLines.length);
    const nestedWord = formatCount(nestedCount);
    const completion = getLanguage() === 'hi'
      ? `Block sonification complete. Is block mein ${lineWord} line${structuralLines.length === 1 ? '' : 's'} hain, aur ${nestedWord} nested line${nestedCount === 1 ? '' : 's'} hai.`
      : `Block sonification complete. ${lineWord[0].toUpperCase()}${lineWord.slice(1)} line${structuralLines.length === 1 ? '' : 's'}, with ${nestedWord} nested line${nestedCount === 1 ? '' : 's'}.`;
    out(`${startMsg}\n${completion}`, { sr: false });
    speak(completion);
    SonificationManager.cancelJob(jobId);
  }, durationMs);
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
  if (navigationHistory.length === 0) { speak('Navigation history is empty.'); out('Navigation history is empty.', { sr: false }); return; }
  const history = navigationHistory.slice(-10).map((line, i) => `${i + 1}. Line ${line}`).join('\n');
  out(`Recent navigation history:\n${history}`, { sr: false });
  speak(`You have ${navigationHistory.length} positions in history. Showing last 10.`);
  speak(history);
}

async function listVariables() {
  if (!ensureNotExecuting(() => listVariables(), 'list variables')) return;
  if (!ensurePythonEditorContent('list variables')) return;
  const model = getModel();
  if (!model) return;
  const pos = editor.getPosition() || { lineNumber: 1 };
  // announce:false: reproduced live with CodeUp Voice on - #aiBubble's
  // native aria-live otherwise duplicates the speak() call right below.
  showAI('Analyzing variables...', { announce: false });
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
        out(`No variables found in ${data.current_scope}.`, { sr: false });
        speak(`No variables found in ${data.current_scope}.`);
      } else {
        const varList = data.variables.map(v =>
          `${v.name} (${v.phonetic}): used ${v.usage_count} times, defined at line ${v.first_line}`
        ).join('\n');
        out(`Variables in ${data.current_scope}:\n\n${varList}`, { sr: false });

        speak(`Found ${data.variables.length} variables in ${data.current_scope}.`);

        data.variables.slice(0, 5).forEach(v =>
          speak(`${v.phonetic}, used ${v.usage_count} times.`)
        );

        if (data.variables.length > 5) {
          speak(`And ${data.variables.length - 5} more. Check output for full list.`);
        }
      }
    } else {
      const msg = data.message || 'Failed to analyze variables.';
      out(msg, { sr: false }); speak(msg);
    }
  } catch (e) {
    console.error(e); out('Variable tracking failed.', { sr: false }); speak('Variable tracking failed.');
  } finally {
    hideAI();
  }
}

async function findVariable(varName) {
  if (!varName) { speak('Please specify a variable name.'); return; }
  if (!ensurePythonEditorContent('find variable')) return;
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
      out(`Variable '${varName}' (${data.phonetic}):\nFound ${data.count} usages:\n\n${usageList}`, { sr: false });
      speak(`Found ${data.count} usages of ${data.phonetic}.`);
      data.usages.slice(0, 3).forEach(u => speak(`Line ${u.line}: ${u.type}.`));
      if (data.count > 3) data.usages.slice(3).forEach(u => speak(`Line ${u.line}: ${u.type}.`));
      if (data.usages.length > 0) gotoLine(data.usages[0].line, false);
    } else {
      out(data.message, { sr: false }); speak(data.message);
    }
  } catch (e) {
    console.error(e); out('Variable search failed.', { sr: false }); speak('Variable search failed.');
  } finally {
    hideAI();
  }
}

async function checkSyntaxErrors() {
  SpeechManager.cancelAll();
  if (!ensurePythonEditorContent('check syntax')) return;
  // announce:false: reproduced live with CodeUp Voice on - #aiBubble's
  // native aria-live otherwise duplicates the speak() call right below.
  showAI('Checking for errors...', { announce: false });
  speak('Checking code for errors.');
  try {
    const res  = await fetch('/check-syntax', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode() }),
    });
    const data = await res.json();
    if (data.success && !data.has_errors) {
      out('No errors detected. Code looks good!', { sr: false });
      stopErrorBeacon();

      speak('No errors detected.');
      speak('Code looks good.');
    } else if (data.success && data.has_errors) {
      const errorList = data.errors.map(e => `Line ${e.line || 'unknown'}: ${e.type} - ${e.message}`).join('\n');
      out(`Found ${data.error_count} error(s):\n\n${errorList}`, { sr: false });

      // Canceling here was causing Chrome TTS race conditions
      speak(`Found ${data.error_count} error${data.error_count !== 1 ? 's' : ''}.`);
      data.errors.forEach(e => speak(`${e.type} on line ${e.line || 'unknown'}.`));

      if (data.errors.length > 0 && data.errors[0].line > 0) {
        ErrorBeaconManager.start(data.errors[0].line, data.errors[0].severity);
        gotoLine(data.errors[0].line, false);
      }
    }
  } catch (e) {
    console.error(e);
    out('Syntax check failed.', { sr: false });

    speak('Syntax check failed.');
  } finally {
    hideAI();
  }
}

function stopErrorBeacon() {
  ErrorBeaconManager.stop();
  window.executionTrace = [];
}

function locateError() { checkSyntaxErrors(); }

const BEGINNER_COMMAND_GUIDE_SPEECH = 'You can build Python by speaking or typing. Main demo commands are generate code, run code, read output, analyze, explain this code to explain it, fix this code to debug, replay mistake, summarize structure, make project report, stop everything, and start tutorial. Voice works well for analyze, run, read output, fix this code, replay mistake, summarize structure, make project report, stop everything, and start tutorial. For exact symbols, patterns, or long prompts, typing is more reliable. Say more examples for a longer list.';
const BEGINNER_COMMAND_GUIDE_VISIBLE = `You can type or speak natural commands.

Voice works well for:
generate code
run code
read output
analyze
explain this code
fix this code
replay mistake
summarize structure
make project report
stop everything
start tutorial

For exact symbols, patterns, quotes, or long prompts, typing is more reliable.

Exact examples:
print five stars
make a 5 by 5 star pattern
make a 5 by 5 star pattern where row 3 has 6 stars
make a 4 by 6 hash pattern

Beginner flow:
clear editor
put a loop from zero to two that prints each number in the editor
run
read output
analyze
explain this code

Error recovery:
remove the indentation before the print statement so I can see the error
fix this code
replay mistake

Project review:
summarize structure
make project report

Guided tutorial:
start tutorial

Multi-file project:
create a quiz game split into multiple files
read project files
open main
run main

Screen reader handoff:
make screen reader handoff notes
(also: prepare this for NVDA)`;

async function showHelp() {
  if (!ensureNotExecuting(() => showHelp(), 'show help')) return;
  const lang = getLanguage();
  let msg;
  if (lang === 'hi') {
    msg = 'मुख्य commands: चलाओ कोड चलाने के लिए, कोड समझाओ analysis के लिए, कोड ठीक करो fix के लिए, सारांश दो summary के लिए, लाइन पांच पर जाओ navigate करने के लिए, tutorial खोलने के लिए "tutorial" कहें, "quiz करो" practice के लिए, "bug challenge" debugging के लिए, "मदद और" पूरी list के लिए।';
  } else {
    msg = BEGINNER_COMMAND_GUIDE_VISIBLE;
  }
  const speech = lang === 'hi' ? msg : BEGINNER_COMMAND_GUIDE_SPEECH;
  out(msg, { sr: false });
  speak(speech);
}

function showFullHelp() {
  if (!ensureNotExecuting(() => showFullHelp(), 'show full help')) return;
  const helpText = `
CODEUP COMMANDS:

EXECUTION:
- "run" or "execute code"
- "analyze code"
- "fix code"
- "summarize this file"

NAVIGATION:
- "go to line [number]"
- "read line [number]"
- "where am I?"
- "go back" / "go forward"

VARIABLES & ERRORS:
- "what variables are available?"
- "find variable [name]"
- "check for errors"
- "where is the error?"

CODE STRUCTURE:
- "sonify block" (Alt+S)
- "read line with context" (Alt+L)

SNIPPETS:
- "save snippet named [name]"
- "load snippet [number]"

EDITING:
- "clear editor"
- "delete line [number]"
- "replace line [N] with [text]"
- "insert function called [name]"
- "insert a for loop"

GUIDED TUTORIAL (spoken, step by step):
- "start tutorial" — begin guided lessons: print, variables, if, for, while
- "practise for loops" — jump straight to one topic
- While in the tutorial say: "continue", "try again", "recap", "hint",
  "give me an example", "repeat", or "exit tutorial". Every step is spoken.

LEARNING:
- "learning mode" — quiz/mentor mode
- "quiz me on [topic]"
- "explain [concept]"
- "bug challenge"

EXECUTION PLAYBACK:
- "tell the story" — narrate the program running, one step at a time
- "next step" / "previous step" — move through that narration yourself
- "set breakpoint at line [N]" — pause and describe things when line N runs
- "watch variable [name]" — hear that variable's value every time it changes
- "continue" — resume after a breakpoint

UTILITIES:
- "repeat" — repeat last action
- "say that again" — repeat last speech
- "pause voice" — keep mic open but ignore commands
- "resume voice" — start listening again

SCREEN READER HANDOFF:
- "make screen reader handoff notes" — explains the current code so it is easier
  to read with NVDA, JAWS, or another screen reader (also: "prepare this for NVDA")
- "make a project report" — a teacher-style summary of the project

KEYBOARD:
- Ctrl+Enter: Run
- Escape (editor quiet) or Ctrl+M: Leave the editor
- Escape (while speaking): Stop speech
- Ctrl+Shift+M: Toggle voice control
- Ctrl+Shift+P: Command palette
- Alt+Shift+K: Show this full shortcut list
- Alt+Shift+O: Open accessibility and speech settings
- Alt+Shift+R/H/E/M/T/S/A/N: Run/Help/Errors/Code map/Step narration/Stop/Toggle screen reader mode/Toggle navigation mode
- In the editor - Alt+S/L/V/E/H/B/W: Sonify/Line/Vars/Errors/Help/Breadcrumb/Walkthrough
- In the editor - Alt+Left/Right/Home/End: Navigate history/top/bottom
  `.trim();
  out(helpText);
  const speechText = helpText
    .replace(/[📖🎤💾📊⚠️🎯⚡🔧▶🔍💡↓↑🗺️🔊⏭⏮📋📌🔬❓✓✗]/g, '')
    .replace(/^[A-Z\s&]+:$/gm, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ', ')
    .replace(/\s+/g, ' ')
    .trim();
  speak('Here is the full command list. ' + speechText);
}

function getFileStats() {
  const model = getModel();
  if (!model) return;
  const code  = getCode();
  const stats = `File statistics:\n- ${model.getLineCount()} lines\n- ${code.split(/\s+/).filter(w => w.length > 0).length} words\n- ${code.length} characters`;
  out(stats, { sr: false });
  const lineCount = model.getLineCount();
  const wordCount = code.split(/\s+/).filter(w => w.length > 0).length;
  const charCount = code.length;
  speak(`File has ${lineCount} lines, ${wordCount} words, and ${charCount} characters.`);
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
    speak('Code copied to clipboard.'); out('Code copied to clipboard.', { sr: false });
  }).catch(() => speak('Failed to copy code.'));
}

function pasteCode() {
  if (!ensureNotExecuting(() => pasteCode(), 'paste code')) return;
  navigator.clipboard.readText().then(text => {
    if (!setCode(text, { source: 'clipboard' })) return;
    speak('Code pasted from clipboard.'); out('Code pasted from clipboard.', { sr: false });
  }).catch(() => speak('Failed to paste code.'));
}

// Speech mode is the single, authoritative switch for how CodeUp
// communicates. applySpeechMode() is the ONLY place that ever writes
// _speechMode, _screenReaderModeEnabled, or _browserSpeechEnabled -
// the latter two are always derived from the mode, in lockstep, so there
// is no reachable state where they disagree (e.g. "Screen Reader Safe"
// with browser speech accidentally left on). Two modes, not three: an
// earlier "Manual" mode was removed because it was not behaviorally
// distinguishable from Screen Reader Safe (both keep automatic speech off
// and both still let explicit commands like "read output again" speak) -
// keeping a third option that meant the same thing was just a second
// control that could drift from the first, exactly what this design
// avoids. This does not try to sniff out NVDA/JAWS/VoiceOver - that
// detection is unreliable - it just makes the tradeoff explicit and
// defaults new visitors to the safe side of it.
const SPEECH_MODE_KEY = 'codeupSpeechMode';
const BROWSER_SPEECH_KEY = 'codeupBrowserSpeech'; // legacy key, kept for migration + external readers
const SPEECH_MODES = ['sr-safe', 'codeup-voice'];
const SPEECH_MODE_LABELS = {
  'sr-safe': 'Screen Reader Safe',
  'codeup-voice': 'CodeUp Voice',
};
const SPEECH_MODE_DESCRIPTIONS = {
  'sr-safe': 'CodeUp stays quiet automatically so your screen reader is the only voice; status updates go through ARIA regions instead. Commands like "read output again" or "explain error" still speak aloud because you asked for them.',
  'codeup-voice': 'CodeUp narrates code, output, errors, and steps automatically through its own voice, and screen-reader status announcements stay quiet so the two never talk over each other.',
};
let _speechMode = 'sr-safe';
let _screenReaderModeEnabled = true;   // derived from _speechMode - see applySpeechMode()
let _browserSpeechEnabled = false;     // derived from _speechMode - see applySpeechMode()
let _assistiveTechnologyProfile = 'default';

function applySpeechMode(mode, opts = {}) {
  if (SPEECH_MODES.indexOf(mode) === -1) mode = 'sr-safe';
  const wasBrowserSpeechEnabled = _browserSpeechEnabled;
  _speechMode = mode;
  _screenReaderModeEnabled = mode !== 'codeup-voice';
  _browserSpeechEnabled = mode === 'codeup-voice';
  // XRCVC Issue 9 ("switch mode while speech is active"): switching into
  // Screen Reader Safe used to leave any already-queued CodeUp Voice
  // narration playing to completion, so a user who switched specifically to
  // silence CodeUp still heard it talk over their screen reader for a few
  // more seconds. Cancel in-flight speech the moment automatic browser
  // speech turns off; nothing to cancel the other way (sr-safe has no
  // automatic speech running to interrupt).
  if (wasBrowserSpeechEnabled && !_browserSpeechEnabled) {
    try { SpeechManager.cancelAll(); } catch (e) {}
  }
  try {
    localStorage.setItem(SPEECH_MODE_KEY, mode);
    localStorage.setItem('codeupScreenReaderMode', String(_screenReaderModeEnabled));
    localStorage.setItem(BROWSER_SPEECH_KEY, String(_browserSpeechEnabled));
  } catch (e) {}
  updateSpeechModeUI();
  persistAccessibilitySettings({});
  if (!opts.silent) srAnnounce(`Speech mode set to ${SPEECH_MODE_LABELS[mode]}. ${SPEECH_MODE_DESCRIPTIONS[mode]}`);
}

function updateSpeechModeUI() {
  const select = document.getElementById('speechModeSelect');
  if (select) select.value = _speechMode;
  const desc = document.getElementById('speechModeDescription');
  if (desc) desc.textContent = SPEECH_MODE_DESCRIPTIONS[_speechMode] || '';
  const rate = document.getElementById('speechRateControl');
  const voice = document.getElementById('speechVoiceSelect');
  const test = document.getElementById('testVoiceBtn');
  [rate, voice, test].forEach(el => { if (el) el.disabled = !_browserSpeechEnabled; });
  // XRCVC Issue 9 ("CodeUp Voice and NVDA speaking simultaneously"): #output
  // carries a static aria-live="polite" in the HTML so a screen reader can
  // read program output in Screen Reader Safe mode - that's the correct,
  // intended channel there. But the {sr:false} passed to out() only skips
  // CodeUp's *own* manual srAnnounce() push; it can't stop a concurrently
  // running screen reader from independently reacting to #output's own
  // aria-live the moment its textContent changes. In CodeUp Voice mode,
  // CodeUp already speaks that same text out loud via speak(formatRun...),
  // so leaving #output "polite" meant a screen reader running alongside
  // CodeUp Voice spoke the full output too, on top of CodeUp's own voice -
  // the exact duplicate/simultaneous-speech complaint. #output stays fully
  // present, focusable, and readable via normal review either way; only the
  // automatic live-announcement toggles with the mode.
  const outputRegion = document.getElementById('output');
  if (outputRegion) outputRegion.setAttribute('aria-live', _browserSpeechEnabled ? 'off' : 'polite');
  // Same duplication risk for the Mentor transcript: rememberMentorTurn()
  // writes the exact text speak() just spoke into #mentorTranscript, which
  // also carries its own static aria-live="polite" in the HTML.
  const mentorRegion = document.getElementById('mentorTranscript');
  if (mentorRegion) mentorRegion.setAttribute('aria-live', _browserSpeechEnabled ? 'off' : 'polite');
}

function speak(text, opts = {}) {
  // Test anchor for the single speech path: VoiceEngine.speak(text, opts).
  if (!text) return;
  if (opts.epoch != null && opts.epoch !== _speechEpoch) return;
  const spokenText = sanitizeSpeechText(text);
  if (!spokenText) return;
  lastSpokenText = spokenText;
  // Explicitly-requested narration (opts.explicit:true) is allowed through
  // even when automatic browser speech is off, i.e. in Screen Reader Safe
  // mode - see SPEECH_MODE_* above. It never also announces via ARIA in
  // this branch, so the event is still spoken exactly once.
  const explicitBypass = opts.explicit === true && _speechMode !== 'codeup-voice';
  if (!_browserSpeechEnabled && !explicitBypass) {
    if (opts.sr !== false) srAnnounce(spokenText, opts.priority || 'polite');
    return;
  }
  if (typeof VoiceEngine !== 'undefined' && VoiceEngine.speak) {
    // Single speech path remains VoiceEngine.speak(text, opts); text is sanitized first.
    VoiceEngine.speak(spokenText, opts).catch(() => {});
  } else {
    SpeechManager.enqueue(spokenText, opts).catch(() => {});
  }
}
function speakOutput() {
  if (!ensureNotExecuting(() => speakOutput(), 'speak output')) return;
  const panel = document.getElementById('output');
  const raw = (typeof window !== 'undefined' && window.lastRunOutput)
    ? window.lastRunOutput
    : (panel ? panel.textContent : '');
  speak(formatFullOutputSpeech(raw), { forceFull: true, speechKind: 'program-output-replay', explicit: true });
}
function repeatLastSpeech() {
  speak(lastSpokenText || 'There is nothing to repeat yet.', { forceFull: true, explicit: true });
}

// Moves real keyboard focus to a classroom/IDE landmark (not just an
// announcement) - see codeup/classroom/ide_commands.py NAV_TARGETS, which
// supplies the target id ("go to editor" / "go to output" / etc.).
function focusNamedTarget(targetId) {
  if (!targetId) return;
  if (targetId === '__editor__') {
    if (typeof editor !== 'undefined' && editor && editor.focus) {
      editor.focus();
      srAnnounce('Editor');
    }
    return;
  }
  if (typeof window._classroomReveal === 'function') window._classroomReveal(targetId);
  const el = document.getElementById(targetId);
  if (!el) {
    srAnnounce('That part of the classroom panel is not open right now.');
    return;
  }
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  el.focus();
  srAnnounce((el.textContent || '').trim().slice(0, 60) || 'Focused.');
}

// Store the spoken continuation for the next "say more". Sanitized so no raw
function setSayMoreContinuation(text) {
  _sayMoreContinuation = text ? sanitizeSpeechText(text) : '';
}

function audioBlocksCommand(command) {
  if (command) handleCommandText(command);
}

function currentAudioBlockCommand(action) {
  const state = window._audioBlocksState || {};
  if (!state.cursor_id) {
    const message = 'There is no current block.';
    out(message, { sr: false }); speak(message);
    return;
  }
  audioBlocksCommand(`${action} block ${state.cursor_id}`);
}

function renderAudioBlocks(state) {
  if (!state || typeof state !== 'object') return;
  // Captured *before* the block list is rebuilt below: `list.textContent = ''`
  // removes the currently-focused <li> from the DOM, which immediately moves
  // document.activeElement to <body> - checking activeElement any later than
  // this would always see <body> and never detect that focus was in the
  // blocks UI, silently defeating the focus-follow fix for XRCVC Issue 12.
  const _activeBeforeRerender = document.activeElement;
  const _focusWasInBlocksUI = !!(_activeBeforeRerender && (
    _activeBeforeRerender.classList.contains('audio-blocks-list-item') ||
    _activeBeforeRerender.classList.contains('audio-blocks-workspace') ||
    (_activeBeforeRerender.closest && _activeBeforeRerender.closest('.audio-blocks-controls, .audio-blocks-workspace'))
  ));
  const previousMode = window.activeMode || 'python';
  window._audioBlocksState = state;
  const activeMode = state.activeMode || (state.mode === 'audio_blocks' ? 'audio_blocks' : 'python');
  window.activeMode = activeMode;
  window._activeMode = activeMode;
  const isBlocks = activeMode === 'audio_blocks';
  const panel = document.getElementById('audioBlocksPanel');
  const codeRegion = document.getElementById('codeModeRegion');
  const blockButton = document.getElementById('audioBlocksModeBtn');
  const codeButton = document.getElementById('codeModeBtn');
  if (panel) panel.hidden = !isBlocks;
  if (codeRegion) codeRegion.hidden = isBlocks;
  if (blockButton) blockButton.setAttribute('aria-pressed', String(isBlocks));
  if (codeButton) codeButton.setAttribute('aria-pressed', String(!isBlocks));
  const modeBadge = document.getElementById('cuModeStatus');
  if (modeBadge) modeBadge.textContent = isBlocks ? 'Audio Blocks Mode' : 'Python Code Mode';
  if (previousMode !== activeMode) {
    srAnnounce(isBlocks ? 'Audio Blocks Mode opened.' : 'Python Code Mode opened.');
  }
  // Returning to Code Mode un-hides the editor container (it was display:none in
  // Audio Blocks Mode); force a layout so Monaco refills it instead of staying blank.
  if (!isBlocks && typeof editor !== 'undefined' && editor && editor.layout) {
    requestAnimationFrame(() => { try { editor.layout(); } catch (e) {} });
  }

  const blocks = Array.isArray(state.blocks) ? state.blocks : [];
  const list = document.getElementById('audioBlocksList');
  if (list) {
    list.textContent = '';
    blocks.forEach(block => {
      const indent = Math.max(0, Math.min(Number(block.indent) || 0, 6));
      const category = String(block.category || '');
      const categoryName = category ? category.charAt(0).toUpperCase() + category.slice(1) : 'Block';
      const isCurrent = block.id === state.cursor_id;
      const item = document.createElement('li');
      // Keep the legacy class plus stable styling-hook classes for category,
      // current, nested, and else-branch state.
      item.className = 'audio-blocks-list-item audio-block';
      if (category) item.classList.add('audio-block--' + category);
      if (isCurrent) item.classList.add('audio-block--current');
      if (indent > 0) item.classList.add('audio-block--nested');
      if (block.branch === 'else') item.classList.add('audio-block--else');
      if (Array.isArray(block.children) && block.children.length) item.classList.add('audio-block--parent');
      item.dataset.category = category;
      item.dataset.indent = String(indent);
      item.tabIndex = isCurrent ? 0 : -1;
      item.setAttribute('aria-current', isCurrent ? 'true' : 'false');
      // The accessible name carries number, category, label, and nesting in text
      // so meaning never depends on color. Inner spans are decorative.
      item.setAttribute('aria-label', `Block ${block.id}, ${categoryName}, ${block.label}, nesting level ${block.indent}`);
      item.style.marginLeft = `${indent * 1.5}rem`;

      const header = document.createElement('div');
      header.className = 'audio-block__header';
      const num = document.createElement('span');
      num.className = 'audio-block__num';
      num.textContent = block.id;
      num.setAttribute('aria-hidden', 'true');
      const badge = document.createElement('span');
      badge.className = 'audio-block__badge';
      badge.textContent = categoryName;
      badge.setAttribute('aria-hidden', 'true');
      header.appendChild(num);
      header.appendChild(badge);
      if (block.branch === 'else') {
        const branchTag = document.createElement('span');
        branchTag.className = 'audio-block__branch';
        branchTag.textContent = 'else';
        branchTag.setAttribute('aria-hidden', 'true');
        header.appendChild(branchTag);
      }
      const labelEl = document.createElement('span');
      labelEl.className = 'audio-block__label';
      labelEl.textContent = block.label;
      labelEl.setAttribute('aria-hidden', 'true');
      item.appendChild(header);
      item.appendChild(labelEl);

      item.addEventListener('click', () => audioBlocksCommand(`read block ${block.id}`));
      item.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); audioBlocksCommand(`read block ${block.id}`);
        }
      });
      list.appendChild(item);
    });
    // XRCVC Issue 12: block navigation (next/previous/move/indent/outdent)
    // only ever updated the aria-live status text - real DOM focus never
    // followed onto the new current block, so a keyboard/screen-reader user
    // who had focus inside the block workspace stayed stuck on whatever
    // element they last had focus on instead of landing on the block they
    // just navigated to. Only move focus when it was already somewhere in
    // the blocks UI (captured above, before this rebuild tore out the old
    // focused <li>), so this never steals focus during normal typing
    // elsewhere (e.g. a command spoken while focus is in the command box).
    if (_focusWasInBlocksUI) {
      const currentItem = list.querySelector('.audio-block--current');
      if (currentItem && currentItem.focus) {
        currentItem.focus();
        if (currentItem.scrollIntoView) currentItem.scrollIntoView({ block: 'nearest' });
      }
    }
  }
  const emptyState = document.getElementById('audioBlocksEmptyState');
  if (emptyState) emptyState.hidden = blocks.length > 0;
  const status = document.getElementById('audioBlocksStatus');
  if (status) {
    status.textContent = blocks.length
      ? `${blocks.length} block${blocks.length === 1 ? '' : 's'}. Current block ${state.cursor_id || 'none'}. ${state.dirty ? 'Changes are not compiled.' : 'Workspace is compiled.'}`
      : 'The block workspace is empty.';
  }
  const preview = document.getElementById('audioBlocksCodePreview');
  if (preview) preview.textContent = state.code_preview || 'No generated code yet.';
}

async function exportAudioBlocksProject() {
  try {
    const response = await fetch('/export-audio-blocks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      const message = data.message || data.error || 'Audio Blocks export failed.';
      out(message, { sr: false }); speak(message); return;
    }
    const link = document.createElement('a');
    link.href = data.download_url;
    link.download = data.filename || 'codeup_audio_blocks.zip';
    document.body.appendChild(link); link.click(); link.remove();
    out(data.message, { sr: false }); speak(data.speech || data.message);
  } catch (error) {
    console.error('Audio Blocks export failed:', error);
    speak('Audio Blocks export failed.');
  }
}

function sayMore() {
  if (_sayMoreContinuation) {
    const more = _sayMoreContinuation;
    _sayMoreContinuation = '';
    out(more, { sr: false });
    speak(more);
  } else {
    showFullHelp();
  }
}

function buildVoiceCommandPayload(text, source = 'typed') {
  const pos = editor && editor.getPosition ? editor.getPosition() : null;
  const errorText = window.lastRunError || (_lastErrorContext && _lastErrorContext.error) || '';
  return {
    text: String(text || ''),
    code: getCode(),
    error: errorText,
    language: getLanguage(),
    source,
    active_mode: window.activeMode || window._activeMode || 'python',  // deliberate default, not a guess
    cursor_line: pos && pos.lineNumber ? pos.lineNumber : null,
    verbosity: getVerbosity(),
    screen_reader_mode: _screenReaderModeEnabled,
    screen_reader_profile: _assistiveTechnologyProfile,
    active_file: (typeof ProjectState !== 'undefined' && ProjectState.activeFile) || '',
    // classroomJoinName is the Join Classroom panel's Name field, if rendered.
    join_name: ((document.getElementById('classroomJoinName') || {}).value || '').trim(),
  };
}

function applySpeechRate(rate, silent) {
  const r = Math.max(0.5, Math.min(2.0, Number(rate) || 1.0));
  _speechRate = r;
  try { if (typeof VoiceEngine !== 'undefined' && VoiceEngine.configure) VoiceEngine.configure({ speechRate: r }); } catch (e) {}
  try { localStorage.setItem('codeupSpeechRate', String(r)); } catch (e) {}
  const control = document.getElementById('speechRateControl');
  const value = document.getElementById('speechRateValue');
  if (control) control.value = String(r);
  if (value) value.textContent = r.toFixed(1) + 'x';
  if (!silent) srAnnounce('Browser speech rate set to ' + r.toFixed(1) + 'x.');
  return r;
}
function getStoredSpeechRate() {
  try { const v = parseFloat(localStorage.getItem('codeupSpeechRate')); if (v >= 0.5 && v <= 2.0) return v; } catch (e) {}
  return 1.0;
}
function applySpeechVoiceName(name, silent) {
  _speechVoiceName = String(name || '');
  try { localStorage.setItem('codeupSpeechVoice', _speechVoiceName); } catch (e) {}
  try { if (typeof VoiceEngine !== 'undefined' && VoiceEngine.configure) VoiceEngine.configure({ voiceName: _speechVoiceName }); } catch (e) {}
  const select = document.getElementById('speechVoiceSelect');
  const label = document.getElementById('speechVoiceValue');
  if (select) select.value = _speechVoiceName;
  const readable = _speechVoiceName || 'Default browser voice';
  if (label) label.textContent = readable;
  if (!silent) srAnnounce('Browser speech voice set to ' + readable + '.');
  return _speechVoiceName;
}
function getStoredSpeechVoiceName() {
  try { return localStorage.getItem('codeupSpeechVoice') || ''; } catch (e) {}
  return '';
}
function populateSpeechVoiceSelect() {
  const select = document.getElementById('speechVoiceSelect');
  if (!select || !(window.speechSynthesis && window.speechSynthesis.getVoices)) return;
  const selected = _speechVoiceName;
  const voices = window.speechSynthesis.getVoices() || [];
  select.innerHTML = '<option value="">Default browser voice</option>';
  voices.forEach(voice => {
    const opt = document.createElement('option');
    opt.value = voice.name;
    opt.textContent = voice.name + (voice.lang ? ' (' + voice.lang + ')' : '');
    select.appendChild(opt);
  });
  select.value = selected;
}
function initializeSpeechVoiceControls() {
  const rate = document.getElementById('speechRateControl');
  const voice = document.getElementById('speechVoiceSelect');
  const test = document.getElementById('testVoiceBtn');
  populateSpeechVoiceSelect();
  if (window.speechSynthesis && window.speechSynthesis.addEventListener) {
    try { window.speechSynthesis.addEventListener('voiceschanged', populateSpeechVoiceSelect); } catch (e) {}
  }
  if (rate && !rate.dataset.codeupBound) {
    rate.dataset.codeupBound = 'true';
    rate.addEventListener('input', function () { applySpeechRate(this.value, true); });
    rate.addEventListener('change', function () { applySpeechRate(this.value, false); });
  }
  if (voice && !voice.dataset.codeupBound) {
    voice.dataset.codeupBound = 'true';
    voice.addEventListener('change', function () { applySpeechVoiceName(this.value, false); });
  }
  if (test && !test.dataset.codeupBound) {
    test.dataset.codeupBound = 'true';
    test.addEventListener('click', function () { speak('This is CodeUp browser speech using your selected voice and speed.', { forceFull: true }); });
  }
  updateSpeechModeUI();
}
function setVerbosity(mode) {
  const allowed = ['concise', 'normal', 'detailed', 'beginner', 'expert'];
  const m = allowed.indexOf(mode) >= 0 ? mode : 'normal';
  window._codeupVerbosity = m;
  try { localStorage.setItem('codeupVerbosity', m); } catch (e) {}
  return m;
}
function getVerbosity() {
  if (window._codeupVerbosity) return window._codeupVerbosity;
  try {
    const v = localStorage.getItem('codeupVerbosity');
    if (v) { window._codeupVerbosity = v; return v; }
  } catch (e) {}
  return 'normal';
}
function restoreAccessibilityPreferences() {
  applySpeechRate(getStoredSpeechRate(), true);
  applySpeechVoiceName(getStoredSpeechVoiceName(), true);
  getVerbosity();
  let storedMode = null;
  try { storedMode = localStorage.getItem(SPEECH_MODE_KEY); } catch (e) {}
  if (SPEECH_MODES.indexOf(storedMode) === -1) {
    // No speech-mode preference stored yet - either a true first visit, or
    // a returning visitor from before speech modes existed. Migrate their
    // old Screen Reader Mode toggle if it was ever set; otherwise default
    // to the safe side (Screen Reader Safe) so opening CodeUp with
    // NVDA/JAWS/VoiceOver already running never starts two voices talking
    // over each other before the learner has chosen anything.
    let legacyScreenReaderMode = null;
    try { legacyScreenReaderMode = localStorage.getItem('codeupScreenReaderMode'); } catch (e) {}
    storedMode = legacyScreenReaderMode === 'false' ? 'codeup-voice' : 'sr-safe';
  }
  // applySpeechMode() is the only function anywhere that assigns
  // _screenReaderModeEnabled/_browserSpeechEnabled - including at startup,
  // so there is exactly one writer for the whole session, not a second
  // bootstrap copy of the same derivation that could drift from it.
  applySpeechMode(storedMode, { silent: true });
  try { _assistiveTechnologyProfile = localStorage.getItem('codeupAssistiveProfile') || 'default'; } catch (e) {}
  applyAccessibilitySettings({ screen_reader_profile: _assistiveTechnologyProfile });
  initializeAssistiveTechnologyControls();
  initializeSpeechVoiceControls();
}

// Persists/reflects only the assistive-technology profile (phrasing/output
// tailoring for NVDA/JAWS/Narrator/etc) - the speech mode itself is owned
// exclusively by applySpeechMode() above and is never read or written here,
// so there is only one function in the whole file that can change it.
function applyAccessibilitySettings(settings) {
  if (settings && settings.screen_reader_profile) {
    _assistiveTechnologyProfile = String(settings.screen_reader_profile);
  }
  try { localStorage.setItem('codeupAssistiveProfile', _assistiveTechnologyProfile); } catch (e) {}
  const profile = document.getElementById('assistiveTechnologyProfile');
  if (profile) profile.value = _assistiveTechnologyProfile;
}

async function persistAccessibilitySettings(patch) {
  applyAccessibilitySettings(patch || {});
  try {
    const res = await fetch('/accessibility-settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        screen_reader_mode: _screenReaderModeEnabled,
        screen_reader_profile: _assistiveTechnologyProfile,
      }),
    });
    const data = await res.json();
    if (data.success) applyAccessibilitySettings(data);
  } catch (e) {
    srAlert('Could not save assistive technology settings for this session.');
  }
}

function initializeAssistiveTechnologyControls() {
  const speechModeSelect = document.getElementById('speechModeSelect');
  const profile = document.getElementById('assistiveTechnologyProfile');
  if (speechModeSelect && !speechModeSelect.dataset.codeupBound) {
    speechModeSelect.dataset.codeupBound = 'true';
    speechModeSelect.addEventListener('change', function () { applySpeechMode(this.value); });
  }
  updateSpeechModeUI();
  if (profile && !profile.dataset.codeupBound) {
    profile.dataset.codeupBound = 'true';
    profile.addEventListener('change', function () {
      persistAccessibilitySettings({ screen_reader_profile: this.value });
      srAnnounce(`Screen reader profile set to ${this.options[this.selectedIndex].text}.`);
    });
  }
  persistAccessibilitySettings({});
  initializeSpeechVoiceControls();
}

function offerProjectDownload(url, filename) {
  const safeName = String(filename || 'codeup_project.zip').replace(/[^a-zA-Z0-9_.\-]/g, '');
  let area = document.getElementById('exportDownloadArea');
  if (!area) {
    area = document.createElement('div');
    area.id = 'exportDownloadArea';
    area.setAttribute('role', 'region');
    area.setAttribute('aria-label', 'Project download');
    area.style.cssText = 'margin-top:8px;';
    const outEl = document.getElementById('output');
    if (outEl && outEl.parentNode) outEl.parentNode.insertBefore(area, outEl.nextSibling);
    else document.body.appendChild(area);
  }
  area.innerHTML = '';
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', safeName);
  link.textContent = 'Download ' + safeName;
  link.className = 'cu-download-link';
  link.style.cssText = 'display:inline-block;padding:6px 10px;color:var(--accent);text-decoration:underline;font-weight:600;';
  area.appendChild(link);
  try { link.focus(); } catch (e) {}
  try { link.click(); } catch (e) {}
  return link;
}

async function exportProject() {
  showAI('Packaging your project...');
  const payload = { code: getCode() };
  try { const project = currentProjectPayload(); if (project) payload.project = project; } catch (e) {}
  try {
    const res = await fetch('/export-project', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.success) {
      const msg = data.message || data.error || 'There is nothing to export yet.';
      out(msg, { sr: false }); speak(msg);
      return;
    }
    out((data.speech || 'Your project is ready to download.') + '\nFile: ' + data.filename +
        ' (' + (data.file_count || 0) + ' file' + (data.file_count === 1 ? '' : 's') + ').', { sr: false });
    offerProjectDownload(data.download_url, data.filename);
    speak(data.speech || 'Your project is ready to download.');
  } catch (e) {
    console.error(e);
    speak('Sorry, the export failed.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

async function requestProjectReport() {
  showAI('Building a project report...');
  const payload = { code: getCode(), verbosity: getVerbosity(), language: getLanguage() };
  try { const project = currentProjectPayload(); if (project) payload.project = project; } catch (e) {}
  const epoch = currentSpeechEpoch();
  try {
    const res = await fetch('/project-report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    const reportText = data.report_md || data.speech || 'No report available.';
    out(reportText, { sr: false });
    // continuation for "say more". The full markdown stays in the output box.
    speak(data.speech || 'Here is your project report.', { epoch });
    setSayMoreContinuation(data.speech_more || '');
  } catch (e) {
    console.error(e);
    speak('Sorry, I could not build the report.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

let _aiBubbleAnnounceRestoreTimer = null;
function showAI(msg, opts = {}) {
  const b = document.getElementById('aiBubble');
  if (!b) return;
  // Announcement-ownership pass: #aiBubble is role="status" aria-live=
  // "polite" (static, never toggled), so a real screen reader announces
  // this "in progress" text on its own. Some callers (analyzeCode,
  // fixCode, describeLine, generateCode) pair it with their own explicit
  // speak() call for the exact same "starting" moment, which is then a
  // second, redundant announcement. opts.announce === false silences just
  // that one call (same technique as updateCommandUnderstanding()) -
  // deliberately opt-in rather than the default, since most showAI()
  // calls have no paired speak() and rely on this to say anything at all
  // about the operation starting.
  if (opts.announce === false) {
    clearTimeout(_aiBubbleAnnounceRestoreTimer);
    b.setAttribute('aria-live', 'off');
    b.textContent = msg;
    showEl(b);
    _aiBubbleAnnounceRestoreTimer = setTimeout(() => b.setAttribute('aria-live', 'polite'), 50);
    return;
  }
  b.textContent = msg;
  showEl(b);
}
function hideAI() {
  const b = document.getElementById('aiBubble');
  if (!b) return;
  b.textContent = 'Idle.';
  setTimeout(() => hideEl(b), 1500);
}

function openApiKeyModal() {
  const modal = document.getElementById('apiKeyModal');
  const input = document.getElementById('apiKeyInput');
  if (!modal) return;
  showEl(modal);
  requestAnimationFrame(() => {
    if (input) input.focus();
  });
  speak('AI features need a Groq API key. Get one at console dot groq dot com. Paste it into the field and press Enter, or press Escape to cancel and continue without AI.');
}

function closeApiKeyModal() {
  const modal = document.getElementById('apiKeyModal');
  if (modal) hideEl(modal);
  if (editor) editor.focus();
}

// XRCVC Issue 19 (keyboard shortcut reference) and Issue 20 (getting started
// guide): both are reachable from a plain visible button in the header, not
// only via a voice/typed command, and behave like ordinary dialogs (Escape
// closes, Tab stays inside while open, closing returns focus to the button
// that opened it).
function _trapFocusWithinModal(modal, event) {
  if (event.key !== 'Tab') return;
  const items = Array.prototype.slice.call(
    modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
  ).filter(el => !el.disabled && el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function _openInfoModal(modalId, closeBtnId, opener) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  modal._codeupOpener = opener || document.activeElement;
  showEl(modal);
  const closeBtn = document.getElementById(closeBtnId);
  setTimeout(() => { if (closeBtn) closeBtn.focus(); }, 0);
  if (!modal._codeupKeydownBound) {
    modal._codeupKeydownBound = true;
    modal.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); _closeInfoModal(modalId); return; }
      _trapFocusWithinModal(modal, e);
    });
  }
}

function _closeInfoModal(modalId) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  hideEl(modal);
  const opener = modal._codeupOpener;
  if (opener && opener.focus) opener.focus();
}

function openShortcutHelp() { _openInfoModal('shortcutHelpModal', 'shortcutHelpCloseBtn'); }
function closeShortcutHelp() { _closeInfoModal('shortcutHelpModal'); }
function openGettingStartedGuide() { _openInfoModal('guideModal', 'guideCloseBtn'); }
function closeGettingStartedGuide() { _closeInfoModal('guideModal'); }

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
      input.value = '';
      closeApiKeyModal();
      speak('API key configured. AI features are now available.');
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

function maybePromptForApiKey(responseText) {
  if (!responseText) return false;
  const lower = String(responseText).toLowerCase();

  if (lower.startsWith('[offline mode]')) {
    if (!window._offlineModeAnnounced) {
      window._offlineModeAnnounced = true;
      speak('Cloud AI is unavailable. Switching to offline AI for this response.');
    }
    return false;
  }

  if (window._apiKeyPromptShown) return false;
  if (lower.includes('not configured')) {
    window._apiKeyPromptShown = true;
    speak('AI features are not configured on this server. Please ask your teacher to set the API key, or install Ollama for offline AI.');
    return true;
  }
  if (lower.includes('offline ai is also not available') || lower.includes('offline ai is not available')) {
    if (!window._noAiAnnounced) {
      window._noAiAnnounced = true;
      speak('Both cloud AI and offline AI are unavailable right now. Core features like running code still work. Try again in a minute.');
    }
    return false;
  }
  return false;
}

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
    glyphMargin:          true,
    automaticLayout:      true,
    accessibilitySupport: 'on',
    ariaLabel:            'Python code editor. Use arrow keys to navigate and type to edit. Tab moves focus out of the editor; use Control right bracket to indent a line and Control left bracket to outdent it. Press Escape when speech is quiet, or Control M, to leave the editor. Press Control Enter to run.',
    lineHeight:           24,
    tabSize:              4,
    insertSpaces:         true,
    wordWrap:             'on',
  });

  let _structureDebounce = null;
  editor.onDidChangeModelContent(() => {
    try { NarrationRequests.invalidate(); } catch (e) {}
    clearEditorErrorMarkers();
    syncActiveProjectFileLocal();
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

  // Guarantee Monaco measures its real container after first paint. If the
  // container size is not final at creation time (web-font load, flex reflow),
  // automaticLayout can race and the editor paints blank until a resize. Forcing
  // a layout on the next frames closes that window so Code Mode is always visible.
  const _forceEditorLayout = () => { try { if (editor) editor.layout(); } catch (e) {} };
  requestAnimationFrame(_forceEditorLayout);
  setTimeout(_forceEditorLayout, 200);
  window.addEventListener('load', _forceEditorLayout, { once: true });
});

function getModel() { return editor && editor.getModel(); }
function getCode()  { return (editor && editor.getValue()) || ''; }
function getLanguage() { return (document.getElementById('languageSelector') || {}).value || 'en'; }

// ==== PROJECT-FILE-ALIASES-START
function normalizeProjectPath(path) {
  let raw = String(path || '').trim().replace(/\\/g, '/');
  raw = raw.replace(/\s+dot\s+/gi, '.').replace(/\s+slash\s+/gi, '/');
  raw = raw.replace(/\s*\/\s*/g, '/').replace(/^\/+|\/+$/g, '');
  if (raw.includes(' ')) raw = raw.split('/').map(part => part.trim().replace(/\s+/g, '_')).join('/');
  if (!raw || raw.includes('..') || /^[a-zA-Z]:/.test(raw)) return '';
  if (!/^[A-Za-z0-9._/-]+$/.test(raw)) return '';
  return raw;
}

function projectPathParts(path) {
  const clean = normalizeProjectPath(path);
  const pieces = clean.split('/');
  const basename = pieces[pieces.length - 1] || '';
  const dot = basename.lastIndexOf('.');
  const stem = dot > 0 ? basename.slice(0, dot) : basename;
  const pathStem = dot > 0 ? clean.slice(0, clean.length - (basename.length - dot)) : clean;
  return { clean, basename, stem, pathStem };
}

function projectAliasKey(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\\/g, '/')
    .replace(/\s+dot\s+/g, '.')
    .replace(/\s+slash\s+/g, '/')
    .replace(/\.[a-z0-9_]+$/i, '')
    .replace(/[._/-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function uniqueProjectFileMatch(files, predicate) {
  const matches = files.filter(predicate);
  if (matches.length === 1) return { path: matches[0] };
  if (matches.length > 1) {
    return { error: 'I found multiple matching files. Please say the full file name.' };
  }
  return null;
}

function resolveProjectFileAlias(path, files) {
  const clean = normalizeProjectPath(path);
  if (!clean) return { path: '' };
  const names = (Array.isArray(files) ? files : Object.keys(files || {}))
    .map(name => normalizeProjectPath(name))
    .filter(Boolean)
    .sort();
  if (!names.length) return { path: clean };

  const cleanLower = clean.toLowerCase();
  const queryKey = projectAliasKey(path);
  const cleanKey = projectAliasKey(clean);
  const candidates = names.map(name => Object.assign({ name }, projectPathParts(name)));

  const stages = [
    item => item.clean.toLowerCase() === cleanLower,
    item => item.basename.toLowerCase() === cleanLower,
    item => item.stem.toLowerCase() === cleanLower,
    item => projectAliasKey(item.stem) === queryKey || projectAliasKey(item.stem) === cleanKey,
    item => projectAliasKey(item.basename) === queryKey || projectAliasKey(item.basename) === cleanKey,
    item => {
      const pathKey = projectAliasKey(item.pathStem);
      return pathKey === queryKey || pathKey.endsWith(` ${queryKey}`) ||
        pathKey === cleanKey || pathKey.endsWith(` ${cleanKey}`);
    },
  ];

  for (const predicate of stages) {
    const result = uniqueProjectFileMatch(candidates, item => predicate(item));
    if (result) return result.error ? result : { path: result.path.name };
  }
  return { path: clean };
}
// ==== PROJECT-FILE-ALIASES-END

function syncActiveProjectFileLocal() {
  if (!ProjectState.active || !ProjectState.activeFile) return;
  ProjectState.files[ProjectState.activeFile] = getCode();
}

function currentProjectPayload(runFile) {
  if (!ProjectState.active) return null;
  syncActiveProjectFileLocal();
  const entry = normalizeProjectPath(runFile || ProjectState.activeFile || ProjectState.entry || 'main.py') || 'main.py';
  return {
    name: ProjectState.manifest && ProjectState.manifest.name ? ProjectState.manifest.name : 'CodeUp Project',
    files: Object.assign({}, ProjectState.files),
    entry,
    active_file: ProjectState.activeFile || entry,
    requirements: ProjectState.requirements || [],
    manifest: ProjectState.manifest || {},
  };
}

function renderProjectFiles() {
  const panel = document.getElementById('projectFileList');
  if (!panel) return;
  const names = Object.keys(ProjectState.files || {}).sort();
  if (!ProjectState.active || names.length === 0) {
    panel.innerHTML = '<div style="color:var(--text-dim);font-style:italic;padding:6px 0;font-size:0.8rem;">Single-file mode</div>';
    return;
  }
  panel.innerHTML = names.map(path => {
    const active = path === ProjectState.activeFile;
    return `<div class="snippet-item" tabindex="0" role="button" data-project-path="${escapeHtml(path)}" aria-label="Open project file ${escapeHtml(path)}${active ? ', active' : ''}" style="${active ? 'border-color:var(--accent);' : ''}">
      <span>${active ? '* ' : ''}${escapeHtml(path)}</span>
    </div>`;
  }).join('');
  panel.querySelectorAll('[data-project-path]').forEach(item => {
    const path = item.getAttribute('data-project-path');
    item.addEventListener('click', () => openProjectFile(path));
    item.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openProjectFile(path);
      }
    });
  });
}

function applyProjectData(data, opts = {}) {
  const files = data.files || (data.project && data.project.files) || {};
  const manifest = data.manifest || (data.project && data.project.manifest) || {};
  const normalized = {};
  Object.keys(files || {}).forEach(path => {
    const clean = normalizeProjectPath(path);
    if (clean) normalized[clean] = String(files[path] || '');
  });
  if (!Object.keys(normalized).length) return false;
  ProjectState.active = true;
  ProjectState.files = normalized;
  ProjectState.manifest = manifest;
  ProjectState.entry = normalizeProjectPath(data.entry || manifest.entry || 'main.py') || 'main.py';
  ProjectState.activeFile = normalizeProjectPath(data.active_file || manifest.active_file || ProjectState.entry) || ProjectState.entry;
  ProjectState.requirements = data.requirements || manifest.requirements || [];
  if (!ProjectState.files[ProjectState.activeFile]) {
    ProjectState.activeFile = Object.keys(ProjectState.files).sort()[0];
  }
  setCode(ProjectState.files[ProjectState.activeFile] || '', { preserveSpeech: true, projectFile: true, allowNonPython: !ProjectState.activeFile.endsWith('.py') });
  renderProjectFiles();
  const speech = data.speech || `${Object.keys(ProjectState.files).length} project files loaded. Active file is ${ProjectState.activeFile}.`;
  out(speech, { sr: false });
  if (!opts.silent) speak(speech);
  return true;
}

async function saveProjectFile(path, content, active = true) {
  const clean = normalizeProjectPath(path);
  if (!clean) {
    speak('That file name is not valid.');
    return false;
  }
  try {
    const res = await fetch('/project/files', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: clean, content: content == null ? '' : String(content), active }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || `Could not save ${clean}.`;
      out(msg, { sr: false }); speak(msg);
      return false;
    }
    ProjectState.active = true;
    ProjectState.files[clean] = content == null ? '' : String(content);
    ProjectState.activeFile = active ? clean : ProjectState.activeFile || clean;
    ProjectState.entry = data.manifest && data.manifest.entry ? data.manifest.entry : (ProjectState.entry || clean);
    ProjectState.manifest = data.manifest || ProjectState.manifest;
    ProjectState.requirements = (ProjectState.manifest && ProjectState.manifest.requirements) || ProjectState.requirements || [];
    renderProjectFiles();
    return true;
  } catch (e) {
    console.error(e);
    speak('Project file save failed.');
    return false;
  }
}

async function openProjectFile(path) {
  const resolved = resolveProjectFileAlias(path, ProjectState.files);
  if (resolved.error) { out(resolved.error, { sr: false }); speak(resolved.error); return; }
  const clean = resolved.path;
  if (!clean) { speak('Please give a valid file name.'); return; }
  syncActiveProjectFileLocal();
  if (ProjectState.files[clean] != null) {
    ProjectState.active = true;
    ProjectState.activeFile = clean;
    setCode(ProjectState.files[clean], { preserveSpeech: true, projectFile: true, allowNonPython: !clean.endsWith('.py') });
    renderProjectFiles();
    const msg = `Opened ${clean}.`;
    out(msg, { sr: false }); speak(msg);
    return;
  }
  try {
    const res = await fetch('/project/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: clean }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || `${clean} was not found.`;
      out(msg, { sr: false }); speak(msg);
      return;
    }
    ProjectState.active = true;
    ProjectState.files[clean] = data.content || '';
    ProjectState.activeFile = clean;
    ProjectState.manifest = data.manifest || ProjectState.manifest;
    setCode(ProjectState.files[clean], { preserveSpeech: true, projectFile: true, allowNonPython: !clean.endsWith('.py') });
    renderProjectFiles();
    out(data.speech || `Opened ${clean}.`, { sr: false });
    speak(data.speech || `Opened ${clean}.`);
  } catch (e) {
    console.error(e);
    speak('Open project file failed.');
  }
}

async function createProjectFile(path) {
  const clean = normalizeProjectPath(path);
  if (!clean) { speak('Please give a valid file name.'); return; }
  syncActiveProjectFileLocal();
  if (ProjectState.files[clean] != null) {
    await openProjectFile(clean);
    return;
  }
  const starter = clean.endsWith('.py') ? '# New project file\n' : '';
  const ok = await saveProjectFile(clean, starter, true);
  if (!ok) return;
  setCode(starter, { preserveSpeech: true, projectFile: true, allowNonPython: !clean.endsWith('.py') });
  const msg = `Created ${clean}.`;
  out(msg, { sr: false }); speak(msg);
}

async function renameProjectFile(oldPath, newPath) {
  syncActiveProjectFileLocal();
  const oldClean = normalizeProjectPath(oldPath || ProjectState.activeFile);
  const newClean = normalizeProjectPath(newPath);
  if (!oldClean || !newClean) { speak('Please give a valid old and new file name.'); return; }
  try {
    const res = await fetch('/project/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_path: oldClean, new_path: newClean }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || `Could not rename ${oldClean}.`;
      out(msg, { sr: false }); speak(msg);
      return;
    }
    ProjectState.files[newClean] = ProjectState.files[oldClean] || getCode();
    delete ProjectState.files[oldClean];
    ProjectState.activeFile = newClean;
    ProjectState.manifest = data.manifest || ProjectState.manifest;
    setCode(ProjectState.files[newClean], { preserveSpeech: true, projectFile: true, allowNonPython: !newClean.endsWith('.py') });
    renderProjectFiles();
    out(data.speech || `Renamed ${oldClean} to ${newClean}.`, { sr: false });
    speak(data.speech || `Renamed ${oldClean} to ${newClean}.`);
  } catch (e) {
    console.error(e);
    speak('Rename project file failed.');
  }
}

async function deleteProjectFile(path) {
  const clean = normalizeProjectPath(path || ProjectState.activeFile);
  if (!clean) { speak('Please give a valid file name to delete.'); return; }
  try {
    const res = await fetch('/project/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: clean }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || `Could not delete ${clean}.`;
      out(msg, { sr: false }); speak(msg);
      return;
    }
    delete ProjectState.files[clean];
    const next = Object.keys(ProjectState.files).sort()[0] || 'main.py';
    ProjectState.activeFile = next;
    ProjectState.manifest = data.manifest || ProjectState.manifest;
    if (ProjectState.files[next] != null) {
      setCode(ProjectState.files[next], { preserveSpeech: true, projectFile: true, allowNonPython: !next.endsWith('.py') });
    }
    renderProjectFiles();
    out(data.speech || `Deleted ${clean}.`, { sr: false });
    speak(data.speech || `Deleted ${clean}.`);
  } catch (e) {
    console.error(e);
    speak('Delete project file failed.');
  }
}

function readProjectFiles() {
  syncActiveProjectFileLocal();
  const names = Object.keys(ProjectState.files || {}).sort();
  if (!ProjectState.active || names.length === 0) {
    const msg = 'Single-file mode is active. There is only the current editor file.';
    out(msg, { sr: false }); speak(msg);
    return;
  }
  const reqs = ProjectState.requirements && ProjectState.requirements.length ? ` Requirements: ${ProjectState.requirements.join(', ')}.` : '';
  const msg = `Project files: ${names.join(', ')}. Active file is ${ProjectState.activeFile}.${reqs}`;
  out(msg, { sr: false }); speak(msg);
}

function explainProjectStructure() {
  syncActiveProjectFileLocal();
  const names = Object.keys(ProjectState.files || {}).sort();
  if (!ProjectState.active || names.length === 0) {
    const msg = 'Single-file mode is active. There is only the current editor file.';
    out(msg, { sr: false }); speak(msg);
    return;
  }
  const roles = names.map(name => {
    const lower = name.toLowerCase();
    if (name === ProjectState.activeFile || lower === 'main.py') return `${name} is the entry point you run.`;
    if (lower.endsWith('requirements.txt')) return `${name} lists the packages this project needs.`;
    if (lower.endsWith('readme.md')) return `${name} explains the project for the learner.`;
    if (lower.startsWith('tests/') || lower.includes('/test_') || lower.startsWith('test_')) return `${name} contains tests for the project.`;
    if (lower.endsWith('.csv') || lower.endsWith('.json')) return `${name} stores data used by the code.`;
    if (lower.endsWith('.py')) return `${name} contains helper Python code imported by another file.`;
    return `${name} is a project file.`;
  });
  const reqs = ProjectState.requirements && ProjectState.requirements.length
    ? ` Requirements: ${ProjectState.requirements.join(', ')}.`
    : '';
  const msg = `Project structure: ${roles.join(' ')}${reqs}`;
  out(msg, { sr: false }); speak(msg);
}

async function explainProjectRequirements() {
  if (ProjectState.active) {
    const reqs = ProjectState.requirements || [];
    const msg = reqs.length
      ? `This project needs: ${reqs.join(', ')}. They are listed in requirements.txt.`
      : 'This project does not need third-party packages.';
    out(msg, { sr: false }); speak(msg);
    return;
  }
  try {
    const res = await fetch('/project/requirements');
    const data = await res.json();
    const msg = data.speech || 'No requirements information available.';
    out(msg, { sr: false }); speak(msg);
  } catch (e) {
    speak('Could not read project requirements.');
  }
}

async function runProjectFile(path) {
  const resolved = resolveProjectFileAlias(path || ProjectState.activeFile || 'main.py', ProjectState.files);
  if (resolved.error) { out(resolved.error, { sr: false }); speak(resolved.error); return; }
  const clean = resolved.path;
  if (!clean) { speak('Please give a valid file name to run.'); return; }
  if (ProjectState.files[clean] != null && ProjectState.activeFile !== clean) {
    await openProjectFile(clean);
  }
  await runCode(clean);
}

function looksLikeNonPythonCode(value) {
  const text = String(value || '').trimStart();
  if (!text) return false;

  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const first = (lines[0] || '').toLowerCase();
  const head = lines.slice(0, 30).join('\n').toLowerCase();
  const headWithoutStrings = head.replace(/("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/g, '');

  if (/^(<!doctype\s+html|<html\b|<head\b|<body\b|<script\b|<style\b|<\/html\b|<\/body\b|<div\b|<section\b|<main\b)/i.test(first)) {
    return true;
  }

  const tagMatches = headWithoutStrings.match(/<\/?[a-z][a-z0-9-]*(?:\s|>|\/>)/g) || [];
  if (tagMatches.length >= 4 && (headWithoutStrings.includes('<html') || headWithoutStrings.includes('<body') || headWithoutStrings.includes('</'))) {
    return true;
  }

  if (/^\s*(body|html|header|main|section|div|p|h[1-6]|[.#][\w-]+)\s*\{/mi.test(headWithoutStrings) && headWithoutStrings.includes('}') && headWithoutStrings.includes(':')) {
    return true;
  }

  return /^\s*(const|let|var)\s+\w+\s*=/m.test(headWithoutStrings)
    || /^\s*function\s+\w+\s*\(/m.test(headWithoutStrings)
    || /=>\s*\{/.test(headWithoutStrings)
    || /\bdocument\.(getElementById|querySelector|addEventListener)\b/.test(headWithoutStrings);
}

function rejectNonPythonCode(source) {
  const suffix = source ? ` Rejected ${source}.` : '';
  const msg = `${PYTHON_ONLY_MESSAGE}${suffix}`;
  out(msg, { sr: false });
  speak(msg);
}

function ensurePythonEditorContent(action) {
  if (looksLikeNonPythonCode(getCode())) {
    rejectNonPythonCode(action || 'this action');
    return false;
  }
  return true;
}

function resetPythonStarter() {
  setCode(DEFAULT_PYTHON_STARTER, { preserveSpeech: true });
  out('Python starter loaded.', { sr: false });
  speak('Python starter loaded.');
}

function setCode(v, opts) {
  opts = opts || {};
  if (!opts.allowNonPython && looksLikeNonPythonCode(v)) {
    rejectNonPythonCode(opts.source || 'non-Python code');
    try { localStorage.removeItem(AUTOSAVE_KEY); } catch (e) {}
    _autosaveLastCode = '';
    return false;
  }
  if (typeof ErrorBeaconManager !== 'undefined') ErrorBeaconManager.stop();
  try { NarrationRequests.invalidate(); } catch (e) {}
  if (!opts.preserveSpeech) SpeechManager.cancelAll();
  lastSpokenText = null;
  window.executionTrace = [];
  window.traceIndex = 0;
  navigationHistory = [];
  historyIndex = -1;
  if (typeof _breakpoints !== 'undefined') {
    _breakpoints.clear();
    _watchedVars.clear();
    if (editor && typeof _breakpointDecorations !== 'undefined') {
      try { _breakpointDecorations = editor.deltaDecorations(_breakpointDecorations, []); } catch (e) {}
    }
  }
  if (editor) editor.setValue(v);
  return true;
}

function out(t, options = {}) {
  const output = document.getElementById('output');
  if (output) output.textContent = t;
  const text = String(t || '').trim();
  if (options.sr === false) return;
  // Announcement-ownership pass: #output carries its own static aria-live,
  // set to "polite" whenever browser speech is off (Screen Reader Safe
  // mode - see updateSpeechModeUI()), so a real screen reader already
  // announces this exact textContent change on its own the instant it
  // happens. A *manual* srAnnounce() of the identical text right after was
  // a second, redundant announcement of the same event for every caller
  // that didn't already know to pass {sr:false} - confirmed live: the
  // native #output mutation AND a separate srAnnounce() call both fired
  // for one out() call. In CodeUp Voice mode #output's aria-live is "off"
  // instead (so it doesn't clash with CodeUp's own spoken narration), and
  // this manual announcement remains the only live-region signal there -
  // left unchanged, since suppressing it too could silence an out() call
  // that has no paired speak().
  if (!_browserSpeechEnabled) return;
  const isError = options.assertive || /^(?:error\b|found \d+ errors?\b|mentor error\b)/i.test(text) ||
    /\b(?:failed|failure)\.?$/i.test(text);
  srAnnounce(text, isError ? 'assertive' : 'polite');
}

let _cuAnnounceRestoreTimer = null;
function updateCommandUnderstanding(update = {}) {
  // Announcement-ownership pass: #commandUnderstanding's aria-live is
  // always "polite" (unlike #output/#mentorTranscript, it is never
  // toggled off), so a real screen reader announces every change here on
  // its own. Several callers (applyCommandUnderstanding, for every
  // successfully-dispatched command) update this status line with a
  // terse, generic label - "Action: Generating code." - in the same
  // synchronous step as a far richer out()/speak() announcement of the
  // actual result for that same event. Left alone, that is a second,
  // redundant automatic announcement of the same event. update.announce
  // === false keeps the visible text (and isError styling) exactly as
  // before but briefly silences just this one DOM write so it can't
  // trigger its own announcement, leaving the richer companion
  // announcement as sole owner. Failure updates (isError:true) have no
  // such companion, so they never pass this - the status line stays the
  // owner there, unchanged from the earlier accessibility gate.
  const container = document.getElementById('commandUnderstanding');
  const suppressAnnounce = update.announce === false && !!container;
  if (suppressAnnounce) {
    // #commandUnderstanding's resting aria-live is always "polite" - no
    // other code ever sets it otherwise - so restoring to that literal
    // value (rather than whatever was read back a moment ago) is always
    // correct and sidesteps a real race: two announce:false updates close
    // together would otherwise have the second one capture "off" (set by
    // the first, not yet restored) as its own "previous" value and
    // restore to that, leaving the region permanently silenced. A single
    // shared timer means only the last of several rapid updates actually
    // restores it, which is fine since they'd all restore to the same
    // value anyway. setTimeout (not requestAnimationFrame) because rAF
    // callbacks are paused entirely in a backgrounded tab, which would
    // leave this stuck off indefinitely.
    clearTimeout(_cuAnnounceRestoreTimer);
    container.setAttribute('aria-live', 'off');
  }
  const heardEl = document.getElementById('heardTranscript');
  const understoodEl = document.getElementById('understoodCommand');
  const nextEl = document.getElementById('nextCommandAction');
  if (heardEl && Object.prototype.hasOwnProperty.call(update, 'heard')) {
    heardEl.textContent = String(update.heard || '').trim() || 'Nothing heard yet.';
  }
  if (understoodEl && Object.prototype.hasOwnProperty.call(update, 'understood')) {
    understoodEl.textContent = String(update.understood || '').trim() || 'Waiting for a command.';
  }
  if (nextEl && Object.prototype.hasOwnProperty.call(update, 'nextAction')) {
    nextEl.textContent = String(update.nextAction || '').trim() || 'None yet.';
    // Product-polish pass: a command/AI failure needs to be visually
    // noticeable to a sighted user without touching #output - that's
    // program output, a different concept, and overwriting it would erase
    // whatever the learner's program actually printed. This status line is
    // the smallest existing surface already wired for exactly this
    // (role="status", its own aria-live, already correctly announced) - it
    // just needed to be visually distinct on failure. --danger is already
    // defined per-theme (default/night/high-contrast - see core.css), so
    // this stays correct in every mode. Every call that starts a new
    // command omits isError (falsy), so this never lingers from a previous
    // failure into a fresh attempt.
    nextEl.style.color = update.isError ? 'var(--danger)' : '';
    nextEl.style.fontWeight = update.isError ? '700' : '';
  }
  if (suppressAnnounce) {
    _cuAnnounceRestoreTimer = setTimeout(() => container.setAttribute('aria-live', 'polite'), 50);
  }
}
window.updateTranscriptStatus = updateCommandUnderstanding;

function describeCommandAction(action) {
  const labels = {
    action_sequence: 'Running the planned actions.',
    run: 'Running code.',
    run_project_file: 'Running project file.',
    generate_code: 'Generating code.',
    clear_editor: 'Clearing the editor.',
    walk_through: 'Walking through the program.',
    mentor_chat: 'Answering the question.',
    mentor_code_map: 'Mapping the code.',
    code_map: 'Mapping the code.',
    sonify_block: 'Sonifying the block.',
    read_project_files: 'Reading project files.',
    open_project_file: 'Opening project file.',
    start_tutorial: 'Starting tutorial.',
    help: 'Showing command guide.',
    exact_symbol_clarification: 'Waiting for clarification.',
    orchestrator_clarification: 'Waiting for clarification.',
  };
  return labels[action] || (action ? `Action: ${String(action).replace(/_/g, ' ')}.` : '');
}

function applyCommandUnderstanding(data, heardText) {
  if (!data) return;
  const understood = data.normalized_text || data.understood || data.spoken_summary || heardText || '';
  const nextAction = data.next_action || data.label || describeCommandAction(data.action);
  // announce:false: this fires for every successfully-dispatched command
  // (run, generate code, analyze, fix, ...), immediately before that
  // command's own, far more informative out()/speak() announcement of
  // what actually happened - see updateCommandUnderstanding()'s own
  // comment. The visible status line still updates; it just isn't a
  // second automatic announcer for the same event.
  updateCommandUnderstanding({
    heard: data.heard || heardText || '',
    understood,
    nextAction,
    announce: false,
  });
}

function tellUser(text, opts) {
  if (!text) return;
  out(text, { sr: false });
  speak(text, opts || {});
}

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

function startHeartbeat() {
  stopHeartbeat();
  _heartbeatTimer = setInterval(() => {
    SonificationManager.playTone(440, 0.03, 0.025);
  }, 500);
}
function stopHeartbeat() {
  if (_heartbeatTimer) {
    clearInterval(_heartbeatTimer);
    _heartbeatTimer = null;
  }
}

let _lastErrorContext = null;  // {code, error, language}

async function runCode(runFile, codeOverride) {
  if (_liveInputMode) {
    return runCodeStreaming();
  }

  SpeechManager.cancelAll();
  try { NarrationRequests.invalidate(); } catch (e) {}

  const hasCodeOverride = codeOverride != null;
  const codeToCheck = hasCodeOverride ? String(codeOverride || '') : getCode();
  if (!codeToCheck.trim()) {
    speak(hasCodeOverride ? 'The Audio Blocks program is empty.' : 'There is no Python code to run.');
    return;
  }
  if (!hasCodeOverride && !ensurePythonEditorContent('run')) return;
  const usesInput = /\binput\s*\(/.test(codeToCheck);
  if (usesInput && _preflightInputs.length > 0) {
    speak(`Pre-flight inputs ready: ${_preflightInputs.length} value${_preflightInputs.length === 1 ? '' : 's'}.`);
  }

  // Rapid Run/Run/switch-file races (Pass 5C live check): runCode() itself
  // used a raw, unguarded fetch, so an earlier run's late-arriving response
  // was never checked for staleness before speaking/mutating state - it
  // could still narrate an old file's output after a newer run (or a file
  // switch) had already taken over. Reuse the same 'run' scope on every
  // call: begin() supersedes the previous in-flight run's guard, and its
  // AbortController cancels that run's own request outright.
  const _runGuard = NarrationRequests.begin('run', narrationContext(codeToCheck));

  AppState.isExecuting = true;
  cueSuccess();
  startHeartbeat();
  const runFileLabel = normalizeProjectPath(runFile || '');
  const _runMsgOut = runFileLabel
    ? `Running ${runFileLabel}...`
    : (getLanguage() === 'hi' ? 'Code run ho raha hai...' : 'Running...');
  const _runMsgSpoken = runFileLabel
    ? `Running ${runFileLabel}.`
    : (getLanguage() === 'hi' ? 'Code run ho raha hai.' : 'Running code.');
  const _runMsgAI = runFileLabel
    ? `Running ${runFileLabel}...`
    : (getLanguage() === 'hi' ? 'Code run ho raha hai...' : 'Running code...');
  if (!usesInput) out(_runMsgOut, { sr: false });
  // announce:false: #aiBubble's aria-live is never toggled by speech mode
  // (unlike #output), so with CodeUp Voice on, this near-duplicate of the
  // speak() call two lines below was independently announced by a real
  // screen reader at the same moment VoiceEngine spoke it aloud - the
  // exact clash reproduced live for the Run flow.
  showAI(_runMsgAI, { announce: false });
  // sr:false only when out() actually wrote #runMsgOut just above - its
  // native aria-live (Screen Reader Safe mode) already announces that, so
  // this speak() call would otherwise be a second announcement of the
  // same "run started" event. When usesInput is true, out() is skipped
  // entirely above, so this remains the only announcement - suppressing
  // it too would be a silent start with nothing said at all.
  speak(_runMsgSpoken, usesInput ? {} : { sr: false });
  try {
    const payload = {
      code: codeToCheck,
      language: getLanguage(),
      inputs: _preflightInputs,
    };
    const project = hasCodeOverride ? null : currentProjectPayload(runFile);
    if (project) {
      payload.project = project;
      payload.file = normalizeProjectPath(runFile || ProjectState.activeFile || project.entry);
    }
    const res = await fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: _runGuard.signal,
    });
    const data = await res.json();
    if (!_runGuard.active(narrationContext())) return;
    window.executionTrace = data.trace || [];
    window.traceIndex = 0;

    if (data.action === 'request_program_input') {
      handleProgramInputRequest(data);
      return;
    }

    if (data.success) {
      const shouldFocusOutputAfterInput = !!(document.activeElement && document.activeElement.id === 'programInputValue');
      _programInputRequest = null;
      hideProgramInputControl('Program finished. Focus moved to output.');
      clearSrAlert();
      clearEditorErrorMarkers();
      // sr:false - out() would otherwise push the raw output into #srAnnouncer
      // on top of the #output live region's own change AND the formatted
      // speak(formatRunOutputSpeech(...)) announcement below, so a Screen
      // Reader Safe user heard the same run output announced twice (once raw,
      // once formatted) - XRCVC "duplicate output speech".
      out(data.output, { sr: false });
      const outputFocus = document.getElementById('output');
      try { if (outputFocus && shouldFocusOutputAfterInput) outputFocus.focus(); } catch (e) {}
      cueSuccess();
      ErrorBeaconManager.stop();
      _lastErrorContext = null;
      window.lastRunOutput = data.output || '';
      window.lastRunError = '';
      window.consecutiveErrors = 0;
      window._mentorSlowWalkthroughOffered = false;

      const diff = data.diff;
      _lastOutputDiff = diff;
      _previousOutput = _lastOutput;
      _lastOutput = data.output || '';

      if (data.speech_summary) speak(data.speech_summary, { sr: false });
      // sr:false: out(data.output, {sr:false}) just above already relies on
      // #output's own aria-live for the Screen Reader Safe announcement
      // (see that comment) - without sr:false here too, this formatted
      // version was a second, separate srAnnounce() of the same run-output
      // event on top of that native one. CodeUp Voice audio (VoiceEngine)
      // is unaffected; "Read output again" (speakOutput(), explicit:true)
      // still gets the full formatted/structured narration on demand.
      speak(formatRunOutputSpeech(data.output), { forceFull: true, speechKind: 'program-output', sr: false });
      if (data.clear_inputs_after_run) {
        _preflightInputs = [];
        _preflightInputPlaceholders = [];
        updateInputsPanel();
      }

      if (diff && !diff.identical && diff.total_changes > 0) {
        speak(diff.summary);
      }

      if (data.semantic_issues && data.semantic_issues.length) {
        data.semantic_issues.forEach(e => speak(`${e.category}. ${e.message}`));
      }
      const truncated = (data.trace || []).some(e => e.type === 'overflow');
      if (truncated) {
        speak('Heads up: your code ran more than five thousand steps, so the execution trace was truncated. Story mode and step-by-step playback may be incomplete.');
      }
      if (typeof window._tutorialOnRunSuccess === 'function') {
        setTimeout(function () { window._tutorialOnRunSuccess(); }, 2000);
      }
      if (typeof window._classroomOnRunResult === 'function') {
        window._classroomOnRunResult(codeToCheck, true, '');
      }
    } else {
      hideProgramInputControl('Run stopped because of an error.');
      out('ERROR:\n' + (data.error || ''), { assertive: true, sr: false });
      cueError();
      _lastErrorContext = {
        code: codeToCheck,
        error: data.error || '',
        language: getLanguage(),
      };
      window.previousCodeSnapshot = codeToCheck;
      window.previousErrorSnapshot = data.error || '';
      window.lastRunError = data.error || '';
      window.lastRunOutput = '';
      window.consecutiveErrors = (window.consecutiveErrors || 0) + 1;
      _lastOutputDiff = null;
      const errLines = (data.error || '').trim().split('\n').filter(Boolean);
      const lastLine = errLines[errLines.length - 1] || 'Unknown error';
      const lineMatch = (data.error || '').match(/line (\d+)/);
      const lineHint = lineMatch ? ` on line ${lineMatch[1]}` : '';
      setEditorErrorMarkerFromText(data.error || '', lastLine);
      speak(`Error${lineHint}: ${lastLine}`, { sr: false, priority: 'assertive' });
      // speak() above is deliberately sr:false so this is the one and only
      // assertive announcement in Screen Reader Safe mode; in CodeUp Voice
      // mode speak() already said it audibly, so calling srAlert() too would
      // duplicate the same error into an ARIA live region at the same time.
      if (_speechMode !== 'codeup-voice') srAlert(`Error${lineHint}: ${lastLine}`);
      if (data.explanation) {
        speak(data.explanation);
      }
      if (data.inputs_hint) {
        speak(data.inputs_hint);
      }
      maybeOfferSlowWalkthroughAfterErrors();
      if (typeof window._tutorialOnRunError === 'function') {
        setTimeout(function () { window._tutorialOnRunError(); }, 1500);
      }
      if (typeof window._classroomOnRunResult === 'function') {
        window._classroomOnRunResult(codeToCheck, false, data.error || '');
      }
    }
  } catch (e) {
    if (e && e.name === 'AbortError') return;
    out('System error.', { sr: false, assertive: true }); console.error(e); cueError(); speak('System error.');
  } finally {
    _runGuard.finish();
    stopHeartbeat();
    AppState.isExecuting = false;
    hideAI();
    await flushPendingActions();
  }
}

async function runCodeStreaming() {
  SpeechManager.cancelAll();
  if (!ensurePythonEditorContent('live run')) return;
  AppState.isExecuting = true;
  cueSuccess();
  startHeartbeat();
  out('Running in live input mode...\n', { sr: false });
  showAI('Live run started.');
  speak('Live input mode. I will read prompts as your code asks for them. Say or type your answer.');

  try {
    const startRes = await fetch('/run-stream/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: getCode(), inputs: _preflightInputs }),
    });
    const startData = await startRes.json();
    if (!startData.success) {
      stopHeartbeat();
      AppState.isExecuting = false;
      hideAI();
      const msg = startData.error || 'Could not start live run.';
      out(msg, { sr: false });
      speak(msg);
      if (msg.toLowerCase().includes('windows') || msg.toLowerCase().includes('posix')) {
        speak('Switching back to pre-flight input mode.');
        _liveInputMode = false;
        updateInputModeUI();
      }
      return;
    }

    const runId = startData.run_id;
    _activeStreamRun = { runId, awaitingPrompt: null, awaitingResolve: null };

    const url = `/run-stream/${encodeURIComponent(runId)}/stream`;
    const es = new EventSource(url);
    _activeStreamRun.eventSource = es;

    let buffer = '';
    let streamHadError = false;
    const streamCode = getCode();
    let outputElement = document.getElementById('output');

    es.onmessage = (evt) => {
      let event;
      try { event = JSON.parse(evt.data); } catch { return; }
      if (event.type === 'stdout') {
        buffer += event.text;
        outputElement.textContent = buffer;
        const chunk = event.text.trim();
        if (chunk) speak(chunk);
      } else if (event.type === 'stderr') {
        buffer += '[error] ' + event.text;
        outputElement.textContent = buffer;
        streamHadError = true;
        const chunk = event.text.trim();
        if (chunk) speak('Error: ' + chunk);
      } else if (event.type === 'input_request') {
        const prompt = event.prompt || 'Input requested';
        _activeStreamRun.awaitingPrompt = prompt;
        const message = `Your code is asking: ${prompt}. Expected text. Type your answer in Program inputs and press Enter, or say your answer.`;
        showProgramInputControl({ prompt, inputIndex: 1, inputCount: 1, expectedType: 'text', streaming: true, message });
        speak(message, { sr: false });
        SonificationManager.playTone(800, 0.15, 0.1);
      } else if (event.type === 'done') {
        hideProgramInputControl('No program input is being requested.');
        speak('Run complete.');
        es.close();
        stopHeartbeat();
        AppState.isExecuting = false;
        hideAI();
        _lastOutput = buffer;
        _activeStreamRun = null;
        if (typeof window._classroomOnRunResult === 'function') {
          window._classroomOnRunResult(streamCode, !streamHadError, streamHadError ? buffer : '');
        }
      } else if (event.type === 'error') {
        speak('Stream error.');
        es.close();
        stopHeartbeat();
        AppState.isExecuting = false;
        hideAI();
        _activeStreamRun = null;
      }
    };

    es.onerror = () => {
      speak('Connection to live run lost.');
      try { es.close(); } catch (e) {}
      stopHeartbeat();
      AppState.isExecuting = false;
      hideAI();
      _activeStreamRun = null;
    };
  } catch (e) {
    console.error(e);
    stopHeartbeat();
    AppState.isExecuting = false;
    hideAI();
    speak('Live run failed to start.');
  }
}

async function sendStreamingInput(value) {
  if (!_activeStreamRun || !_activeStreamRun.runId) return false;
  if (!_activeStreamRun.awaitingPrompt) {
    speak('Your code is not waiting for input right now.');
    return false;
  }
  try {
    await fetch(`/run-stream/${encodeURIComponent(_activeStreamRun.runId)}/input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: value }),
    });
    _activeStreamRun.awaitingPrompt = null;
    speak(`Sent: ${value}`);
    return true;
  } catch (e) {
    speak('Failed to send input.');
    return false;
  }
}

async function analyzeCode() {
  if (!ensurePythonEditorContent('analyze')) return;
  const codeSnapshot = getCode();
  // Deduplication: #output ("Analyzing...") and #aiBubble ("Analyzing code
  // with AI...") both carry their own aria-live for this same "started"
  // moment; speak() adds a third. sr:false / announce:false leave #output
  // as the sole automatic announcer in Screen Reader Safe mode.
  cueSuccess(); out('Analyzing...', { sr: false }); showAI('Analyzing code with AI...', { announce: false }); speak('Analyzing code.', { sr: false });
  try {
    const result = await guardedJson('analysis', '/analyze', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: codeSnapshot, language: getLanguage() }),
    }, narrationContext(codeSnapshot));
    if (staleResult(result)) return;
    const data = result.value;
    // sr:false: without it, out() manually announces this in CodeUp Voice
    // mode too (its own early-return only covers Screen Reader Safe mode -
    // #output's aria-live is "off" here, so nothing else would announce it
    // otherwise) - reproduced live as a real clash with the audible
    // speak() call below via VoiceEngine. In Screen Reader Safe mode,
    // #output's own aria-live still covers it either way.
    out(data.analysis || 'No analysis.', { sr: false });
    maybePromptForApiKey(data.analysis);
    if (data.analysis) {
      const spoken = data.analysis
        .replace(/^\[offline mode\]\s*/i, '')
        .replace(/\s*want a deeper line by line walkthrough\??.*$/i, '')
        .replace(/\s*just say:?\s*analyze deeper\.?\s*$/i, '');
      speak(spoken, { sr: false });
      window._lastAnalyzeContext = { code: codeSnapshot, at: Date.now() };
    } else {
      speak('No analysis available.', { sr: false });
    }
  } catch (e) {
    out('Analyze failed.', { sr: false }); console.error(e); cueError(); speak('Analyze failed.', { sr: false });
  } finally {
    hideAI();
  }
}

async function analyzeDeep() {
  if (!window._lastAnalyzeContext || (Date.now() - window._lastAnalyzeContext.at > 5 * 60 * 1000)) {
    speak('Please run analyze first, then say analyze deeper.');
    return;
  }
  const codeForDeep = window._lastAnalyzeContext.code;
  if (looksLikeNonPythonCode(codeForDeep)) {
    rejectNonPythonCode('deep analysis');
    window._lastAnalyzeContext = null;
    return;
  }
  const currentCode = getCode();
  if (codeForDeep !== currentCode) {
    SpeechManager.cancelAll();
    speak('Your code has changed since the last analyze. I will analyze the version you originally asked about. Say analyze again to refresh.');
  }
  cueSuccess(); showAI('Going deeper...'); speak('Going line by line.');
  try {
    const result = await guardedJson('analysis', '/analyze-deep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: codeForDeep, language: getLanguage() }),
    }, narrationContext(codeForDeep), () => ({ code: codeForDeep, file: ProjectState.activeFile || '' }));
    if (staleResult(result)) return;
    const data = result.value;
    out(data.analysis || 'No deeper analysis.', { sr: false });
    if (data.analysis) speak(data.analysis);
  } catch (e) {
    console.error(e); speak('Deeper analysis failed.');
  } finally {
    hideAI();
  }
}

let _demoPresetsCache = null;
let _demoPresetsCacheAt = 0;
const _DEMO_CACHE_TTL_MS = 5 * 60 * 1000;  // 5 minutes

async function fetchDemoPresets() {
  if (_demoPresetsCache && (Date.now() - _demoPresetsCacheAt) < _DEMO_CACHE_TTL_MS) {
    return _demoPresetsCache;
  }
  try {
    const res = await fetch('/demo-presets');
    const data = await res.json();
    if (data.success) {
      _demoPresetsCache = data.presets;
      _demoPresetsCacheAt = Date.now();
      return data.presets;
    }
  } catch (e) {
    console.error('Failed to load demos:', e);
  }
  return _demoPresetsCache || [];
}

async function listDemos() {
  const presets = await fetchDemoPresets();
  if (!presets.length) {
    tellUser('No demos available.');
    return;
  }
  let display = 'AVAILABLE DEMOS:\n\n';
  presets.forEach((p, i) => {
    display += `${i + 1}. ${p.title} — ${p.description}\n   Say: "demo ${p.id}"\n\n`;
  });
  out(display, { sr: false });
  speak(`There are ${presets.length} demos available.`);
  presets.forEach(p => speak(`${p.title}. ${p.description}. Say "demo ${p.id}" to load it.`));
  speak('Or just say "demo" followed by the name.');
}

async function runDemo(presetId) {
  const presets = await fetchDemoPresets();
  if (!presets.length) {
    speak('Demos are not available right now.');
    return;
  }

  if (!presetId || !presetId.trim()) {
    const first = presets[0];
    speak(`Loading the ${first.title} demo. ${first.description}`);
    await loadDemoById(first.id);
    return;
  }

  let match = presets.find(p => p.id === presetId.toLowerCase().trim());

  if (!match) {
    const needle = presetId.toLowerCase().trim();
    match = presets.find(p =>
      p.title.toLowerCase().includes(needle) ||
      p.id.toLowerCase().includes(needle)
    );
  }

  if (!match) {
    const names = presets.map(p => p.id).join(', ');
    speak(`I do not know a demo called ${presetId}. Available demos are: ${names}. Say "show demos" for descriptions.`);
    return;
  }

  await loadDemoById(match.id);
}

async function loadDemoById(id) {
  showAI(`Loading demo: ${id}...`);
  try {
    const res = await fetch(`/demo-presets/${encodeURIComponent(id)}`);
    const data = await res.json();
    if (data.success) {
      setCode(data.code, { preserveSpeech: true });
      out(`DEMO LOADED: ${data.title}\n\n${data.description}\n\nPress Ctrl+Enter or say "run" to execute.`, { sr: false });
      speak(`Demo loaded: ${data.title}. ${data.description}.`);
      speak('The code is in the editor. Press Control Enter, or say "run", to execute it. Say "narrate" to hear it line by line first.');
    } else {
      speak(`Could not load demo: ${data.error || 'unknown error'}`);
    }
  } catch (e) {
    console.error(e);
    speak('Demo loading failed.');
  } finally {
    hideAI();
  }
}

function readMyCodeAloud() {
  SpeechManager.cancelAll();
  const trimmed = String(getCode() || '').replace(/\s+$/, '');
  if (!trimmed.trim()) {
    out('The editor is empty.', { sr: false });
    speak('The editor is empty. There is nothing to read yet.');
    return;
  }
  const lines = trimmed.split('\n');
  out(trimmed, { sr: false });
  speak(`Your program has ${lines.length} line${lines.length === 1 ? '' : 's'}.`);
  for (let i = 0; i < lines.length; i++) {
    const lead = (lines[i].match(/^[ \t]*/) || [''])[0].replace(/\t/g, '    ');
    const note = lead.length >= 4 ? 'indented, ' : '';
    const body = lines[i].trim();
    speak(`Line ${i + 1}. ${note}${body || 'blank'}.`);
  }
  srAnnounce('Read your code');
}

async function narrateFile() {
  if (!ensureNotExecuting(() => narrateFile(), 'narrate file')) return;
  SpeechManager.cancelAll();
  const code = getCode();
  if (!code.trim()) {
    speak('The editor is empty. There is nothing to narrate.');
    return;
  }
  if (!ensurePythonEditorContent('narrate file')) return;
  cueSuccess();
  out('Narrating file...', { sr: false });
  showAI('Narrating the entire file...');
  speak('Narrating the file from start to finish. Press Escape at any time to stop.');
  try {
    const res = await fetch('/narrate-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) {
      const prefix = data.truncated
        ? `File has ${data.line_count} lines. Narrating the first 50.\n\n`
        : `File has ${data.line_count} lines.\n\n`;
      out(prefix + data.narration, { sr: false });
      if (data.truncated) {
        speak(`Your file has ${data.line_count} lines. I will narrate the first 50.`);
      }
      speak(data.narration);
      if (data.truncated) {
        speak(`That covers the first 50 lines. There are ${data.line_count - 50} more lines below.`);
      } else {
        speak('Narration complete.');
      }
      srAnnounce('File narration ready');
    } else {
      const msg = data.error || 'Could not narrate file.';
      out(msg, { sr: false });
      speak(msg);
    }
  } catch (e) {
    console.error(e);
    out('Narration failed.', { sr: false });
    speak('Narration failed. Please try again.');
  } finally {
    hideAI();
  }
}

async function summarizeFile() {
  if (!ensurePythonEditorContent('summarize')) return;
  cueSuccess(); out('Summarizing file...', { sr: false }); showAI('Summarizing this file...'); speak('Summarizing this file.');
  try {
    const res  = await fetch('/summarize', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    const summary = data.summary || 'No summary generated.';
    out(summary, { sr: false }); speak(summary);
  } catch (e) {
    console.error(e); out('Summary failed.', { sr: false }); speak('Summary failed.');
  } finally {
    speak('Task completed.'); hideAI();
  }
}

async function adviseCode() {
  if (!ensurePythonEditorContent('advise')) return;
  cueSuccess(); out('Advising on your code...', { sr: false }); showAI('Generating improvement suggestions...'); speak('Advising on your code.');
  try {
    const res  = await fetch('/advise', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    const data = await res.json();
    const advice = data.advice || 'No advice generated.';
    out(advice, { sr: false }); speak('Here are some ways you can improve this code.'); speak(advice);
  } catch (e) {
    out('Advice failed.', { sr: false }); console.error(e); cueError(); speak('Advice failed.');
  } finally {
    speak('Task completed.'); hideAI();
  }
}

async function fixCode() {
  const before = getCode();
  if (!ensurePythonEditorContent('fix')) return;
  cueSuccess(); out('Fixing...', { sr: false }); showAI('Fixing code with AI...', { announce: false }); speak('Fixing code.', { sr: false });
  try {
    const res  = await fetch('/fix', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: before, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) {
      if (typeof window._classroomReviewFix === 'function') {
        // In a classroom assignment context: show an accessible review
        // (apply/reject/explain) before touching the learner's code,
        // instead of applying it immediately.
        let explanation = data.speech || data.explanation || '';
        try {
          const diffRes = await fetch('/diff-explain', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ before, after: data.code, language: getLanguage() }),
          });
          const diffData = await diffRes.json();
          if (diffData.explanation) explanation = diffData.explanation;
        } catch (e) { /* review still works without the extra explanation */ }
        await window._classroomReviewFix(before, data.code, explanation);
      } else {
        setCode(data.code);
        const fixedSpeech = data.speech || data.explanation || 'Code has been fixed.';
        out(fixedSpeech, { sr: false }); speak(fixedSpeech, { sr: false });
        const diffRes  = await fetch('/diff-explain', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ before, after: data.code, language: getLanguage() }),
        });
        const diffData = await diffRes.json();
        if (diffData.explanation) { out(diffData.explanation, { sr: false }); speak(diffData.explanation, { sr: false }); }
      }
    } else {
      out('Fix failed.', { sr: false }); speak('Fix failed.', { sr: false });
    }
  } catch (e) {
    console.error(e); out('Fix failed.', { sr: false }); speak('Fix failed.', { sr: false });
  } finally {
    hideAI();
  }
}

async function describeLine(line) {
  if (!ensurePythonEditorContent('describe line')) return;
  showAI('Describing line ' + line, { announce: false }); speak('Describing line ' + line);
  try {
    const res  = await fetch('/describe', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: getCode(), line, language: getLanguage() }),
    });
    const data = await res.json();
    if (data.success) { out(data.description, { sr: false }); speak(data.description, { sr: false }); }
    else { const msg = data.message || 'Describe failed.'; out(msg, { sr: false }); speak(msg, { sr: false }); }
  } catch (e) {
    out('Describe failed.', { sr: false }); console.error(e); cueError(); speak('Describe failed.', { sr: false });
  } finally {
    hideAI();
  }
}

async function generateCode(prompt, context = {}) {
  if (!prompt) {
    SpeechManager.cancelAll();
    speak('Please provide a description of what you want to generate.');
    return;
  }

  SpeechManager.cancelAll();

  cueSuccess();
  out('Generating code for: ' + prompt, { sr: false });
  showAI('Generating code for: ' + prompt, { announce: false });
  speak('Generating code for ' + prompt + '. One moment please.', { sr: false });

  try {
    const res = await fetch('/generate-code', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ prompt, language: getLanguage(), source: context.input_source || context.source || 'typed' }),
    });
    const data = await res.json();
    if (data.success && data.project && data.files) {
      applyProjectData(data);
      cueSuccess();
      return;
    }
    if (data.success && data.code) {
      window.executionTrace = []; window.traceIndex = 0;
      setCode(data.code, { preserveSpeech: true });
      suggestPreflightInputsFromCode(data.code);
      cueSuccess();

      const usesInput = /\binput\s*\(/.test(data.code);
      if (data.exact_symbol) {
        const message = data.message || 'Exact-symbol code generated and inserted into editor. Press Control Enter to run.';
        out(message + '\n\nCode inserted. Press Control Enter to run.', { sr: false });
        speak(data.speech || message, { sr: false });
      } else if (usesInput) {
        const inputCount = (data.code.match(/\binput\s*\(/g) || []).length;
        out(`Code generated with ${inputCount} input() call${inputCount === 1 ? '' : 's'}.\n\nBefore running, declare your inputs by saying:\n  "set inputs to value1 and value2"`, { sr: false });
        speak(`Code is ready. Heads up: it uses input ${inputCount} time${inputCount === 1 ? '' : 's'}. Before pressing run, say "set inputs to" followed by your values.`, { sr: false });
      } else {
        out('Code generated and inserted into editor. Press Control Enter to run, or say "analyze" to hear an explanation.', { sr: false });
        speak('Code is ready in the editor. Press Control Enter to run it, or say "walk through code" to hear it explained.', { sr: false });
      }
    } else if (data.clarification) {
      const reason = data.message || data.error || 'Please type a more precise command.';
      out(reason, { sr: false });
      speak(data.speech || reason, { sr: false });
    } else {
      const reason = data.error || 'the AI returned an empty response. Please try rephrasing your request.';
      out('Code generation failed: ' + reason, { sr: false });
      cueError();
      speak('Code generation did not work. ' + reason, { sr: false });
    }
  } catch (e) {
    console.error(e);
    out('Code generation failed.', { sr: false });
    cueError();
    speak('Code generation failed.', { sr: false });
  } finally {
    hideAI();
  }
}

function gotoLine(line, record = true) {
  if (!ensureNotExecuting(() => gotoLine(line, record), 'go to line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) { const msg = `Line ${line} is out of range. File has ${maxLine} lines.`; out(msg, { sr: false }); speak(msg); return; }
  if (record) recordNavigation(line);
  try {
    editor.setPosition({ lineNumber: line, column: 1 });
  } catch (e) { console.warn('gotoLine: setPosition failed', e); return; }
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`, { sr: false });
  SpeechManager.cancelAll();
  speak(`Moved to line ${line}. ${text || 'Empty line.'}`);
}

function readLine(line) {
  if (!ensureNotExecuting(() => readLine(line), 'read line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) { const msg = `Line ${line} is out of range. File has ${maxLine} lines.`; out(msg, { sr: false }); speak(msg); return; }
  SpeechManager.cancelAll();
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`, { sr: false });
  speak(`Line ${line}: ${text || 'Empty line.'}`);
}

function readCurrentLine() {
  if (!ensureNotExecuting(() => readCurrentLine(), 'read current line')) return;
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const pos  = editor.getPosition() || { lineNumber: 1 };
  SpeechManager.cancelAll();
  const text = model.getLineContent(pos.lineNumber);
  out(`Line ${pos.lineNumber}: ${text}`, { sr: false });
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
  out(`Line ${line}: ${text}`, { sr: false });
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
  out(`Line ${line}: ${text}`, { sr: false });
  speak(`Line ${line}: ${text || 'Empty line.'}`);
}

function clearEditor() {
  navigationHistory = [];
  historyIndex = -1;
  ProjectState.active = false;
  ProjectState.files = {};
  ProjectState.activeFile = '';
  ProjectState.entry = 'main.py';
  ProjectState.requirements = [];
  ProjectState.manifest = {};
  renderProjectFiles();
  try { localStorage.removeItem(AUTOSAVE_KEY); } catch (e) {}
  _autosaveLastCode = '';
  setCode('');
  out('Editor cleared.', { sr: false });
  speak('Editor cleared.');
}

function deleteLine(line) {
  const model = getModel();
  if (!model) return;
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine) { const msg = `Line ${line} is out of range.`; out(msg, { sr: false }); speak(msg); return; }
  const text = model.getLineContent(line);

  let range;
  if (line === maxLine && maxLine > 1) {
    const prevLen = model.getLineContent(maxLine - 1).length;
    range = new monaco.Range(maxLine - 1, prevLen + 1, maxLine, text.length + 1);
  } else {
    range = new monaco.Range(line, 1, Math.min(line + 1, maxLine + 1), 1);
  }

  model.pushEditOperations([], [{ range, text: '' }], () => null);
  out(`Deleted line ${line}: ${text}`, { sr: false });
  speak(`Deleted line ${line}.`);
}

function applyConversationalEdit(aiAction) {
  const edit = aiAction || {};
  const kind = edit.action;
  const target = edit.target || {};
  const model = getModel();
  const confirmation = edit.spoken_confirmation || 'I applied that edit.';

  if (edit.requires_confirmation) {
    const msg = confirmation || 'That change needs confirmation before I replace the program.';
    out(msg, { sr: false });
    speak(msg);
    return;
  }

  if (kind === 'undo') {
    if (!editor) { speak('Editor not ready.'); return; }
    editor.trigger('voice', 'undo', null);
    out(confirmation, { sr: false });
    speak(confirmation);
    return;
  }

  if (!model || !editor) {
    speak('Editor not ready.');
    return;
  }

  const lineCount = model.getLineCount();
  const rawCode = String(edit.code || '').replace(/\r\n/g, '\n');
  const lineNumber = Number(target.line_number || edit.line_number || 0);

  function validLine(n) {
    return Number.isInteger(n) && n >= 1 && n <= lineCount;
  }

  function validInsertLine(n) {
    return Number.isInteger(n) && n >= 1 && n <= lineCount + 1;
  }

  function appendBlock(code) {
    const lastLine = model.getLineCount();
    const lastCol = model.getLineMaxColumn(lastLine);
    const current = getCode();
    const prefix = current && !current.endsWith('\n') ? '\n' : '';
    model.pushEditOperations([], [{
      range: new monaco.Range(lastLine, lastCol, lastLine, lastCol),
      text: prefix + code,
    }], () => null);
    const addedLines = (prefix + code).split('\n').length - 1;
    const newLine = Math.min(model.getLineCount(), lastLine + addedLines);
    editor.setPosition({ lineNumber: newLine, column: model.getLineMaxColumn(newLine) });
    editor.revealLineInCenter(newLine);
  }

  function insertBlock(n, code) {
    if (n === lineCount + 1) {
      appendBlock(code);
      return;
    }
    model.pushEditOperations([], [{
      range: new monaco.Range(n, 1, n, 1),
      text: code.endsWith('\n') ? code : code + '\n',
    }], () => null);
    editor.setPosition({ lineNumber: n, column: 1 });
    editor.revealLineInCenter(n);
  }

  function replaceLine(n, code) {
    const col = model.getLineMaxColumn(n);
    model.pushEditOperations([], [{
      range: new monaco.Range(n, 1, n, col),
      text: code,
    }], () => null);
    editor.setPosition({ lineNumber: n, column: 1 });
    editor.revealLineInCenter(n);
  }

  function deleteRawLine(n) {
    const col = model.getLineMaxColumn(n);
    const range = n === lineCount && lineCount > 1
      ? new monaco.Range(n - 1, model.getLineMaxColumn(n - 1), n, col)
      : new monaco.Range(n, 1, Math.min(n + 1, lineCount + 1), 1);
    model.pushEditOperations([], [{ range, text: '' }], () => null);
    const nextLine = Math.min(n, model.getLineCount());
    editor.setPosition({ lineNumber: nextLine, column: 1 });
    editor.revealLineInCenter(nextLine);
  }

  function indentLine(n) {
    replaceLine(n, '    ' + model.getLineContent(n));
  }

  function dedentLine(n) {
    const line = model.getLineContent(n);
    let next = line;
    if (next.startsWith('    ')) next = next.slice(4);
    else if (next.startsWith('\t')) next = next.slice(1);
    else {
      const msg = 'That line is not indented.';
      out(msg, { sr: false });
      speak(msg);
      return false;
    }
    replaceLine(n, next);
    return true;
  }

  let applied = true;
  if (kind === 'append_code') {
    if (!rawCode.trim()) { speak('No code was provided for that edit.'); return; }
    appendBlock(rawCode);
  } else if (kind === 'insert_line') {
    if (!validInsertLine(lineNumber) || !rawCode.trim()) { speak('I could not place that inserted line safely.'); return; }
    insertBlock(lineNumber, rawCode);
  } else if (kind === 'replace_line') {
    if (!validLine(lineNumber) || !rawCode.trim()) { speak('I could not replace that line safely.'); return; }
    replaceLine(lineNumber, rawCode);
  } else if (kind === 'delete_line') {
    if (!validLine(lineNumber)) { speak('I could not delete that line safely.'); return; }
    deleteRawLine(lineNumber);
  } else if (kind === 'indent_line') {
    if (!validLine(lineNumber)) { speak('I could not indent that line safely.'); return; }
    indentLine(lineNumber);
  } else if (kind === 'dedent_line') {
    if (!validLine(lineNumber)) { speak('I could not remove indentation safely.'); return; }
    applied = dedentLine(lineNumber);
  } else if (kind === 'replace_code') {
    if (!rawCode.trim()) { speak('No replacement code was provided.'); return; }
    if (!setCode(rawCode, { preserveSpeech: true, source: 'conversational edit' })) return;
  } else {
    speak('I could not apply that edit safely.');
    return;
  }

  if (!applied) return;
  suggestPreflightInputsFromCode(getCode());
  out(confirmation, { sr: false });
  speak(confirmation);
}

const _liveRegionTimers = {};
const _lastLiveRegionMessages = {};
const _lastLiveRegionTimes = {};
function srAnnounce(msg, priority = 'polite') {
  const region = priority === 'assertive' ? 'assertive' : 'polite';
  const el = document.getElementById(region === 'assertive' ? 'srAlert' : 'srAnnouncer');
  if (!el) return;
  // Cap must stay >= CODEUP_SPOKEN_OUTPUT_LIMIT (4000, see below) plus room for its
  // own truncation-notice sentence. Screen Reader Mode routes program-output speech
  // (already bounded/notice-appended by formatRunOutputSpeech) through this function
  // instead of audible TTS - a smaller cap here silently re-truncated that text mid-word
  // and dropped the "Read output again" hint before screen-reader users ever heard it.
  const cleaned = sanitizeSpeechText(String(msg || '').replace(/<module>/g, 'top-level code'))
    .replace(/\s+/g, ' ').trim().slice(0, 4200);
  const now = Date.now();
  if (!cleaned || (_lastLiveRegionMessages[region] === cleaned && now - (_lastLiveRegionTimes[region] || 0) < 1200)) return;
  _lastLiveRegionMessages[region] = cleaned;
  _lastLiveRegionTimes[region] = now;
  clearTimeout(_liveRegionTimers[region]);
  el.textContent = '';
  _liveRegionTimers[region] = setTimeout(function () { el.textContent = cleaned; }, 50);
}

function srAlert(msg) {
  srAnnounce(msg, 'assertive');
}

function clearSrAlert() {
  const el = document.getElementById('srAlert');
  if (!el) return;
  clearTimeout(_liveRegionTimers.assertive);
  el.textContent = '';
  _lastLiveRegionMessages.assertive = '';
  _lastLiveRegionTimes.assertive = 0;
}

function parseErrorLineNumber(errorText) {
  const text = String(errorText || '');
  const matches = Array.from(text.matchAll(/line\s+(\d+)/gi));
  if (!matches.length) return null;
  const last = matches[matches.length - 1];
  const n = Number(last[1]);
  return Number.isFinite(n) && n > 0 ? n : null;
}
function setEditorErrorMarkerFromText(errorText, fallbackMessage) {
  const model = getModel();
  if (!model || typeof monaco === 'undefined' || !monaco.editor) return;
  const line = Math.min(parseErrorLineNumber(errorText) || 1, model.getLineCount());
  const message = String(fallbackMessage || errorText || 'Python error').split('\n').filter(Boolean).pop() || 'Python error';
  const maxColumn = Math.max(1, model.getLineMaxColumn(line));
  monaco.editor.setModelMarkers(model, 'codeup-runtime', [{
    severity: monaco.MarkerSeverity.Error,
    startLineNumber: line,
    startColumn: 1,
    endLineNumber: line,
    endColumn: maxColumn,
    message: 'CodeUp error marker: ' + message,
    source: 'CodeUp runtime',
  }]);
  try {
    _editorErrorDecorationIds = editor.deltaDecorations(_editorErrorDecorationIds, [{
      range: new monaco.Range(line, 1, line, maxColumn),
      options: {
        isWholeLine: true,
        className: 'cu-monaco-error-line',
        glyphMarginClassName: 'cu-monaco-error-glyph',
        glyphMarginHoverMessage: { value: 'CodeUp error: ' + message },
      },
    }]);
  } catch (e) {}
  const summary = document.getElementById('errorSummary');
  const text = document.getElementById('errorSummaryText');
  if (summary) summary.hidden = false;
  if (text) text.textContent = 'Error on line ' + line + ': ' + message;
}
function clearEditorErrorMarkers() {
  const model = getModel();
  if (model && typeof monaco !== 'undefined' && monaco.editor) {
    try { monaco.editor.setModelMarkers(model, 'codeup-runtime', []); } catch (e) {}
  }
  if (editor && editor.deltaDecorations && _editorErrorDecorationIds.length) {
    try { _editorErrorDecorationIds = editor.deltaDecorations(_editorErrorDecorationIds, []); } catch (e) { _editorErrorDecorationIds = []; }
  }
  const summary = document.getElementById('errorSummary');
  const text = document.getElementById('errorSummaryText');
  if (summary) summary.hidden = true;
  if (text) text.textContent = 'No editor errors.';
}

async function saveSnippet() {
  await saveSnippetAccessible();
}

async function saveSnippetAccessible(voiceName) {
  const input = document.getElementById('snippetNameInput');
  const typedName = input && input.value.trim();
  const name  = normalizeSnippetVoiceName(voiceName || typedName);
  const saved = await saveSnippetWithName(name);
  if (saved) {
    if (input) input.value = '';
    srAnnounce('Snippet saved: ' + name);
  }
}

function normalizeSnippetVoiceName(name) {
  return String(name || '')
    .replace(/\s+/g, ' ')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .slice(0, 80);
}

function nextSnippetName() {
  const names = new Set(snippetsCache.map(sn => String(sn.name || '').toLowerCase()));
  let index = 1;
  while (names.has(`snippet ${index}`)) index += 1;
  return `Snippet ${index}`;
}

async function saveSnippetWithName(name) {
  if (!ensurePythonEditorContent('save snippet')) return;
  const code = getCode();
  if (!code.trim()) {
    const msg = getLanguage() === 'hi'
      ? 'Editor empty hai. Snippet save karne ke liye pehle code likho.'
      : 'The editor is empty. Nothing to save as a snippet.';
    out(msg, { sr: false }); speak(msg);
    return false;
  }
  try {
    await loadSnippets();
    const cleanName = normalizeSnippetVoiceName(name) || nextSnippetName();
    const duplicate = snippetsCache.find(sn => String(sn.name || '').toLowerCase() === cleanName.toLowerCase());
    if (duplicate) {
      const msg = getLanguage() === 'hi'
        ? `${cleanName} naam ka snippet already saved hai. Save karne ke liye alag naam choose karo.`
        : `A snippet named ${cleanName} already exists. Choose a different name before saving.`;
      out(msg, { sr: false }); speak(msg);
      return false;
    }
    const res = await fetch('/snippets', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ name: cleanName, code }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || 'Snippet was not saved.';
      out(msg, { sr: false }); speak(msg);
      return false;
    }
    await loadSnippets();
    const msg = getLanguage() === 'hi'
      ? `${cleanName} naam ka snippet save ho gaya.`
      : `Saved this code as the snippet ${cleanName}.`;
    out(msg, { sr: false });
    speak(msg);
    return true;
  } catch (e) {
    console.error('Snippet save failed:', e);
    out('Snippet save failed.', { sr: false });
    speak('Snippet save failed.');
    return false;
  }
}

let _loadSnippetsQueued = false;
async function loadSnippets() {
  if (_loadingSnippets) {
    _loadSnippetsQueued = true;
    return;
  }
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
      div.setAttribute('tabindex', '0');
      div.setAttribute('role', 'button');
      div.setAttribute('aria-label', `Load snippet: ${sn.name || 'Untitled'}`);
      div.addEventListener('click',   () => setCode(sn.code));
      div.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCode(sn.code); } });
      fragment.appendChild(div);
    });

    list.innerHTML = '';
    list.appendChild(fragment);
  } catch (e) {
    console.error('Snippet load failed:', e);
    out('Could not load snippets.');
  } finally {
    _loadingSnippets = false;
    if (_loadSnippetsQueued) {
      _loadSnippetsQueued = false;
      loadSnippets();
    }
  }
}

async function loadSnippetById(id, confirmed) {
  await loadSnippets();
  const needle = String(id || '').toLowerCase();
  const sn = snippetsCache.find(s => String(s.id) === String(id) ||
                                    (s.name && String(s.name).toLowerCase() === needle));
  if (!sn) {
    const msg = getLanguage() === 'hi'
      ? `${id} naam ka snippet nahi mila.`
      : `Snippet ${id} not found.`;
    speak(msg); out(msg, { sr: false }); return;
  }
  const current = getCode().trim();
  if (!confirmed && current && current !== String(sn.code || '').trim()) {
    pendingConfirm = {
      options: ['load_snippet'],
      context: { id, confirmed: true },
      expiresAt: Date.now() + 30000,
    };
    const msg = getLanguage() === 'hi'
      ? `${sn.name || id} snippet load karne se current editor replace hoga. Continue ke liye yes bolo, ya code rakhne ke liye cancel bolo.`
      : `Loading snippet ${sn.name || id} will replace the current editor. Say yes to continue or cancel to keep your code.`;
    out(msg, { sr: false });
    speak(msg);
    return;
  }
  if (!setCode(sn.code, { source: `snippet ${sn.name || id}` })) return;
  const msg = getLanguage() === 'hi'
    ? `${sn.name || id} snippet editor mein load ho gaya.`
    : `Loaded the snippet ${sn.name || id} into the editor.`;
  speak(msg); out(msg, { sr: false });
}

async function listSnippetsAccessible() {
  await loadSnippets();
  if (!snippetsCache.length) {
    const msg = getLanguage() === 'hi' ? 'Aapke paas saved snippets nahi hain.' : 'You have no saved snippets.';
    out(msg, { sr: false }); speak(msg);
    return;
  }
  const names = snippetsCache.map(sn => sn.name || 'Untitled');
  const msg = getLanguage() === 'hi'
    ? `Aapke snippets hain: ${names.join(', ')}.`
    : (names.length === 1
      ? `Your snippet is: ${names[0]}.`
      : `Your snippets are: ${names.join(', ')}.`);
  out(msg, { sr: false });
  speak(msg);
}

async function deleteSnippetById(id) {
  if (!id) { speak('Please specify which snippet to delete.'); return; }
  try {
    const res = await fetch(`/snippets/${encodeURIComponent(id)}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || `Could not delete snippet ${id}.`;
      out(msg, { sr: false }); speak(msg);
      return;
    }
    await loadSnippets();
    speak(data.speech || `Deleted snippet ${id}.`); out(data.speech || `Deleted snippet ${id}.`, { sr: false });
  } catch (e) {
    console.error('Snippet delete failed:', e);
    speak('Snippet delete failed.');
  }
}

async function renameSnippetById(id, newName) {
  if (!id) { speak('Please specify which snippet to rename.'); return; }
  if (!String(newName || '').trim()) { speak('Please give the snippet a new name.'); return; }
  try {
    const res = await fetch(`/snippets/${encodeURIComponent(id)}`, {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ name: newName }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const msg = data.error || `Could not rename snippet ${id}.`;
      out(msg, { sr: false }); speak(msg);
      return;
    }
    await loadSnippets();
    speak(data.speech || `Renamed snippet ${id} to ${newName}.`); out(data.speech || `Renamed snippet ${id} to ${newName}.`, { sr: false });
  } catch (e) {
    console.error('Snippet rename failed:', e);
    speak('Snippet rename failed.');
  }
}

async function previewSnippetById(id) {
  if (!id) { speak('Please specify which snippet to preview.'); return; }
  const sn = snippetsCache.find(s => String(s.id) === String(id) ||
                                      (s.name && s.name.toLowerCase() === String(id).toLowerCase()));
  if (!sn) {
    speak(`I could not find a snippet matching ${id}. Say list snippets to hear what is saved.`);
    return;
  }
  const lines = (sn.code || '').split('\n').slice(0, 5);
  out(`PREVIEW: ${sn.name}\n\n${lines.join('\n')}${(sn.code || '').split('\n').length > 5 ? '\n...' : ''}`, { sr: false });
  speak(`Preview of ${sn.name}. First ${lines.length} line${lines.length === 1 ? '' : 's'}:`);
  lines.forEach((l, i) => speak(`Line ${i + 1}: ${l || 'empty line'}`));
  speak('Say load snippet ' + sn.name + ' to open it in the editor.');
}

async function executeActionSequence(payload) {
  const actions = Array.isArray(payload && payload.actions) ? payload.actions : [];
  if (!actions.length) {
    const message = (payload && payload.spoken_summary) || 'No safe actions were planned.';
    out(message, { sr: false });
    speak(message);
    return;
  }
  const summary = (payload && payload.spoken_summary) || `I will do ${actions.length} actions.`;
  out(summary, { sr: false });
  speak(summary);
  for (const planned of actions) {
    if (!planned || !planned.action) continue;
    updateCommandUnderstanding({
      heard: payload && payload.heard,
      understood: payload && payload.normalized_text,
      nextAction: planned.label || planned.action,
    });
    await handleConfirmedAction(planned.action, planned);
  }
  updateCommandUnderstanding({
    heard: payload && payload.heard,
    understood: payload && payload.normalized_text,
    nextAction: 'Plan complete.',
  });
}

async function handleConfirmedAction(action, payload) {
  if (payload && payload.audio_blocks) renderAudioBlocks(payload.audio_blocks);
  const _noCancelActions = new Set(['action_sequence', 'generate_code', 'analyze', 'analyze_deep', 'fix', 'summarize', 'narrate_file', 'walk_through', 'advise', 'story_mode', 'mentor_chat', 'mentor_progress', 'mentor_code_map', 'code_map', 'step_narration', 'compare_before_after', 'replay_mistake', 'why_fixed_works', 'read_project_files', 'explain_project_structure', 'explain_requirements']);
  if (!_noCancelActions.has(action)) {
    SpeechManager.cancelAll();
  }
  if (action === 'run')              await runCode();
  else if (action === 'request_program_input') handleProgramInputRequest(payload || {});
  else if (action === 'audio_blocks_run') {
    await runCode('', (payload && payload.code) || '');
  }
  else if (action === 'action_sequence') await executeActionSequence(payload || {});
  else if (action === 'mentor_stop') { SpeechManager.cancelAll(); speak('Mentor stopped.'); }
  else if (action === 'stop_speaking') { SpeechManager.cancelAll(); srAnnounce('Speech stopped'); }
  else if (action === 'mentor_chat') {
    const _mentorMode = payload && payload.mode ? payload.mode : 'general';
    if (_mentorMode !== 'concept' && typeof VoiceEngine !== 'undefined' && typeof talkToMentorStreaming === 'function') {
      await talkToMentorStreaming(payload && payload.message, _mentorMode);
    } else {
      await talkToMentor(payload && payload.message, _mentorMode);
    }
  }
  else if (action === 'mentor_progress') await checkProgressWithMentor();
  else if (action === 'mentor_code_map') await speakCodeMap();
  else if (action === 'mentor_preference') setMentorPreference(payload && payload.key, payload && payload.value);
  else if (action === 'analyze')     await analyzeCode();
  else if (action === 'walk_through')       await walkThroughCode();
  else if (action === 'analyze_deep') await analyzeDeep();
  else if (action === 'fix')         await fixCode();
  else if (action === 'stop_everything') {
    if (typeof _stepNarrationJob !== 'undefined' && _stepNarrationJob) { _stepNarrationJob.cancelled = true; }
    stopListeningNow();
    SpeechManager.cancelAll();
    SonificationManager.clearAll();
    ErrorBeaconManager.stop();
    // Preserve the last program output/error in the output box — only announce
    srAnnounce('Stopped listening and speech.');
    SonificationManager.playTone(400, 0.08, 0.08);
  }
  else if (action === 'speak')       speakOutput();
  else if (action === 'read_output') speakOutput();
  else if (action === 'describe_line') await describeLine(payload && payload.line ? payload.line : 1);
  else if (action === 'next_step' || action === 'previous_step' || action === 'what_changed') {
    const text = (payload && (payload.speech || payload.message)) || 'No trace event available.';
    out(text, { sr: false }); speak(text);
  }
  else if (action === 'accessibility_setting') {
    applyAccessibilitySettings(payload || {});
    const message = (payload && (payload.message || payload.speech)) || 'Assistive technology settings updated.';
    out(message, { sr: false });
    speak(message);
  }
  else if (action === 'focus_target') focusNamedTarget(payload && payload.target);
  else if (action === 'navigate') {
    const msg = (payload && payload.speech) || (payload && payload.message) || '';
    if (msg) speak(msg);
    if (payload && payload.url) window.location.href = payload.url;
  }
  else if (action === 'generate_code') await generateCode(payload && payload.prompt ? payload.prompt : '', payload || {});
  else if (action === 'exact_symbol_clarification' || action === 'orchestrator_clarification' || action === 'deterministic_message' || action === 'clarify') {
    const message = (payload && (payload.message || payload.speech)) || 'No guidance available.';
    // sr:false on out() is already correct (Screen Reader Safe mode relies
    // on #output's own aria-live for this); sr:false on speak() too - the
    // spoken text usually matches (or nearly matches) what out() just
    // wrote, and without it speak()'s own srAnnounce() fallback announces
    // the same event a second time in that mode. Audible speech when
    // CodeUp Voice is on is unaffected (opts.sr is never read there).
    out((payload && payload.report) ? `${message}\n\n${payload.report}` : message, { sr: false });
    speak((payload && payload.speech) || message, { sr: false });
  }
  else if (action === 'set_speech_rate') {
    applySpeechRate(payload && payload.rate ? payload.rate : 1.0);
    const msg = (payload && payload.speech) || 'Speed changed.';
    speak(msg);  // spoken at the new rate
  }
  else if (action === 'set_verbosity') {
    setVerbosity(payload && payload.verbosity ? payload.verbosity : 'normal');
    const msg = (payload && payload.speech) || 'Detail level changed.';
    speak(msg);
  }
  else if (action === 'export_project') {
    if (payload && payload.speech) speak(payload.speech);
    await exportProject();
  }
  else if (action === 'export_audio_blocks') await exportAudioBlocksProject();
  else if (action === 'audio_blocks_project_report') {
    const report = (payload && payload.report) || 'No Audio Blocks report available.';
    const message = (payload && (payload.speech || payload.message)) || 'Audio Blocks project report ready.';
    out(report, { sr: false });
    speak(message);
  }
  else if (action === 'project_report') {
    // requestProjectReport() owns the speech (concise summary + "say more"
    await requestProjectReport();
  }
  else if (action === 'say_more') sayMore();
  else if (action === 'navigate_code' || action === 'bookmark_read') {
    const message = (payload && (payload.message || payload.speech)) || 'Here is the block.';
    if (payload && payload.line && typeof gotoLine === 'function') gotoLine(payload.line);
    const excerpt = (payload && payload.code_excerpt) ? '\n\n' + payload.code_excerpt : '';
    out(message + excerpt, { sr: false });
    speak(message);
  }
  else if (action === 'bookmark_created' || action === 'bookmark_deleted' || action === 'bookmark_list' || action === 'bookmark_error') {
    const message = (payload && (payload.message || payload.speech)) || 'Done.';
    out(message, { sr: false });
    speak(message);
  }
  else if (action === 'read_project_files') readProjectFiles();
  else if (action === 'open_project_file') await openProjectFile(payload && payload.path);
  else if (action === 'create_project_file') await createProjectFile(payload && payload.path);
  else if (action === 'rename_project_file') await renameProjectFile(payload && payload.old_path, payload && payload.path);
  else if (action === 'delete_project_file') await deleteProjectFile(payload && payload.path);
  else if (action === 'run_project_file') await runProjectFile(payload && payload.path);
  else if (action === 'explain_project_structure') explainProjectStructure();
  else if (action === 'explain_requirements') await explainProjectRequirements();
  else if (action === 'save_snippet_named') await saveSnippetAccessible(payload && payload.name ? payload.name : 'Untitled');
  else if (action === 'save_snippet_auto')  await saveSnippetAccessible();
  else if (action === 'list_snippets')   await listSnippetsAccessible();
  else if (action === 'load_snippet')    await loadSnippetById(payload && payload.id, payload && payload.confirmed);
  else if (action === 'delete_snippet')  await deleteSnippetById(payload && payload.id);
  else if (action === 'rename_snippet')  await renameSnippetById(payload && payload.id, payload && payload.new_name ? payload.new_name : 'Renamed');
  else if (action === 'preview_snippet') await previewSnippetById(payload && payload.snippet_id);
  else if (action === 'goto_line')       gotoLine(payload && payload.line ? payload.line : 1);
  else if (action === 'read_line')       readLine(payload && payload.line ? payload.line : 1);
  else if (action === 'read_current_line') readCurrentLine();
  else if (action === 'next_line')       nextLine();
  else if (action === 'prev_line')       prevLine();
  else if (action === 'clear_editor')    clearEditor();
  else if (action === 'conversational_edit') applyConversationalEdit(payload && payload.ai_action);
  else if (action === 'delete_line')     deleteLine(payload && payload.line ? payload.line : 1);
  else if (action === 'read_function')   readFunction(payload && payload.function_name ? payload.function_name : '');
  else if (action === 'summarize')       await summarizeFile();
  else if (action === 'read_code')       readMyCodeAloud();
  else if (action === 'narrate_file')    await narrateFile();
  else if (action === 'demo_list')       await listDemos();
  else if (action === 'demo_run')        await runDemo(payload && payload.preset ? payload.preset : '');
  else if (action === 'pause_voice')     pauseVoiceRecognition();
  else if (action === 'resume_voice')    resumeVoiceRecognition();
  else if (action === 'advise')          await adviseCode();
  else if (action === 'read_line_enhanced') await readLineEnhanced(payload && payload.line ? payload.line : (editor && editor.getPosition() ? editor.getPosition().lineNumber : 1));
  else if (action === 'sonify_block')    await sonifyCurrentBlock();
  else if (action === 'sonify_file')     await sonifyWholeFile();
  else if (action === 'sonify_function') await sonifyFunction(payload && payload.function_name ? payload.function_name : '');
  else if (action === 'sonify_class')    await sonifyClass(payload && payload.class_name ? payload.class_name : '');
  else if (action === 'find_function')   findFunction(payload && payload.function_name ? payload.function_name : '');
  else if (action === 'find_class')      findClass(payload && payload.class_name ? payload.class_name : '');
  else if (action === 'show_structure')  toggleStructurePanel();
  else if (action === 'read_outline')    await readStructureOutline();
  else if (action === 'list_variables')  await listVariables();
  else if (action === 'find_variable')   await findVariable(payload && payload.variable ? payload.variable : '');
  else if (action === 'check_errors')    await checkSyntaxErrors();
  else if (action === 'locate_error')    locateError();
  else if (action === 'stop_beacon')     { stopErrorBeacon(); speak('Error beacon stopped.'); }
  else if (action === 'go_back')         navigateBack();
  else if (action === 'go_forward')      navigateForward();
  else if (action === 'show_history')    showNavigationHistory();
  else if (action === 'help')            showHelp();
  else if (action === 'more_help')       showFullHelp();
  else if (action === 'file_stats')      getFileStats();
  else if (action === 'go_to_top')       goToTop();
  else if (action === 'go_to_bottom')    goToBottom();
  else if (action === 'copy_code')       copyCode();
  else if (action === 'paste_code')         pasteCode();
  else if (action === 'restart_tutorial')   restartTutorial();
  else if (action === 'toggle_dyslexia')    { document.getElementById('dyslexiaToggle').click(); }
  else if (action === 'toggle_motion')      { document.getElementById('motionToggle').click(); }
  else if (action === 'toggle_night')       { document.getElementById('nightToggle').click(); }
  else if (action === 'cycle_color_mode')   {
    const s = document.getElementById('colorVisionMode');
    s.selectedIndex = (s.selectedIndex + 1) % s.options.length;
    s.dispatchEvent(new Event('change'));
  }
  else if (action === 'set_color_mode') {
    const s = document.getElementById('colorVisionMode');
    if (!s) { speak('Color mode selector not found.'); return; }
    const mode = (payload && payload.mode) || 'default';
    let found = false;
    for (let i = 0; i < s.options.length; i++) {
      if (s.options[i].value === mode) {
        s.selectedIndex = i;
        s.dispatchEvent(new Event('change'));
        found = true;
        break;
      }
    }
    if (!found) {
      speak(`Color mode "${mode}" not recognized. Try protanopia, deuteranopia, tritanopia, high contrast, or default.`);
    }
  }
  else if (action === 'list_variables_voice') await listVariablesWithValues();
  else if (action === 'start_tutorial')     { if (window.TutorialController) window.TutorialController.open(); }
  else if (action === 'skip_tutorial')      {
    if (window.TutorialController && window.TutorialController.active) window.TutorialController.exit(true);
    else speak('The tutorial is not open right now. Say start tutorial to begin.');
  }
  else if (action === 'tutorial_next')      { if (window.TutorialController && window.TutorialController.active) window.TutorialController.next(); }
  else if (action === 'tutorial_practice')  {
    if (window.TutorialController && typeof window.TutorialController.practice === 'function') {
      window.TutorialController.practice(payload && payload.module);
    }
  }
  else if (action === 'insert_function')    insertFunctionVoice(payload && payload.function_name);
  else if (action === 'insert_class')       insertClassVoice(payload && payload.class_name);
  else if (action === 'insert_loop')        insertLoopVoice(payload && payload.loop_var, payload && payload.iterable);
  else if (action === 'insert_if')          insertIfVoice(payload && payload.condition);
  else if (action === 'insert_while')       insertWhileVoice(payload && payload.condition);
  else if (action === 'insert_variable')    insertVariableVoice(payload && payload.name, payload && payload.value);
  else if (action === 'append_line')        appendLineVoice(payload && payload.text);
  else if (action === 'replace_line')       replaceLineVoice(payload && payload.line_number, payload && payload.text);
  else if (action === 'insert_line')        insertLineVoice(payload && payload.line_number, payload && payload.text);
  else if (action === 'add_parameter')      addParameterVoice(payload && payload.param_name, payload && payload.function_name);
  else if (action === 'suggest_next')       await suggestNextLine();
  else if (action === 'choose_suggestion')  chooseSuggestion(payload && payload.choice);
  else if (action === 'story_mode')         await tellExecutionStory();
  else if (action === 'set_audio_breakpoint') await requestAudioBreakpoint('add', payload && payload.condition);
  else if (action === 'list_audio_breakpoints') {
    if (payload && payload.breakpoint_scope === 'line') listBreakpoints();
    else await requestAudioBreakpoint('list');
  }
  else if (action === 'export_teacher_report') {
    const report = (payload && payload.report) || '';
    const blob = new Blob([report], { type: 'text/markdown;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = (payload && payload.filename) || 'CodeUp_Teacher_Report.md';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    const message = (payload && payload.speech) || 'Teacher report exported locally.';
    out(message, { sr: false }); speak(message);
  }
  else if (action === 'accessible_data_sonify') {
    const values = Array.isArray(payload && payload.values) ? payload.values.map(Number).filter(Number.isFinite) : [];
    SonificationManager.clearAll();
    if (!values.length) { out('No numeric values to sonify.', { sr: false }); speak('No numeric values to sonify.'); }
    else {
      const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
      const message = (payload && payload.speech) || `Sonifying ${values.length} values.`;
      out(message, { sr: false }); speak(message);
      values.forEach((value, index) => setTimeout(() => {
        SonificationManager.playTone(220 + ((value - low) / span) * 660, 0.12, 0.08);
      }, index * 170));
    }
  }
  else if (action === 'stop_data_sonification') {
    SonificationManager.clearAll();
    const message = (payload && payload.speech) || 'Sonification stopped.';
    out(message, { sr: false }); speak(message);
  }
  else if (action === 'open_accessible_tools') {
    const message = (payload && payload.speech) || 'Opening accessible coding tools.';
    speak(message);
    window.location.assign('/accessible-coding-tools');
  }
  else if (action === 'why_audio_breakpoint') await requestAudioBreakpoint('why');
  else if (action === 'set_breakpoint')     setBreakpoint(payload && payload.line_number);
  else if (action === 'clear_breakpoints')  {
    clearBreakpoints();
    await requestAudioBreakpoint('clear', null, { silent: true });
    out('Cleared all breakpoints.', { sr: false });
    speak('Cleared all breakpoints.');
  }
  else if (action === 'remove_breakpoint')  removeBreakpoint(payload && payload.line_number);
  else if (action === 'disable_breakpoints') disableBreakpoints();
  else if (action === 'enable_breakpoints') enableBreakpoints();
  else if (action === 'watch_variable')     await requestWatchVariable(payload && payload.variable, 'add');
  else if (action === 'debug_continue')     await continueDebugging();
  else if (action === 'debug_step_in')      speak('Step in is not yet supported in sandbox mode.');
  else if (action === 'debug_step_out')     speak('Step out is not yet supported in sandbox mode.');
  else if (action === 'mentor_mode')        startMentorMode();
  else if (action === 'quiz_me')            await quizMe(payload && payload.topic);
  else if (action === 'explain_concept')    await explainConcept(payload && payload.concept);
  else if (action === 'bug_challenge')      await bugChallenge();
  else if (action === 'set_inputs')         setPreflightInputs(payload && payload.values, payload && (payload.speech || payload.message));
  else if (action === 'clear_inputs')       clearPreflightInputs();
  else if (action === 'list_inputs')        listPreflightInputs();
  else if (action === 'live_input_mode')    enableLiveInputMode();
  else if (action === 'preflight_input_mode') enablePreflightInputMode();
  else if (action === 'save_macro')         await saveMacro(payload && payload.name);
  else if (action === 'use_macro')          await useMacro(payload && payload.name);
  else if (action === 'list_macros')        await listMacrosVoice();
  else if (action === 'share_macro')        await shareCurrentMacro(payload && payload.name);
  else if (action === 'use_shared_macro')   await useSharedMacro(payload && payload.share_code);
  else if (action === 'bookmark_output')    await bookmarkOutput(payload && payload.label);
  else if (action === 'read_bookmark')      await readBookmark(payload && payload.label);
  else if (action === 'list_bookmarks')     await listBookmarks();
  else if (action === 'where_am_i')         await reportPosition();
  else if (action === 'explain_simply')     await explainErrorSimply();
  else if (action === 'narrate_diff')       narrateOutputDiff();
  else if (action === 'explain_diff')       await explainOutputDiff();
  else if (action === 'code_map')           await requestCodeMap(payload && payload.query);
  else if (action === 'watch_var')          await requestWatchVariable(payload && payload.variable, 'add');
  else if (action === 'stop_watching')      await requestWatchVariable(payload && payload.variable, 'remove');
  else if (action === 'clear_watched')      await requestWatchVariable('', 'clear');
  else if (action === 'step_narration')     await requestStepNarration();
  else if (action === 'read_var_values')    await requestStepNarration();
  else if (action === 'what_changed_step') {
    const text = (payload && (payload.speech || payload.message)) || 'No trace event available.';
    out(text, { sr: false }); speak(text);
  }
  else if (action === 'only_announce_changes') { speak('I will only announce variable changes during step narration.'); }
  else if (action === 'compare_before_after') await requestMistakeReplay('compare');
  else if (action === 'replay_mistake')       await requestMistakeReplay('replay');
  else if (action === 'why_fixed_works')      await requestMistakeReplay('why');
  else if (action === 'show_changed_lines')   await requestMistakeReplay('changed lines');

  if (_TUTORIAL_EDIT_ACTIONS.has(action) &&
      window.TutorialController && window.TutorialController.active &&
      typeof window.TutorialController.onInsert === 'function') {
    try { window.TutorialController.onInsert(action); } catch (e) { console.error('Tutorial onInsert error:', e); }
  }

  // Generic, action-independent: a typed/spoken join just succeeded -
  // refresh the classroom panel from the Join form straight to the joined
  // dashboard, the same way the panel's own Join button already does.
  if (payload && payload.classroom_refresh && typeof window._classroomRefreshDashboard === 'function') {
    window._classroomRefreshDashboard();
  }

  // Generic, action-independent: a conversational join response ("join
  // ABC123", no name yet) reflects the code it captured into the visible
  // Classroom code field, so the visual form and the spoken conversation
  // never disagree about which code is in play.
  if (payload && payload.join_code_hint) {
    const codeField = document.getElementById('classroomJoinCode');
    if (codeField) codeField.value = payload.join_code_hint;
  }

  // Generic, action-independent: some classroom responses (e.g. "join
  // ABC123" while WAITING_FOR_NAME) carry a focus_hint alongside their
  // normal action/message - move real keyboard focus there too, silently
  // (no extra announcement; the response's own speech already covers it).
  if (payload && payload.focus_hint) {
    if (typeof window._classroomReveal === 'function') window._classroomReveal(payload.focus_hint);
    const target = document.getElementById(payload.focus_hint);
    if (target) { try { target.focus(); } catch (e) {} }
  }

  // Generic, action-independent: a typed/spoken "submit" just succeeded -
  // reveal the same "Back to CodeUp" control the Submit button's own click
  // handler shows, without navigating (that would cut off the confirmation
  // speech already spoken above) and without re-rendering the panel.
  if (payload && payload.assignment_submitted) {
    const backLink = document.getElementById('classroomBackToIdeLink');
    if (backLink) backLink.hidden = false;
  }
}

const _TUTORIAL_EDIT_ACTIONS = new Set([
  'insert_variable', 'insert_while', 'insert_if', 'insert_loop',
  'insert_function', 'insert_class', 'append_line', 'insert_line', 'replace_line',
]);

function inputRequestMessage(req, fallback) {
  if (!req) return fallback || 'This program needs input.';
  const type = req.expectedType || 'text';
  const prompt = req.prompt || 'This program needs input.';
  // Product-polish pass: "Input 1. <prompt>" numbered every request even
  // when there was only ever going to be one, which reads like a stray
  // internal counter to a first-time learner. Only surface the count when
  // there is actually more than one input to track.
  if (req.inputCount && req.inputCount > 1) {
    return `Input ${req.inputIndex || 1} of ${req.inputCount}. ${prompt} Expected ${type}.`;
  }
  return `${prompt} Expected ${type}.`;
}
function showProgramInputControl(req) {
  const section = document.getElementById('programInputSection');
  const status = document.getElementById('programInputStatus');
  const label = document.getElementById('programInputLabel');
  const input = document.getElementById('programInputValue');
  const submit = document.getElementById('programInputSubmitBtn');
  const cancel = document.getElementById('programInputCancelBtn');
  const message = req.message || inputRequestMessage(req);
  const accessiblePrompt = inputRequestMessage(req);
  const accessibleName = message.includes(req.prompt || '') ? message : `${accessiblePrompt} ${message}`;
  if (status) status.textContent = message;
  if (label) label.textContent = `Program input answer for ${req.prompt || 'input request'}`;
  if (input) {
    input.disabled = false;
    input.value = '';
    input.placeholder = req.prompt || 'Type the program input and press Enter';
    input.setAttribute('aria-label', accessibleName + ' Type your answer.');
  }
  if (submit) submit.disabled = false;
  if (cancel) cancel.disabled = false;
  if (section) section.classList.add('cu-program-input--active');
  const focusProgramInput = () => {
    try {
      if (input && !input.disabled) input.focus();
    } catch (e) {}
  };
  focusProgramInput();
  requestAnimationFrame(focusProgramInput);
  setTimeout(focusProgramInput, 80);
}
function hideProgramInputControl(message) {
  const section = document.getElementById('programInputSection');
  const status = document.getElementById('programInputStatus');
  const input = document.getElementById('programInputValue');
  const submit = document.getElementById('programInputSubmitBtn');
  const cancel = document.getElementById('programInputCancelBtn');
  if (status) status.textContent = message || 'No program input is being requested.';
  if (input) { input.disabled = true; input.value = ''; input.placeholder = 'Program input will appear here when needed...'; }
  if (submit) submit.disabled = true;
  if (cancel) cancel.disabled = true;
  if (section) section.classList.remove('cu-program-input--active');
}
function handleProgramInputRequest(payload) {
  const prompt = String((payload && payload.prompt) || 'This program needs input.').trim();
  const inputIndex = Number((payload && payload.input_index) || 1);
  const inputCount = Number((payload && payload.input_count) || inputIndex || 1);
  _programInputRequest = {
    prompt,
    inputIndex,
    inputCount,
    expectedType: (payload && payload.expected_type) || 'text',
    values: Array.isArray(payload && payload.values) ? payload.values.slice() : [],
    code: getCode(),
  };
  const message = (payload && (payload.speech || payload.message)) || inputRequestMessage(_programInputRequest);
  showProgramInputControl(Object.assign({}, _programInputRequest, { message }));
  out(`${inputRequestMessage(_programInputRequest)}\nType or say the value now.`, { sr: false });
  speak(message, { sr: false });
}
async function submitProgramInputValue() {
  const input = document.getElementById('programInputValue');
  const value = input ? input.value.trim() : '';
  if (!value) { srAnnounce('Type an answer before submitting program input.'); return; }
  if (_activeStreamRun && _activeStreamRun.runId && _activeStreamRun.awaitingPrompt) {
    await sendStreamingInput(value);
    hideProgramInputControl('Program input sent. Waiting for the program.');
    return;
  }
  if (!_programInputRequest) { srAnnounce('No program input is being requested.'); return; }
  const values = (_programInputRequest.values || []).concat([value]);
  _preflightInputs = values.slice();
  _preflightInputPlaceholders = [];
  updateInputsPanel();
  await runCode(undefined, _programInputRequest.code || getCode());
}
function cancelProgramInputRequest() {
  _programInputRequest = null;
  _preflightInputs = [];
  _preflightInputPlaceholders = [];
  updateInputsPanel();
  hideProgramInputControl('Program input cancelled.');
  out('Program input cancelled.', { sr: false });
  speak('Program input cancelled.', { sr: false });
  focusEditor();
}

function setPreflightInputs(values, speechMessage) {
  if (!Array.isArray(values) || values.length === 0) {
    speak('No values heard. Try saying "set inputs to" followed by your values separated by "and" or commas.');
    return;
  }
  _preflightInputs = values.map(v => String(v));
  _preflightInputPlaceholders = [];
  updateInputsPanel();
  const summary = _preflightInputs.join(', ');
  speak(speechMessage || `${_preflightInputs.length} input${_preflightInputs.length === 1 ? '' : 's'} ready: ${summary}.`);
  out(`PRE-FLIGHT INPUTS SET (${_preflightInputs.length}):\n${_preflightInputs.map((v, i) => `  ${i + 1}. ${v}`).join('\n')}\n\nThese will be used in order when your code calls input().`, { sr: false });
}

function clearPreflightInputs() {
  _preflightInputs = [];
  _preflightInputPlaceholders = [];
  updateInputsPanel();
  speak('Pre-flight inputs cleared.');
  out('Pre-flight inputs cleared.', { sr: false });
}

function listPreflightInputs() {
  if (_preflightInputs.length === 0) {
    speak('You have no pre-flight inputs declared.');
    out('No pre-flight inputs declared.', { sr: false });
    return;
  }
  speak(`You have ${_preflightInputs.length} input${_preflightInputs.length === 1 ? '' : 's'}:`);
  _preflightInputs.forEach((v, i) => speak(`Input ${i + 1}: ${v}.`));
  out(`PRE-FLIGHT INPUTS (${_preflightInputs.length}):\n${_preflightInputs.map((v, i) => `  ${i + 1}. ${v}`).join('\n')}`, { sr: false });
}

function enableLiveInputMode() {
  _liveInputMode = true;
  updateInputModeUI();
  speak('Switched to live input mode. When you press run, your code will pause and ask for each input as it needs it. Note: live mode requires Linux or macOS on the server.');
  out('LIVE INPUT MODE ON\n\nYour code will pause at each input() call and wait for you to answer by voice or by typing in the command box.', { sr: false });
}

function enablePreflightInputMode() {
  _liveInputMode = false;
  updateInputModeUI();
  speak('Switched to pre-flight input mode. Declare your inputs ahead of time, then press run.');
  out('PRE-FLIGHT INPUT MODE ON (default)\n\nDeclare inputs first: say "set inputs to value1 and value2", then press run.', { sr: false });
}

function updateInputModeUI() {
  const indicator = document.getElementById('inputModeIndicator');
  if (indicator) {
    indicator.textContent = _liveInputMode ? 'LIVE' : 'PRE-FLIGHT';
    indicator.title = _liveInputMode
      ? 'Live input mode: your code pauses and asks for inputs in real time.'
      : 'Pre-flight input mode: inputs are declared ahead of time.';
  }
}

function updateInputsPanel() {
  const panel = document.getElementById('inputsPanelList');
  if (!panel) return;
  if (_preflightInputs.length === 0) {
    if (_preflightInputPlaceholders.length > 0) {
      panel.innerHTML = _preflightInputPlaceholders.map((v, i) =>
        `<div class="snippet-item" style="font-size:0.8rem;padding:6px 10px;color:var(--text-dim);"><span>${i + 1}. ${escapeHtml(v)}: empty</span></div>`
      ).join('');
      return;
    }
    panel.innerHTML = '<div style="color:var(--text-dim);font-style:italic;padding:6px 0;font-size:0.8rem;">No inputs declared</div>';
    return;
  }
  panel.innerHTML = _preflightInputs.map((v, i) =>
    `<div class="snippet-item" style="font-size:0.8rem;padding:6px 10px;"><span>${i + 1}. ${escapeHtml(v)}</span></div>`
  ).join('');
}

function detectInputPromptsFromCode(code) {
  const prompts = [];
  const re = /\binput\s*\(\s*(?:"([^"]*)"|'([^']*)')?\s*\)/g;
  let match;
  while ((match = re.exec(code)) !== null && prompts.length < 50) {
    prompts.push((match[1] || match[2] || `Input ${prompts.length + 1}`).trim() || `Input ${prompts.length + 1}`);
  }
  return prompts;
}

function suggestPreflightInputsFromCode(code) {
  if (_preflightInputs.length > 0) return;
  _preflightInputPlaceholders = detectInputPromptsFromCode(code);
  updateInputsPanel();
  if (_preflightInputPlaceholders.length > 0) {
    out(`INPUTS DETECTED (${_preflightInputPlaceholders.length}):\n${_preflightInputPlaceholders.map((v, i) => `  ${i + 1}. ${v}: empty`).join('\n')}\n\nFill the inputs panel or say "set inputs to" followed by values before running.`);
    srAnnounce(`${_preflightInputPlaceholders.length} input placeholders detected`);
  }
}

async function saveMacro(name) {
  if (!name) { speak('Please give the macro a name. Say "remember this as" followed by a name.'); return; }
  const code = getCode();
  if (!code.trim()) { speak('The editor is empty. Nothing to save as a macro.'); return; }
  if (!ensurePythonEditorContent('save macro')) return;
  try {
    const res = await fetch('/macros', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, code }),
    });
    const data = await res.json();
    if (data.success) {
      speak(data.speech || `Macro ${name} saved.`);
    } else {
      speak(data.error || 'Could not save macro.');
    }
  } catch (e) {
    speak('Macro save failed.');
  }
}

async function useMacro(name) {
  if (!name) { speak('Which macro? Say "use macro" followed by the name.'); return; }
  try {
    const res = await fetch(`/macros/get/${encodeURIComponent(name)}`);
    const data = await res.json();
    if (data.success) {
      if (!setCode(data.code, { source: `macro ${name}` })) return;
      speak(`Loaded macro ${name}.`);
      out(`Macro "${name}" loaded into the editor.`, { sr: false });
    } else {
      speak(`No macro named ${name}. Say "list macros" to hear what is saved.`);
    }
  } catch (e) {
    speak('Macro load failed.');
  }
}

async function listMacrosVoice() {
  try {
    const res = await fetch('/macros');
    const data = await res.json();
    if (!data.success) { speak('Could not list macros.'); return; }
    speak(data.speech);
    if (data.names && data.names.length) {
      out(`MACROS (${data.names.length}):\n${data.names.map((n, i) => `  ${i + 1}. ${n}`).join('\n')}\n\nSay "use macro" followed by a name.`, { sr: false });
    } else {
      out('No macros saved. Save one by writing code, then saying "remember this as" followed by a name.', { sr: false });
    }
  } catch (e) {
    speak('Macro list failed.');
  }
}

async function shareCurrentMacro(name) {
  const code = getCode();
  if (!code.trim()) { speak('The editor is empty. Nothing to share.'); return; }
  if (!ensurePythonEditorContent('share macro')) return;
  try {
    const res = await fetch('/macros/share', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name || 'shared macro', code }),
    });
    const data = await res.json();
    if (data.success) {
      speak(`Shared macro code ${data.share_code}.`);
      out(`SHARED MACRO\n\nCode: ${data.share_code}\n\nStudents can say: use shared macro ${data.share_code}`, { sr: false });
    } else {
      speak(data.error || 'Could not share macro.');
    }
  } catch (e) {
    speak('Macro sharing failed.');
  }
}

async function useSharedMacro(shareCode) {
  if (!shareCode) { speak('Say use shared macro followed by the four character code.'); return; }
  try {
    const res = await fetch(`/macros/shared/${encodeURIComponent(shareCode)}`);
    const data = await res.json();
    if (data.success) {
      if (!setCode(data.code, { source: `shared macro ${shareCode}` })) return;
      speak(`Loaded shared macro ${data.share_code}.`);
      out(`Shared macro "${data.name}" loaded into the editor.`, { sr: false });
    } else {
      speak(data.error || 'Shared macro not found.');
    }
  } catch (e) {
    speak('Shared macro load failed.');
  }
}

async function bookmarkOutput(label) {
  const outputEl = document.getElementById('output');
  const position = outputEl ? (outputEl.textContent || '').length : 0;
  try {
    const res = await fetch('/bookmarks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: label || '', position }),
    });
    const data = await res.json();
    if (data.success) {
      SonificationManager.playTone(750, 0.08, 0.08);
      speak(data.speech);
    }
  } catch (e) {
    speak('Could not save bookmark.');
  }
}

async function readBookmark(label) {
  if (!label) {
    const res = await fetch('/bookmarks');
    const data = await res.json();
    if (!data.success || !data.bookmarks.length) {
      speak('No bookmarks saved.');
      return;
    }
    label = data.bookmarks[data.bookmarks.length - 1].label;
  }
  const outputEl = document.getElementById('output');
  const fullOutput = outputEl ? outputEl.textContent : '';
  try {
    const res = await fetch('/bookmarks/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, output: fullOutput }),
    });
    const data = await res.json();
    if (data.success) {
      speak(`Reading from bookmark ${data.label}.`);
      speak(data.slice || 'Empty.');
    } else {
      speak(data.error || 'Bookmark not found.');
    }
  } catch (e) {
    speak('Could not read from bookmark.');
  }
}

async function listBookmarks() {
  try {
    const res = await fetch('/bookmarks');
    const data = await res.json();
    if (!data.success) { speak('Could not list bookmarks.'); return; }
    if (!data.bookmarks.length) {
      speak('You have no bookmarks.');
      out('No bookmarks. Say "bookmark this" while output is showing to save one.', { sr: false });
      return;
    }
    speak(`You have ${data.bookmarks.length} bookmark${data.bookmarks.length === 1 ? '' : 's'}:`);
    data.bookmarks.forEach(b => speak(b.label));
    out(`BOOKMARKS (${data.bookmarks.length}):\n${data.bookmarks.map((b, i) => `  ${i + 1}. ${b.label}`).join('\n')}`, { sr: false });
  } catch (e) {
    speak('Bookmark list failed.');
  }
}

async function reportPosition() {
  if (_activeStreamRun && _activeStreamRun.runId) {
    try {
      const res = await fetch(`/run-stream/${encodeURIComponent(_activeStreamRun.runId)}/position`);
      const data = await res.json();
      if (data.success && data.alive) {
        const sec = (data.elapsed_ms / 1000).toFixed(1);
        if (data.awaiting_input) {
          speak(`Your code has been running for ${sec} seconds and is waiting for your input.`);
        } else {
          speak(`Your code has been running for ${sec} seconds.`);
        }
        return;
      }
    } catch (e) {}
  }
  await readBreadcrumb();
}

async function readBreadcrumb() {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  if (!ensurePythonEditorContent('read position')) return;
  const pos = editor.getPosition() || { lineNumber: 1 };
  try {
    const res = await fetch('/breadcrumbs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: getCode(), line: pos.lineNumber }),
    });
    const data = await res.json();
    if (data.success) {
      const ctx = data.context ? ' ' + data.context : '';
      speak(`You are in: ${data.breadcrumb}.${ctx}`);
      out(`Position: ${data.breadcrumb}${ctx}`, { sr: false });
    } else {
      speak(data.message || 'Could not determine position.');
    }
  } catch (e) {
    speak('Position lookup failed.');
  }
}

async function explainErrorSimply() {
  if (!_lastErrorContext) {
    speak('There is no recent error to explain. Run your code first.');
    return;
  }
  if (looksLikeNonPythonCode(_lastErrorContext.code || '')) {
    rejectNonPythonCode('error explanation');
    _lastErrorContext = null;
    return;
  }
  showAI('Explaining in simpler terms...');
  speak('Let me explain that more simply.');
  try {
    const res = await fetch('/explain-error-beginner', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_lastErrorContext),
    });
    const data = await res.json();
    if (data.success) {
      out(data.explanation, { sr: false });
      speak(data.explanation);
    } else {
      speak('Could not generate a simpler explanation.');
    }
  } catch (e) {
    speak('Explanation failed.');
  } finally {
    hideAI();
  }
}

function narrateOutputDiff() {
  if (!_lastOutputDiff) {
    speak('No diff available. Run your code at least twice to compare outputs.');
    return;
  }
  if (_lastOutputDiff.identical) {
    speak('The output is identical to the previous run.');
    return;
  }
  if (!_lastOutputDiff.changed_lines || _lastOutputDiff.changed_lines.length === 0) {
    speak(_lastOutputDiff.summary || 'First run — nothing to compare yet.');
    return;
  }
  speak(_lastOutputDiff.summary);
  _lastOutputDiff.changed_lines.forEach(c => {
    if (c.kind === 'added') speak(`Line ${c.line_no}, added: ${c.after}.`);
    else if (c.kind === 'removed') speak(`Line ${c.line_no}, removed: ${c.before}.`);
    else speak(`Line ${c.line_no}, was "${c.before}", now "${c.after}".`);
  });
}

async function explainOutputDiff() {
  if (!_previousOutput && !_lastOutput) {
    speak('No outputs are available yet. Run your code twice, then ask why the output is different.');
    return;
  }
  if (!ensurePythonEditorContent('explain output difference')) return;
  try {
    showAI('Explaining output difference...');
    const res = await fetch('/explain-diff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: getCode(),
        previous_output: _previousOutput,
        current_output: _lastOutput,
        language: getLanguage(),
      }),
    });
    const data = await res.json();
    const msg = data.success ? data.explanation : (data.error || 'Could not explain the output difference.');
    out(msg, { sr: false });
    speak(msg);
  } catch (e) {
    speak('Could not explain the output difference.');
  } finally {
    hideAI();
  }
}

function getMentorContext() {
  return {
    code: getCode(),
    output: window.lastRunOutput || _lastOutput || '',
    error: window.lastRunError || (_lastErrorContext && _lastErrorContext.error) || '',
    language: getLanguage(),
    history: (window.mentorHistory || []).slice(-6),
    preferences: window.mentorPreferences || {},
  };
}

function rememberMentorTurn(studentText, mentorText) {
  window.mentorHistory = window.mentorHistory || [];
  if (studentText) window.mentorHistory.push({ role: 'student', text: String(studentText) });
  if (mentorText) window.mentorHistory.push({ role: 'mentor', text: String(mentorText) });
  if (window.mentorHistory.length > 12) {
    window.mentorHistory = window.mentorHistory.slice(-12);
  }
  window.lastMentorReply = mentorText || window.lastMentorReply || '';
  renderMentorTranscript();
}

function renderMentorTranscript() {
  const log = document.getElementById('mentorTranscript');
  if (!log) return;
  log.textContent = '';
  const turns = (window.mentorHistory || []).slice(-12);
  if (!turns.length) {
    const empty = document.createElement('p');
    empty.className = 'mentor-transcript-empty';
    empty.textContent = 'Mentor transcript will appear here.';
    log.appendChild(empty);
    return;
  }
  turns.forEach(turn => {
    const row = document.createElement('div');
    row.className = 'mentor-transcript-turn';
    const speaker = document.createElement('strong');
    speaker.textContent = turn.role === 'mentor' ? 'Mentor: ' : 'Student: ';
    const text = document.createElement('span');
    text.textContent = turn.text || '';
    row.append(speaker, text);
    log.appendChild(row);
  });
}

function showMentorReply(studentText, reply) {
  const text = reply || 'The mentor did not return a reply.';
  out('MENTOR\n\n' + text, { sr: false });
  showAI('Mentor answered.');
  speak(text);
  rememberMentorTurn(studentText, text);
  setTimeout(() => hideAI(), 1200);
}

async function talkToMentor(message, mode = 'general') {
  if (mode === 'repeat') {
    if (window.lastMentorReply) {
      showMentorReply('repeat that', window.lastMentorReply);
    } else {
      speak('There is no mentor reply to repeat yet.');
    }
    return;
  }
  if ((mode === 'shorter' || mode === 'simpler') && !window.lastMentorReply) {
    speak('There is no mentor reply to revise yet.');
    return;
  }
  let msg = message || 'Help me with my code.';
  if (mode === 'shorter') msg = 'Say your previous mentor reply shorter.';
  if (mode === 'simpler') msg = 'Say your previous mentor reply simpler.';
  if (!ensurePythonEditorContent('ask mentor')) return;
  showAI('Asking CodeUp Mentor...');
  try {
    const context = getMentorContext();
    const result = await guardedJson('mentor', '/mentor/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: context.code,
        message: msg,
        output: context.output,
        error: context.error,
        language: context.language,
        mode,
        history: context.history,
        preferences: context.preferences,
      }),
    }, narrationContext(context.code));
    if (staleResult(result)) return;
    const data = result.value;
    const reply = data.reply || data.error || 'Mentor is not available right now.';
    showMentorReply(msg, reply);
    maybePromptForApiKey(reply);
  } catch (e) {
    console.error(e);
    speak('Mentor failed. Please try again.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

async function checkProgressWithMentor() {
  if (!ensurePythonEditorContent('check progress')) return;
  showAI('Checking progress...');
  try {
    const codeSnapshot = getCode();
    const result = await guardedJson('mentor', '/mentor/check-progress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        previousCode: window.previousCodeSnapshot || '',
        currentCode: codeSnapshot,
        previousError: window.previousErrorSnapshot || '',
        currentOutput: window.lastRunOutput || '',
        currentError: window.lastRunError || '',
        language: getLanguage(),
        history: (window.mentorHistory || []).slice(-6),
        preferences: window.mentorPreferences || {},
      }),
    }, narrationContext(codeSnapshot));
    if (staleResult(result)) return;
    const data = result.value;
    const reply = data.reply || data.error || 'Could not check progress.';
    showMentorReply('Did I fix it?', reply);
    maybePromptForApiKey(reply);
  } catch (e) {
    console.error(e);
    speak('Progress check failed.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

function setMentorPreference(key, value) {
  window.mentorPreferences = window.mentorPreferences || {};
  if (!key) return;
  window.mentorPreferences[key] = value;
  const label = key === 'level'
    ? `Mentor level set to ${value}.`
    : key === 'languageStyle'
      ? `Mentor language style set to ${value}.`
      : 'Mentor will keep hints first and avoid direct answers.';
  speak(label);
  out(label, { sr: false });
}

async function speakCodeMap() {
  if (!ensurePythonEditorContent('code map')) return;
  showAI('Mapping your code...');
  try {
    const codeSnapshot = getCode();
    const result = await guardedJson('code-map', '/mentor/code-map', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: codeSnapshot, language: getLanguage() }),
    }, narrationContext(codeSnapshot));
    if (staleResult(result)) return;
    const data = result.value;
    const reply = data.reply || data.error || 'Could not map the code.';
    showMentorReply('Give me a map of my code.', reply);
  } catch (e) {
    console.error(e);
    speak('Code map failed.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

async function requestCodeMap(query) {
  if (!ensurePythonEditorContent('code map')) return;
  showAI('Mapping your code...');
  const _codeMapEpoch = currentSpeechEpoch();
  try {
    const codeSnapshot = getCode();
    const result = await guardedJson('code-map', '/audio-code-map', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: codeSnapshot, query: query || '', language: getLanguage() }),
    }, narrationContext(codeSnapshot));
    if (staleResult(result)) return;
    const data = result.value;
    const reply = data.reply || data.speech || data.error || 'Could not map the code.';
    out(reply, { sr: false });
    speak(reply, { epoch: _codeMapEpoch });
  } catch (e) {
    console.error(e);
    speak('Code map failed.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

async function requestWatchVariable(variable, action) {
  try {
    const res = await fetch('/watch-variable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variable: variable || '', action: action || 'add' }),
    });
    const data = await res.json();
    const msg = data.speech || data.error || 'Done.';
    out(msg, { sr: false });
    speak(msg);
  } catch (e) {
    console.error(e);
    speak('Could not update watch list.');
  }
}

async function requestAudioBreakpoint(action, condition, options) {
  const opts = options || {};
  try {
    const body = { action: action || 'add' };
    if (condition) body.condition = condition;
    const res = await fetch('/audio-breakpoints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    const msg = data.speech || data.error || 'Done.';
    const inactive = data.active === false && data.success === false;
    if (!opts.silent && !(opts.silentInactive && inactive)) {
      out(msg, { sr: false });
      speak(msg);
    }
    return data;
  } catch (e) {
    console.error(e);
    if (!opts.silentErrors) speak('Could not update conditional breakpoint.');
    return null;
  }
}

let _stepNarrationJob = null;

function _playDepthCue(depth) {
  if (depth < 0) return;
  const freq = 260 + Math.min(depth, 5) * 150;
  SonificationManager.playTone(freq, 0.1, 0.08);
}

async function requestStepNarration() {
  if (!ensurePythonEditorContent('step narration')) return;
  if (_stepNarrationJob) {
    _stepNarrationJob.cancelled = true;
    SpeechManager.cancelAll();
    SonificationManager.clearAll();
  }
  const job = { cancelled: false };
  _stepNarrationJob = job;
  const _narrEpoch = currentSpeechEpoch();

  showAI('Running with step narration...');
  try {
    const res = await fetch('/step-narration', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: getCode(), language: getLanguage() }),
    });
    if (job.cancelled) return;
    const data = await res.json();
    if (job.cancelled) return;

    if (data.output) { out(data.output); }

    const depths = data.indent_depths || [];
    const steps = data.narration || [];

    if (steps.length > 0 && depths.length === steps.length) {
      const fullText = data.narration_text || data.speech || steps.join(' ');
      out(fullText);

      for (let i = 0; i < steps.length; i++) {
        if (job.cancelled || _narrEpoch !== currentSpeechEpoch()) return;
        const depth = depths[i];
        if (depth >= 0) {
          _playDepthCue(depth);
          await sleep(150);
        }
        if (job.cancelled) return;
        await SpeechManager.enqueue(steps[i]);
        if (job.cancelled) return;
        let drainAttempts = 0;
        while (window.speechSynthesis && window.speechSynthesis.speaking && !job.cancelled && drainAttempts < 80) {
          await sleep(100);
          drainAttempts++;
        }
        if (job.cancelled) return;
        await sleep(200);
      }
    } else {
      const narration = data.paused
        ? (data.speech || data.narration_text || steps.join(' '))
        : (data.narration_text || data.speech || steps.join(' ') || data.error || 'No narration available.');
      out(narration, { sr: false });
      speak(narration);
    }

    if (!job.cancelled) srAnnounce('Step narration complete');
  } catch (e) {
    if (!job.cancelled) { console.error(e); speak('Step narration failed.'); }
  } finally {
    if (_stepNarrationJob === job) _stepNarrationJob = null;
    setTimeout(() => hideAI(), 1200);
  }
}

// ---------- MISTAKE REPLAY ----------
async function requestMistakeReplay(query) {
  showAI('Comparing versions...');
  try {
    const res = await fetch('/mistake-replay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: getCode(), query: query || 'compare', language: getLanguage() }),
    });
    const data = await res.json();
    const reply = data.reply || data.speech || data.error || 'No comparison available.';
    out(reply, { sr: false });
    speak(reply);
  } catch (e) {
    console.error(e);
    speak('Mistake replay failed.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

function maybeOfferSlowWalkthroughAfterErrors() {
  if ((window.consecutiveErrors || 0) < 3) return;
  if (window._mentorSlowWalkthroughOffered) return;
  window._mentorSlowWalkthroughOffered = true;
  const msg = 'You have hit a few errors in a row. Want a slow walkthrough? Say: slow walkthrough.';
  speak(msg);
}

function tryResolveConfirmation(txt) {
  if (!pendingConfirm) return false;
  if (pendingConfirm.expiresAt && Date.now() > pendingConfirm.expiresAt) {
    pendingConfirm = null;
    return false;
  }
  const options = pendingConfirm.options || [];
  const context = pendingConfirm.context || {};
  const lower   = txt.toLowerCase().trim();

  const cancelWords = ['cancel', 'no', 'neither', 'nope', 'none', 'forget it',
                       'never mind', 'nevermind', 'skip',
                       'नहीं', 'रद्द करो', 'छोड़ो'];
  if (cancelWords.some(w => lower === w)) {
    pendingConfirm = null;
    speak('Cancelled.');
    return true;
  }

  const yesWords = ['yes', 'yeah', 'yep', 'confirm', 'continue', 'go ahead', 'ok', 'okay'];
  if (yesWords.some(w => lower === w) && options.length === 1) {
    const chosen = options[0];
    pendingConfirm = null;
    speak('Got it. ' + chosen.replace(/_/g, ' ') + '.');
    handleConfirmedAction(chosen, context);
    return true;
  }

  for (const opt of options) {
    if (lower === opt || lower === opt.replace(/_/g, ' ')) {
      pendingConfirm = null;
      speak('Got it. ' + opt.replace(/_/g, ' ') + '.');
      handleConfirmedAction(opt, context);
      return true;
    }
  }

  const positionalMap = {
    'first': 0, '1': 0, 'one': 0, 'option one': 0, 'option 1': 0,
    'second': 1, '2': 1, 'two': 1, 'option two': 1, 'option 2': 1,
    'पहला': 0, 'दूसरा': 1, 'एक': 0, 'दो': 1,
  };
  if (positionalMap.hasOwnProperty(lower) && positionalMap[lower] < options.length) {
    const chosen = options[positionalMap[lower]];
    pendingConfirm = null;
    speak('Got it. ' + chosen.replace(/_/g, ' ') + '.');
    handleConfirmedAction(chosen, context);
    return true;
  }

  pendingConfirm = null;
  speak('Okay, listening for a new command.');
  return false;
}

async function handleCommandText(txt) {
  const field = document.getElementById('voiceText');
  if (field) field.value = txt;
  updateCommandUnderstanding({ heard: txt, understood: '', nextAction: 'Interpreting command.' });

  if (window.TutorialController && window.TutorialController.active &&
      typeof window.TutorialController.handleUtterance === 'function') {
    try {
      if (window.TutorialController.handleUtterance(txt)) return;
    } catch (e) { console.error('Tutorial utterance error:', e); }
  }

  const _ts = tabState();
  if (_ts._pendingQuizAnswer && document.hidden) return false;
  if (_ts._pendingQuizAnswer) {
    if (_ts._pendingQuizAnswer.expiresAt && Date.now() > _ts._pendingQuizAnswer.expiresAt) {
      _ts._pendingQuizAnswer = null;
    }
  }
  if (_ts._pendingQuizAnswer && document.hidden) return false;
  if (_ts._pendingQuizAnswer) {
    const t = txt.toLowerCase().trim();
    const match = t.match(/(?:answer|option|choose)\s+([abcd])|^([abcd])$/);
    if (match) {
      const q = _ts._pendingQuizAnswer;
      _ts._pendingQuizAnswer = null;
      const chosen = (match[1] || match[2]).toUpperCase();
      if (chosen === q.answer) {
        SonificationManager.playTone(900, 0.1, 0.1);
        speak(`Correct! ${q.explanation}`);
        out(`CORRECT!\n\n${q.explanation}`, { sr: false });
      } else {
        SonificationManager.playTone(200, 0.15, 0.08);
        speak(`Not quite. The correct answer was ${q.answer}. ${q.explanation}`);
        out(`Incorrect. The correct answer was ${q.answer}.\n\n${q.explanation}`, { sr: false });
      }
      return;
    }
  }

  if (_ts._pendingBugChallenge) {
    if (_ts._pendingBugChallenge.expiresAt && Date.now() > _ts._pendingBugChallenge.expiresAt) {
      _ts._pendingBugChallenge = null;
    }
  }
  if (_ts._pendingBugChallenge) {
    const t = txt.toLowerCase().trim();
    if (t.includes('show answer') || t.includes('give up') || t.includes('reveal') || t.includes('answer दिखाओ')) {
      const ch = _ts._pendingBugChallenge;
      _ts._pendingBugChallenge = null;
      out(`THE BUG:\n${ch.bug}\n\nFIXED CODE:\n${ch.fixed}`, { sr: false });
      speak(`The bug was: ${ch.bug}`);
      setTimeout(() => setCode(ch.fixed), 2000);
      return;
    }
  }

  if (pendingConfirm) {
    const handled = tryResolveConfirmation(txt);
    if (handled) return;
  }

  // Live Assistant control/meta commands are handled locally and never sent to
  // the backend; cockpit commands fall through to the normal /voice-command path.
  if (window.LiveAssistant && window.LiveAssistant.handleMetaCommand(txt)) return;

  if (_activeStreamRun && _activeStreamRun.runId && _activeStreamRun.awaitingPrompt) {
    await sendStreamingInput(txt);
    hideProgramInputControl('Program input sent. Waiting for the program.');
    const field = document.getElementById('voiceText');
    if (field) field.value = '';
    return;
  }

  if (window.LiveAssistant) window.LiveAssistant.noteProcessing(true);
  try {
    // XRCVC: "Voice mode can become stuck on 'command understanding'" - if
    // the backend request never settles (a hung network request or a slow
    // AI call with nothing to abort it), the status readout above stayed on
    // "Interpreting command." forever with no way out. This bounds that
    // wait so the UI always reaches a final state, one way or another.
    const result = await Promise.race([
      guardedJson('voice-command', '/voice-command', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(buildVoiceCommandPayload(txt, 'typed')),
      }, narrationContext()),
      new Promise((_, reject) => setTimeout(() => reject(new Error('command_understanding_timeout')), 20000)),
    ]);
    if (staleResult(result)) return;
    const data = result.value;
    if (window.LiveAssistant) {
      window.LiveAssistant.recordTurn(txt, data.speech || data.message || '', data.action || '');
    }
    applyCommandUnderstanding(data, txt);

    if (!data.success) { speak(data.message || 'Command not recognized.'); return; }

    const action = data.action;

    if (action === 'unknown') {
      const heard = data.heard || txt;
      const message = data.message || `I heard "${heard}", but I could not match it to a command. Say help to hear available commands.`;
      speak(message);
      out(`Unrecognized command: "${heard}"`, { sr: false });
      return;
    }

    if (action === 'confirm') {
      const opts = data.options || [];
      pendingConfirm = {
        options:   opts,
        expiresAt: Date.now() + 15000,
        context:   { heard: data.heard || txt, raw: txt },
      };
      const optsSpoken = opts.map(o => o.replace(/_/g, ' ')).join(' or ');
      const heard = data.heard || txt;
      speak(`Did you mean ${optsSpoken}? Say first or second, or just say a new command.`);
      out(`Heard: "${heard}"\nDid you mean: ${optsSpoken}?\nSay "first" / "second", a new command, or "cancel".`, { sr: false });
      return;
    }

    await handleConfirmedAction(action, data);
  } catch (err) {
    console.error(err);
    const timedOut = err && err.message === 'command_understanding_timeout';
    // Product-polish pass: a failure here previously only changed the small
    // "Next action" status line, with no visible cue - easy for a sighted
    // user to miss. It must NOT write to #output: that is the learner's
    // actual program output (see the "Hello" test case), a different
    // concept, and overwriting it would erase something they still need to
    // see. isError:true instead makes this same, already-announced status
    // line visually distinct (color, via updateCommandUnderstanding).
    updateCommandUnderstanding({
      understood: '',
      nextAction: timedOut ? 'Timed out. Try the command again.' : 'Command failed. Try again.',
      isError: true,
    });
    // sr:false here: with browser speech off, speak() would otherwise call
    // srAnnounce() itself (see speak()'s own !_browserSpeechEnabled branch),
    // a *second*, separately-worded live-region announcement on top of the
    // status-line update just above - confirmed live via NVDA-equivalent
    // DOM/mutation tracing: "Command failed. Try again." then immediately
    // "Voice command failed." for one failure. sr:false only skips that
    // fallback; it has no effect on VoiceEngine's audible path (opts.sr is
    // never read there), so CodeUp Voice still speaks this normally when
    // browser speech is intentionally on.
    speak(timedOut ? 'That took too long. Please try the command again.' : 'Voice command failed.', { sr: false });
  } finally {
    if (window.LiveAssistant) window.LiveAssistant.noteProcessing(false);
  }
}

async function submitCommand() {
  const field = document.getElementById('voiceText');
  if (!field) return;
  const txt = field.value.trim();
  if (!txt) return;
  if (_activeStreamRun && _activeStreamRun.runId && _activeStreamRun.awaitingPrompt) {
    await sendStreamingInput(txt);
    field.value = '';
    return;
  }
  await handleCommandText(txt);
  field.value = '';
}

// ---------- VOICE ----------
function voiceUnavailableMessage() {
  const isFirefox = navigator.userAgent.toLowerCase().includes('firefox');
  return isFirefox
    ? 'Voice input is not supported in Firefox. Please open CodeUp in Chrome or Edge for voice control. Keyboard shortcuts and the typed command box still work in Firefox.'
    : 'Speech recognition is not supported in this browser. Please use Chrome or Edge for voice input. Keyboard shortcuts and the typed command box still work.';
}

function setVoiceButtonOff() {
  const btn = document.getElementById('voiceButton');
  if (!btn) return;
  btn.textContent = '\uD83C\uDFA4 Voice (Off)';
  btn.setAttribute('aria-pressed', 'false');
  btn.classList.remove('cu-button-voice--active');
  btn.classList.remove('cu-button-voice--paused');
}

function markVoiceListeningOff() {
  _voiceEnabledByUser = false;
  isListening = false;
  AppState.isListening = false;
  _voicePaused = false;
  _voiceStartIsUserInitiated = false;
  if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
  _stopRecognitionWatchdog();
  setVoiceButtonOff();
}

function stopListeningNow() {
  try {
    if (typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput &&
        (VoiceEngine.VoiceInput.isActive() || VoiceEngine.VoiceInput.isPaused())) {
      VoiceEngine.VoiceInput.stop();
    }
  } catch (e) {}
  try { if (recognition) recognition.stop(); } catch (e) {}
  markVoiceListeningOff();
}

function toggleVoice() {
  if (typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput) {
    if (VoiceEngine.VoiceInput.isActive() || VoiceEngine.VoiceInput.isPaused()) {
      VoiceEngine.VoiceInput.stop();
      markVoiceListeningOff();
      speak('Voice control deactivated.');
    } else {
      _voiceEnabledByUser = true;
      const started = VoiceEngine.VoiceInput.start(true);
      if (!started) {
        const msg = voiceUnavailableMessage();
        markVoiceListeningOff();
        out(msg, { sr: false });
        speak(msg);
        return;
      }
      isListening = true;
      AppState.isListening = true;
      _voicePaused = false;
      setTimeout(() => {
        if (_voiceEnabledByUser && isListening && typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput && !VoiceEngine.VoiceInput.isActive()) {
          const msg = 'Microphone access blocked. Please grant microphone permission and toggle voice again. Keyboard shortcuts and the typed command box still work.';
          out(msg, { sr: false });
          speak(msg);
          markVoiceListeningOff();
        }
      }, 1500);
      if (typeof cueSuccess === 'function') cueSuccess();
      const code = getCode();
      const hasCode = code.trim().length > 0;
      speak('Voice on.');
      if (hasCode) {
        speak(`${code.split('\n').length} lines in the editor.`);
      }
      speak("Say help for commands.");
    }
    return;
  }
  if (isListening) stopListening(); else startListening();
}

// ---- Live Assistant Mode wiring (state machine: static/live-assistant.js) ----
function _assistantRecognitionAvailable() {
  try {
    if (typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput
        && typeof VoiceEngine.VoiceInput.isSupported === 'function') {
      return !!VoiceEngine.VoiceInput.isSupported();
    }
  } catch (e) {}
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition
            || (typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput));
}

function _assistantEnsureListening() {
  try {
    if (typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput) {
      if (!VoiceEngine.VoiceInput.isActive()) toggleVoice();
    } else if (!isListening) {
      startListening();
    }
  } catch (e) {}
}

function _assistantStopListening() {
  try {
    if (typeof VoiceEngine !== 'undefined' && VoiceEngine.VoiceInput) {
      if (VoiceEngine.VoiceInput.isActive() || VoiceEngine.VoiceInput.isPaused()) {
        VoiceEngine.VoiceInput.stop();
      }
      if (typeof markVoiceListeningOff === 'function') markVoiceListeningOff();
    } else if (isListening) {
      stopListening();
    }
  } catch (e) {}
}

function _renderLiveAssistant(snap) {
  if (!snap) return;
  const statusEl = document.getElementById('liveAssistantStatus');
  const heardEl = document.getElementById('liveAssistantHeard');
  const respEl = document.getElementById('liveAssistantResponse');
  const startBtn = document.getElementById('liveAssistantStartBtn');
  const pauseBtn = document.getElementById('liveAssistantPauseBtn');
  if (statusEl) statusEl.textContent = 'Live Assistant: ' + snap.status;
  if (heardEl) heardEl.textContent = 'You said: ' + (snap.lastHeardCommand || '(nothing yet)');
  if (respEl) respEl.textContent = 'CodeUp said: ' + (snap.lastAssistantResponse || '(nothing yet)');
  if (startBtn) {
    startBtn.textContent = snap.assistantEnabled ? 'Stop live assistant' : 'Start live assistant';
    startBtn.setAttribute('aria-pressed', snap.assistantEnabled ? 'true' : 'false');
  }
  if (pauseBtn) {
    pauseBtn.disabled = !snap.assistantEnabled;
    pauseBtn.textContent = snap.paused ? 'Resume listening' : 'Pause listening';
  }
}

window.LiveAssistant = (typeof createLiveAssistant === 'function') ? createLiveAssistant({
  speak: function (t) { try { speak(t); } catch (e) {} },
  cancelSpeech: function () { try { SpeechManager.cancelAll(); } catch (e) {} },
  startListening: _assistantEnsureListening,
  stopListening: _assistantStopListening,
  recognitionAvailable: _assistantRecognitionAvailable(),
  speechAvailable: (typeof window !== 'undefined' && 'speechSynthesis' in window),
  getMode: function () { return window.activeMode || window._activeMode || 'python'; },
  getFile: function () { return (window.currentFileName || window._activeFileName || ''); },
  onStateChange: function (snap) { try { _renderLiveAssistant(snap); } catch (e) {} },
}) : null;

function _wireLiveAssistantButtons() {
  if (!window.LiveAssistant) return;
  const startBtn = document.getElementById('liveAssistantStartBtn');
  const pauseBtn = document.getElementById('liveAssistantPauseBtn');
  const stopSpeakBtn = document.getElementById('liveAssistantStopSpeakBtn');
  const repeatBtn = document.getElementById('liveAssistantRepeatBtn');
  if (startBtn) startBtn.addEventListener('click', function () {
    if (window.LiveAssistant.getState().assistantEnabled) window.LiveAssistant.stop();
    else window.LiveAssistant.start();
  });
  if (pauseBtn) pauseBtn.addEventListener('click', function () {
    const s = window.LiveAssistant.getState();
    if (!s.assistantEnabled) return;
    if (s.paused) window.LiveAssistant.resumeListening();
    else window.LiveAssistant.pauseListening();
  });
  if (stopSpeakBtn) stopSpeakBtn.addEventListener('click', function () { window.LiveAssistant.stopSpeaking(); });
  if (repeatBtn) repeatBtn.addEventListener('click', function () { window.LiveAssistant.repeat(); });
  _renderLiveAssistant(window.LiveAssistant.getState());
}

function startListening() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    const msg = voiceUnavailableMessage();
    setVoiceButtonOff();
    out(msg, { sr: false });
    speak(msg);
    return;
  }
  if (isListening) { speak('Already listening.'); return; }
  if (_recognitionStarting) { _debugLog('Voice: start already in flight'); return; }

  if (recognition) {
    try {
      recognition.onend = null;
      recognition.onerror = null;
      recognition.onresult = null;
      recognition.abort();
    } catch (e) {}
  }
  if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }

  _voiceEnabledByUser = true;
  _voicePaused = false;
  _recognitionRestartCount = 0;
  _lastRecognitionStartAt = 0;
  recognition = new SR();
  recognition.continuous      = true;
  recognition.interimResults  = true;
  recognition.lang            = getLanguage() === 'hi' ? 'hi-IN' : 'en-US';

  recognition.onstart = () => {
    if (!_voiceEnabledByUser) {
      try { recognition.stop(); } catch (e) {}
      markVoiceListeningOff();
      _debugLog('Voice: blocked stale recognition start after manual off');
      return;
    }
    isListening = true;
    AppState.isListening = true;
    _recognitionStarting = false;
    _lastRecognitionStartAt = Date.now();
    _lastRecognitionActivity = Date.now();
    _startRecognitionWatchdog();

    if (_voicePaused) {
      const btn = document.getElementById('voiceButton');
      if (btn) {
        btn.textContent = 'Voice (Paused)';
        btn.setAttribute('aria-pressed', 'mixed');
        btn.classList.remove('cu-button-voice--active');
        btn.classList.add('cu-button-voice--paused');
      }
      _debugLog('Voice: session restarted while paused — staying silent');
      return;
    }

    const btn = document.getElementById('voiceButton');
    if (btn) {
      btn.textContent = 'Voice (ON)';
      btn.setAttribute('aria-pressed', 'true');
      btn.classList.remove('cu-button-voice--paused');
      btn.classList.add('cu-button-voice--active');
    }

    // Auto-restart from Chrome's idle timeout: silent except for a brief tone
    if (!_voiceStartIsUserInitiated) {
      cueSuccess();
      _debugLog('Voice: silent auto-restart');
      return;
    }
    _voiceStartIsUserInitiated = false;
    cueSuccess();

    const lang = getLanguage();
    const code = getCode();
    const hasCode = code.trim().length > 0;
    const lineCount = code.split('\n').length;

    if (!window._voiceGreetedThisSession) {
      window._voiceGreetedThisSession = true;
      if (lang === 'hi') {
        speak('Voice on. Code run karo keh sakte ho.');
      } else {
        speak('Voice on.');
        if (hasCode) {
          speak(`${lineCount} line${lineCount === 1 ? '' : 's'} in the editor.`);
        } else {
          speak('Editor is empty.');
        }
        speak('Say "help" for commands.');
      }
    }
    _debugLog('Voice: Listening started');
  };

  recognition.onresult = async (event) => {
    if (!_voiceEnabledByUser) return;
    let finalTranscript = '';
    let interimTranscript = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalTranscript += chunk;
      else interimTranscript += chunk;
    }
    if (interimTranscript.trim()) {
      updateCommandUnderstanding({ heard: interimTranscript.trim(), nextAction: 'Listening.' });
    }
    const transcript = finalTranscript.trim();
    if (!transcript) return;
    updateCommandUnderstanding({ heard: transcript, nextAction: 'Interpreting voice command.' });
    _debugLog('Voice heard:', transcript);
    _lastRecognitionActivity = Date.now();

    if (_voicePaused) {
      const lower = transcript.toLowerCase().trim();
      const resumePhrases = new Set([
        'resume', 'resume please',
        'resume voice', 'resume voice recognition', 'resume voice control',
        'resume voice input', 'voice resume',
        'start listening', 'continue listening',
        'unmute', 'wake up', 'come back',
        'are you listening', 'listening',
        'आवाज़ चालू करो', 'voice चालू करो', 'voice resume करो',
        'फिर से सुनो', 'सुनो', 'जागो',
      ]);
      if (resumePhrases.has(lower)) {
        resumeVoiceRecognition();
      } else {
        _debugLog('Voice paused — dropped:', transcript);
      }
      return;
    }

    await handleVoiceCommand(transcript);
  };

  recognition.onerror = (event) => {
    _debugLog('Voice recognition error:', event.error);
    _recognitionStarting = false;
    if (event.error === 'no-speech') { return; }
    if (event.error === 'aborted') {
      if (!_voiceEnabledByUser) {
        markVoiceListeningOff();
      } else {
        isListening = false;
        AppState.isListening = false;
      }
      return;
    }
    if (event.error === 'audio-capture' || event.error === 'not-allowed') {
      const msg = 'Microphone access blocked. Please grant microphone permission and toggle voice again. Keyboard shortcuts and the typed command box still work.';
      out(msg, { sr: false });
      speak(msg);
      markVoiceListeningOff();
      return;
    }
    _debugLog('Voice error (will retry):', event.error);
  };

  recognition.onend = () => {
    _debugLog('Voice: Session ended');
    isListening = false;
    AppState.isListening = false;
    _recognitionStarting = false;
    if (!_voiceEnabledByUser) return;
    _scheduleRecognitionRestart();
  };

  try {
    _voiceStartIsUserInitiated = true;
    _recognitionStarting = true;
    recognition.start();
  } catch (e) {
    _recognitionStarting = false;
    _debugLog('Failed to start recognition:', e && e.message ? e.message : e);
    const msg = 'Failed to start voice control. Keyboard shortcuts and the typed command box still work.';
    out(msg, { sr: false });
    speak(msg);
    markVoiceListeningOff();
    _voiceStartIsUserInitiated = false;
  }
}

function stopListening() {
  if (!recognition || !isListening) { speak('Voice control is not active.'); return; }
  markVoiceListeningOff();
  try { recognition.stop(); } catch (e) {}
  speak('Voice control deactivated.');
  _debugLog('Voice: Listening stopped');
}

function pauseVoiceRecognition() {
  const voiceEngineActive = typeof VoiceEngine !== 'undefined'
    && VoiceEngine.VoiceInput
    && (VoiceEngine.VoiceInput.isActive() || VoiceEngine.VoiceInput.isPaused());
  if (!isListening && !voiceEngineActive) { speak('Voice control is not active.'); return; }
  if (voiceEngineActive) {
    VoiceEngine.VoiceInput.stop();
  }
  if (recognition) {
    try { recognition.stop(); } catch (e) {}
  }
  markVoiceListeningOff();
  SonificationManager.playTone(700, 0.08, 0.1);
  setTimeout(() => SonificationManager.playTone(500, 0.12, 0.1), 100);
  const offLang = getLanguage();
  const offMsg = offLang === 'hi'
    ? 'Voice off kar diya. Jab ready ho, Voice on kar sakte ho.'
    : 'Listening stopped. I will not listen until you turn Voice on again.';
  out(offMsg, { sr: false });
  speak(offMsg);
  _debugLog('Voice: Manually off');
  return;
  if (!isListening) { speak('Voice control is not active.'); return; }
  if (_voicePaused) { speak('Voice is already paused.'); return; }
  _voicePaused = true;

  if (recognition) {
    try {
    } catch (e) {  }
  }

  const btn = document.getElementById('voiceButton');
  if (btn) {
    btn.textContent = 'Voice (Paused)';
    btn.setAttribute('aria-pressed', 'mixed');
    btn.classList.remove('cu-button-voice--active');
    btn.classList.add('cu-button-voice--paused');
  }

  SonificationManager.playTone(700, 0.08, 0.1);
  setTimeout(() => SonificationManager.playTone(500, 0.12, 0.1), 100);

  const lang = getLanguage();
  if (lang === 'hi') {
    speak('Voice रुक गया। बात करते रहें — मैं नहीं सुनूंगा। फिर से शुरू करने के लिए "resume" कहें।');
  } else {
    speak('Voice paused. Talk freely — I will ignore everything until you say "resume".');
  }
  srAnnounce('Voice paused');
  _debugLog('Voice: Paused');
}

function resumeVoiceRecognition() {
  if (!isListening) {
    _voiceEnabledByUser = true;
    _voicePaused = false;
    toggleVoice();
    return;
  }
  if (!_voicePaused) { speak('Voice is already listening.'); return; }
  _voiceEnabledByUser = true;
  _voicePaused = false;

  const btn = document.getElementById('voiceButton');
  if (btn) {
    btn.textContent = 'Voice (ON)';
    btn.setAttribute('aria-pressed', 'true');
    btn.classList.remove('cu-button-voice--paused');
    btn.classList.add('cu-button-voice--active');
  }

  try {
    recognition.stop();
    _lastRecognitionActivity = Date.now();
  } catch (e) {
    _debugLog('Resume: stop failed, trying direct restart', e.message);
    try { if (_voiceEnabledByUser) { recognition.start(); _lastRecognitionActivity = Date.now(); } }
    catch (e2) { _debugLog('Resume: direct restart also failed', e2.message); }
  }

  SonificationManager.playTone(500, 0.08, 0.1);
  setTimeout(() => SonificationManager.playTone(700, 0.12, 0.1), 100);

  const lang = getLanguage();
  if (lang === 'hi') {
    speak('Voice फिर से चालू है।');
  } else {
    speak('Voice is back on.');
  }
  srAnnounce('Voice resumed');
  _debugLog('Voice: Resumed');
}

async function handleVoiceCommand(rawText) {
  if (_stepNarrationJob && !_stepNarrationJob.cancelled) {
    const t = rawText.toLowerCase().trim();
    const stopWords = ['stop', 'stop it', 'shut up', 'be quiet', 'silence',
                       'stop talking', 'cancel', 'enough', 'quit',
                       'रुको', 'बंद करो', 'चुप', 'रुक'];
    if (stopWords.some(w => t === w)) {
      _stepNarrationJob.cancelled = true;
      SpeechManager.cancelAll();
      SonificationManager.clearAll();
      ErrorBeaconManager.stop();
      // out() already announces via srAnnounce() internally - the extra
      // srAnnounce('Stopped') here duplicated it with slightly different
      // text ("Stopped." vs "Stopped"), so the two calls didn't even
      // dedupe against each other (XRCVC "duplicate output speech").
      out('Stopped.');
      SonificationManager.playTone(400, 0.08, 0.08);
    }
    return;
  }

  updateCommandUnderstanding({ heard: rawText, understood: '', nextAction: 'Interpreting voice command.' });

  // normal IDE commands like "run" or "read line 2" still flow through.
  if (window.TutorialController && window.TutorialController.active &&
      typeof window.TutorialController.handleUtterance === 'function') {
    try {
      if (window.TutorialController.handleUtterance(rawText)) return;
    } catch (e) { _debugLog('Tutorial utterance error:', e); }
  }

  // BARGE-IN: cancel any ongoing legacy speech (VoiceEngine handles its own
  if (typeof VoiceEngine !== 'undefined') {
    SpeechManager.cancelAll();
  }

  const _ts = tabState();
  if (_ts._pendingQuizAnswer) {
    if (_ts._pendingQuizAnswer.expiresAt && Date.now() > _ts._pendingQuizAnswer.expiresAt) {
      _ts._pendingQuizAnswer = null;
    }
  }
  if (_ts._pendingQuizAnswer) {
    const t = rawText.toLowerCase().trim();
    const match = t.match(/(?:answer|option|choose)\s+([abcd])|^([abcd])$/);
    if (match) {
      const q = _ts._pendingQuizAnswer;
      _ts._pendingQuizAnswer = null;
      const chosen = (match[1] || match[2]).toUpperCase();
      if (chosen === q.answer) {
        SonificationManager.playTone(900, 0.1, 0.1);
        speak(`Correct! ${q.explanation}`);
        out(`CORRECT!\n\n${q.explanation}`, { sr: false });
      } else {
        SonificationManager.playTone(200, 0.15, 0.08);
        speak(`Not quite. The correct answer was ${q.answer}. ${q.explanation}`);
        out(`Incorrect. The correct answer was ${q.answer}.\n\n${q.explanation}`, { sr: false });
      }
      return;
    }
  }

  if (_ts._pendingBugChallenge) {
    if (_ts._pendingBugChallenge.expiresAt && Date.now() > _ts._pendingBugChallenge.expiresAt) {
      _ts._pendingBugChallenge = null;
    }
  }
  if (_ts._pendingBugChallenge) {
    const t = rawText.toLowerCase().trim();
    if (t.includes('show answer') || t.includes('give up') || t.includes('reveal') || t.includes('answer दिखाओ')) {
      const ch = _ts._pendingBugChallenge;
      _ts._pendingBugChallenge = null;
      out(`THE BUG:\n${ch.bug}\n\nFIXED CODE:\n${ch.fixed}`, { sr: false });
      speak(`The bug was: ${ch.bug}`);
      setTimeout(() => setCode(ch.fixed), 2000);
      return;
    }
  }

  if (pendingConfirm) {
    const handled = tryResolveConfirmation(rawText);
    if (handled) return;
  }

  const cleaned = String(rawText || '').trim()
    .replace(/^(please|can you|could you|would you|hey|okay|ok)\s+/gi, '')
    .replace(/\s+(please|thanks|thank you)$/gi, '')
    .trim();

  _debugLog('Voice parsing:', cleaned);

  try {
    // XRCVC: "Voice mode can become stuck on 'command understanding'" - see
    // the matching fix/comment in handleCommandText(); this is the same
    // request made from the voice-recognition path instead of the typed
    // command box, so it needs the same bound.
    const result = await Promise.race([
      guardedJson('voice-command', '/voice-command', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(buildVoiceCommandPayload(cleaned, 'voice')),
      }, narrationContext()),
      new Promise((_, reject) => setTimeout(() => reject(new Error('command_understanding_timeout')), 20000)),
    ]);
    if (staleResult(result)) return;
    const data = result.value;
    applyCommandUnderstanding(data, cleaned);

    if (data.action === 'confirm') {
      const opts = data.options || [];
      pendingConfirm = {
        options:   opts,
        expiresAt: Date.now() + 15000,
        context:   { heard: data.heard || rawText, raw: rawText },
      };
      const optsSpoken = opts.map(o => o.replace(/_/g, ' ')).join(' or ');
      const heard = data.heard || rawText;
      speak(`Did you mean ${optsSpoken}? Say first or second, or just say a new command.`);
      out(`Heard: "${heard}"\nDid you mean: ${optsSpoken}?\nSay "first" / "second", a new command, or "cancel".`, { sr: false });
      return;
    }

    if (data.success && data.action && data.action !== 'unknown') {
      _debugLog('Backend action:', data.action);
      await handleConfirmedAction(data.action, data);
    } else {
      speak(data.message || "I didn't understand that command. Say 'help' for available commands.");
      _debugLog('Command not recognized:', cleaned);
    }
  } catch (e) {
    console.error('Backend interpretation failed:', e);
    const timedOut = e && e.message === 'command_understanding_timeout';
    // This catch means the request itself failed (network/timeout) - the
    // command was never actually understood one way or the other, so
    // "not recognized" (which means something different: the backend
    // answered but didn't match a known command) was a misleading label
    // for this case.
    updateCommandUnderstanding({
      understood: '',
      nextAction: timedOut ? 'Timed out. Try the command again.' : 'Command failed. Try again.',
      isError: true,
    });
    // sr:false: see the matching comment in handleCommandText()'s catch
    // block - without it, speak() duplicates the status-line announcement
    // just above via its own srAnnounce() fallback when browser speech is
    // off.
    speak(timedOut ? 'That took too long. Please try the command again.' : 'Voice command failed.', { sr: false });
  }
}

window.addEventListener('DOMContentLoaded', () => {
  try { restoreAccessibilityPreferences(); } catch (e) {}
  try { _wireLiveAssistantButtons(); } catch (e) {}

  // Regression: "Jump to editor" only scrolled #editor into view - Monaco's
  // real focusable element is a hidden textarea nested deep inside that
  // div, and a plain div with no tabindex is not a native browser focus
  // target on a hash jump, so keyboard focus silently stayed on <body>.
  // "Jump to output" worked because #output itself has tabindex="0"; the
  // editor needs the same promise kept, via Monaco's own focus() instead.
  const skipToEditor = document.querySelector('.cu-skip-links a[href="#editor"]');
  if (skipToEditor) {
    skipToEditor.addEventListener('click', (event) => {
      if (typeof editor !== 'undefined' && editor && editor.focus) {
        event.preventDefault();
        document.getElementById('editor').scrollIntoView({ block: 'center' });
        editor.focus();
      }
    });
  }

  const readAgain = document.getElementById('readOutputAgainBtn');
  if (readAgain) readAgain.addEventListener('click', speakOutput);
  const stopSpeech = document.getElementById('stopSpeechBtn');
  if (stopSpeech) stopSpeech.addEventListener('click', () => { SpeechManager.cancelAll(); srAnnounce('Speech stopped.'); });
  const leaveEditorBtn = document.getElementById('leaveEditorBtn');
  if (leaveEditorBtn) leaveEditorBtn.addEventListener('click', leaveEditor);
  const shortcutHelpBtn = document.getElementById('shortcutHelpBtn');
  if (shortcutHelpBtn) shortcutHelpBtn.addEventListener('click', () => openShortcutHelp());
  const shortcutHelpCloseBtn = document.getElementById('shortcutHelpCloseBtn');
  if (shortcutHelpCloseBtn) shortcutHelpCloseBtn.addEventListener('click', () => closeShortcutHelp());
  const guideBtn = document.getElementById('guideBtn');
  if (guideBtn) guideBtn.addEventListener('click', () => openGettingStartedGuide());
  const guideCloseBtn = document.getElementById('guideCloseBtn');
  if (guideCloseBtn) guideCloseBtn.addEventListener('click', () => closeGettingStartedGuide());
  const programInput = document.getElementById('programInputValue');
  if (programInput) programInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); submitProgramInputValue(); }
    if (event.key === 'Escape') { event.preventDefault(); cancelProgramInputRequest(); }
  });
  const programSubmit = document.getElementById('programInputSubmitBtn');
  if (programSubmit) programSubmit.addEventListener('click', submitProgramInputValue);
  const programCancel = document.getElementById('programInputCancelBtn');
  if (programCancel) programCancel.addEventListener('click', cancelProgramInputRequest);

  const codeModeBtn = document.getElementById('codeModeBtn');
  const audioBlocksModeBtn = document.getElementById('audioBlocksModeBtn');
  if (codeModeBtn) codeModeBtn.addEventListener('click', () => audioBlocksCommand('switch to code mode'));
  if (audioBlocksModeBtn) audioBlocksModeBtn.addEventListener('click', () => audioBlocksCommand('open audio blocks'));
  document.querySelectorAll('[data-block-command]').forEach(button => {
    button.addEventListener('click', () => audioBlocksCommand(button.getAttribute('data-block-command')));
  });
  const moveUp = document.getElementById('audioBlockMoveUpBtn');
  const moveDown = document.getElementById('audioBlockMoveDownBtn');
  const indent = document.getElementById('audioBlockIndentBtn');
  const outdent = document.getElementById('audioBlockOutdentBtn');
  const remove = document.getElementById('audioBlockDeleteBtn');
  if (moveUp) moveUp.addEventListener('click', () => {
    const state = window._audioBlocksState || {};
    if (state.cursor_id) audioBlocksCommand(`move block ${state.cursor_id} up`);
  });
  if (moveDown) moveDown.addEventListener('click', () => {
    const state = window._audioBlocksState || {};
    if (state.cursor_id) audioBlocksCommand(`move block ${state.cursor_id} down`);
  });
  if (indent) indent.addEventListener('click', () => currentAudioBlockCommand('indent'));
  if (outdent) outdent.addEventListener('click', () => currentAudioBlockCommand('outdent'));
  if (remove) remove.addEventListener('click', () => currentAudioBlockCommand('delete'));
  const blockWorkspace = document.querySelector('.audio-blocks-workspace');
  if (blockWorkspace) blockWorkspace.addEventListener('keydown', event => {
    const state = window._audioBlocksState || {};
    let command = '';
    if (event.key === 'ArrowDown' && event.ctrlKey && state.cursor_id) command = `move block ${state.cursor_id} down`;
    else if (event.key === 'ArrowUp' && event.ctrlKey && state.cursor_id) command = `move block ${state.cursor_id} up`;
    else if (event.key === 'ArrowDown') command = 'next block';
    else if (event.key === 'ArrowUp') command = 'previous block';
    else if (event.key === ']' && state.cursor_id) command = `indent block ${state.cursor_id}`;
    else if (event.key === '[' && state.cursor_id) command = `outdent block ${state.cursor_id}`;
    else if (event.key === 'Delete' && state.cursor_id) command = `delete block ${state.cursor_id}`;
    else if (event.key === 'Enter' && event.ctrlKey && event.shiftKey) command = 'run blocks';
    else if (event.key === 'Enter' && event.ctrlKey) command = 'compile blocks to Python';
    if (command) { event.preventDefault(); audioBlocksCommand(command); }
  });

  const resumeAudio = () => {
    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  };
  document.addEventListener('click', resumeAudio);

  document.addEventListener('keydown', e => {
    resumeAudio();
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey) {
      const commands = {
        R: 'run', H: 'what can I do here', E: 'read errors only', M: 'code map',
        T: 'run with step narration', S: 'stop', A: 'toggle screen reader mode',
        K: 'show keyboard shortcuts', N: 'what navigation mode am I in',
        O: 'open accessibility options',
      };
      const key = String(e.key || '').toUpperCase();
      if (commands[key]) {
        e.preventDefault();
        if (key === 'N') {
          const navOn = localStorage.getItem('codeupNavigationMode') === 'true';
          const command = navOn ? 'navigation mode off' : 'navigation mode on';
          localStorage.setItem('codeupNavigationMode', navOn ? 'false' : 'true');
          handleCommandText(command);
        } else if (key === 'O') {
          openAccessibilityOptionsPanel();
        } else if (key === 'K') {
          openShortcutHelp();
        } else {
          handleCommandText(commands[key]);
        }
        return;
      }
    }
    const editableTarget = e.target && (
      e.target.isContentEditable ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName || '')
    );
    if (!editableTarget && !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey && e.key === '2') {
      e.preventDefault();
      if (typeof _stepNarrationJob !== 'undefined' && _stepNarrationJob) _stepNarrationJob.cancelled = true;
      stopListeningNow();
      SpeechManager.cancelAll();
      SonificationManager.clearAll();
      ErrorBeaconManager.stop();
      const msg = 'Stopped listening and speech. Editor unchanged.';
                  // silent screen-reader status (always)
      speak(msg);
      return;
    }
    if (e.ctrlKey && e.shiftKey && e.key === 'M') { e.preventDefault(); toggleVoice(); }
    if (e.key === 'Escape') {
      const paletteOverlay = document.getElementById('commandPaletteOverlay');
      const paletteOpen = paletteOverlay && !paletteOverlay.hasAttribute('hidden');
      const inputDialog = document.getElementById('_cuInputDialog');
      const dialogOpen  = !!(inputDialog && !inputDialog.hidden);
      if (paletteOpen || dialogOpen) return;
      if (AppState.isSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking) || (_stepNarrationJob && !_stepNarrationJob.cancelled)) {
        if (_stepNarrationJob) _stepNarrationJob.cancelled = true;
        SpeechManager.cancelAll();
        SonificationManager.clearAll();
        ErrorBeaconManager.stop();
        srAnnounce('Speech stopped');
        SonificationManager.playTone(600, 0.05, 0.06);
        e.preventDefault();
      }
    }
  });

  const paletteInput = document.getElementById('commandPaletteInput');
  if (paletteInput) {
    paletteInput.addEventListener('input', e => {
      commandPaletteSelectedIndex = 0;
      renderCommandPalette(e.target.value);
    });
    paletteInput.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeCommandPalette(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); commandPaletteSelectedIndex++; renderCommandPalette(paletteInput.value); }
      else if (e.key === 'ArrowUp')   { e.preventDefault(); commandPaletteSelectedIndex--; renderCommandPalette(paletteInput.value); }
      else if (e.key === 'Enter')     { e.preventDefault(); executeCommandPaletteItem(commandPaletteSelectedIndex); }
    });
  }

  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.shiftKey && e.key === 'P') { e.preventDefault(); openCommandPalette(); }
  });

  // Click outside the container closes the palette
  const paletteOverlay = document.getElementById('commandPaletteOverlay');
  if (paletteOverlay) {
    paletteOverlay.addEventListener('click', e => {
      if (e.target === paletteOverlay) closeCommandPalette();
    });
    // Keep Tab/Shift+Tab inside the palette while it's open - without this,
    // Tab from the last focusable item (the results listbox) escapes to
    // whatever comes next in the underlying page's tab order (e.g. the
    // "Jump to editor" skip link), even though the overlay is still open
    // and visually blocking the page (Pass 5B modal focus audit).
    paletteOverlay.addEventListener('keydown', e => {
      if (e.key !== 'Tab') return;
      const items = Array.prototype.slice.call(
        paletteOverlay.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
      ).filter(el => !el.disabled && el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }
});

function focusEditor() {
  try { if (editor && editor.focus) editor.focus(); } catch (e) {}
}
function leaveEditor() {
  const next = document.getElementById('runBtn') || document.getElementById('voiceText') || document.getElementById('output');
  if (next && next.focus) next.focus();
  srAnnounce('Left editor. Press Tab to continue through CodeUp controls.');
}

// XRCVC Issue 18: "Accessibility Options shortcut does not work" - there was
// no keyboard shortcut that opened the Accessibility settings disclosure at
// all, so any documentation/expectation of one was simply a mismatch. This
// gives Alt+Shift+O a real, working target: opens the <details> in the
// header and moves focus inside it, rather than just announcing text.
function openAccessibilityOptionsPanel() {
  const details = document.querySelector('.cu-header-settings');
  if (!details) return;
  details.open = true;
  const firstControl = document.getElementById('speechModeSelect');
  if (firstControl && firstControl.focus) {
    firstControl.focus();
  } else if (details.querySelector('summary') && details.querySelector('summary').focus) {
    details.querySelector('summary').focus();
  }
  srAnnounce('Accessibility and speech settings opened.');
}

function registerEditorShortcuts() {
  if (window._editorShortcutsRegistered) return;
  if (!editor) return;
  window._editorShortcutsRegistered = true;

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => { runCode(); });

  editor.addCommand(monaco.KeyCode.Escape, () => {
    if (AppState.isSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking)) {
      SpeechManager.cancelAll();
      ErrorBeaconManager.stop();
      srAnnounce('Speech stopped');
      SonificationManager.playTone(600, 0.05, 0.06);
    } else {
      leaveEditor();
    }
  });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyM, () => { leaveEditor(); });
  // accessibilitySupport:'on' (above) puts Monaco in tab-focus-move mode, so plain
  // Tab moves focus out of the editor instead of indenting - these are the only
  // reachable keyboard shortcuts to indent/outdent a line (see ariaLabel above).
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.BracketRight, () => { editor.trigger('keyboard', 'editor.action.indentLines', {}); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.BracketLeft, () => { editor.trigger('keyboard', 'editor.action.outdentLines', {}); });
  const editorDom = editor.getDomNode();
  if (editorDom) {
    editorDom.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (AppState.isSpeaking || (window.speechSynthesis && window.speechSynthesis.speaking)) {
          SpeechManager.cancelAll();
          ErrorBeaconManager.stop();
          srAnnounce('Speech stopped');
          SonificationManager.playTone(600, 0.05, 0.06);
        } else {
          leaveEditor();
        }
      }
    }, true);
  }

  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyS, () => { SpeechManager.cancelAll(); sonifyCurrentBlock(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyL, () => { SpeechManager.cancelAll(); const pos = editor.getPosition() || { lineNumber: 1 }; readLineEnhanced(pos.lineNumber); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyV, () => { SpeechManager.cancelAll(); listVariables(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyE, () => { SpeechManager.cancelAll(); checkSyntaxErrors(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyN, () => { SpeechManager.cancelAll(); speakNextStep(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyH, () => { SpeechManager.cancelAll(); showHelp(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyB, () => { SpeechManager.cancelAll(); readBreadcrumb(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyW, () => { walkThroughCode(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.LeftArrow,  () => { navigateBack(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.RightArrow, () => { navigateForward(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.Home, () => { goToTop(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.End,  () => { goToBottom(); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyC, () => { copyCode(); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyV, () => { pasteCode(); });

  try { loadSnippets(); } catch (e) {}
  try { startAutosave(); } catch (e) { console.warn('autosave init failed', e); }
  try { recoverAutosaveDraft(); } catch (e) { console.warn('autosave recover failed', e); }
  updateInputModeUI();
  updateInputsPanel();
  if (typeof window._classroomInit === 'function') {
    try { window._classroomInit(); } catch (e) { console.warn('classroom init failed', e); }
  }
  _debugLog('All accessibility features loaded.');
}

function startAutosave() {
  if (_autosaveTimer) return;
  _autosaveTimer = setInterval(() => {
    try {
      const code = getCode();
      if (code === _autosaveLastCode) return;  // no-op if nothing changed
      if (!code.trim()) return;                 // don't autosave empty
      if (looksLikeNonPythonCode(code)) {
        localStorage.removeItem(AUTOSAVE_KEY);
        _autosaveLastCode = '';
        return;
      }
      localStorage.setItem(AUTOSAVE_KEY, JSON.stringify({
        code,
        timestamp: Date.now(),
        language: 'python',
        app: 'codeup-python',
      }));
      _autosaveLastCode = code;
      if (typeof window._classroomOnAutosave === 'function') {
        window._classroomOnAutosave(code);
      }
    } catch (e) { /* localStorage full or disabled — silent fail */ }
  }, AUTOSAVE_INTERVAL_MS);
}

function recoverAutosaveDraft() {
  try {
    const raw = localStorage.getItem(AUTOSAVE_KEY);
    if (!raw) return;
    const draft = JSON.parse(raw);
    if (!draft || !draft.code) return;
    if ((draft.language && draft.language !== 'python') || looksLikeNonPythonCode(draft.code)) {
      localStorage.removeItem(AUTOSAVE_KEY);
      out('Removed a stale non-Python draft. CodeUp now keeps this editor Python-only.');
      srAnnounce('Stale non-Python draft removed');
      return;
    }
    const current = getCode().trim();
    const isDefault = !current || current === DEFAULT_PYTHON_STARTER;
    if (!isDefault) return;
    const ageMs = Date.now() - (draft.timestamp || 0);
    if (ageMs > 7 * 24 * 60 * 60 * 1000) {
      localStorage.removeItem(AUTOSAVE_KEY);
      return;
    }
    if (!setCode(draft.code, { source: 'autosaved draft' })) return;
    speak('A draft from your previous session has been restored. Press Control Z to undo if you did not want this.');
  } catch (e) { /* corrupted or missing — silent fail */ }
}

async function speakNextStep() {
  await handleCommandText('next step');
}

let lastStructureData = null;

async function updateStructurePanel() {
  if (!editor) return;
  const code    = editor.getValue();
  const panel   = document.getElementById('structurePanel');
  const content = document.getElementById('structureContent');
  if (!code.trim()) { hideEl(panel); return; }
  if (looksLikeNonPythonCode(code)) {
    content.innerHTML = '<p class="structure-info">CodeUp is Python-only. Remove HTML, CSS, or JavaScript.</p>';
    showEl(panel);
    return;
  }

  try {
    const res  = await fetch('/structure', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code }),
    });
    const data = await res.json();

    if (!data.success || !data.structure) {
      const detail = data.error
        ? escapeHtml(data.error)
        : 'Unable to parse structure. Check for a missing colon, bracket, quote, or indentation before using the structure map.';
      content.innerHTML = `<p class="structure-info">${detail}</p>`;
      showEl(panel);
      return;
    }

    lastStructureData = data.structure;
    const { imports, functions, classes, loops } = data.structure;
    let html = '';

    if (imports.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">Imports</div>';
      imports.forEach(imp => {
        html += `<div class="structure-item" role="button" tabindex="0" data-line="1">
          <span class="structure-item-label">${escapeHtml(imp)}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (classes.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">Classes</div>';
      classes.forEach(cls => {
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${cls.line}" aria-label="Go to class ${escapeHtml(cls.name)} at line ${cls.line}">
          <span class="structure-item-label">${escapeHtml(cls.name)}</span>
          <span class="structure-item-line">L${cls.line}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (functions.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">Functions</div>';
      functions.forEach(fn => {
        const params = fn.params.map(p => p.name).join(', ');
        const asyncBadge = fn.is_async ? '<span style="color:#facc15;font-size:0.7rem;margin-right:4px;">async</span>' : '';
        const parentLabel = fn.parent_class ? `<span style="color:#64748b;font-size:0.75rem;">${escapeHtml(fn.parent_class)}.</span>` : '';
        const ariaLabel = `Go to ${fn.is_async ? 'async ' : ''}function ${fn.parent_class ? fn.parent_class + '.' : ''}${fn.name} at line ${fn.line}`;
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${fn.line}" aria-label="${escapeHtml(ariaLabel)}">
          <span class="structure-item-label">${asyncBadge}${parentLabel}${escapeHtml(fn.name)}(${escapeHtml(params)})</span>
          <span class="structure-item-line">L${fn.line}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (loops.length > 0) {
      html += '<div class="structure-group"><div class="structure-group-title">Loops</div>';
      loops.forEach((loop, idx) => {
        html += `<div class="structure-item" role="button" tabindex="0" data-line="${loop.line}" aria-label="Go to loop at line ${loop.line}">
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

async function sonifyFunction(functionName) {
  if (!functionName) { speak('Please specify a function name.'); return; }
  if (!ensurePythonEditorContent('sonify function')) return;
  const lines = getCode().split('\n');
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
  if (!ensurePythonEditorContent('sonify class')) return;
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

async function sonifyRange(startLine, endLine, context = 'block', delayMs = 100) {
  const lines = getCode().split('\n');
  const contextFreqs = { function: 900, class: 1000, loop: 400, block: 600 };
  const baseFreq = contextFreqs[context] || 600;

  for (let i = startLine - 1; i < Math.min(endLine, lines.length); i++) {
    const indent = lines[i].search(/\S/);
    SonificationManager.playTone(baseFreq + 50 * Math.min(indent / 2, 5), 0.05, 0.08);
    await sleep(delayMs);
  }
  speak(`Finished sonifying ${context}.`);
}

async function sonifyWholeFile() {
  if (!ensurePythonEditorContent('sonify file')) return;
  const lines = getCode().split('\n');
  if (!getCode().trim()) {
    speak('The file is empty.');
    return;
  }
  speak(`Sonifying the whole file, ${lines.length} line${lines.length === 1 ? '' : 's'}.`);
  await sonifyRange(1, lines.length, 'file', 50);
}

function findFunctionLine(functionName) {
  if (!functionName) return null;
  const pattern = new RegExp(`^\\s*def\\s+${escapeRegex(functionName)}\\s*\\(`, 'i');
  const lines = getCode().split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(lines[i])) return i + 1;
  }
  return null;
}

function findClassLine(className) {
  if (!className) return null;
  const pattern = new RegExp(`^\\s*class\\s+${escapeRegex(className)}\\s*[:\\(]`, 'i');
  const lines = getCode().split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(lines[i])) return i + 1;
  }
  return null;
}

function findFunction(functionName) {
  if (!functionName) { speak('Please specify a function name.'); return; }
  if (!ensurePythonEditorContent('find function')) return;
  const line = findFunctionLine(functionName);
  if (!line) { speak(`Function ${functionName} not found.`); out(`Function ${functionName} not found.`, { sr: false }); return; }
  gotoLine(line, false);
  speak(`Function ${functionName} starts on line ${line}.`);
}

function findClass(className) {
  if (!className) { speak('Please specify a class name.'); return; }
  if (!ensurePythonEditorContent('find class')) return;
  const line = findClassLine(className);
  if (!line) { speak(`Class ${className} not found.`); out(`Class ${className} not found.`, { sr: false }); return; }
  gotoLine(line, false);
  speak(`Class ${className} starts on line ${line}.`);
}

function readFunction(functionName) {
  if (!functionName) { speak('Please specify a function name.'); return; }
  if (!ensurePythonEditorContent('read function')) return;
  const startLine = findFunctionLine(functionName);
  if (!startLine) { speak(`Function ${functionName} not found.`); out(`Function ${functionName} not found.`, { sr: false }); return; }
  const lines = getCode().split('\n');
  const baseIndent = lines[startLine - 1].search(/\S/);
  let endLine = lines.length;
  for (let i = startLine; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() && line.search(/\S/) <= baseIndent) {
      endLine = i;
      break;
    }
  }
  const body = lines.slice(startLine - 1, endLine).join('\n');
  out(`Function ${functionName}, lines ${startLine} to ${endLine}:\n\n${body}`, { sr: false });
  speak(`Function ${functionName} starts on line ${startLine} and ends on line ${endLine}.`);
  body.split('\n').slice(0, 12).forEach((line, idx) => speak(`Line ${startLine + idx}: ${line || 'empty line'}`));
}

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
    if (indent !== -1 && indent % 4 !== 0 && indent % 2 !== 0) {
      issues.push({ line: idx + 1, type: 'Style', message: 'Inconsistent indentation' });
    }
    const trimmed = line.trim();
    if (/^(if|elif|while)\s+/.test(trimmed) && /[^=!<>]=(?!=)/.test(trimmed)) {
      issues.push({ line: idx + 1, type: 'Warning', message: 'Possible assignment in condition' });
    }
  });

  if (issues.length === 0) { speak('No code issues detected.'); return; }
  speak(`Found ${issues.length} issue${issues.length !== 1 ? 's' : ''}.`);
  for (const issue of issues) { sonifyError(issue.message, issue.line); await sleep(300); }
}

async function getDebugSuggestions() {
  const code = getCode();
  if (!code.trim()) { speak('Code is empty.'); return; }
  if (!ensurePythonEditorContent('debug suggestions')) return;
  speak('Analyzing code for improvement suggestions...');
  try {
    const result = await guardedJson('debug-suggestions', '/debug-suggestions', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code, language: getLanguage() }),
    }, narrationContext(code));
    if (staleResult(result)) return;
    const data = result.value;
    if (!data.success || !data.suggestions) { out('No suggestions available.', { sr: false }); speak('Could not generate suggestions.'); return; }
    if (data.suggestions.length === 0) { out('Code looks good! No issues found.', { sr: false }); speak('Code looks good. No issues found.'); return; }

    let output = 'DEBUG SUGGESTIONS:\n\n';
    let speech = `Found ${data.suggestions.length} suggestion${data.suggestions.length !== 1 ? 's' : ''}. `;
    data.suggestions.forEach((sugg, idx) => { output += `${sugg.type === 'warning' ? 'Warning' : 'Suggestion'}: ${sugg.text}\n\n`; speech += `Item ${idx + 1}: ${sugg.text}. `; });
    out(output, { sr: false }); speak(speech);
  } catch (e) {
    console.error('Debug suggestions error:', e); speak('Error getting suggestions.');
  }
}

const COMMAND_PALETTE_COMMANDS = [
  { id: 'run',              title: 'Run Code',           desc: 'Execute Python code',                icon: '',  keys: 'Ctrl+Enter',   action: () => runCode() },
  { id: 'analyze',          title: 'Analyze Code',        desc: 'AI analysis of code',               icon: '', keys: 'Ctrl+Alt+A',   action: () => analyzeCode() },
  { id: 'fix',              title: 'Fix Code',            desc: 'Automatically fix errors',          icon: '', keys: 'Ctrl+Alt+F',   action: () => fixCode() },
  { id: 'advise',           title: 'Get Advice',          desc: 'Suggestions for improvements',      icon: '', keys: 'Ctrl+Alt+I',   action: () => adviseCode() },
  { id: 'python_starter',   title: 'Python Starter',      desc: 'Load a clean Python starter',       icon: 'Py', keys: '',             action: () => resetPythonStarter() },
  { id: 'goto_line',        title: 'Go to Line',          desc: 'Jump to specific line',             icon: '', keys: 'Ctrl+G',       action: () => showInputDialog('Enter line number:', gotoLine) },
  { id: 'read_line',        title: 'Read Line',           desc: 'Read current line with context',    icon: '', keys: '',             action: () => readCurrentLine() },
  { id: 'next_line',        title: 'Next Line',           desc: 'Move to next line',                 icon: '↓',  keys: 'Down',         action: () => nextLine() },
  { id: 'prev_line',        title: 'Previous Line',       desc: 'Move to previous line',             icon: '↑',  keys: 'Up',           action: () => prevLine() },
  { id: 'show_structure',   title: 'Show Structure',      desc: 'Display code navigation map',       icon: '', keys: 'Ctrl+Shift+S', action: () => toggleStructurePanel() },
  { id: 'project_files',    title: 'Project Files',       desc: 'Read active project file list',     icon: 'Files', keys: '',          action: () => readProjectFiles() },
  { id: 'open_project_file',title: 'Open Project File',   desc: 'Open a file by name',               icon: 'Open', keys: '',           action: () => showInputDialog('File name:', openProjectFile) },
  { id: 'create_project_file', title: 'Create Project File', desc: 'Create a file in this project',  icon: 'New', keys: '',            action: () => showInputDialog('New file name:', createProjectFile) },
  { id: 'requirements',     title: 'Requirements',        desc: 'Explain project dependencies',      icon: 'Req', keys: '',            action: () => explainProjectRequirements() },
  { id: 'sonify_block',     title: 'Sonify Block',        desc: 'Hear current code block',           icon: '', keys: 'Alt+S',        action: () => sonifyCurrentBlock() },
  { id: 'next_step',        title: 'Next step',           desc: 'Step forward in execution trace',   icon: '',  keys: 'Alt+N',        action: () => speakNextStep() },
  { id: 'prev_step',        title: 'Previous step',       desc: 'Step back in execution trace',      icon: '',  keys: '',             action: () => handleCommandText('previous step') },
  { id: 'save_snippet',     title: 'Save Snippet',        desc: 'Save code as snippet',              icon: '', keys: 'Ctrl+S',       action: () => saveSnippet() },
  { id: 'list_variables',   title: 'List Variables',      desc: 'Show all variables in scope',       icon: '', keys: 'Ctrl+Alt+V',   action: () => listVariables() },
  { id: 'check_errors',     title: 'Check Errors',        desc: 'Find syntax errors',                icon: '', keys: '',             action: () => checkSyntaxErrors() },
  { id: 'locate_error',     title: 'Locate Error',        desc: 'Jump to first error',               icon: '', keys: 'Ctrl+Alt+E',   action: () => locateError() },
  { id: 'clear_editor',     title: 'Clear Editor',        desc: 'Delete all code',                   icon: '', keys: '',             action: () => clearEditor() },
  { id: 'copy_code',        title: 'Copy Code',           desc: 'Copy to clipboard',                 icon: '', keys: 'Ctrl+C',       action: () => copyCode() },
  { id: 'paste_code',       title: 'Paste Code',          desc: 'Paste from clipboard',              icon: '', keys: 'Ctrl+V',       action: () => pasteCode() },
  { id: 'debug_suggestions',title: 'Debug Suggestions',   desc: 'Get AI debugging hints',            icon: '', keys: '',             action: () => getDebugSuggestions() },
  { id: 'sonify_issues',    title: 'Sonify Issues',       desc: 'Hear code problems',                icon: '', keys: '',             action: () => sonifyCodeIssues() },
  { id: 'help',             title: 'Show Help',           desc: 'Display all commands',              icon: '', keys: 'F1',           action: () => showHelp() },
];

let commandPaletteSelectedIndex = 0;

let _commandPaletteOpener = null;
function openCommandPalette() {
  const overlay = document.getElementById('commandPaletteOverlay');
  const input   = document.getElementById('commandPaletteInput');
  _commandPaletteOpener = document.activeElement;
  // Opening the palette is a context switch like run/setCode/etc, all of
  // which cancel prior narration before speaking their own message.
  // Without this, "Command palette open..." silently queued behind
  // whatever was already speaking instead of announcing immediately
  // (Pass 5C live check: speech -> palette -> run -> input dialog).
  SpeechManager.cancelAll();
  showEl(overlay);
  commandPaletteSelectedIndex = 0;
  renderCommandPalette('');
  requestAnimationFrame(() => { if (input) { input.focus(); speak('Command palette open. Type to search, arrow keys to navigate, Enter to select, Escape to close.'); } });
}

function closeCommandPalette() {
  hideEl(document.getElementById('commandPaletteOverlay'));
  // Return focus to whatever had focus before the palette opened, so keyboard users
  // aren't relocated into the editor when they opened the palette from elsewhere.
  const opener = _commandPaletteOpener;
  _commandPaletteOpener = null;
  if (opener && document.contains(opener) && typeof opener.focus === 'function') {
    opener.focus();
  } else if (editor) {
    editor.focus();
  }
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
  if (panel.hasAttribute('hidden')) {
    showEl(panel);
    speak('Structure panel shown.');
  } else {
    hideEl(panel);
    speak('Structure panel hidden.');
  }
}

async function readStructureOutline() {
  const code = getCode();
  if (!code.trim()) {
    speak('The file is empty.');
    return;
  }
  if (!ensurePythonEditorContent('read structure')) return;
  try {
    const res = await fetch('/structure-outline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    const data = await res.json();
    const outline = data.success ? data.outline : (data.error || 'Unable to read the outline.');
    out(outline, { sr: false });
    speak(outline);
  } catch (e) {
    speak('Unable to read the outline.');
  }
}


// ==== SPOKEN-CODE-NORMALIZERS-START (pure; mirrored by tests/spoken_code.test.js) ====
const CODEUP_SPOKEN_OUTPUT_LIMIT = 4000;
const SPOKEN_CODE_NUMBERS = Object.freeze({
  zero: '0',
  one: '1',
  two: '2',
  three: '3',
  four: '4',
  five: '5',
  six: '6',
  seven: '7',
  eight: '8',
  nine: '9',
  ten: '10',
});

function normalizeSpokenCodeExpression(text) {
  let expr = String(text || '').replace(/\s+/g, ' ').trim();
  if (!expr) return '';
  expr = expr
    .replace(/\b(?:open|left)\s+(?:paren|parenthesis)\b/gi, '(')
    .replace(/\b(?:close|right)\s+(?:paren|parenthesis)\b/gi, ')')
    .replace(/\bequals\s+equals\b/gi, ' == ')
    .replace(/\bnot\s+equals\b/gi, ' != ')
    .replace(/\bless\s+than\s+or\s+equal(?:\s+to)?\b/gi, ' <= ')
    .replace(/\bgreater\s+than\s+or\s+equal(?:\s+to)?\b/gi, ' >= ')
    .replace(/\bless\s+than\b/gi, ' < ')
    .replace(/\bgreater\s+than\b/gi, ' > ')
    .replace(/\bplus\b/gi, ' + ')
    .replace(/\bminus\b/gi, ' - ')
    .replace(/\btimes\b|\bmultiplied\s+by\b/gi, ' * ')
    .replace(/\bdivided\s+by\b/gi, ' / ')
    .replace(/\bmodulo\b|\bmod\b/gi, ' % ')
    .replace(/\bequals?\b|\bequal\s+to\b/gi, ' = ')
    .replace(/\bcolon\b/gi, ':')
    .replace(/\b(zero|one|two|three|four|five|six|seven|eight|nine|ten)\b/gi,
      word => SPOKEN_CODE_NUMBERS[word.toLowerCase()] || word);
  return expr
    .replace(/\s+([),:])/g, '$1')
    .replace(/([(])\s+/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function isSimplePythonExpression(text) {
  const expr = String(text || '').trim();
  if (!expr) return false;
  if (/^["'][\s\S]*["']$/.test(expr)) return true;
  if (/^-?\d+(?:\.\d+)?$/.test(expr)) return true;
  if (/^[A-Za-z_]\w*(?:\([^)]*\))?$/.test(expr)) return true;
  return /^(?:[A-Za-z_]\w*|-?\d+(?:\.\d+)?)(?:\s*(?:\+|-|\*|\/|%|==|!=|<=|>=|<|>)\s*(?:[A-Za-z_]\w*|-?\d+(?:\.\d+)?))+$/.test(expr);
}

function quotePythonString(text) {
  return `"${String(text || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function normalizeSpokenPrintArgument(text) {
  const raw = String(text || '').replace(/\s+/g, ' ').trim();
  const expr = normalizeSpokenCodeExpression(raw);
  return isSimplePythonExpression(expr) ? expr : quotePythonString(raw);
}

function normalizeSpokenCodeText(text) {
  let raw = String(text || '').replace(/\s+/g, ' ').trim();
  if (!raw) return '';

  let indent = '';
  // Strip a leading "indented" / "four spaces" / "tab" cue, tolerating an
  while (/^(?:an?\s+|the\s+)?(?:indent|indented|four\s+spaces|tab)\s+/i.test(raw)) {
    indent += '    ';
    raw = raw.replace(/^(?:an?\s+|the\s+)?(?:indent|indented|four\s+spaces|tab)\s+/i, '').trim();
  }

  raw = raw.replace(/^(?:prent|prnt|preent|pirnt|printt|brint|prind|prinr)\b/i, 'print');

  if (/^[A-Za-z_]\w*\s*=/.test(raw) || /^\s*(?:print|range)\s*\(/i.test(raw) || /:\s*$/.test(raw)) {
    return indent + raw;
  }

  const forMatch = raw.match(/^for\s+([A-Za-z_]\w*)\s+in\s+range(?:\s+of)?\s+(.+?)(?:\s+colon|:)?$/i);
  if (forMatch) {
    const count = normalizeSpokenCodeExpression(forMatch[2]);
    return `${indent}for ${forMatch[1]} in range(${count}):`;
  }

  const printSayingMatch = raw.match(/^print(?:\s+statement)?\s+(?:saying|that\s+says)\s+(.+)$/i);
  if (printSayingMatch) {
    return `${indent}print(${quotePythonString(printSayingMatch[1].trim())})`;
  }
  const printMatch = raw.match(/^print(?:\s+statement)?\s+(.+)$/i);
  if (printMatch) {
    return `${indent}print(${normalizeSpokenPrintArgument(printMatch[1])})`;
  }

  const assignMatch = raw.match(/^([A-Za-z_]\w*)\s+(?:equals?|equal\s+to|is|set\s+to)\s+(.+)$/i);
  if (assignMatch) {
    return `${indent}${assignMatch[1]} = ${normalizeSpokenCodeExpression(assignMatch[2])}`;
  }

  return indent + normalizeSpokenCodeExpression(raw);
}


// Bare words are usually intended as text, not variable references.
function normalizeSpokenValue(value) {
  const raw = String(value || '').trim();
  if (!raw) return '""';
  const expr = normalizeSpokenCodeExpression(raw);
  if (/^-?\d+(?:\.\d+)?$/.test(expr)) return expr;            // number
  const low = expr.toLowerCase();
  if (low === 'true') return 'True';
  if (low === 'false') return 'False';
  if (low === 'none' || low === 'nothing') return 'None';
  if (/^(['"])[\s\S]*\1$/.test(expr)) return expr;            // already quoted
  if (/[-+*/%]/.test(expr) && isSimplePythonExpression(expr)) return expr;  // arithmetic
  return quotePythonString(raw);                              // text literal
}

function normalizeSpokenCondition(text) {
  let c = String(text || '').trim();
  if (!c) return 'True';
  c = c.replace(/^(?:whether|that|if)\s+/i, '');
  c = c.replace(/\bis\s+(greater|less|more|bigger|smaller|not|equal)\b/gi, '$1');
  c = c.replace(/\bbigger\s+than\b/gi, 'greater than');
  c = c.replace(/\bsmaller\s+than\b/gi, 'less than');
  c = c.replace(/\bmore\s+than\b/gi, 'greater than');
  let expr = normalizeSpokenCodeExpression(c);
  expr = expr.replace(/(^|[^<>=!])=(?!=)/g, '$1==');
  expr = expr.replace(/={3,}/g, '==').replace(/\s+/g, ' ').trim();
  return expr || 'True';
}

function spokenConditionPhrase(cond) {
  return String(cond || '')
    .replace(/>=/g, ' is greater than or equal to ')
    .replace(/<=/g, ' is less than or equal to ')
    .replace(/==/g, ' equals ')
    .replace(/!=/g, ' is not equal to ')
    .replace(/>/g, ' is greater than ')
    .replace(/</g, ' is less than ')
    .replace(/\s+/g, ' ').trim();
}

function _outputPeriod(s) { return /[.!?:]$/.test(String(s || '')) ? '' : '.'; }
const _NO_OUTPUT_PLACEHOLDER = 'Program finished with no output.';

// XRCVC Issue 6: Python container punctuation ([1, 2, 3], (1, 2), {'a': 1})
// was being spoken with the raw bracket/brace characters, which most TTS
// voices either mumble or silently skip - a screen-reader/CodeUp-Voice user
// heard "one two three" with no indication it was a list at all. This walks
// each line once and only touches bracket/paren/brace characters (open/close
// names) and colons *inside* a detected container, so ordinary sentences
// with no container punctuation are left completely untouched - narrating
// every comma/colon in normal prose would be "unbearable" per the ticket.
// Commas and decimal points are left as-is; TTS already pauses on commas and
// reads "3.14"/"-3" correctly on its own.
const _OPEN_WORDS  = { '[': ' open bracket ', '(': ' open parenthesis ', '{': ' open brace ' };
const _CLOSE_WORDS = { ']': ' close bracket ', ')': ' close parenthesis ', '}': ' close brace ' };
function narrateStructuredOutputLine(line) {
  const text = String(line == null ? '' : line);
  if (!/[[\](){}]/.test(text)) return text;
  let spoken = '';
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (_OPEN_WORDS[ch]) { depth++; spoken += _OPEN_WORDS[ch]; }
    else if (_CLOSE_WORDS[ch]) { depth = Math.max(0, depth - 1); spoken += _CLOSE_WORDS[ch]; }
    else if (ch === ':' && depth > 0) { spoken += ' colon '; }
    else { spoken += ch; }
  }
  return spoken.replace(/\s+/g, ' ').trim();
}

function shortenOutputForSpeech(text) {
  const cleaned = String(text || '').replace(/\s+/g, ' ').trim();
  if (cleaned.length <= CODEUP_SPOKEN_OUTPUT_LIMIT) return { text: cleaned, shortened: false };
  const clipped = cleaned.slice(0, CODEUP_SPOKEN_OUTPUT_LIMIT).replace(/\s+\S*$/, '').trim();
  return { text: clipped, shortened: true };
}
function formatRunOutputSpeech(output) {
  const raw = String(output == null ? '' : output);
  if (raw.trim() === _NO_OUTPUT_PLACEHOLDER) return 'Program ran successfully with no printed output.';
  const lines = raw.split('\n').map(s => s.trim()).filter(s => s.length > 0).map(narrateStructuredOutputLine);
  if (lines.length === 0) return 'Program ran successfully with no printed output.';
  const joined = lines.join(', ');
  if (joined.length <= CODEUP_SPOKEN_OUTPUT_LIMIT) {
    return 'Program output: ' + joined + _outputPeriod(joined);
  }
  const limited = shortenOutputForSpeech(joined);
  return 'Program output shortened for speech after ' + CODEUP_SPOKEN_OUTPUT_LIMIT + ' characters: ' +
    limited.text + _outputPeriod(limited.text) + ' Use the visible output, or choose Read output again to hear the complete output.';
}

function formatFullOutputSpeech(output) {
  const raw = String(output == null ? '' : output);
  if (raw.trim() === _NO_OUTPUT_PLACEHOLDER) return 'The program finished with no printed output.';
  const lines = raw.split('\n').map(s => s.trim()).filter(s => s.length > 0).map(narrateStructuredOutputLine);
  if (lines.length === 0) return 'No output available.';
  const joined = lines.join(', ');
  return 'Complete program output: ' + joined + _outputPeriod(joined);
}
// ==== SPOKEN-CODE-NORMALIZERS-END ====

function tutorialBuildingActivity() {
  return !!(window.TutorialController && window.TutorialController.active &&
            window.TutorialController.model &&
            window.TutorialController.model.stage === 'activity');
}

function insertAtCursor(text) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  if (!model.getValue().trim()) {
    model.setValue(text);
    const last = model.getLineCount();
    editor.setPosition({ lineNumber: last, column: model.getLineMaxColumn(last) });
    editor.revealLineInCenter(last);
    return;
  }
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
}

function insertClassVoice(className) {
  const name = className || 'MyClass';
  const code = `class ${name}:\n    def __init__(self):\n        pass`;
  insertAtCursor(code);
  speak(`Inserted class ${name} with an init method.`);
}

function insertLoopVoice(loopVar, iterable) {
  const v = loopVar  || 'i';
  const it = iterable || 'range(10)';
  if (tutorialBuildingActivity()) {
    insertAtCursor(`for ${v} in ${it}:`);
    speak(`Inserted a for loop header: for ${v} in ${it}. The next line must be indented; that is the action the loop repeats.`);
  } else {
    insertAtCursor(`for ${v} in ${it}:\n    pass`);
    speak(`Inserted for loop. Variable ${v} in ${it}. Replace pass with your loop body.`);
  }
  srAnnounce('For loop inserted');
}

function insertIfVoice(condition) {
  const cond = normalizeSpokenCondition(condition) || 'True';
  if (tutorialBuildingActivity()) {
    insertAtCursor(`if ${cond}:`);
    speak(`Inserted an if statement: if ${spokenConditionPhrase(cond)}. The next line must be indented, so it only runs when this is true.`);
  } else {
    insertAtCursor(`if ${cond}:\n    pass`);
    speak(`Inserted if statement checking ${spokenConditionPhrase(cond)}. Replace pass with your code.`);
  }
  srAnnounce('If statement inserted');
}

function insertWhileVoice(condition) {
  const cond = normalizeSpokenCondition(condition) || 'True';
  if (tutorialBuildingActivity()) {
    insertAtCursor(`while ${cond}:`);
    speak(`Inserted a while loop: while ${spokenConditionPhrase(cond)}. The next line must be indented. Remember to change something inside so the loop can stop.`);
  } else {
    insertAtCursor(`while ${cond}:\n    pass`);
    speak(`Inserted while loop checking ${spokenConditionPhrase(cond)}. Replace pass with your loop body, and change the condition inside so it can stop.`);
  }
  srAnnounce('While loop inserted');
}

function insertVariableVoice(name, value) {
  let n = String(name || '').trim();
  if (!/^[A-Za-z_]\w*$/.test(n)) n = 'value';
  const v = normalizeSpokenValue(value);
  insertAtCursor(`${n} = ${v}`);
  const isText = /^(['"])[\s\S]*\1$/.test(v);
  const spokenValue = isText
    ? `the text ${v.replace(/^['"]|['"]$/g, '')}`
    : `the value ${v}`;
  speak(`Inserted: ${n} equals ${spokenValue}.`);
}

function appendLineVoice(text) {
  if (!text) { speak('No text to append.'); return; }
  const code = normalizeSpokenCodeText(text);
  insertAtCursor(code);
  speak(`Inserted: ${spokenCodeReadback(code)}`);
}

function spokenCodeReadback(code) {
  const raw = String(code || '');
  const indentMatch = raw.match(/^(\s*)/);
  const spaces = indentMatch ? indentMatch[1].length : 0;
  const body = raw.trim();
  const prefix = spaces >= 4 ? 'indented, ' : '';
  return prefix + body;
}

function replaceLineVoice(lineNum, text) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (lineNum < 1 || lineNum > maxLine) { speak(`Line ${lineNum} is out of range.`); return; }
  const code = normalizeSpokenCodeText(text);
  const col = model.getLineMaxColumn(lineNum);
  model.pushEditOperations([], [{
    range: new monaco.Range(lineNum, 1, lineNum, col),
    text:  code,
  }], () => null);
  editor.setPosition({ lineNumber: lineNum, column: 1 });
  speak(`Replaced line ${lineNum} with: ${code}`);
}

function insertLineVoice(lineNum, text) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (lineNum < 1 || lineNum > maxLine + 1) { speak(`Line ${lineNum} is out of range.`); return; }
  const code = normalizeSpokenCodeText(text);
  model.pushEditOperations([], [{
    range: new monaco.Range(lineNum, 1, lineNum, 1),
    text:  code + '\n',
  }], () => null);
  editor.setPosition({ lineNumber: lineNum, column: 1 });
  speak(`Inserted at line ${lineNum}: ${code}`);
}

function addParameterVoice(paramName, functionName) {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const code  = getCode();
  const lines = code.split('\n');

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
}


let _lastSuggestions = [];
let _suggestionsLang = 'en';

async function suggestNextLine() {
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  if (!ensurePythonEditorContent('suggest next line')) return;
  const pos  = editor.getPosition() || { lineNumber: model.getLineCount() };
  showAI('Thinking of next lines...');
  speak('Analyzing context. Suggesting next lines.');

  try {
    const codeSnapshot = getCode();
    const result = await guardedJson('suggest-next', '/suggest-next', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: codeSnapshot, line: pos.lineNumber, language: getLanguage() }),
    }, narrationContext(codeSnapshot));
    if (staleResult(result)) return;
    const data = result.value;

    if (!data.success || !data.suggestions || data.suggestions.length === 0) {
      speak('Could not generate suggestions. Try writing a bit more code first.');
      hideAI(); return;
    }

    _lastSuggestions = data.suggestions;
    _suggestionsLang = getLanguage();

    const numbered = data.suggestions.map((s, i) => `${i + 1}: ${s}`).join('\n');
    out(`Suggested next lines:\n${numbered}\n\nSay "choose 1", "choose 2", or "choose 3" to insert.`, { sr: false });

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
}


async function tellExecutionStory() {
  SpeechManager.cancelAll();
  if (!ensurePythonEditorContent('execution story')) return;
  showAI('Narrating your execution...');
  speak('Narrating what happened when your code ran.');
  try {
    const codeSnapshot = getCode();
    const result = await guardedJson('execution-story', '/execution-story', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code: codeSnapshot, language: getLanguage() }),
    }, narrationContext(codeSnapshot));
    if (staleResult(result)) return;
    const data = result.value;
    if (data.success) {
      out(data.story, { sr: false });
      speak(data.story);
    } else {
      out(data.story || 'No story available.', { sr: false });
      speak(data.story || 'Run your code first, then ask for the story.');
    }
  } catch (e) {
    console.error(e);
    speak('Could not narrate execution. Please try again.');
  } finally {
    hideAI();
  }
}


let _breakpoints = new Set();
let _watchedVars = new Set();
let _breakpointDecorations = [];
let _breakpointsEnabled = true;

function refreshBreakpointDecorations() {
  if (!editor) return;
  _breakpointDecorations = editor.deltaDecorations(_breakpointDecorations, [
    ...Array.from(_breakpoints).map(line => ({
      range: new monaco.Range(line, 1, line, 1),
      options: {
        isWholeLine: true,
        className: 'bp-line',
        glyphMarginClassName: 'bp-glyph',
        glyphMarginHoverMessage: { value: `Breakpoint at line ${line}` },
      }
    }))
  ]);
}

function setBreakpoint(lineNum) {
  if (!lineNum) { speak('Please specify a line number for the breakpoint.'); return; }
  const model = getModel();
  if (!model) { speak('Editor not ready.'); return; }
  const maxLine = model.getLineCount();
  if (lineNum < 1 || lineNum > maxLine) { speak(`Line ${lineNum} is out of range.`); return; }

  _breakpoints.add(lineNum);
  refreshBreakpointDecorations();

  SonificationManager.playTone(600, 0.1, 0.1);
  speak(`Breakpoint set at line ${lineNum}.`);
  out(`Breakpoints active: ${Array.from(_breakpoints).sort((a,b)=>a-b).join(', ')}`, { sr: false });
}

function clearBreakpoints() {
  _breakpoints.clear();
  _watchedVars.clear();
  _breakpointDecorations = editor.deltaDecorations(_breakpointDecorations, []);
}

function listBreakpoints() {
  const lines = Array.from(_breakpoints).sort((a, b) => a - b);
  let message = 'You have no line breakpoints.';
  if (lines.length) {
    message = `You have breakpoints on lines ${lines.join(' and ')}.`;
    if (!_breakpointsEnabled) message += ' Breakpoints are disabled.';
  }
  out(message, { sr: false });
  speak(message);
}

function removeBreakpoint(lineNum) {
  if (!_breakpoints.has(lineNum)) {
    const message = `There is no breakpoint on line ${lineNum}.`;
    out(message, { sr: false }); speak(message);
    return;
  }
  _breakpoints.delete(lineNum);
  refreshBreakpointDecorations();
  const message = `Removed the breakpoint on line ${lineNum}.`;
  out(message, { sr: false }); speak(message);
}

function disableBreakpoints() {
  _breakpointsEnabled = false;
  out('Breakpoints disabled.', { sr: false });
  speak('Breakpoints disabled.');
}

function enableBreakpoints() {
  _breakpointsEnabled = true;
  out('Breakpoints enabled.', { sr: false });
  speak('Breakpoints enabled.');
}

function watchVariable(varName) {
  if (!varName) { speak('Please specify a variable name to watch.'); return; }
  _watchedVars.add(varName);
  speak(`Now watching variable ${varName}. I will report its value at each breakpoint.`);
  out(`Watched variables: ${Array.from(_watchedVars).join(', ')}`, { sr: false });
}

async function continueDebugging() {
  const data = await requestAudioBreakpoint('continue', null, {
    silentInactive: true,
    silentErrors: true,
  });
  if (data && data.success && data.continued) return;
  debugContinue();
}

function debugContinue() {
  if (!_breakpointsEnabled) { speak('Breakpoints are disabled.'); return; }
  const trace = window.executionTrace || [];
  if (!trace.length) { speak('No trace available. Run your code first.'); return; }

  const truncated = trace.some(e => e.type === 'overflow');
  if (truncated && _breakpoints.size > 0) {
    const bpLines = Array.from(_breakpoints).sort((a, b) => a - b);
    const linesHitInTrace = new Set(
      trace.filter(e => e.type === 'line_exec').map(e => e.line)
    );
    const unreachable = bpLines.filter(l => !linesHitInTrace.has(l));
    if (unreachable.length > 0) {
      speak(`Heads up: your trace was truncated at five thousand steps. Breakpoint${unreachable.length === 1 ? '' : 's'} at line ${unreachable.join(', line ')} may not be reachable. Try simplifying the loop and re-running.`);
    }
  }

  let idx = window.traceIndex || 0;
  let hitBreakpoint = false;

  while (idx < trace.length) {
    const event = trace[idx];
    idx++;
    if (event.type === 'line_exec' && _breakpoints.has(event.line)) {
      hitBreakpoint = true;
      window.traceIndex = idx;

      const stateEvents = trace.slice(0, idx).filter(e => e.type === 'state_change' && e.line === event.line);
      let varReport = '';
      if (_watchedVars.size > 0 && stateEvents.length > 0) {
        const lastState = stateEvents[stateEvents.length - 1];
        const relevant  = (lastState.changes || []).filter(c =>
          Array.from(_watchedVars).some(v => c.startsWith(v + ' '))
        );
        if (relevant.length) varReport = ' ' + relevant.join(', ');
      }

      SonificationManager.playTone(800, 0.15, 0.12);
      gotoLine(event.line, false);
      speak(`Hit breakpoint at line ${event.line}.${varReport || ''}`);
      return;
    }
  }

  if (!hitBreakpoint) {
    window.traceIndex = 0;
    speak('No more breakpoints hit. Execution complete.');
  }
}


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
  SpeechManager.cancelAll();
  const t = topic || 'Python basics';
  showAI(`Creating quiz on ${t}...`);
  speak(`Creating a quiz question on ${t}.`);
  try {
    const result = await guardedJson('learning', '/mentor/quiz', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ topic: t, language: getLanguage() }),
    }, narrationContext());
    if (staleResult(result)) return;
    const data = result.value;
    if (!data.success) { speak('Could not generate quiz. Try again.'); hideAI(); return; }

    const q = data.quiz;
    _currentQuiz = q;

    const display = `QUIZ: ${q.question}\n\n${q.options.join('\n')}\n\nSay "answer A", "answer B", or "answer C".`;
    out(display, { sr: false });
    speak(q.question);
    q.options.forEach(o => speak(o));
    speak('Say answer A, answer B, or answer C.');

    tabState()._pendingQuizAnswer = {
      answer: q.answer,
      explanation: q.explanation,
      expiresAt: Date.now() + 5 * 60 * 1000,
    };

  } catch (e) {
    console.error(e);
    speak('Quiz failed. Please try again.');
  } finally {
    hideAI();
  }
}

async function explainConcept(concept) {
  SpeechManager.cancelAll();
  const c = concept || 'variables';
  showAI(`Explaining ${c}...`);
  speak(`Explaining ${c}.`);
  try {
    const result = await guardedJson('learning', '/mentor/explain', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ concept: c, language: getLanguage() }),
    }, narrationContext());
    if (staleResult(result)) return;
    const data = result.value;
    if (data.success) {
      out(data.explanation, { sr: false });
      speak(data.explanation);
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
  SpeechManager.cancelAll();
  showAI('Generating bug challenge...');
  speak('Generating a bug fixing challenge. Get ready.');
  try {
    const result = await guardedJson('learning', '/mentor/bug-challenge', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ language: getLanguage() }),
    }, narrationContext());
    if (staleResult(result)) return;
    const data = result.value;
    if (!data.success) { speak('Could not generate challenge. Try again.'); hideAI(); return; }

    const ch = data.challenge;
    setCode(ch.code);
    out(`BUG CHALLENGE\n\nFind and fix the bug in the editor.\nHint: ${ch.hint}\n\nSay "show answer" when ready to reveal.`, { sr: false });
    speak(`Bug challenge loaded into editor. ${ch.hint}. Say show answer when you are ready.`);

    tabState()._pendingBugChallenge = {
      bug: ch.bug,
      fixed: ch.fixed,
      expiresAt: Date.now() + 10 * 60 * 1000,
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
  }
}

function showInputDialog(promptText, callback) {
  const overlay = document.getElementById('_cuInputDialog');
  const label = document.getElementById('_cuDialogLabel');
  const input = document.getElementById('_cuDialogInput');
  const ok = document.getElementById('_cuDialogOk');
  const cancel = document.getElementById('_cuDialogCancel');
  if (!overlay || !label || !input || !ok || !cancel) return;
  if (typeof overlay._cuClose === 'function') overlay._cuClose();

  label.textContent = promptText;
  overlay.setAttribute('aria-label', promptText);
  input.value = '';
  overlay.hidden = false;

  function confirm() {
    const val = input.value.trim();
    close();
    if (editor) editor.focus();
    if (val) callback(parseInt(val, 10) || 1);
  }
  function dismiss() {
    close();
    if (editor) editor.focus();
    speak('Cancelled.');
  }
  function close() {
    overlay.hidden = true;
    ok.removeEventListener('click', confirm);
    cancel.removeEventListener('click', dismiss);
    input.removeEventListener('keydown', onInputKeydown);
    overlay.removeEventListener('click', onOverlayClick);
    overlay.removeEventListener('keydown', onDialogKeydown);
    delete overlay._cuClose;
  }

  function focusableDialogControls() {
    return [input, ok, cancel].filter(el => el && !el.disabled && !el.hidden);
  }
  function onInputKeydown(e) {
    if (e.key === 'Enter')  { e.preventDefault(); confirm(); }
  }
  function onDialogKeydown(e) {
    if (e.key === 'Escape') { e.preventDefault(); dismiss(); return; }
    if (e.key !== 'Tab') return;
    const controls = focusableDialogControls();
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
  function onOverlayClick(e) {
    if (e.target === overlay) dismiss();
  }

  ok.addEventListener('click', confirm);
  cancel.addEventListener('click', dismiss);
  input.addEventListener('keydown', onInputKeydown);
  overlay.addEventListener('click', onOverlayClick);
  overlay.addEventListener('keydown', onDialogKeydown);
  overlay._cuClose = close;

  requestAnimationFrame(() => {
    input.focus();
    speak(promptText + '. Type a number and press Enter, or press Escape to cancel.');
  });
}
// ---------- LIST VARIABLES WITH VALUES (from execution trace) ----------
async function listVariablesWithValues() {
  const trace = window.executionTrace || [];
  if (!trace.length) {
    speak('No variables to report yet. Please run your code first by pressing Control Enter or saying "run".');
    out('No execution trace available. Run your code first.', { sr: false });
    return;
  }
  const vars = {};
  for (const event of trace) {
    if (event.type === 'state_change' && event.changes) {
      for (const change of event.changes) {
        const initMatch = change.match(/^(\w+) initialized to (.+)$/);
        const changeMatch = change.match(/^(\w+) changed from .+ to (.+)$/);
        if (initMatch) vars[initMatch[1]] = initMatch[2];
        else if (changeMatch) vars[changeMatch[1]] = changeMatch[2];
      }
    }
  }
  const names = Object.keys(vars);
  if (!names.length) {
    speak('Your code ran but no variables were declared.');
    out('No variables found in execution trace.', { sr: false });
    return;
  }
  let display = `VARIABLES AND VALUES (${names.length}):\n\n`;
  let speech = `You have ${names.length} variable${names.length === 1 ? '' : 's'}. `;
  for (const name of names) {
    display += `  ${name} = ${vars[name]}\n`;
    speech += `${pronounceVariableJS(name)} equals ${vars[name]}. `;
  }
  out(display, { sr: false });
  speak(speech);
}

function pronounceVariableJS(name) {
  if (!name || name.length !== 1) return name;
  const map = {a:'a',b:'bee',c:'see',d:'dee',e:'ee',f:'eff',g:'gee',h:'aitch',i:'eye',j:'jay',k:'kay',l:'ell',m:'em',n:'en',o:'oh',p:'pee',q:'cue',r:'arr',s:'ess',t:'tee',u:'you',v:'vee',w:'double-you',x:'ex',y:'why',z:'zee'};
  return map[name.toLowerCase()] || name;
}


async function walkThroughCode() {
  if (!ensurePythonEditorContent('walk through')) return;
  const code = getCode();
  if (!code.trim()) { speak('The editor is empty. Write some code first.'); return; }

  showAI('Walking through your code...');
  speak('Let me walk through your code.');
  const _walkEpoch = currentSpeechEpoch();
  try {
    const result = await guardedJson('walkthrough', '/walkthrough', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, language: getLanguage() }),
    }, narrationContext(code));
    if (staleResult(result)) return;
    const data = result.value;
    const explanation = data.explanation || data.speech || data.error || 'No walkthrough available.';
    out(explanation, { sr: false });
    speak(explanation, { epoch: _walkEpoch });
  } catch (e) {
    console.error(e);
    speak('Walkthrough failed.');
  } finally {
    setTimeout(() => hideAI(), 1200);
  }
}

(function _initVoiceEngineIntegration() {
  if (typeof VoiceEngine === 'undefined') return;

  VoiceEngine.setCommandHandler(async function (transcript) {
    await handleVoiceCommand(transcript);
  });

  VoiceEngine.setStreamUICallback(function (fullText, chunk) {
    const outputEl = document.getElementById('output');
    if (outputEl) {
      outputEl.textContent = fullText;
    }
  });

  const voiceLangSelect = document.getElementById('voiceLangSelector');
  if (voiceLangSelect) {
    const saved = VoiceEngine.Config.language || 'auto';
    voiceLangSelect.value = saved;

    voiceLangSelect.addEventListener('change', function () {
      const lang = this.value;
      VoiceEngine.configure({ language: lang });
      VoiceEngine.VoiceInput.setLanguage(lang === 'auto' ? 'en' : lang);
    });
  }

  const langSelector = document.getElementById('languageSelector');
  if (langSelector) {
    langSelector.addEventListener('change', function () {
      const lang = this.value;
      if (VoiceEngine.Config.language === 'auto') {
        VoiceEngine.VoiceInput.setLanguage(lang);
      }
    });
  }

  _debugLog('VoiceEngine integration initialized');
})();

async function talkToMentorStreaming(message, mode) {
  if (typeof VoiceEngine === 'undefined') {
    return talkToMentor(message, mode);
  }

  if (mode === 'repeat') {
    if (window.lastMentorReply) {
      showMentorReply('repeat that', window.lastMentorReply);
    } else {
      speak('There is no mentor reply to repeat yet.');
    }
    return;
  }
  if ((mode === 'shorter' || mode === 'simpler') && !window.lastMentorReply) {
    speak('There is no mentor reply to revise yet.');
    return;
  }

  let msg = message || 'Help me with my code.';
  if (mode === 'shorter') msg = 'Say your previous mentor reply shorter.';
  if (mode === 'simpler') msg = 'Say your previous mentor reply simpler.';

  if (!ensurePythonEditorContent('ask mentor')) return;

  showAI('Thinking...');
  const outputEl = document.getElementById('output');
  if (outputEl) outputEl.textContent = '';

  const context = getMentorContext();
  const guard = NarrationRequests.begin('mentor', narrationContext(context.code));
  const result = await VoiceEngine.streamingRequest('/mentor/chat-stream', {
    code: context.code,
    message: msg,
    output: context.output,
    error: context.error,
    language: context.language,
    mode: mode || 'general',
    history: context.history,
    preferences: context.preferences,
  });

  hideAI();

  if (result.aborted || !guard.active(narrationContext())) { guard.finish(); return; }
  guard.finish();

  if (result.error) {
    out('Mentor error: ' + result.error, { sr: false });
    speak('Sorry, I could not reach the mentor right now.');
  } else if (result.fullText) {
    window.mentorHistory.push({ role: 'assistant', content: result.fullText });
    if (window.mentorHistory.length > 20) window.mentorHistory.shift();
    window.lastMentorReply = result.fullText;
    maybePromptForApiKey(result.fullText);
  }
}
