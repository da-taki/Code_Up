'use strict';

/**
 * VoiceEngine — unified real-time conversational voice system for CodeUp.
 * Single module containing: VoiceManager, SpeechManager, streaming handler,
 * interrupt controller, and state machine.
 */
const VoiceEngine = (function () {

  // ─── STATE MACHINE ───────────────────────────────────────────────────────────
  const States = Object.freeze({
    IDLE: 'IDLE',
    LISTENING: 'LISTENING',
    PROCESSING: 'PROCESSING',
    RESPONDING: 'RESPONDING',
    SPEAKING: 'SPEAKING',
  });

  const VALID_TRANSITIONS = {
    IDLE: ['LISTENING'],
    LISTENING: ['PROCESSING', 'IDLE'],
    PROCESSING: ['RESPONDING', 'LISTENING', 'IDLE'],
    RESPONDING: ['SPEAKING', 'LISTENING', 'IDLE'],
    SPEAKING: ['LISTENING', 'IDLE'],
  };

  let _state = States.IDLE;
  let _stateListeners = [];

  function getState() { return _state; }

  function setState(newState) {
    if (_state === newState) return true;
    const allowed = VALID_TRANSITIONS[_state];
    if (!allowed || !allowed.includes(newState)) {
      _debug(`Invalid transition: ${_state} → ${newState}`);
      return false;
    }
    const prev = _state;
    _state = newState;
    _debug(`State: ${prev} → ${newState}`);
    _stateListeners.forEach(fn => { try { fn(newState, prev); } catch (e) {} });
    _updateUIState(newState);
    return true;
  }

  function onStateChange(fn) { _stateListeners.push(fn); }

  function _updateUIState(state) {
    const indicator = document.getElementById('voiceStateIndicator');
    if (indicator) {
      const labels = {
        IDLE: '',
        LISTENING: 'Listening…',
        PROCESSING: 'Thinking…',
        RESPONDING: 'Responding…',
        SPEAKING: 'Speaking…',
      };
      indicator.textContent = labels[state] || '';
      indicator.className = 'voice-state-indicator voice-state-' + state.toLowerCase();
    }
  }

  // ─���─ DEBUG ───────────────────────────────────────────────────────────────────
  function _debug(...args) {
    if (typeof window !== 'undefined' && window.CODEUP_DEBUG) {
      console.log('[VoiceEngine]', ...args);
    }
  }

  // ─── CONFIGURATION ──────────────────────────────────────────────────────────
  const Config = {
    microChunkSize: 20,
    speechRate: 1.0,
    speechPitch: 1.0,
    language: 'auto',  // 'auto', 'en', 'hi'
    voiceEnabled: true,
  };

  function configure(opts) {
    Object.assign(Config, opts);
    if (opts.language) {
      _persistLanguagePreference(opts.language);
    }
  }

  function _persistLanguagePreference(lang) {
    try { localStorage.setItem('codeup_voice_lang', lang); } catch (e) {}
  }

  function _loadLanguagePreference() {
    try { return localStorage.getItem('codeup_voice_lang') || 'auto'; } catch (e) { return 'auto'; }
  }

  // ─── LANGUAGE DETECTION ─────────────────────────────────────────────────────
  const DEVANAGARI_RANGE = /[ऀ-ॿ]/;

  function detectLanguage(text) {
    if (Config.language !== 'auto') return Config.language;
    if (!text) return 'en';
    const devanagariChars = (text.match(/[ऀ-ॿ]/g) || []).length;
    const latinChars = (text.match(/[a-zA-Z]/g) || []).length;
    if (devanagariChars > latinChars * 0.3) return 'hi';
    return 'en';
  }

  function splitByLanguage(text) {
    if (!text) return [];
    const segments = [];
    let current = '';
    let currentLang = null;

    for (const char of text) {
      const isDevanagari = DEVANAGARI_RANGE.test(char);
      const charLang = isDevanagari ? 'hi' : 'en';

      if (currentLang === null) {
        currentLang = charLang;
        current = char;
      } else if (charLang === currentLang || /\s/.test(char) || /[.,!?;:]/.test(char)) {
        current += char;
      } else {
        if (current.trim()) segments.push({ text: current.trim(), lang: currentLang });
        current = char;
        currentLang = charLang;
      }
    }
    if (current.trim()) segments.push({ text: current.trim(), lang: currentLang || 'en' });
    return segments;
  }

  // ─── VOICE SELECTION ────────────────────────────────────────────────────────
  let _voices = [];
  let _englishVoice = null;
  let _hindiVoice = null;

  function _loadVoices() {
    if (!('speechSynthesis' in window)) return;
    _voices = window.speechSynthesis.getVoices();
    if (!_voices.length) return;

    // Prefer high-quality voices
    const enVoices = _voices.filter(v => v.lang && v.lang.startsWith('en'));
    const hiVoices = _voices.filter(v => v.lang && v.lang.startsWith('hi'));

    // Prefer: Google > Microsoft > default, non-local over local for quality
    _englishVoice = enVoices.find(v => v.name.includes('Google')) ||
                    enVoices.find(v => v.name.includes('Microsoft') && v.name.includes('Online')) ||
                    enVoices.find(v => !v.localService) ||
                    enVoices[0] || null;

    _hindiVoice = hiVoices.find(v => v.name.includes('Google')) ||
                  hiVoices.find(v => v.name.includes('Microsoft')) ||
                  hiVoices.find(v => !v.localService) ||
                  hiVoices[0] || null;

    _debug('Voices loaded. EN:', _englishVoice?.name, 'HI:', _hindiVoice?.name);
  }

  function _getVoiceForLang(lang) {
    if (lang === 'hi') return _hindiVoice;
    return _englishVoice;
  }

  // ─── SPEECH MANAGER (TTS output) ───────────────────────────────────────────
  let _narrationQueue = [];
  let _currentUtterance = null;
  let _isSpeaking = false;
  let _narrationAborted = false;

  function speak(text, opts = {}) {
    if (!text || !Config.voiceEnabled) return Promise.resolve();
    if (!('speechSynthesis' in window)) return Promise.resolve();

    _narrationAborted = false;
    const chunks = _microChunk(text);
    if (!chunks.length) return Promise.resolve();

    return new Promise(resolve => {
      let remaining = chunks.length;
      const done = () => { if (--remaining <= 0) resolve(); };
      chunks.forEach(chunk => {
        _narrationQueue.push({ text: chunk, lang: opts.lang || detectLanguage(chunk), resolve: done, ...opts });
      });
      _dequeueNarration();
    });
  }

  function _microChunk(text) {
    const normalized = String(text || '').replace(/\s+/g, ' ').trim();
    if (!normalized) return [];

    const maxChunk = Config.microChunkSize;
    if (normalized.length <= maxChunk) return [normalized];

    const chunks = [];
    let remaining = normalized;

    while (remaining.length > 0) {
      if (remaining.length <= maxChunk) {
        chunks.push(remaining);
        break;
      }

      // Find a word boundary near the target size
      let boundary = -1;
      // Look for sentence-end punctuation first
      const sentEnd = remaining.slice(0, maxChunk + 5).search(/[.!?;:]\s/);
      if (sentEnd >= 8 && sentEnd <= maxChunk + 3) {
        boundary = sentEnd + 1;
      } else {
        // Find last space within range
        const window_ = remaining.slice(0, maxChunk + 5);
        boundary = window_.lastIndexOf(' ');
        if (boundary < 8) boundary = maxChunk;
      }

      chunks.push(remaining.slice(0, boundary).trim());
      remaining = remaining.slice(boundary).trim();
    }

    return chunks.filter(Boolean);
  }

  function _dequeueNarration() {
    if (_currentUtterance || !_narrationQueue.length || _narrationAborted) return;
    if (window.speechSynthesis && window.speechSynthesis.speaking) {
      setTimeout(_dequeueNarration, 50);
      return;
    }

    const item = _narrationQueue.shift();
    if (!item || !item.text) {
      if (item && item.resolve) item.resolve();
      _dequeueNarration();
      return;
    }

    _isSpeaking = true;
    _currentUtterance = new SpeechSynthesisUtterance(item.text);
    _currentUtterance.rate = item.rate || Config.speechRate;
    _currentUtterance.pitch = item.pitch || Config.speechPitch;
    _currentUtterance.lang = item.lang === 'hi' ? 'hi-IN' : 'en-US';

    const voice = _getVoiceForLang(item.lang);
    if (voice) _currentUtterance.voice = voice;

    let finished = false;
    const cleanup = () => {
      if (finished) return;
      finished = true;
      _isSpeaking = false;
      _currentUtterance = null;
      if (item.resolve) item.resolve();
      // Chain next chunk immediately for smooth flow
      if (!_narrationAborted) {
        _dequeueNarration();
      } else {
        _checkSpeakingDone();
      }
    };

    _currentUtterance.onend = cleanup;
    _currentUtterance.onerror = cleanup;

    // Safety timeout
    const timeout = Math.max(5000, item.text.length * 100);
    const timer = setTimeout(() => {
      if (!finished) {
        try { window.speechSynthesis.cancel(); } catch (e) {}
        cleanup();
      }
    }, timeout);

    _currentUtterance.onend = () => { clearTimeout(timer); cleanup(); };
    _currentUtterance.onerror = () => { clearTimeout(timer); cleanup(); };

    window.speechSynthesis.speak(_currentUtterance);
  }

  function cancelSpeech() {
    _narrationAborted = true;
    _narrationQueue.length = 0;
    _currentUtterance = null;
    _isSpeaking = false;
    try { window.speechSynthesis.cancel(); } catch (e) {}
    // Multi-cancel for Chrome quirk
    [50, 150, 300].forEach(delay => {
      setTimeout(() => {
        try { if (window.speechSynthesis.speaking) window.speechSynthesis.cancel(); } catch (e) {}
      }, delay);
    });
  }

  function _checkSpeakingDone() {
    if (_narrationQueue.length === 0 && !_isSpeaking && _state === States.SPEAKING) {
      _resumeListeningAfterSpeech();
    }
  }

  function _resumeListeningAfterSpeech() {
    if (Config.voiceEnabled && VoiceInput.isActive()) {
      setState(States.LISTENING);
    } else {
      setState(States.IDLE);
    }
  }

  // ─── INTERRUPT CONTROLLER (BARGE-IN) ───────────────────────────────────────
  let _activeAbortController = null;
  let _interruptTimestamp = 0;
  let _requestEpoch = 0;

  function interrupt() {
    _interruptTimestamp = Date.now();
    _requestEpoch++;
    _debug('INTERRUPT triggered');

    // 1. Cancel speech immediately
    cancelSpeech();

    // 2. Abort active AI request
    if (_activeAbortController) {
      _activeAbortController.abort();
      _activeAbortController = null;
    }

    // 3. Clear streaming state
    StreamHandler.abort();

    // 4. Reset to LISTENING
    if (VoiceInput.isActive()) {
      _state = States.LISTENING; // Force — bypass validation for interrupt
      _updateUIState(States.LISTENING);
    } else {
      _state = States.IDLE;
      _updateUIState(States.IDLE);
    }
  }

  function isStaleCallback(epoch) {
    return epoch < _requestEpoch;
  }

  // ─── REQUEST LOCK (prevent duplicates) ─────────────────────────────────────
  let _requestLocked = false;
  let _lastTranscript = '';
  let _lastTranscriptTime = 0;
  const DEDUP_WINDOW_MS = 2000;

  function _acquireRequestLock(transcript) {
    if (_requestLocked) {
      _debug('Request locked — dropping duplicate');
      return false;
    }
    // Deduplication: ignore identical transcript within window
    const now = Date.now();
    if (transcript === _lastTranscript && (now - _lastTranscriptTime) < DEDUP_WINDOW_MS) {
      _debug('Duplicate transcript — ignoring');
      return false;
    }
    _requestLocked = true;
    _lastTranscript = transcript;
    _lastTranscriptTime = now;
    return true;
  }

  function _releaseRequestLock() {
    _requestLocked = false;
  }

  // ─── STREAMING HANDLER ──────────────────────────────────────────────────────
  const StreamHandler = (function () {
    let _reader = null;
    let _aborted = false;

    async function streamResponse(url, body, onChunk, onDone) {
      _aborted = false;
      const epoch = _requestEpoch;
      _activeAbortController = new AbortController();

      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: _activeAbortController.signal,
        });

        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          if (onDone) onDone(null, errData.error || `HTTP ${response.status}`);
          return;
        }

        const contentType = response.headers.get('content-type') || '';

        // SSE stream
        if (contentType.includes('text/event-stream')) {
          await _handleSSE(response, epoch, onChunk, onDone);
        }
        // NDJSON stream
        else if (contentType.includes('application/x-ndjson') || contentType.includes('text/plain')) {
          await _handleNDJSON(response, epoch, onChunk, onDone);
        }
        // Fallback: standard JSON (non-streaming)
        else {
          const data = await response.json();
          if (!isStaleCallback(epoch) && !_aborted) {
            if (onChunk) onChunk(data.reply || data.speech || data.output || '');
            if (onDone) onDone(data, null);
          }
        }
      } catch (e) {
        if (e.name === 'AbortError') {
          _debug('Stream aborted');
          return;
        }
        if (onDone && !isStaleCallback(epoch)) onDone(null, e.message);
      } finally {
        _activeAbortController = null;
      }
    }

    async function _handleSSE(response, epoch, onChunk, onDone) {
      const reader = response.body.getReader();
      _reader = reader;
      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';

      try {
        while (true) {
          if (_aborted || isStaleCallback(epoch)) break;
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (_aborted || isStaleCallback(epoch)) break;
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') {
                if (onDone) onDone({ reply: fullText, speech: fullText }, null);
                return;
              }
              try {
                const parsed = JSON.parse(data);
                const chunk = parsed.chunk || parsed.text || parsed.delta || '';
                if (chunk) {
                  fullText += chunk;
                  if (onChunk) onChunk(chunk);
                }
              } catch (e) {
                // Plain text SSE data
                if (data.trim()) {
                  fullText += data;
                  if (onChunk) onChunk(data);
                }
              }
            }
          }
        }
        if (!_aborted && !isStaleCallback(epoch) && fullText) {
          if (onDone) onDone({ reply: fullText, speech: fullText }, null);
        }
      } finally {
        _reader = null;
        try { reader.releaseLock(); } catch (e) {}
      }
    }

    async function _handleNDJSON(response, epoch, onChunk, onDone) {
      const reader = response.body.getReader();
      _reader = reader;
      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';

      try {
        while (true) {
          if (_aborted || isStaleCallback(epoch)) break;
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.trim()) continue;
            if (_aborted || isStaleCallback(epoch)) break;
            try {
              const parsed = JSON.parse(line);
              const chunk = parsed.chunk || parsed.text || '';
              if (chunk) {
                fullText += chunk;
                if (onChunk) onChunk(chunk);
              }
            } catch (e) {}
          }
        }
        if (!_aborted && !isStaleCallback(epoch) && fullText) {
          if (onDone) onDone({ reply: fullText, speech: fullText }, null);
        }
      } finally {
        _reader = null;
        try { reader.releaseLock(); } catch (e) {}
      }
    }

    function abort() {
      _aborted = true;
      if (_reader) {
        try { _reader.cancel(); } catch (e) {}
        _reader = null;
      }
    }

    return { streamResponse, abort };
  })();

  // ─── VOICE INPUT MANAGER ────────────────────────────────────────────────────
  const VoiceInput = (function () {
    let _recognition = null;
    let _active = false;
    let _paused = false;
    let _restartTimer = null;
    let _watchdogTimer = null;
    let _lastActivity = Date.now();
    let _userInitiated = false;
    let _restartAttempts = 0;
    const MAX_RESTARTS = 5;

    function isActive() { return _active; }
    function isPaused() { return _paused; }

    function start(userInitiated = true) {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        _debug('Speech recognition not available');
        return false;
      }
      if (_active) return true;

      _recognition = new SR();
      _recognition.continuous = true;
      _recognition.interimResults = false;

      const lang = Config.language === 'auto' ? 'en'
                 : Config.language === 'hi' ? 'hi'
                 : 'en';
      _recognition.lang = lang === 'hi' ? 'hi-IN' : 'en-US';

      _userInitiated = userInitiated;

      _recognition.onstart = () => {
        _active = true;
        _restartAttempts = 0;
        _lastActivity = Date.now();
        _startWatchdog();
        if (!_paused) {
          setState(States.LISTENING);
        }
        _updateVoiceButton(true, _paused);
      };

      _recognition.onresult = (event) => {
        const transcript = event.results[event.results.length - 1][0].transcript;
        _lastActivity = Date.now();
        _debug('Heard:', transcript);

        if (_paused) {
          const lower = transcript.toLowerCase().trim();
          const resumeWords = new Set(['resume', 'resume voice', 'start listening', 'wake up', 'unmute', 'फिर से सुनो', 'जागो']);
          if (resumeWords.has(lower)) {
            unpause();
          }
          return;
        }

        _handleInput(transcript);
      };

      _recognition.onerror = (event) => {
        _debug('Recognition error:', event.error);
        if (event.error === 'no-speech') return;
        if (event.error === 'aborted') { _active = false; return; }
        if (event.error === 'audio-capture' || event.error === 'not-allowed') {
          _active = false;
          setState(States.IDLE);
          _updateVoiceButton(false, false);
        }
      };

      _recognition.onend = () => {
        _debug('Recognition session ended');
        if (!_active) return;
        _restartAttempts++;
        if (_restartAttempts > MAX_RESTARTS) {
          _restartAttempts = 0;
        }
        if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
        _restartTimer = setTimeout(() => {
          _restartTimer = null;
          if (_active) {
            try {
              _recognition.start();
              _lastActivity = Date.now();
            } catch (e) {
              setTimeout(() => {
                try { if (_active) _recognition.start(); } catch (e2) {
                  _debug('Restart failed permanently');
                  _active = false;
                  setState(States.IDLE);
                  _updateVoiceButton(false, false);
                }
              }, 1000);
            }
          }
        }, 400);
      };

      try {
        _recognition.start();
        return true;
      } catch (e) {
        _debug('Failed to start recognition:', e);
        _active = false;
        return false;
      }
    }

    function stop() {
      if (!_recognition || !_active) return;
      _active = false;
      _paused = false;
      _stopWatchdog();
      if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
      try { _recognition.stop(); } catch (e) {}
      setState(States.IDLE);
      _updateVoiceButton(false, false);
    }

    function pause() {
      if (!_active) return;
      _paused = true;
      _updateVoiceButton(true, true);
    }

    function unpause() {
      if (!_active) return;
      _paused = false;
      _updateVoiceButton(true, false);
      if (_state !== States.PROCESSING && _state !== States.RESPONDING && _state !== States.SPEAKING) {
        setState(States.LISTENING);
      }
      // Force fresh session
      try {
        _recognition.stop();
        _lastActivity = Date.now();
      } catch (e) {
        try { _recognition.start(); } catch (e2) {}
      }
    }

    function setLanguage(lang) {
      if (_recognition && _active) {
        _recognition.lang = lang === 'hi' ? 'hi-IN' : 'en-US';
        // Restart to apply
        try {
          _recognition.stop(); // onend will restart
          _lastActivity = Date.now();
        } catch (e) {}
      }
    }

    function _startWatchdog() {
      _stopWatchdog();
      _watchdogTimer = setInterval(() => {
        if (!_active) return;
        const idle = Date.now() - _lastActivity;
        if (idle > 45000) {
          _debug('Watchdog: kicking recognition');
          _lastActivity = Date.now();
          try { _recognition.stop(); } catch (e) {
            try { _recognition.start(); } catch (e2) {}
          }
        }
      }, 15000);
    }

    function _stopWatchdog() {
      if (_watchdogTimer) { clearInterval(_watchdogTimer); _watchdogTimer = null; }
    }

    function _updateVoiceButton(active, paused) {
      const btn = document.getElementById('voiceButton');
      if (!btn) return;
      if (!active) {
        btn.textContent = '🎤 Voice (Off)';
        btn.setAttribute('aria-pressed', 'false');
        btn.classList.remove('cu-button-voice--active', 'cu-button-voice--paused');
      } else if (paused) {
        btn.textContent = '🎤 Voice (Paused)';
        btn.setAttribute('aria-pressed', 'mixed');
        btn.classList.remove('cu-button-voice--active');
        btn.classList.add('cu-button-voice--paused');
      } else {
        btn.textContent = '🎤 Voice (ON)';
        btn.setAttribute('aria-pressed', 'true');
        btn.classList.remove('cu-button-voice--paused');
        btn.classList.add('cu-button-voice--active');
      }
    }

    return { isActive, isPaused, start, stop, pause, unpause, setLanguage };
  })();

  // ─── INPUT HANDLING (voice → action pipeline) ───────────────────────────────
  let _onCommand = null;

  function setCommandHandler(fn) {
    _onCommand = fn;
  }

  async function _handleInput(transcript) {
    const cleaned = transcript.trim();
    if (!cleaned) return;

    // Barge-in: if currently speaking or responding, interrupt
    if (_state === States.SPEAKING || _state === States.RESPONDING) {
      interrupt();
      // Small delay to let cancellation complete
      await new Promise(r => setTimeout(r, 50));
    }

    if (!_acquireRequestLock(cleaned)) return;

    try {
      if (!setState(States.PROCESSING)) {
        // Force transition for interrupt recovery
        _state = States.PROCESSING;
        _updateUIState(States.PROCESSING);
      }

      if (_onCommand) {
        await _onCommand(cleaned);
      }
    } finally {
      _releaseRequestLock();
    }
  }

  // ─── STREAMING CONVERSATION ─────────────────────────────────────────────────
  let _streamBuffer = '';
  let _streamUICallback = null;

  function setStreamUICallback(fn) {
    _streamUICallback = fn;
  }

  async function streamingRequest(url, body, opts = {}) {
    const epoch = ++_requestEpoch;
    _activeAbortController = new AbortController();
    _streamBuffer = '';

    if (!setState(States.RESPONDING)) {
      _state = States.RESPONDING;
      _updateUIState(States.RESPONDING);
    }

    let fullText = '';
    let narrationBuffer = '';
    const narrationThreshold = Config.microChunkSize;

    return new Promise((resolve, reject) => {
      StreamHandler.streamResponse(url, body,
        // onChunk
        (chunk) => {
          if (isStaleCallback(epoch)) return;
          fullText += chunk;
          narrationBuffer += chunk;

          // Update UI immediately
          if (_streamUICallback) _streamUICallback(fullText, chunk);

          // Micro-chunk narration: speak when buffer is large enough
          if (opts.narrate !== false && narrationBuffer.length >= narrationThreshold) {
            const boundary = narrationBuffer.lastIndexOf(' ');
            if (boundary > 8) {
              const toSpeak = narrationBuffer.slice(0, boundary);
              narrationBuffer = narrationBuffer.slice(boundary);
              speak(toSpeak, { lang: detectLanguage(toSpeak) });
              if (_state === States.RESPONDING) {
                _state = States.SPEAKING;
                _updateUIState(States.SPEAKING);
              }
            }
          }
        },
        // onDone
        (data, error) => {
          if (isStaleCallback(epoch)) { resolve({ aborted: true }); return; }

          // Speak remaining buffer
          if (narrationBuffer.trim() && opts.narrate !== false) {
            speak(narrationBuffer.trim(), { lang: detectLanguage(narrationBuffer) });
          }

          if (error) {
            _releaseRequestLock();
            if (VoiceInput.isActive()) setState(States.LISTENING);
            else setState(States.IDLE);
            resolve({ error, fullText });
          } else {
            // After narration completes, resume listening
            if (opts.narrate !== false && fullText) {
              setState(States.SPEAKING);
              // Wait for speech queue to drain, then resume
              const checkDone = setInterval(() => {
                if (!_isSpeaking && _narrationQueue.length === 0) {
                  clearInterval(checkDone);
                  _resumeListeningAfterSpeech();
                }
              }, 200);
            } else {
              if (VoiceInput.isActive()) setState(States.LISTENING);
              else setState(States.IDLE);
            }
            resolve({ data, fullText });
          }
        }
      );
    });
  }

  // Non-streaming request with narration support
  async function request(url, body, opts = {}) {
    const epoch = ++_requestEpoch;
    _activeAbortController = new AbortController();

    if (!setState(States.PROCESSING)) {
      _state = States.PROCESSING;
      _updateUIState(States.PROCESSING);
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: _activeAbortController.signal,
      });

      if (isStaleCallback(epoch)) return { aborted: true };

      const data = await response.json();

      if (!setState(States.RESPONDING)) {
        _state = States.RESPONDING;
        _updateUIState(States.RESPONDING);
      }

      // Narrate response
      const speechText = data.speech || data.reply || data.output || '';
      if (speechText && opts.narrate !== false) {
        setState(States.SPEAKING);
        await speak(speechText, { lang: detectLanguage(speechText) });
        _resumeListeningAfterSpeech();
      } else {
        if (VoiceInput.isActive()) setState(States.LISTENING);
        else setState(States.IDLE);
      }

      return { data };
    } catch (e) {
      if (e.name === 'AbortError') return { aborted: true };
      if (VoiceInput.isActive()) setState(States.LISTENING);
      else setState(States.IDLE);
      return { error: e.message };
    } finally {
      _activeAbortController = null;
    }
  }

  // ─── INITIALIZATION ─────────────────────────────────────────────────────────
  function init() {
    Config.language = _loadLanguagePreference();

    if ('speechSynthesis' in window) {
      _loadVoices();
      window.speechSynthesis.addEventListener('voiceschanged', _loadVoices);
      setTimeout(_loadVoices, 1000);
    }

    _debug('VoiceEngine initialized. Language:', Config.language);
  }

  // ─── PUBLIC API ─────────────────────────────────────────────────────────────
  return {
    // State
    States,
    getState,
    setState,
    onStateChange,

    // Config
    configure,
    Config,

    // Speech output
    speak,
    cancelSpeech,

    // Voice input
    VoiceInput,

    // Streaming
    streamingRequest,
    request,
    StreamHandler,
    setStreamUICallback,

    // Interrupt
    interrupt,
    isStaleCallback,

    // Command handling
    setCommandHandler,

    // Language
    detectLanguage,
    splitByLanguage,

    // Lifecycle
    init,

    // Internals (for legacy integration)
    _acquireRequestLock,
    _releaseRequestLock,
  };
})();

// Auto-initialize when DOM is ready
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => VoiceEngine.init());
  } else {
    VoiceEngine.init();
  }
}
