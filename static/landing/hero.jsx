/* global React */
const { useEffect, useState, useRef } = React;

function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll(".reveal");
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.12 }
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

function Nav() {
  return (
    <nav className="nav">
      <a className="nav-brand" href="#top">
        <span className="dot"></span>
        CodeUp<span style={{ color: "var(--dim)", marginLeft: 4 }}>/v0.8.0</span>
      </a>
      <div className="nav-links">
        <a href="#hear">Structure</a>
        <a href="#speak">Commands</a>
        <a href="#ide">State</a>
        <a href="#features">Features</a>
        <a href="#start-here">Start</a>
      </div>
      <a className="nav-cta" href="/ide">
        Open CodeUp <span aria-hidden="true">→</span>
      </a>
    </nav>
  );
}

function Waveform({ count = 96 }) {
  const [t, setT] = useState(0);
  const noMotion = useRef(false);
  useEffect(() => {
    noMotion.current = document.body.classList.contains("no-motion");
    if (noMotion.current) return;
    let raf;
    let last = performance.now();
    const tick = (now) => {
      const dt = (now - last) / 1000;
      last = now;
      setT((v) => v + dt);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const bars = [];
  for (let i = 0; i < count; i++) {
    const x = i / count;
    const env =
      0.55 +
      0.35 * Math.sin(x * Math.PI * 1.6 + t * 0.6) +
      0.18 * Math.sin(x * Math.PI * 5.7 + t * 1.2) +
      0.12 * Math.sin(x * Math.PI * 13 + t * 2.4);
    const h = Math.max(0.06, Math.min(1, env)) * 100;
    const isAccent = i % 11 === 3 || i % 13 === 7;
    bars.push(
      <div
        key={i}
        className={"bar" + (isAccent ? " accent" : "")}
        style={{ height: `${h}%` }}
      ></div>
    );
  }
  return <div className="hero-wave" aria-hidden="true" role="presentation">{bars}</div>;
}

function Hero() {
  return (
    <section id="top" className="hero" aria-labelledby="hero-heading">
      {
}
      <a href="#features" className="skip-link" style={{top: '40px'}}>Skip decorative hero</a>
      <div className="hero-grid-bg" aria-hidden="true" role="presentation"></div>
      <div className="wrap hero-top">
        <div className="meta">
          <span><b>blind-first</b> python ide</span>
          <span>est. <b>2025</b></span>
          <span>license <b>MIT</b></span>
        </div>
        <div className="meta">
          <span>English</span>
          <span><b>200+</b> tests</span>
        </div>
      </div>

      <div className="wrap hero-headline">
        <div className="hero-eyebrow-row">
          <span className="version-pill">
            <span className="blip"></span> v0.8.0 · COFFEE
          </span>
          <span className="eyebrow">Blind-first Python learning</span>
        </div>
        <h1 className="h1">
          CodeUp<span className="accent">.</span>
          <br />
          <span className="stroke">learn Python</span> by voice<span className="accent">.</span>
        </h1>
        <p className="hero-tagline">
          CodeUp is a blind-first Python learning IDE for visually impaired
          beginners. It teaches code structure through voice and typed commands,
          with plain feedback for loops, indentation, errors, input, output,
          project structure, state, safe fixes, teacher reports, and Audio Blocks.
        </p>
        <div className="hero-cta-row">
          <a className="btn btn-primary" href="/ide">
            Open CodeUp <span className="arrow">→</span>
          </a>
          <a className="btn btn-ghost" href="https://github.com/da-taki/Code_Up" target="_blank" rel="noreferrer">
            View GitHub
          </a>
          <a className="btn btn-ghost" href="https://github.com/da-taki/Code_Up#readme" target="_blank" rel="noreferrer">
            Read README
          </a>
        </div>
      </div>

      <Waveform count={120} />

      <Ticker />
    </section>
  );
}

function Ticker() {
  const cmds = [
    'run', 'go to line twenty five', 'sonify block', 'tell the story',
    'explain this code', 'what changed here', 'set breakpoint at line 10',
    'help', 'find variable x', 'explain simply', 'remember this as quick sort',
    'read output', 'next step', 'bookmark this', 'quiz me on loops',
    'live input mode', 'start tutorial', 'check for errors'
  ];
  const items = [...cmds, ...cmds];
  return (
    <div className="ticker" aria-hidden="true">
      <div className="ticker-track">
        {items.map((c, i) => (
          <React.Fragment key={i}>
            <span><span className="quote">"{c}"</span></span>
            <span className="sep">●</span>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { Nav, Hero, useReveal });
