let editor;
window._editorReady = false;
window._editorReadyQueue = [];
let audioCtx = null;
let snippetsCache = [];
let pendingConfirm = null; // { options: ["run","analyze"], expiresAt:timestamp, context: {} }
// ✅ MEMORY
let lastAction = null;
let lastPayload = null;
let lastSpokenText = null;
let isListening = false;
let recognition = null;
let _restartTimer = null;
let _loadingSnippets = false;


// ---------- APP STATE & MANAGERS ----------
const AppState = {
  isListening: false,
  isSpeaking: false,
  isExecuting: false,
};

// Speech manager: FIFO queue with optional priority and single-speaker lock
const SpeechManager = (function(){
  try {
    const queue = [];
    let currentUtterance = null;
    const settings = {
      maxAutoSpeakLength: 240,
      mode: localStorage.getItem('speech.mode') || 'concise'
    };

    function dequeue(){
      if(currentUtterance || !queue.length) return;
      speakNow(queue.shift());
    }

    function speakNow(item){
      if(!('speechSynthesis' in window) || !item || !item.text){
        if(item?.resolve) item.resolve();
        return;
      }

      AppState.isSpeaking = true;
      currentUtterance = new SpeechSynthesisUtterance(item.text);
      currentUtterance.rate = item.rate || 1;
      currentUtterance.pitch = item.pitch || 1;

      let finished = false;
      const cleanup = ()=>{
        if(finished) return;
        finished = true;
        AppState.isSpeaking = false;
        currentUtterance = null;
        if(item.timeout) clearTimeout(item.timeout);
        if(item.resolve) item.resolve();
        dequeue();
      };

      currentUtterance.onend = cleanup;
      currentUtterance.onerror = cleanup;
      item.timeout = setTimeout(cleanup, 30000);

      window.speechSynthesis.speak(currentUtterance);
    }

    function enqueue(text, opts = {}){
      return new Promise(resolve=>{
        queue.push({ text, ...opts, resolve });
        dequeue();
      });
    }

    function cancelAll(){
      queue.length = 0;
      try { window.speechSynthesis.cancel(); } catch(e){}
      AppState.isSpeaking = false;
      currentUtterance = null;
    }

    return { enqueue, cancelAll };

  } catch(e) {
    // Fallback: NO-OP speech manager (prevents crashes)
    return {
      enqueue: () => Promise.resolve(),
      cancelAll: () => {}
    };
  }
})();



// Sonification manager: cancelable jobs, respects reduced-motion preference
const SonificationManager = (function(){
  const jobs = new Map();
  const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function ensureAudio(){
    if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  
    // Always try to resume if suspended (Chrome policy)
    if(audioCtx.state === 'suspended') {
      audioCtx.resume().catch(e => console.warn('AudioContext resume failed:', e));
    }
    
    return audioCtx;
  }
  function playTone(freq, duration = 0.08, vol = 0.1){
    if(REDUCED) return;
    try{
      const ctx = ensureAudio();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = vol;
      osc.frequency.value = freq;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      const stopTime = ctx.currentTime + duration;
      osc.stop(stopTime);
      // Clean up nodes after playback
      setTimeout(() => {
        try {
          osc.disconnect();
          gain.disconnect();
        } catch(e){}
      }, (duration + 0.1) * 1000);
      return null;
    }catch(e){ return null; }
  }
  return {
    startJob(id){ jobs.set(id, []); },
    pushTimer(jobId, timerId){
      const arr = jobs.get(jobId);
      if(arr) arr.push(timerId);
    },
    cancelJob(jobId){
      const arr = jobs.get(jobId) || [];
      arr.forEach(t => clearTimeout(t));
      jobs.delete(jobId);
    },
    clearAll(){
      for(const [k,arr] of jobs.entries()){ arr.forEach(t=>clearTimeout(t)); }
      jobs.clear();
    },
    playTone
  };
})();

// Error beacon manager to avoid duplicates and allow auto-stop/downgrade
const ErrorBeaconManager = (function(){
  let intervalId = null;
  let currentLine = null;
  let currentSeverity = null;

  function start(line, severity='high'){
    stop();
    currentLine = line; currentSeverity = severity;
    const freq = severity === 'high' ? 800 : severity === 'medium' ? 600 : 400;
    const interval = severity === 'high' ? 2000 : severity === 'medium' ? 3000 : 4000;
    intervalId = setInterval(()=>{ SonificationManager.playTone(freq, 0.12, 0.05); }, interval);
  }

  function stop(){
    if(intervalId){ clearInterval(intervalId); intervalId = null; }
    currentLine = null; currentSeverity = null;
  }

  function downgrade(newSeverity){
    if(!currentLine) return;
    start(currentLine, newSeverity);
  }

  return { start, stop, downgrade, getState: ()=> ({active: !!intervalId, line: currentLine, severity: currentSeverity}) };
})();



// ---------- AUDIO ----------
function cueSuccess(){ if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(900,0.05,0.08); }
function cueError(){ if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(200,0.15,0.08); }

// Enhanced audio feedback with structure awareness
const TONES = {
  indent0: 200,   // Bass - top level
  indent1: 300,   // Low - one indent
  indent2: 450,   // Mid - two indents
  indent3: 600,   // High - three indents
  indent4: 800,   // Higher - deep nesting
  
  function: 900,  // Function definition
  class: 1000,    // Class definition
  loop: 400,      // Loop structure
  conditional: 500, // If/else
  comment: 150,   // Comment line
  blank: 100      // Blank line
};

