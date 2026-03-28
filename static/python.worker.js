try {
  importScripts('https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/base/worker/workerMain.js');
} catch (e) {
  console.warn('Monaco worker failed to load from CDN. Syntax highlighting may be unavailable.', e);
}