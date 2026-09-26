'use strict';

const VoiceEngine = (function () {

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

  function _debug(...args) {
    if (typeof window !== 'undefined' && window.CODEUP_DEBUG) {
      console.log('[VoiceEngine]', ...args);
    }
  }

  const Config = {
    speechChunkSize: 260,
    streamNarrationMinChars: 140,
    speechRate: 1.0,
    speechPitch: 1.0,
    voiceName: '',
    language: 'auto',  // 'auto', 'en', 'hi'
    voiceEnabled: true,
  };

  function configure(opts) {
    opts = opts || {};
    if (opts.microChunkSize && !opts.speechChunkSize) {
      opts.speechChunkSize = opts.microChunkSize;
      delete opts.microChunkSize;
    }
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

  let _voices = [];
  let _englishVoice = null;
  let _hindiVoice = null;

  function _loadVoices() {
    if (!('speechSynthesis' in window)) return;
    _voices = window.speechSynthesis.getVoices();
    if (!_voices.length) return;

    const enVoices = _voices.filter(v => v.lang && v.lang.startsWith('en'));
    const hiVoices = _voices.filter(v => v.lang && v.lang.startsWith('hi'));

    const FEMALE = /(zira|aria|jenny|michelle|clara|samantha|susan|google us english|google uk english female|female|heera|swara|kalpana)/i;
    const MALE = /(\bdavid\b|\bmark\b|george|james|\bguy\b|daniel|google uk english male|\bmale\b|ravi|hemant|madhur|prabhat)/i;

    _englishVoice = enVoices.find(v => v.name.includes('Google') && !MALE.test(v.name)) ||
                    enVoices.find(v => FEMALE.test(v.name)) ||
                    enVoices.find(v => v.name.includes('Microsoft') && v.name.includes('Online') && !MALE.test(v.name)) ||
                    enVoices.find(v => !v.localService && !MALE.test(v.name)) ||
                    enVoices.find(v => !MALE.test(v.name)) ||
                    enVoices[0] || null;

    _hindiVoice = hiVoices.find(v => v.name.includes('Google') && !MALE.test(v.name)) ||
                  hiVoices.find(v => FEMALE.test(v.name)) ||
                  hiVoices.find(v => v.name.includes('Microsoft') && !MALE.test(v.name)) ||
                  hiVoices.find(v => !v.localService && !MALE.test(v.name)) ||
                  hiVoices[0] || null;

    _debug('Voices loaded. EN:', _englishVoice?.name, 'HI:', _hindiVoice?.name);
  }

  function _getVoiceForLang(lang) {
    if (Config.voiceName) {
      const selected = _voices.find(v => v.name === Config.voiceName);
      if (selected) return selected;
    }
    return _englishVoice;
  }

  let _narrationQueue = [];
  let _currentUtterance = null;
  let _isSpeaking = false;
  let _narrationAborted = false;
  let _cancelGeneration = 0;
  let _synthKeepAliveTimer = null;

  // Chrome's speechSynthesis silently stops producing audio (while
  // `speaking` stays true and neither onend nor onerror ever fires) once a
  // single utterance has been playing for roughly 15 seconds - this is the
  // long-documented Chromium bug behind "speech stops mid-output" (XRCVC:
  // a prime-number list read aloud went silent around 29 with nothing
  // wrong in the text itself). pause()+resume() resets Chrome's internal
  // timer without audibly interrupting playback, so kicking it well inside
  // that window keeps long single chunks (slow speech rates, chunk text
  // near the 260-char boundary) from ever reaching it.
  function _startSynthKeepAlive() {
    _stopSynthKeepAlive();
    _synthKeepAliveTimer = setInterval(() => {
      try {
        if (window.speechSynthesis && window.speechSynthesis.speaking) {
          window.speechSynthesis.pause();
          window.speechSynthesis.resume();
        }
      } catch (e) {}
    }, 10000);
  }
  function _stopSynthKeepAlive() {
    if (_synthKeepAliveTimer) { clearInterval(_synthKeepAliveTimer); _synthKeepAliveTimer = null; }
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

  function speak(text, opts = {}) {
    if (!text || !Config.voiceEnabled) return Promise.resolve();
    if (!('speechSynthesis' in window)) return Promise.resolve();

    if (_narrationAborted && (Date.now() - _interruptTimestamp) < 100) {
      return Promise.resolve();
    }
    _narrationAborted = false;
    // New narration is starting: invalidate any deferred cancels still pending
    _cancelGeneration++;

    const chunks = _semanticSpeechChunks(sanitizeSpeechText(text));
    if (!chunks.length) return Promise.resolve();
    _noteSpoken(chunks.join(' '));

    return new Promise(resolve => {
      let remaining = chunks.length;
      const done = () => { if (--remaining <= 0) resolve(); };
      chunks.forEach(chunk => {
        _narrationQueue.push({ text: chunk, lang: opts.lang || detectLanguage(chunk), resolve: done, ...opts });
      });
      _dequeueNarration();
    });
  }

  // ─── SELF-ECHO GUARD ────────────────────────────────────────────────────────
  // Recognition keeps listening while CodeUp talks (so the learner can barge
  // in). On speakers, the microphone can hear CodeUp's own voice ("...say run
  // to see the output") and the transcript would be executed as a command.
  // A transcript that is just a fragment of what CodeUp said in the last few
  // seconds is dropped; explicit barge-in words always get through.
  const ECHO_WINDOW_MS = 8000;
  // Short voice-control commands. They are always accepted as barge-in,
  // EXCEPT while CodeUp itself is saying those exact words (help text such as
  // "say stop listening to turn voice off"): the microphone hearing that must
  // not silence CodeUp or switch voice off.
  const _CONTROL_PHRASES = new Set(['stop', 'stop it', 'cancel', 'quiet', 'be quiet', 'stop talking',
    'stop speaking', 'silence', 'shut up', 'pause', 'pause voice', 'stop listening', 'turn voice off',
    'turn off voice', 'voice off', 'disable microphone', 'mute microphone', 'stop everything',
    'ruko', 'bas', 'chup', 'रुको', 'बस', 'चुप']);
  let _spokenLog = [];

  function _echoNorm(text) {
    return String(text || '').toLowerCase().replace(/[^a-z0-9ऀ-ॿ\s]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function _noteSpoken(text) {
    const now = Date.now();
    const norm = _echoNorm(text);
    if (!norm) return;
    _spokenLog = _spokenLog.filter(e => now < e.until);
    // An echo can only arrive while the words are being spoken: roughly
    // 15 characters a second, plus a short tail for recognition latency.
    const speakingMs = Math.min(ECHO_WINDOW_MS, (norm.length / 15) * 1000);
    _spokenLog.push({ text: norm, ts: now, until: now + speakingMs + 1500 });
  }

  function isLikelyEcho(transcript) {
    const heard = _echoNorm(transcript);
    if (!heard) return false;
    const now = Date.now();
    const recent = _spokenLog.filter(e => now < e.until);
    if (!recent.length) return false;
    if (_CONTROL_PHRASES.has(heard)) {
      return recent.some(e => (' ' + e.text + ' ').includes(' ' + heard + ' '));
    }
    const words = heard.split(' ');
    // A learner repeating a suggested command ("check my work", "run it")
    // is a real command: only longer fragments of CodeUp's sentence count.
    if (words.length < 4) return false;
    return recent.some(e => {
      if (e.text.includes(heard)) return true;
      const spoken = new Set(e.text.split(' '));
      const overlap = words.filter(w => spoken.has(w)).length / words.length;
      return overlap >= 0.8;
    });
  }

  function _semanticSpeechChunks(text) {
    const normalized = String(text || '').replace(/\s+/g, ' ').trim();
    if (!normalized) return [];

    const maxChunk = Config.speechChunkSize || 260;
    if (normalized.length <= maxChunk) return [normalized];

    const chunks = [];
    let remaining = normalized;

    while (remaining.length > 0) {
      if (remaining.length <= maxChunk) {
        chunks.push(remaining);
        break;
      }

      let boundary = -1;
      const window_ = remaining.slice(0, maxChunk + 1);
      const sentenceMatches = Array.from(window_.matchAll(/[.!?]\s+/g));
      const sentenceEnd = sentenceMatches.length
        ? sentenceMatches[sentenceMatches.length - 1].index
        : -1;
      if (sentenceEnd >= 80) {
        boundary = sentenceEnd + 1;
      } else {
        const pauseBoundary = Math.max(
          window_.lastIndexOf('; '),
          window_.lastIndexOf(': '),
          window_.lastIndexOf(', ')
        );
        if (pauseBoundary >= 140) {
          boundary = pauseBoundary + 1;
        } else {
          const spaceBoundary = window_.lastIndexOf(' ');
          boundary = spaceBoundary >= 120 ? spaceBoundary : maxChunk;
        }
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

    // Skip empty items without recursion (prevent stack overflow)
    let item = _narrationQueue.shift();
    while (item && !item.text) {
      if (item.resolve) try { item.resolve(); } catch (e) {}
      if (!_narrationQueue.length) return;
      item = _narrationQueue.shift();
    }
    if (!item || !item.text) return;

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
      _stopSynthKeepAlive();
      _isSpeaking = false;
      _currentUtterance = null;
      if (item.resolve) item.resolve();
      if (!_narrationAborted) {
        _dequeueNarration();
      } else {
        _checkSpeakingDone();
      }
    };

    _currentUtterance.onend = cleanup;
    _currentUtterance.onerror = cleanup;

    // Must scale with the utterance's own rate (user-configurable 0.5x-2.0x,
    // see applySpeechRate()) - a fixed chars*100ms estimate assumes 1x and
    // fires early at slower rates, cancelling speech that is still
    // legitimately playing and reproducing the exact "speech stops
    // mid-output" symptom XRCVC reported (this is a *safety* timeout for a
    // genuinely stuck utterance, not a length cap - the text itself is
    // already bounded by _semanticSpeechChunks()).
    const rate = Math.max(0.5, Number(_currentUtterance.rate) || 1);
    const timeout = Math.min(90000, Math.max(8000, Math.ceil(item.text.length * 120 / rate) + 6000));
    const timer = setTimeout(() => {
      if (!finished) {
        try { window.speechSynthesis.cancel(); } catch (e) {}
        cleanup();
      }
    }, timeout);

    _startSynthKeepAlive();
    _currentUtterance.onend = () => { clearTimeout(timer); cleanup(); };
    _currentUtterance.onerror = () => { clearTimeout(timer); cleanup(); };

    window.speechSynthesis.speak(_currentUtterance);
  }

  function cancelSpeech() {
    _narrationAborted = true;
    const myGeneration = ++_cancelGeneration;
    const pending = _narrationQueue.splice(0);
    pending.forEach(item => { if (item.resolve) try { item.resolve(); } catch (e) {} });
    _stopSynthKeepAlive();
    _currentUtterance = null;
    _isSpeaking = false;
    try { window.speechSynthesis.cancel(); } catch (e) {}
    [50, 150, 300].forEach(delay => {
      setTimeout(() => {
        if (_cancelGeneration !== myGeneration) return;
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
    if (Config.voiceEnabled && VoiceInput.isEnabledByUser() && VoiceInput.isActive()) {
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

    cancelSpeech();

    if (_activeAbortController) {
      _activeAbortController.abort();
      _activeAbortController = null;
    }

    StreamHandler.abort();

    _requestLocked = false;

    if (VoiceInput.isEnabledByUser() && VoiceInput.isActive()) {
      _state = States.LISTENING;
      _updateUIState(States.LISTENING);
    } else {
      _state = States.IDLE;
      _updateUIState(States.IDLE);
    }
  }

  function isStaleCallback(epoch) {
    return epoch < _requestEpoch;
  }

  let _requestLocked = false;
  let _lastTranscript = '';
  let _lastTranscriptTime = 0;
  const DEDUP_WINDOW_MS = 2000;

  function _acquireRequestLock(transcript) {
    if (_requestLocked) {
      _debug('Request locked — dropping duplicate');
      return false;
    }
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

        if (contentType.includes('text/event-stream')) {
          await _handleSSE(response, epoch, onChunk, onDone);
        }
        else if (contentType.includes('application/x-ndjson') || contentType.includes('text/plain')) {
          await _handleNDJSON(response, epoch, onChunk, onDone);
        }
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

  const VoiceInput = (function () {
    // One source of truth for microphone recognition. Invariants:
    //   * at most ONE live SpeechRecognition instance - every new instance
    //     first detaches and aborts the previous one, and every event handler
    //     ignores events from an instance that is no longer current;
    //   * _enabledByUser means "the learner wants voice on"; it only becomes
    //     false through an explicit stop()/pause() or a user-initiated start
    //     that the browser refused (permission denied);
    //   * automatic restarts never run in a hidden tab - they wait for the tab
    //     to become visible again (switching to the instructor window, another
    //     app, or another tab must not permanently turn voice off);
    //   * "voice on" is remembered for this browser tab (sessionStorage), so a
    //     same-tab navigation (opening an assignment, lesson or project reloads
    //     the IDE) can resume listening instead of silently dropping it.
    let _recognition = null;
    let _active = false;
    let _starting = false;
    let _paused = false;
    let _enabledByUser = false;
    let _restartTimer = null;
    let _watchdogTimer = null;
    let _lastActivity = Date.now();
    let _userInitiated = false;
    let _resuming = false;
    let _restartAttempts = 0;
    let _lastStartAt = 0;
    let _waitingForVisible = false;
    const _statusListeners = [];
    const MAX_RAPID_RESTARTS = 6;
    const WANT_KEY = 'codeupVoiceWanted';

    function isActive() { return _active; }
    function isPaused() { return _paused; }
    function isEnabledByUser() { return _enabledByUser; }

    function _hidden() { return typeof document !== 'undefined' && !!document.hidden; }

    function _setWanted(on) {
      try {
        if (on) sessionStorage.setItem(WANT_KEY, '1');
        else sessionStorage.removeItem(WANT_KEY);
      } catch (e) {}
    }

    function wasWanted() {
      try { return sessionStorage.getItem(WANT_KEY) === '1'; } catch (e) { return false; }
    }

    function onStatusChange(fn) { if (typeof fn === 'function') _statusListeners.push(fn); }

    function _emit(status, detail) {
      _debug('Voice status:', status, detail || '');
      _statusListeners.forEach(fn => { try { fn(status, detail || {}); } catch (e) {} });
    }

    function _detach(rec) {
      if (!rec) return;
      rec.onstart = null;
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
    }

    function _giveUp(reason) {
      _enabledByUser = false;
      _active = false;
      _starting = false;
      _paused = false;
      _waitingForVisible = false;
      _setWanted(false);
      _stopWatchdog();
      if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
      setState(States.IDLE);
      _updateVoiceButton(false, false);
      _emit(reason);
    }

    function start(userInitiated = true) {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        _debug('Speech recognition not available');
        return false;
      }
      if (userInitiated) {
        _enabledByUser = true;
        _setWanted(true);
        _restartAttempts = 0;
      } else if (!_enabledByUser) {
        return false;
      }
      if (_active || _starting) return true;

      if (_recognition) {
        // Never leave a second recognizer alive: detach first so its late
        // onend cannot restart anything, then abort it.
        const old = _recognition;
        _detach(old);
        try { old.abort(); } catch (e) {}
      }
      const rec = new SR();
      _recognition = rec;
      _recognition.continuous = true;
      _recognition.interimResults = true;
      const lang = Config.language === 'hi' ? 'hi' : 'en';
      rec.lang = lang === 'hi' ? 'hi-IN' : 'en-US';
      _userInitiated = userInitiated;

      rec.onstart = () => {
        if (rec !== _recognition) return;
        if (!_enabledByUser) {
          try { rec.stop(); } catch (e) {}
          _active = false;
          _starting = false;
          _paused = false;
          _stopWatchdog();
          _updateVoiceButton(false, false);
          return;
        }
        _active = true;
        _starting = false;
        _waitingForVisible = false;
        _lastActivity = Date.now();
        _startWatchdog();
        if (!_paused) setState(States.LISTENING);
        _updateVoiceButton(true, _paused);
        const wasResume = _resuming;
        _resuming = false;
        _emit('listening', { userInitiated: _userInitiated, resumed: wasResume });
      };

      rec.onresult = (event) => {
        if (rec !== _recognition || !_enabledByUser) return;
        let finalTranscript = '';
        let interimTranscript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const chunk = event.results[i][0].transcript;
          if (event.results[i].isFinal) finalTranscript += chunk;
          else interimTranscript += chunk;
        }
        if (interimTranscript.trim() && typeof window !== 'undefined' && window.updateTranscriptStatus) {
          window.updateTranscriptStatus({ heard: interimTranscript.trim(), nextAction: 'Listening.' });
        }
        const transcript = finalTranscript.trim();
        if (!transcript) return;
        _lastActivity = Date.now();
        _restartAttempts = 0;
        if (isLikelyEcho(transcript)) {
          _debug('Ignored CodeUp speech echo:', transcript);
          return;
        }
        if (typeof window !== 'undefined' && window.updateTranscriptStatus) {
          window.updateTranscriptStatus({ heard: transcript, nextAction: 'Interpreting voice command.' });
        }
        _debug('Heard:', transcript);

        if (_paused) {
          const lower = transcript.toLowerCase().trim();
          const resumeWords = new Set(['resume', 'resume voice', 'start listening', 'wake up', 'unmute', 'फिर से सुनो', 'जागो']);
          if (resumeWords.has(lower)) unpause();
          return;
        }

        _handleInput(transcript);
      };

      rec.onerror = (event) => {
        if (rec !== _recognition) return;
        const error = event && event.error;
        _debug('Recognition error:', error);
        if (error === 'no-speech') return;
        if (error === 'not-allowed' || error === 'service-not-allowed' || error === 'audio-capture') {
          _active = false;
          _starting = false;
          if (_hidden()) {
            // Background tabs may not open the microphone: wait for the
            // learner to come back instead of turning voice off for good.
            _waitingForVisible = true;
            _emit('suspended', { error });
            return;
          }
          if (_userInitiated) {
            _giveUp('blocked');
          } else if (_resuming) {
            _resuming = false;
            _giveUp('resume_failed');
          } else {
            // An automatic restart was refused while the page was visible;
            // back off and try again (bounded by MAX_RAPID_RESTARTS).
            _emit('retrying', { error });
          }
          return;
        }
        // 'aborted' (another tab or app took the microphone, or our own
        // abort), 'network', ... are transient: onend follows and restarts.
        _active = false;
      };

      rec.onend = () => {
        if (rec !== _recognition) return;
        _debug('Recognition session ended');
        _active = false;
        _starting = false;
        if (!_enabledByUser) return;
        _scheduleRestart();
      };

      try {
        _starting = true;
        _lastStartAt = Date.now();
        rec.start();
        return true;
      } catch (e) {
        _debug('Failed to start recognition:', e);
        _starting = false;
        _active = false;
        if (!userInitiated) _scheduleRestart();
        return false;
      }
    }

    function _scheduleRestart() {
      if (!_enabledByUser) return;
      if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
      if (_hidden()) {
        _waitingForVisible = true;
        return;
      }
      const sessionMs = _lastStartAt ? Date.now() - _lastStartAt : 0;
      if (sessionMs > 0 && sessionMs < 1500) _restartAttempts++;
      else _restartAttempts = 0;
      if (_restartAttempts > MAX_RAPID_RESTARTS) {
        _restartAttempts = 0;
        _giveUp('failed');
        return;
      }
      const delay = Math.min(4000, 300 * Math.pow(2, _restartAttempts));
      _restartTimer = setTimeout(() => {
        _restartTimer = null;
        if (_enabledByUser && !_active && !_starting && !_hidden()) start(false);
        else if (_enabledByUser && _hidden()) _waitingForVisible = true;
      }, delay);
    }

    function resumeIfWanted() {
      // Called once when the IDE loads: the learner had voice on in this tab
      // before a same-tab navigation. Not a new user gesture, so a refusal is
      // reported (resume_failed) instead of repeated.
      if (_enabledByUser || !wasWanted()) return false;
      _enabledByUser = true;
      _resuming = true;
      const ok = start(false);
      if (!ok) { _resuming = false; _giveUp('resume_failed'); }
      return ok;
    }

    function stop() {
      _enabledByUser = false;
      _active = false;
      _starting = false;
      _paused = false;
      _resuming = false;
      _waitingForVisible = false;
      _setWanted(false);
      _stopWatchdog();
      if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null; }
      if (_recognition) {
        try { _recognition.stop(); } catch (e) {}
      }
      setState(States.IDLE);
      _updateVoiceButton(false, false);
      _emit('stopped');
    }

    function pause() {
      stop();
    }

    function unpause() {
      if (!_active) {
        start(true);
        return;
      }
      _enabledByUser = true;
      _paused = false;
      _updateVoiceButton(true, false);
      if (_state !== States.PROCESSING && _state !== States.RESPONDING && _state !== States.SPEAKING) {
        setState(States.LISTENING);
      }
    }

    function setLanguage(lang) {
      if (_recognition && _enabledByUser) {
        _recognition.lang = lang === 'hi' ? 'hi-IN' : 'en-US';
        if (_active) {
          try {
            _recognition.stop(); // onend restarts with the new language
            _lastActivity = Date.now();
          } catch (e) {}
        }
      }
    }

    if (typeof document !== 'undefined' && document.addEventListener) {
      document.addEventListener('visibilitychange', () => {
        if (!_hidden() && _enabledByUser && !_active && !_starting) {
          _waitingForVisible = false;
          _restartAttempts = 0;
          _lastStartAt = 0;
          _scheduleRestart();
        }
      });
    }

    function _startWatchdog() {
      _stopWatchdog();
      _watchdogTimer = setInterval(() => {
        if (!_active || !_enabledByUser) return;
        const idle = Date.now() - _lastActivity;
        if (idle > 45000) {
          _debug('Watchdog: kicking recognition');
          _lastActivity = Date.now();
          try { _recognition.stop(); } catch (e) { _scheduleRestart(); }
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
        btn.textContent = 'Voice input (Off)';
        btn.setAttribute('aria-pressed', 'false');
        btn.classList.remove('cu-button-voice--active', 'cu-button-voice--paused');
      } else if (paused) {
        btn.textContent = 'Voice input (Paused)';
        btn.setAttribute('aria-pressed', 'mixed');
        btn.classList.remove('cu-button-voice--active');
        btn.classList.add('cu-button-voice--paused');
      } else {
        btn.textContent = 'Voice input (On)';
        btn.setAttribute('aria-pressed', 'true');
        btn.classList.remove('cu-button-voice--paused');
        btn.classList.add('cu-button-voice--active');
      }
    }

    function _debugState() {
      return { active: _active, starting: _starting, enabledByUser: _enabledByUser, paused: _paused,
               waitingForVisible: _waitingForVisible, restartAttempts: _restartAttempts, wanted: wasWanted() };
    }

    return { isActive, isPaused, isEnabledByUser, start, stop, pause, unpause, setLanguage,
             resumeIfWanted, wasWanted, onStatusChange, _debugState };
  })();

  // ─── INPUT HANDLING (voice → action pipeline) ───────────────────────────────
  let _onCommand = null;

  function setCommandHandler(fn) {
    _onCommand = fn;
  }

  async function _handleInput(transcript) {
    const cleaned = transcript.trim();
    if (!cleaned) return;

    if (_state === States.SPEAKING || _state === States.RESPONDING || _state === States.PROCESSING) {
      interrupt();
      await new Promise(r => setTimeout(r, 50));
    }

    if (!_acquireRequestLock(cleaned)) return;

    try {
      if (!setState(States.PROCESSING)) {
        _state = States.PROCESSING;
        _updateUIState(States.PROCESSING);
      }

      if (_onCommand) {
        await _onCommand(cleaned);
      }
    } catch (e) {
      _debug('Command handler error:', e);
    } finally {
      _releaseRequestLock();
      if (_state === States.PROCESSING) {
        if (VoiceInput.isEnabledByUser() && VoiceInput.isActive()) {
          _state = States.LISTENING;
          _updateUIState(States.LISTENING);
        } else {
          _state = States.IDLE;
          _updateUIState(States.IDLE);
        }
      }
    }
  }

  let _streamBuffer = '';
  let _streamUICallback = null;

  function setStreamUICallback(fn) {
    _streamUICallback = fn;
  }

  async function streamingRequest(url, body, opts = {}) {
    const epoch = ++_requestEpoch;
    _streamBuffer = '';

    if (!setState(States.RESPONDING)) {
      _state = States.RESPONDING;
      _updateUIState(States.RESPONDING);
    }

    let fullText = '';
    let narrationBuffer = '';
    const narrationThreshold = Config.streamNarrationMinChars || 140;

    function takeSpeakableStreamText(force = false) {
      const buffer = String(narrationBuffer || '');
      const trimmed = buffer.trim();
      if (!trimmed) return '';
      if (force) {
        narrationBuffer = '';
        return trimmed;
      }

      const sentenceMatches = Array.from(buffer.matchAll(/[.!?]\s+/g));
      for (const match of sentenceMatches) {
        const end = match.index + 1;
        if (end >= 60) {
          const spoken = buffer.slice(0, end).trim();
          narrationBuffer = buffer.slice(end).trimStart();
          return spoken;
        }
      }

      if (buffer.length < Math.max(narrationThreshold, 220)) {
        return '';
      }

      const windowText = buffer.slice(0, Config.speechChunkSize || 260);
      const boundary = Math.max(
        windowText.lastIndexOf('; '),
        windowText.lastIndexOf(': '),
        windowText.lastIndexOf(', '),
        windowText.lastIndexOf('\n')
      );
      if (boundary >= narrationThreshold) {
        const spoken = buffer.slice(0, boundary + 1).trim();
        narrationBuffer = buffer.slice(boundary + 1).trimStart();
        return spoken;
      }

      if (buffer.length >= (Config.speechChunkSize || 260)) {
        const space = windowText.lastIndexOf(' ');
        const end = space >= narrationThreshold ? space : windowText.length;
        const spoken = buffer.slice(0, end).trim();
        narrationBuffer = buffer.slice(end).trimStart();
        return spoken;
      }

      return '';
    }

    return new Promise((resolve, reject) => {
      StreamHandler.streamResponse(url, body,
        (chunk) => {
          if (isStaleCallback(epoch)) return;
          fullText += chunk;
          narrationBuffer += chunk;

          if (_streamUICallback) _streamUICallback(fullText, chunk);

          if (opts.narrate !== false) {
            let toSpeak = takeSpeakableStreamText(false);
            while (toSpeak) {
              speak(toSpeak, { lang: detectLanguage(toSpeak) });
              toSpeak = takeSpeakableStreamText(false);
            }
            if (_state === States.RESPONDING && (_isSpeaking || _narrationQueue.length > 0)) {
              _state = States.SPEAKING;
              _updateUIState(States.SPEAKING);
            }
          }
        },
        (data, error) => {
          if (isStaleCallback(epoch)) { resolve({ aborted: true }); return; }

          if (opts.narrate !== false) {
            const remainingSpeech = takeSpeakableStreamText(true);
            if (remainingSpeech) {
              speak(remainingSpeech, { lang: detectLanguage(remainingSpeech) });
            }
          }

          if (error) {
            if (VoiceInput.isEnabledByUser() && VoiceInput.isActive()) setState(States.LISTENING);
            else setState(States.IDLE);
            resolve({ error, fullText });
          } else {
            if (opts.narrate !== false && fullText) {
              setState(States.SPEAKING);
              let checkCount = 0;
              const checkDone = setInterval(() => {
                checkCount++;
                if (!_isSpeaking && _narrationQueue.length === 0) {
                  clearInterval(checkDone);
                  _resumeListeningAfterSpeech();
                } else if (checkCount > 150) {
                  clearInterval(checkDone);
                  cancelSpeech();
                  _resumeListeningAfterSpeech();
                }
              }, 200);
            } else {
              if (VoiceInput.isEnabledByUser() && VoiceInput.isActive()) setState(States.LISTENING);
              else setState(States.IDLE);
            }
            resolve({ data, fullText });
          }
        }
      );
    });
  }

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

      const speechText = data.speech || data.reply || data.output || '';
      if (speechText && opts.narrate !== false) {
        setState(States.SPEAKING);
        await speak(speechText, { lang: detectLanguage(speechText) });
        _resumeListeningAfterSpeech();
      } else {
        if (VoiceInput.isEnabledByUser() && VoiceInput.isActive()) setState(States.LISTENING);
        else setState(States.IDLE);
      }

      return { data };
    } catch (e) {
      if (e.name === 'AbortError') return { aborted: true };
      if (VoiceInput.isEnabledByUser() && VoiceInput.isActive()) setState(States.LISTENING);
      else setState(States.IDLE);
      return { error: e.message };
    } finally {
      _activeAbortController = null;
    }
  }

  function init() {
    Config.language = _loadLanguagePreference();

    if ('speechSynthesis' in window) {
      _loadVoices();
      window.speechSynthesis.addEventListener('voiceschanged', _loadVoices);
      setTimeout(_loadVoices, 1000);
    }

    _debug('VoiceEngine initialized. Language:', Config.language);
  }

  return {
    States,
    getState,
    setState,
    onStateChange,

    configure,
    Config,

    speak,
    cancelSpeech,

    VoiceInput,

    streamingRequest,
    request,
    StreamHandler,
    setStreamUICallback,

    interrupt,
    isStaleCallback,

    setCommandHandler,

    detectLanguage,
    splitByLanguage,

    init,

    _acquireRequestLock,
    _releaseRequestLock,
    isLikelyEcho,
    noteSpoken: _noteSpoken,
  };
})();

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => VoiceEngine.init());
  } else {
    VoiceEngine.init();
  }
}