function sonifyLine(lineContent, indentLevel) {
  const trimmed = lineContent.trim();
  
  // Play indent tone (pitch increases with depth)
  const indentTone = TONES[`indent${Math.min(indentLevel, 4)}`];
  if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(indentTone, 0.05, 0.08);
  
  // Play structure tone based on content
  setTimeout(() => {
    if (!trimmed) {
      if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(TONES.blank, 0.03, 0.05);
    } else if (trimmed.startsWith("#")) {
      if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(TONES.comment, 0.06, 0.06);
    } else if (trimmed.startsWith("def ")) {
      if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(TONES.function, 0.08, 0.1);
    } else if (trimmed.startsWith("class ")) {
      if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(TONES.class, 0.08, 0.12);
    } else if (trimmed.startsWith("for ") || trimmed.startsWith("while ")) {
      if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(TONES.loop, 0.08, 0.09);
    } else if (trimmed.startsWith("if ") || trimmed.startsWith("elif ") || trimmed.startsWith("else")) {
      if (typeof SonificationManager !== 'undefined') SonificationManager.playTone(TONES.conditional, 0.08, 0.09);
    }
  }, 60);
}

// Enhanced line reading with context
async function readLineEnhanced(line) {
  const model = getModel();
  if (!model) return;
  const maxLine = model.getLineCount();
  
  if (line < 1 || line > maxLine) {
    const msg = `Line ${line} is out of range. File has ${maxLine} lines.`;
    out(msg);
    speak(msg);
    return;
  }
  
  try {
    const res = await fetch("/read-line-context", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ code: getCode(), line })
    });
    
    const data = await res.json();
    
    if (data.success) {
      // Play sonification first
      sonifyLine(data.content, data.indent_level);
      
      // Then speak the detailed context
      setTimeout(() => {
        out(data.response);
        speak(data.response);
      }, 150);
    }
  } catch (e) {
    console.error(e);
    speak("Failed to read line context.");
  }
}

// Sonify entire function or block
async function sonifyCurrentBlock() {
  const model = getModel();
  if (!model) return;
  
  const pos = editor.getPosition() || { lineNumber: 1 };
  const currentLine = pos.lineNumber;
  const lines = getCode().split('\n');
  
  // Find block boundaries
  let startLine = currentLine;
  let endLine = currentLine;
  const currentIndent = (lines[currentLine - 1].length - lines[currentLine - 1].trimStart().length) / 4;
  
  // Scan backward for block start
  for (let i = currentLine - 2; i >= 0; i--) {
    const line = lines[i];
    const indent = (line.length - line.trimStart().length) / 4;
    if (indent < currentIndent && line.trim()) {
      startLine = i + 1;
      break;
    }
  }
  
  // Scan forward for block end
  for (let i = currentLine; i < lines.length; i++) {
    const line = lines[i];
    const indent = (line.length - line.trimStart().length) / 4;
    if (indent < currentIndent && line.trim()) {
      endLine = i + 1; // keep endLine as 1-based (inclusive/exclusive boundary)
      break;
    }
    if (i === lines.length - 1) endLine = i + 1;
  }
  
  // Cancel any previous sonification jobs
  if (typeof SonificationManager !== 'undefined') SonificationManager.clearAll();

  speak(`Sonifying block from line ${startLine} to line ${endLine}`);

  const jobId = Date.now();
  if (typeof SonificationManager !== 'undefined') SonificationManager.startJob(jobId);

  // Play audio representation of block structure (cancelable)
  let delay = 0;
  for (let i = startLine - 1; i < endLine; i++) {
    const line = lines[i];
    const indent = Math.floor((line.length - line.trimStart().length) / 4);

    const t = setTimeout(() => {
      try { sonifyLine(line, indent); } catch(e){}
    }, delay);

    if (typeof SonificationManager !== 'undefined') SonificationManager.pushTimer(jobId, t);

    delay += 120; // Stagger each line by 120ms
  }

  const finishTimer = setTimeout(() => {
    speak("Block sonification complete.");
    if (typeof SonificationManager !== 'undefined') SonificationManager.cancelJob(jobId);
  }, delay + 200);
  if (typeof SonificationManager !== 'undefined') SonificationManager.pushTimer(jobId, finishTimer);
}

// ==========================
// NAVIGATION HISTORY
// ==========================

let navigationHistory = [];
let historyIndex = -1;
const MAX_HISTORY = 50;

function recordNavigation(line) {
  // Don't record if it's the same as the last position
  if (navigationHistory.length > 0 && 
      navigationHistory[navigationHistory.length - 1] === line) {
    return;
  }
  
  navigationHistory.push(line);
  if (navigationHistory.length > MAX_HISTORY) {
    navigationHistory.shift();
  }
  historyIndex = navigationHistory.length - 1;
}

function navigateBack() {
  if (!ensureNotExecuting(()=> navigateBack(), 'navigate back')) return;
  if (historyIndex <= 0) {
    speak("Already at the oldest position in history.");
    return;
  }
  // stop any speech to avoid overlap
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  historyIndex--;
  const line = navigationHistory[historyIndex];
  gotoLine(line, false);
  speak(`Navigated back to line ${line}.`);
}

function navigateForward() {
  if (!ensureNotExecuting(()=> navigateForward(), 'navigate forward')) return;
  if (historyIndex >= navigationHistory.length - 1) {
    speak("Already at the newest position in history.");
    return;
  }
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  historyIndex++;
  const line = navigationHistory[historyIndex];
  gotoLine(line, false);
  speak(`Navigated forward to line ${line}.`);
}

function showNavigationHistory() {
  if (navigationHistory.length === 0) {
    speak("Navigation history is empty.");
    out("Navigation history is empty.");
    return;
  }
  
  const history = navigationHistory
    .slice(-10) // Last 10 positions
    .map((line, i) => `${i + 1}. Line ${line}`)
    .join("\n");
  
  out(`Recent navigation history:\n${history}`);
  speak(`You have ${navigationHistory.length} positions in history. Showing last 10.`);
  speak(history);
}

// ==========================
// VARIABLE TRACKING
// ==========================

