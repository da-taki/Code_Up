'use strict';

(function () {
  // Guard: if the canvas isn't in the DOM yet (e.g. script moved to <head>), bail cleanly
  const canvas = document.getElementById('matrixCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let width, height, dots = [];
  const DOT_SPACING = 40;
  const DOT_SIZE    = 2.2;
  const GLOW_RADIUS = 120;

  let cursor = { x: -999, y: -999 };

  // Cache the reduced-motion preference once and update it via the change event
  // rather than re-querying matchMedia on every animation frame
  let reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', e => {
    reducedMotion = e.matches;
    if (!reducedMotion && !rafId) draw(); // resume if preference flipped off
  });

  // Throttle mousemove to rAF cadence — on high-refresh screens the raw event
  // fires hundreds of times per second, all writing the same two numbers
  let pendingMouseX = cursor.x;
  let pendingMouseY = cursor.y;
  let mousePending  = false;
  window.addEventListener('mousemove', e => {
    pendingMouseX = e.clientX;
    pendingMouseY = e.clientY;
    mousePending  = true;
  });

  // Debounced resize — rebuilding the dots array synchronously on every pixel
  // of a drag-resize allocates thousands of objects per second
  let resizeTimer = null;
  function resize() {
    width  = canvas.width  = window.innerWidth;
    height = canvas.height = window.innerHeight;
    dots   = [];
    for (let x = 0; x < width;  x += DOT_SPACING) {
      for (let y = 0; y < height; y += DOT_SPACING) {
        dots.push({ x, y });
      }
    }
  }

  function onResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 150);
  }

  resize();
  window.addEventListener('resize', onResize);

  let rafId = null;

  function draw() {
    // If the user prefers reduced motion or the tab is hidden, stop the loop
    // entirely rather than spinning rAF while rendering nothing
    if (reducedMotion || document.hidden) {
      rafId = null;
      return;
    }

    // Apply buffered mouse position once per frame
    if (mousePending) {
      cursor.x    = pendingMouseX;
      cursor.y    = pendingMouseY;
      mousePending = false;
    }

    ctx.clearRect(0, 0, width, height);

    // Reset shadow state once before the loop — setting it per-dot forces a
    // compositing pass on every single dot even when glow is zero
    ctx.shadowBlur  = 0;
    ctx.shadowColor = 'transparent';

    for (const dot of dots) {
      const dist = Math.hypot(dot.x - cursor.x, dot.y - cursor.y);
      const glow = Math.max(0, (GLOW_RADIUS - dist) / GLOW_RADIUS);

      ctx.beginPath();
      ctx.arc(dot.x, dot.y, DOT_SIZE + glow * 4, 0, Math.PI * 2);

      if (glow > 0.001) {
        ctx.fillStyle   = `rgba(${50 + glow * 200}, ${255 * glow}, ${200 + glow * 55}, ${0.35 + glow * 0.6})`;
        ctx.shadowBlur  = glow * 25;
        ctx.shadowColor = `rgba(0,255,200,${glow})`;
        ctx.fill();
        // Reset shadow after glowing dot so non-glowing dots aren't affected
        ctx.shadowBlur  = 0;
        ctx.shadowColor = 'transparent';
      } else {
        ctx.fillStyle = 'rgba(100, 150, 180, 0.18)';
        ctx.fill();
      }
    }

    rafId = requestAnimationFrame(draw);
  }

  // Listen for the tab becoming visible again so the loop can resume
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !reducedMotion && !rafId) draw();
  });

  draw();
}());