'use strict';

(function () {

  let voicesChecked = false;
  let hindiAvailable = false;

  function checkVoices() {
    if (!('speechSynthesis' in window)) {
      window._ttsAvailable = false;
      window._hindiTtsAvailable = false;
      voicesChecked = true;
      return;
    }
    const voices = window.speechSynthesis.getVoices();
    if (voices.length === 0) return; // not loaded yet
    hindiAvailable = voices.some(v => v.lang && v.lang.toLowerCase().startsWith('hi'));
    window._ttsAvailable = true;
    window._hindiTtsAvailable = hindiAvailable;
    voicesChecked = true;
    console.log('TTS voices loaded. Hindi available:', hindiAvailable);
  }

  checkVoices();
  if ('speechSynthesis' in window) {
    window.speechSynthesis.addEventListener('voiceschanged', checkVoices);
    setTimeout(checkVoices, 1000);
  }

  window.warnIfHindiUnavailable = function (selectedLang) {
    if (selectedLang !== 'hi') return;
    if (!voicesChecked) return;
    if (!hindiAvailable) {
      const msg = 'Hindi voice is not installed on this device. Narration will use the default voice. ' +
                  'For best results, install a Hindi text-to-speech voice in your operating system settings.';
      console.warn(msg);

      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        [880, 440].forEach((freq, i) => {
          setTimeout(() => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            gain.gain.value = 0.1;
            osc.frequency.value = freq;
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.15);
          }, i * 200);
        });
      } catch (e) {}

      try {
        const u = new SpeechSynthesisUtterance(msg);
        u.lang = 'en-US';
        u.rate = 1;
        setTimeout(() => window.speechSynthesis.speak(u), 600);
      } catch (e) {}

      const out = document.getElementById('output');
      if (out) {
        out.textContent = msg + '\n\n' + (out.textContent || '');
      }
    }
  };

  window._speechRecognitionAvailable = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  if (!window._speechRecognitionAvailable) {
    console.warn('Speech recognition not supported in this browser. Voice input disabled.');
  }
}());