async function listVariables() {
  if (!ensureNotExecuting(()=> listVariables(), 'list variables')) return;
  const model = getModel();
  if (!model) return;
  
  const pos = editor.getPosition() || { lineNumber: 1 };
  const currentLine = pos.lineNumber;
  
  showAI("Analyzing variables...");
  speak("Analyzing variables in scope.");
  
  try {
    const res = await fetch("/track-variables", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ code: getCode(), line: currentLine })
    });
    
    const data = await res.json();
    
    if (data.success) {
      const vars = data.variables;
      
      if (vars.length === 0) {
        out(`No variables found in ${data.current_scope}.`);
        speak(`No variables found in ${data.current_scope}.`);
      } else {
        const varList = vars.map(v => 
          `${v.name} (${v.phonetic}): used ${v.usage_count} times, defined at line ${v.first_line}`
        ).join("\n");
        
        out(`Variables in ${data.current_scope}:\n\n${varList}`);
        speak(`Found ${vars.length} variables in ${data.current_scope}.`);
        
        // Speak first 5 variables
        if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
        vars.slice(0, 5).forEach(v => {
          speak(`${v.phonetic}, used ${v.usage_count} times.`);
        });
        
        if (vars.length > 5) {
          speak(`And ${vars.length - 5} more. Check output for full list.`);
        }
      }
    } else {
      out(data.message || "Failed to analyze variables.");
      speak(data.message || "Failed to analyze variables.");
    }
  } catch (e) {
    console.error(e);
    out("Variable tracking failed.");
    speak("Variable tracking failed.");
  } finally {
    hideAI();
  }
}

async function findVariable(varName) {
  if (!varName) {
    speak("Please specify a variable name.");
    return;
  }
  
  showAI(`Finding variable ${varName}...`);
  speak(`Finding all uses of ${varName}.`);
  
  try {
    const res = await fetch("/find-variable-usage", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ code: getCode(), variable: varName })
    });
    
    const data = await res.json();
    
    if (data.success) {
      const usageList = data.usages.map(u => 
        `Line ${u.line} (${u.type}): ${u.content}`
      ).join("\n");
      
      out(`Variable '${varName}' (${data.phonetic}):\nFound ${data.count} usages:\n\n${usageList}`);
      speak(`Found ${data.count} usages of ${data.phonetic}.`);
      
      // Speak first 3 usages
      data.usages.slice(0, 3).forEach(u => {
        speak(`Line ${u.line}: ${u.type}.`);
      });
      
      if (data.count > 3) {
        speak(`And ${data.count - 3} more. Check output for details.`);
      }
      
      // Jump to first usage (system jump - don't record in user history)
      if (data.usages.length > 0) {
        gotoLine(data.usages[0].line, false);
      }
    } else {
      out(data.message);
      speak(data.message);
    }
  } catch (e) {
    console.error(e);
    out("Variable search failed.");
    speak("Variable search failed.");
  } finally {
    hideAI();
  }
}

// ==========================
// ERROR LOCATION BEACON (managed by ErrorBeaconManager)
// ==========================

async function checkSyntaxErrors() {
  showAI("Checking for errors...");
  speak("Checking code for errors.");
  
  try {
    const res = await fetch("/check-syntax", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ code: getCode() })
    });
    
    const data = await res.json();
    
    if (data.success && !data.has_errors) {
      out("✓ No errors detected. Code looks good!");
      speak("No errors detected. Code looks good!");
      stopErrorBeacon();
    } else if (data.success && data.has_errors) {
      const errorList = data.errors.map(e => 
        `Line ${e.line || "unknown"}: ${e.type} - ${e.message}`
      ).join("\n");
      
      out(`⚠ Found ${data.error_count} error(s):\n\n${errorList}`);
      speak(`Found ${data.error_count} errors.`);
      
      // Speak errors
      data.errors.forEach(e => {
        speak(`${e.type} on line ${e.line || "unknown"}.`);
      });
      
      // Start error beacon (managed) and jump to first error without polluting history
      if (data.errors.length > 0 && data.errors[0].line > 0) {
        ErrorBeaconManager.start(data.errors[0].line, data.errors[0].severity);
      }
      
      // Jump to first error (system jump - don't record in user history)
      if (data.errors.length > 0 && data.errors[0].line > 0) {
        gotoLine(data.errors[0].line, false);
      }
    }
  } catch (e) {
    console.error(e);
    out("Syntax check failed.");
    speak("Syntax check failed.");
  } finally {
    hideAI();
  }
}

function stopErrorBeacon() {
  ErrorBeaconManager.stop();
  window.executionTrace = [];
}

function locateError() {
  checkSyntaxErrors(); // This will auto-jump to error
}

// ==========================
// QUICK WINS
// ==========================

function showHelp() {
  if (!ensureNotExecuting(()=> showHelp(), 'show help')) return;
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
- "what just happened?" (explain last action)
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
  speak("Help menu displayed. Check the output panel for all commands.");
}

function getFileStats() {
  const model = getModel();
  if (!model) return;
  
  const lineCount = model.getLineCount();
  const code = getCode();
  const charCount = code.length;
  const wordCount = code.split(/\s+/).filter(w => w.length > 0).length;
  
  const stats = `File statistics:
- ${lineCount} lines
- ${wordCount} words
- ${charCount} characters`;
  
  out(stats);
  speak(`File has ${lineCount} lines, ${wordCount} words.`);
}

function goToTop() {
  gotoLine(1);
  speak("Jumped to top of file.");
}

function goToBottom() {
  const model = getModel();
  if (!model) return;
  const lastLine = model.getLineCount();
  gotoLine(lastLine);
  speak("Jumped to bottom of file.");
}

function copyCode() {
  if (!ensureNotExecuting(()=> copyCode(), 'copy code')) return;
  const code = getCode();
  navigator.clipboard.writeText(code).then(() => {
    speak("Code copied to clipboard.");
    out("Code copied to clipboard.");
  }).catch(() => {
    speak("Failed to copy code.");
  });
}

function pasteCode() {
  if (!ensureNotExecuting(()=> pasteCode(), 'paste code')) return;
  navigator.clipboard.readText().then(text => {
    setCode(text);
    speak("Code pasted from clipboard.");
    out("Code pasted from clipboard.");
  }).catch(() => {
    speak("Failed to paste code.");
  });
}

// ---------- TTS ----------
// Central speak wrapper using SpeechManager queue
function speak(text, opts={}){
  if (!text) return;
  lastSpokenText = text;
  if (typeof SpeechManager !== 'undefined') {
    SpeechManager.enqueue(text, opts).catch(()=>{});
  } else {
    // fallback
    if (!('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.rate = opts.rate || 1;
    u.pitch = opts.pitch || 1;
    try { window.speechSynthesis.speak(u); } catch(e){}
  }
}
function speakOutput(){
  if (!ensureNotExecuting(()=> speakOutput(), 'speak output')) return;
  const t = document.getElementById("output").textContent || "No output available.";
  speak(t, { forceFull: true });
}
function repeatLastSpeech(){
  if(lastSpokenText){
    speak(lastSpokenText, { forceFull: true });
  } else {
    speak("There is nothing to repeat yet.");
  }
}

// ---------- AI BUBBLE ----------
function showAI(msg){
  const b = document.getElementById("aiBubble");
  if (!b) return;
  b.textContent = msg;
  b.style.display = "block";
}
function hideAI(){
  const b = document.getElementById("aiBubble");
  if (!b) return;
  b.style.display = "none";
}

// ---------- MONACO ----------
window.MonacoEnvironment = {
  getWorkerUrl: function () {
    return "/static/python.worker.js";
  }
};

require.config({
  paths: { "vs": "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs" }
});

require(["vs/editor/editor.main"], function () {
  if (editor) {
    console.warn('Editor already initialized, skipping');
    return;
  }
  
  editor = monaco.editor.create(document.getElementById("editor"), {
    value: 'print("Hello CodeUp!")',
    language: "python",
    theme: "vs-dark",
    fontSize: 16,
    minimap: { enabled: false },
    automaticLayout: true
  });
  
  // mark editor ready and flush queued functions
  window._editorReady = true;
  try{ window._resolveEditorReady(); } catch(e){}
  while(window._editorReadyQueue && window._editorReadyQueue.length){
    const fn = window._editorReadyQueue.shift();
    try{ fn(); } catch(e){ console.warn('editor queued fn failed', e); }
  }
  
  // Register shortcuts ONCE here
  registerEditorShortcuts();
  loadSnippets();
});

// ---------- HELPER: MODEL ----------
function getModel(){ return editor?.getModel(); }

// ---------- HELPERS ----------
function getCode(){ return editor?.getValue() || ""; }
function getLanguage(){ 
  return document.getElementById("languageSelector")?.value || "en"; 
}
function setCode(v){
  if (typeof ErrorBeaconManager !== 'undefined') ErrorBeaconManager.stop();
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  // Reset memory/state on code mutation
  lastAction = null;
  lastPayload = null;
  lastSpokenText = null;
  navigationHistory = [];
  historyIndex = -1;
  window.executionTrace = [];
  window.traceIndex = 0;
  if(editor) editor.setValue(v);
}
function out(t){ document.getElementById("output").textContent = t; }

// Pending actions queued while executing
const pendingActions = [];
async function flushPendingActions(){
  while(pendingActions.length){
    const a = pendingActions.shift();
    try{ await a(); } catch(e){ console.error('Pending action failed', e); }
  }
}
function enqueueAfterExecution(fn){
  pendingActions.push(fn);
  if (typeof SpeechManager !== 'undefined') SpeechManager.enqueue('Queued your request; will run after execution completes.');
}

// helper guard
function ensureNotExecuting(actionFn, description){
  if (AppState.isExecuting){
    enqueueAfterExecution(actionFn);
    return false;
  }
  return true;
}

// ---------- RUN ----------
async function runCode(){
  // start execution: cancel other speech to maintain atomicity
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  AppState.isExecuting = true;
  cueSuccess();
  out("Running...");
  showAI("Running code...");
  speak("Running code.");
  try{
    const res = await fetch("/run", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ code: getCode(), language: getLanguage() })
    });
    const data = await res.json();
    window.executionTrace = (data.trace || []).slice(0, 1000);

    window.traceIndex = 0;

    if(data.success){
      out(data.output);
      cueSuccess();
      // stop any active error beacon when run succeeds
      if (typeof ErrorBeaconManager !== 'undefined') ErrorBeaconManager.stop();
      speak("Program output:");
      speak(data.output);
      if (data.semantic_issues?.length) {
        data.semantic_issues.forEach(e => {
          speak(`${e.category}. ${e.message}`);
        });
      }
    } else {
      out("ERROR:\n" + (data.error || ""));
      cueError();
      if (data.explanation){
        speak("There was an error.");
        speak(data.explanation);
      } else {
        speak("There was an error.");
      }
    }
  } catch(e){
    out("System error.");
    console.error(e);
    cueError();
    speak("System error.");
  } finally {
    AppState.isExecuting = false;
    hideAI();
    // Process any queued actions now that execution is done
    await flushPendingActions();
  }
}

// ---------- ANALYZE ----------
async function analyzeCode(){
  cueSuccess();
  out("Analyzing...");
  showAI("Analyzing code with AI...");
  speak("Analyzing code.");
  try{
    const res = await fetch("/analyze", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ code: getCode(), language: getLanguage() })
    });
    const data = await res.json();
    out(data.analysis || "No analysis.");
    if (data.analysis){
      speak("Analysis ready.");
      speak(data.analysis);
    } else {
      speak("No analysis available.");
    }
  } catch(e){
    out("Analyze failed.");
    console.error(e);
    cueError();
    speak("Analyze failed.");
  } finally {
    speak("Task completed.");
    hideAI();
  }
}

// ---------- SUMMARIZE FILE ----------
async function summarizeFile(){
  cueSuccess();
  out("Summarizing file...");
  showAI("Summarizing this file...");
  speak("Summarizing this file.");

  try{
    const res = await fetch("/summarize", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ code: getCode(), language: getLanguage() })
    });

    const data = await res.json();
    const summary = data.summary || "No summary generated.";
    out(summary);
    speak(summary);

  } catch(e){
    console.error(e);
    out("Summary failed.");
    speak("Summary failed.");
  } finally {
    speak("Task completed.");
    hideAI();
  }
}


// ---------- ADVISE ----------
async function adviseCode(){
  cueSuccess();
  out("Advising on your code...");
  showAI("Generating improvement suggestions...");
  speak("Advising on your code.");
  try{
    const res = await fetch("/advise", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ code: getCode(), language: getLanguage() })
    });
    const data = await res.json();
    const advice = data.advice || "No advice generated.";
    out(advice);
    speak("Here are some ways you can improve this code.");
    speak(advice);
  } catch(e){
    out("Advice failed.");
    console.error(e);
    cueError();
    speak("Advice failed.");
  } finally {
    speak("Task completed.");
    hideAI();
  }
}

// ---------- FIX WITH DIFF ----------
async function fixCode(){
  const before = getCode();

  cueSuccess();
  out("Fixing...");
  showAI("Fixing code with AI...");
  speak("Fixing code.");

  try{
    const res = await fetch("/fix", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ code: before, language: getLanguage() })
    });

    const data = await res.json();

    if(data.success){
      const after = data.code;
      setCode(after);

      out("Code fixed.");
      speak("Code has been fixed.");

      const diffRes = await fetch("/diff-explain", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ before, after, language: getLanguage() })
      });

      const diffData = await diffRes.json();
      if(diffData.explanation){
        out(diffData.explanation);
        speak(diffData.explanation);
      }

    } else {
      out("Fix failed.");
      speak("Fix failed.");
    }

  } catch(e){
    console.error(e);
    out("Fix failed.");
    speak("Fix failed.");
  } finally {
    hideAI();
  }
}


// ---------- DESCRIBE LINE ----------
async function describeLine(line){
  showAI("Describing line " + line);
  speak("Describing line " + line);
  try{
    const res = await fetch("/describe", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ code: getCode(), line, language: getLanguage() })
    });
    const data = await res.json();
    if(data.success){
      out(data.description);
      speak(data.description);
    } else {
      const msg = data.message || "Describe failed.";
      out(msg);
      speak(msg);
    }
  } catch(e){
    out("Describe failed.");
    console.error(e);
    cueError();
    speak("Describe failed.");
  } finally {
    hideAI();
  }
}

// ---------- GENERATE CODE (NL -> CODE) ----------
async function generateCode(prompt){
  showAI("Generating code for: " + prompt);
  speak("Generating code for " + prompt);
  if(getCode().trim()){
    speak("Warning. This will overwrite the current code.");
  }

  try{
    const res = await fetch("/generate-code", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ prompt, language: getLanguage() })
    });
    const data = await res.json();
    if (data.success && data.code){
      window.executionTrace = [];
      window.traceIndex = 0;
      setCode(data.code);
      out("Code generated and inserted into editor.");
      cueSuccess();
      speak("Code generated and inserted into the editor.");
    } else {
      out("Code generation failed.");
      cueError();
      speak("Code generation failed.");
    }
  } catch(e){
    console.error(e);
    out("Code generation failed.");
    cueError();
    speak("Code generation failed.");
  } finally {
    hideAI();
  }
}

// ---------- BASIC NAVIGATION ----------
function gotoLine(line, record = true){
  if (!ensureNotExecuting(()=> gotoLine(line, record), 'go to line')) return;
  const model = getModel();
  if (!model) {
    const msg = "Editor not ready.";
    out(msg);
    speak(msg);
    return;
  }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine){
    const msg = `Line ${line} is out of range. File has ${maxLine} lines.`;
    out(msg);
    speak(msg);
    return;
  }
  
  if (record) recordNavigation(line); // ✅ Record for history unless caller disabled it

  try {
    editor.setPosition({ lineNumber: line, column: 1 });
  } catch(e) {
    console.warn('gotoLine: editor.setPosition failed', e);
    return;
  }
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  speak(`Moved to line ${line}. ${text || "Empty line."}`);
}

function readLine(line){
  if (!ensureNotExecuting(()=> readLine(line), 'read line')) return;
  const model = getModel();
  if (!model) {
    const msg = "Editor not ready.";
    out(msg);
    speak(msg);
    return;
  }
  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine){
    const msg = `Line ${line} is out of range. File has ${maxLine} lines.`;
    out(msg);
    speak(msg);
    return;
  }
  // Cancel current speech to avoid overlapping with the line read
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Line ${line}: ${text || "Empty line."}`);
}

function readCurrentLine(){
  if (!ensureNotExecuting(()=> readCurrentLine(), 'read current line')) return;
  const model = getModel();
  if (!model) {
    const msg = "Editor not ready.";
    out(msg);
    speak(msg);
    return;
  }
  const pos = editor.getPosition() || { lineNumber: 1 };
  const line = pos.lineNumber;
  // Cancel current speech
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Current line ${line}: ${text || "Empty line."}`);
}

function nextLine(){
  if (!ensureNotExecuting(()=> nextLine(), 'next line')) return;
  const model = getModel();
  if (!model) {
    const msg = "Editor not ready.";
    out(msg);
    speak(msg);
    return;
  }
  const pos = editor.getPosition() || { lineNumber: 1 };
  const maxLine = model.getLineCount();
  const line = Math.min(maxLine, pos.lineNumber + 1);
  // Cancel current speech
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  editor.setPosition({ lineNumber: line, column: 1 });
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Line ${line}: ${text || "Empty line."}`);
}

function prevLine(){
  if (!ensureNotExecuting(()=> prevLine(), 'previous line')) return;
  const model = getModel();
  if (!model) {
    const msg = "Editor not ready.";
    out(msg);
    speak(msg);
    return;
  }
  const pos = editor.getPosition() || { lineNumber: 1 };
  const line = Math.max(1, pos.lineNumber - 1);
  // Cancel current speech
  if (typeof SpeechManager !== 'undefined') SpeechManager.cancelAll();
  editor.setPosition({ lineNumber: line, column: 1 });
  editor.revealLineInCenter(line);
  const text = model.getLineContent(line);
  out(`Line ${line}: ${text}`);
  speak(`Line ${line}: ${text || "Empty line."}`);
}

// ---------- BASIC EDITING ----------
function clearEditor(){
  setCode("");
  out("Editor cleared.");
  speak("Editor cleared. Previous code erased.");
}

function deleteLine(line){
  const model = getModel();
  if (!model) return;

  const maxLine = model.getLineCount();
  if (line < 1 || line > maxLine){
    const msg = `Line ${line} is out of range.`;
    out(msg); speak(msg);
    return;
  }

  const endLine = Math.min(line + 1, maxLine + 1);
  const text = model.getLineContent(line);

  model.pushEditOperations([], [{
    range: new monaco.Range(line, 1, endLine, 1),
    text: ""
  }], () => null);

  out(`Deleted line ${line}: ${text}`);
  speak(`Deleted line ${line}.`);
}


// ---------- SNIPPETS ----------
async function saveSnippet(){
  const name = prompt("Snippet name:") || "Untitled";
  await saveSnippetWithName(name);
}

async function saveSnippetWithName(name){
  await fetch("/snippets", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ name, code: getCode() })
  });
  await loadSnippets();
  speak(`Snippet saved as ${name}.`);
}

async function loadSnippets(){
  if (_loadingSnippets) return;
  _loadingSnippets = true;

  const list = document.getElementById("snippetList");
  if (!list) {
    _loadingSnippets = false;
    return;
  }

  try {
    const res = await fetch("/snippets");
    const data = await res.json();
    snippetsCache = data.snippets || [];

    // Build off-DOM
    const fragment = document.createDocumentFragment();
    snippetsCache.forEach(sn=>{
      const div = document.createElement("div");
      div.className = "snippet-item";
      div.textContent = sn.name || "Untitled Snippet";
      div.dataset.id = sn.id;
      div.onclick = ()=> setCode(sn.code);
      fragment.appendChild(div);
    });

    // Atomic replace
    list.innerHTML = "";
    list.appendChild(fragment);
  } finally {
    _loadingSnippets = false;
  }
}

async function loadSnippetById(id){
  const sn = snippetsCache.find(s => String(s.id) === String(id));
  if (!sn){
    speak(`Snippet ${id} not found.`);
    out(`Snippet ${id} not found.`);
    return;
  }
  setCode(sn.code);
  speak(`Loaded snippet ${id}: ${sn.name}.`);
  out(`Loaded snippet ${id}: ${sn.name}.`);
}

async function deleteSnippetById(id){
  await fetch(`/snippets/${id}`, {
    method: "DELETE"
  });
  await loadSnippets();
  speak(`Deleted snippet ${id}.`);
  out(`Deleted snippet ${id}.`);
}

async function renameSnippetById(id, newName){
  await fetch(`/snippets/${id}`, {
    method: "PUT",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ name: newName })
  });
  await loadSnippets();
  speak(`Renamed snippet ${id} to ${newName}.`);
  out(`Renamed snippet ${id} to ${newName}.`);
}

// ---------- COMMAND HANDLER (VOICE + TYPED) ----------
async function handleConfirmedAction(action, payload){
    // ✅ STORE LAST ACTION (EXCEPT WHEN REPEATING)
  if(action !== "repeat_last_action"){
    lastAction = action;
    lastPayload = payload;
  }

  // ✅ REPEAT LAST ACTION
  if(action === "repeat_last_action"){
    if(lastAction){
      speak("Repeating last action.");
      await handleConfirmedAction(lastAction, lastPayload);
    } else {
      speak("No previous action to repeat.");
    }
    return;
  }

  // ✅ REPEAT LAST SPOKEN MESSAGE
  if(action === "repeat_last_speech"){
    repeatLastSpeech();
    return;
  }
    // ✅ EXPLAIN LAST ACTION
  if(action === "explain_last_action"){
    if(!lastAction){
      speak("No action has been performed yet.");
      out("No action has been performed yet.");
      return;
    }

    const usedAI = [
      "analyze",
      "fix",
      "advise",
      "summarize",
      "generate_code"
    ].includes(lastAction);

    const explanation =
      `Last action was ${lastAction.replace(/_/g, " ")}. ` +
      (usedAI
        ? "This action used artificial intelligence. "
        : "This action was deterministic. ") +
      "The result is shown in the output panel.";

    speak(explanation);
    out(explanation);
    return;
  }


  if(action === "run")             await runCode();
  else if(action === "analyze")    await analyzeCode();
  else if(action === "fix")        await fixCode();
  else if(action === "speak")      speakOutput();
  else if(action === "read_output") speakOutput();
  else if(action === "describe_line") await describeLine(payload?.line || 1);
  else if(action === "generate_code") await generateCode(payload?.prompt || "");
  else if(action === "save_snippet_named") await saveSnippetWithName(payload?.name || "Untitled");
  else if(action === "load_snippet")   await loadSnippetById(payload?.id);
  else if(action === "delete_snippet") await deleteSnippetById(payload?.id);
  else if(action === "rename_snippet") await renameSnippetById(payload?.id, payload?.new_name || "Renamed");
  else if(action === "goto_line")      gotoLine(payload?.line || 1);
  else if(action === "read_line")      readLine(payload?.line || 1);
  else if(action === "read_current_line") readCurrentLine();
  else if(action === "next_line")      nextLine();
  else if(action === "prev_line")      prevLine();
  else if(action === "clear_editor")   clearEditor();
  else if(action === "delete_line")    deleteLine(payload?.line || 1);
  else if(action === "summarize")     await summarizeFile();
  else if(action === "advise")         await adviseCode();
  else if(action === "read_line_enhanced") await readLineEnhanced(payload?.line || editor.getPosition().lineNumber);
  else if(action === "sonify_block") await sonifyCurrentBlock();
  else if(action === "list_variables") await listVariables();
  else if(action === "find_variable") await findVariable(payload?.variable || "");
  else if(action === "check_errors") await checkSyntaxErrors();
  else if(action === "locate_error") locateError();
  else if(action === "stop_beacon") {
    stopErrorBeacon();
    speak("Error beacon stopped.");
  }
  else if(action === "go_back") navigateBack();
  else if(action === "go_forward") navigateForward();
  else if(action === "show_history") showNavigationHistory();
  else if(action === "help") showHelp();
  else if(action === "file_stats") getFileStats();
  else if(action === "go_to_top") goToTop();
  else if(action === "go_to_bottom") goToBottom();
  else if(action === "copy_code") copyCode();
  else if(action === "paste_code") pasteCode();
}

function tryResolveConfirmation(txt){
  if(!pendingConfirm) return false;
  
  // expire stale confirmations
  if(pendingConfirm.expiresAt && Date.now() > pendingConfirm.expiresAt){
    pendingConfirm = null;
    speak("Confirmation timed out. Please repeat the command if you still want to proceed.");
    return true;
  }
  
  const options = pendingConfirm.options || [];
  const lower = txt.toLowerCase();

  let chosen = null;
  for(const opt of options){
    if(lower.includes(opt)) chosen = opt;
  }
  
  if(!chosen){
    speak("I did not understand your choice. Please say one of: " + options.join(" or "));
    return true;
  }
  
  pendingConfirm = null;
  speak("Confirmed " + chosen + ".");
  handleConfirmedAction(chosen, {});
  return true;
}

async function handleCommandText(txt){
  const field = document.getElementById("voiceText");

  // 🚫 HARD BLOCK: while voice is active, typed commands are ignored
  // AppState.isListening is the single source of truth
  if (AppState.isListening) {
    speak("Voice control is active. Please speak your command.");
    return;
  }

  if (field) field.value = txt;

  // If we're waiting for confirmation, try to resolve it locally
  if (pendingConfirm) {
    const handled = tryResolveConfirmation(txt);
    if (handled) return;
  }

  try {
    const res = await fetch("/voice-command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: txt })
    });

    const data = await res.json();

    if (!data.success) {
      speak(data.message || "Command not recognized.");
      return;
    }

    const action = data.action;

    if (action === "unknown") {
      const heard = data.heard || txt;
      speak(
        `I heard "${heard}", but I could not match it to a command. ` +
        `Say help to hear available commands.`
      );
      out(`Unrecognized command: "${heard}"`);
      return;
    }

    if (action === "confirm") {
      pendingConfirm = {
        options: data.options || [],
        expiresAt: Date.now() + 15000,
        context: { heard: data.heard || txt, raw: txt }
      };

      const opts = (data.options || []).join(" or ");
      const heard = data.heard || txt;

      speak(
        `I am not fully sure what you meant. I heard "${heard}". ` +
        `Did you mean ${opts}?`
      );

      out(
        `Command ambiguous.\n` +
        `Heard: "${heard}"\n` +
        `Possible actions: ${opts}\n` +
        `Please say one option to confirm.`
      );

      return;
    }

    await handleConfirmedAction(action, data);

  } catch (err) {
    console.error(err);
    speak("Voice command failed.");
  }
}


async function submitCommand(){
  const field = document.getElementById("voiceText");
  if (!field) return;
  const txt = field.value.trim();
  if (!txt) return;
  await handleCommandText(txt);
  field.value = "";  // ✅ Clear after submission
}

// ---------- VOICE (CONVERSATIONAL LOOP) ----------
function toggleVoice(){
  if(isListening){
    stopListening();
  } else {
    startListening();
  }
}

function startListening(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){
    alert("Speech recognition not supported in this browser.");
    speak("Speech recognition not supported.");
    return;
  }

  if(isListening){
    speak("Already listening.");
    return;
  }

  recognition = new SR();
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = "en-US";
  recognition._restartAttempts = 0;
  recognition._maxRestarts = 5;
  recognition._backoffBase = 300;

  recognition.onstart = () => {
    isListening = true;
    AppState.isListening = true;
    recognition._restartAttempts = 0;
    const btn = document.getElementById("voiceButton");
    if(btn) btn.textContent = "🎤 Voice (ON)";
    cueSuccess();
    speak(
      "Voice control activated. " +
      "I will ask for confirmation if I am unsure of your intent. " +
      "Say help at any time to hear available commands."
    );
    console.log("✅ Voice: Listening started");
  };

  recognition.onresult = async (event) => {
    const transcript = event.results[event.results.length - 1][0].transcript;
    console.log("🎤 Heard:", transcript);
    await handleVoiceCommand(transcript);
  };

  recognition.onerror = (event) => {
    console.error("❌ Voice error:", event.error);
    // gentle handling: no-speech is benign; aborted means user stopped
    if(event.error === "no-speech"){
      speak("No speech detected. Still listening.");
      return;
    }
    if(event.error === "aborted"){
      isListening = false;
      AppState.isListening = false;
      return;
    }

    // Otherwise try to restart with backoff, but avoid restart storms
    recognition._restartAttempts = (recognition._restartAttempts || 0) + 1;
    if(recognition._restartAttempts > recognition._maxRestarts){
      speak("Voice recognition repeatedly failed. Voice control is paused.");
      isListening = false;
      AppState.isListening = false;
      return;
    }

    const delay = recognition._backoffBase * Math.pow(2, recognition._restartAttempts - 1);
    speak("Voice recognition error. Attempting to restart.");
    setTimeout(() => {
      try{ if(isListening) recognition.start(); } catch(e){ console.error('Restart failed', e); }
    }, delay);
  };

  recognition.onend = () => {
    console.log("🔄 Voice: Session ended");
    if(!isListening) return;
    
    recognition._restartAttempts = (recognition._restartAttempts || 0) + 1;
    if(recognition._restartAttempts > recognition._maxRestarts){
      speak("Voice recognition stopped after repeated failures.");
      isListening = false;
      AppState.isListening = false;
      return;
    }

    // 🔒 clear any pending restart
    if (_restartTimer) {
      clearTimeout(_restartTimer);
      _restartTimer = null;
    }

    const delay = recognition._backoffBase * Math.pow(2, recognition._restartAttempts - 1);
    _restartTimer = setTimeout(() => {
      _restartTimer = null;
      try { if(isListening) recognition.start(); }
      catch(e){ console.error("Auto-restart failed", e); }
    }, delay);
  };

    try {
      recognition.start();
    } catch(e){
      console.error("Failed to start recognition:", e);
      speak("Failed to start voice control.");
      isListening = false;
      AppState.isListening = false;
    }
  }
function stopListening(){
  if(!recognition || !isListening){
    speak("Voice control is not active.");
    return;
  }
  if (_restartTimer) {
    clearTimeout(_restartTimer);
    _restartTimer = null;
  }
  isListening = false;
  AppState.isListening = false;
  const btn = document.getElementById("voiceButton");
  if(btn) btn.textContent = "🎤 Voice (Off)";
  recognition.stop();
  speak("Voice control deactivated.");
  console.log("🛑 Voice: Listening stopped");
}

// ============ VOICE COMMAND HANDLER ============
async function handleVoiceCommand(rawText){
  // CRITICAL: if pending confirmation exists, try to resolve it first BEFORE parsing new commands
  // If confirmation is pending, HARD BLOCK all other commands
  if(pendingConfirm){
    const handled = tryResolveConfirmation(rawText);
    if(handled) return; // Confirmation consumed this text
    // If confirmation didn't match, still block other commands - don't fall through
    speak("Waiting for confirmation. Please respond to the pending question.");
    return;
  }

  const text = rawText.toLowerCase().trim();
  
  // Remove noise words
  const cleaned = text
    .replace(/^(please|can you|could you|would you|hey|okay|ok)\s+/gi, '')
    .replace(/\s+(please|thanks|thank you)$/gi, '')
    .trim();

  console.log("🔍 Parsing:", cleaned);
  console.log("ℹ️ Sending to backend for single-authority parsing (no local dispatch)");
  
  // Send EVERYTHING to backend for parsing - single source of truth
  speak("Let me interpret that command.");
  
  try {
    const res = await fetch("/voice-command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ text: cleaned })
    });
    
    const data = await res.json();
    
    if(data.success && data.action){
      console.log("✅ Backend action:", data.action);
      await handleConfirmedAction(data.action, data);
    } else if(data.action === "confirm"){
      pendingConfirm = { options: data.options || [], expiresAt: Date.now() + 15000, context: { heard: data.heard || text, raw: text } };
      const opts = (data.options || []).join(" or ");
      const heard = data.heard || text;
      speak(`I am not fully sure what you meant. I heard "${heard}". Did you mean ${opts}?`);
      out(`Command ambiguous.\nHeard: "${heard}"\nPossible actions: ${opts}\nPlease say one option to confirm.`);
    } else {
      speak("I didn't understand that command. Say 'help' for available commands.");
      console.log("❌ Command not recognized:", cleaned);
    }
  } catch(e){
    console.error("Backend interpretation failed:", e);
    speak("Command not recognized. Say 'help' for available commands.");
  }
}

// Add keyboard shortcuts for accessibility features
window.addEventListener('DOMContentLoaded', () => {
  // Resume AudioContext on user interaction (required by modern browsers)
  const resumeAudioContext = () => {
    if(audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume().catch(e => console.warn('AudioContext resume failed:', e));
    }
  };
  document.addEventListener('click', resumeAudioContext);
  document.addEventListener('keydown', resumeAudioContext);
  
  // Voice control: Ctrl+Shift+M to toggle
  document.addEventListener('keydown', (e) => {
    if(e.ctrlKey && e.shiftKey && e.key === 'M'){
      e.preventDefault();
      toggleVoice();
    }
  });
});

// Centralized registration to avoid race conditions when editor is created
function registerEditorShortcuts(){
  if (window._editorShortcutsRegistered) return;
  if (!editor) return;
  window._editorShortcutsRegistered = true;

  // Alt+S: Sonify current code block
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyS, () => { sonifyCurrentBlock(); });
  // Alt+L: Read current line with full context
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyL, () => { const pos = editor.getPosition() || { lineNumber: 1 }; readLineEnhanced(pos.lineNumber); });
  // Alt+V: List variables in scope
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyV, () => { listVariables(); });
  // Alt+E: Check for errors
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyE, () => { checkSyntaxErrors(); });
  // Alt+N: Next execution step
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyN, () => { speakNextStep(); });
  // Alt+H: Show help
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.KeyH, () => { showHelp(); });
  // Alt+Left / Right: Navigate history
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.LeftArrow, () => { navigateBack(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.RightArrow, () => { navigateForward(); });
  // Alt+Home / End: Top/Bottom
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.Home, () => { goToTop(); });
  editor.addCommand(monaco.KeyMod.Alt | monaco.KeyCode.End, () => { goToBottom(); });
  // Ctrl+Shift+C / V: Copy/Paste
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyC, () => { copyCode(); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyV, () => { pasteCode(); });

  // Load snippets now that DOM/editor are available
  try { loadSnippets(); } catch (e) { /* silent */ }

  console.log("✅ All accessibility features loaded (registered):");
}

function speakNextStep() {
  if (!window.executionTrace || !window.executionTrace.length) {
    speak("No execution trace available.");
    return;
  }

  if (typeof window.traceIndex !== 'number') window.traceIndex = 0;

  if (window.traceIndex >= window.executionTrace.length) {
    speak("Execution finished.");
    return;
  }

  const step = window.executionTrace[window.traceIndex++];

  if (step.changes) {
    speak(`Line ${step.line}. ${step.changes.join(". ")}`);
  }
}
