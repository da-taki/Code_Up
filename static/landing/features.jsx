/* global React */
const { useEffect, useState } = React;


function Features() {
  return (
    <section id="features" className="features">
      <div className="wrap">
        <div className="reveal section-head">
          <div>
            <div className="eyebrow" style={{ marginBottom: 16 }}>04. Features</div>
            <h2>Made for <span className="accent">beginner Python.</span></h2>
          </div>
          <p className="desc">
            CodeUp helps a student understand the shape of code before moving
            into the full professional toolchain.
          </p>
        </div>

        <div className="feat-grid">
          <div className="reveal feat span6">
            <div className="feat-num">Python Code Mode</div>
            <h3>Write and run real beginner Python.</h3>
            <p>Students can make small programs, run them, give input values, hear output, and ask what the code is doing.</p>
            <div className="feat-art"><div className="eq"><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div></div>
          </div>

          <div className="reveal delay-1 feat span6">
            <div className="feat-num">Audio Blocks</div>
            <h3>Build Python through numbered blocks.</h3>
            <p>Audio Blocks lets students create real Python through accessible numbered blocks before moving into full syntax.</p>
            <div className="feat-art"><div className="pulse-art"><div className="ring"></div><div className="ring"></div><div className="ring"></div><div className="core"></div></div></div>
          </div>

          <div className="reveal feat span4">
            <div className="feat-num">Error Trace Narration</div>
            <h3>Hear where a program broke.</h3>
            <p>CodeUp explains the crash location, the failing line, the error type, and the next thing to test.</p>
            <div className="feat-art">
              <div className="sandbox-art">
                <div className="row"><span>wall clock</span><b>5s</b></div>
                <div className="bar"><div className="fill"></div></div>
                <div className="row" style={{ marginTop: 10 }}><span>memory cap</span><b>512MB</b></div>
                <div className="bar"><div className="fill" style={{ animationDelay: "0.6s" }}></div></div>
                <div className="row" style={{ marginTop: 10 }}><span>trace events</span><b>5,000</b></div>
                <div className="bar"><div className="fill" style={{ animationDelay: "1.2s" }}></div></div>
              </div>
            </div>
          </div>

          <div className="reveal delay-1 feat span4">
            <div className="feat-num">Safe Apply and Reject</div>
            <h3>Review fixes before they land.</h3>
            <p>Students can hear what changed, accept a proposed fix, reject it, or undo the last change.</p>
            <div className="feat-art">
              <div className="bil-art">
                <div className="en">
                  <span>say</span>
                  <b>"run"</b>
                </div>
                <div className="arrow">⇄</div>
                <div className="en">
                  <span>type</span>
                  <b>run</b>
                </div>
              </div>
            </div>
          </div>

          <div className="reveal delay-2 feat span4">
            <div className="feat-num">Project Map</div>
            <h3>Understand files, functions, and entry points.</h3>
            <p>CodeUp can read project structure, imports, functions, comments, and where the program starts.</p>
            <div className="feat-art">
              <div className="ladder">
                <div className="row"><div className="lvl" style={{ width: "20%" }}></div><span className="lab">in 0</span></div>
                <div className="row"><div className="lvl" style={{ width: "35%" }}></div><span className="lab">in 1</span></div>
                <div className="row"><div className="lvl" style={{ width: "50%" }}></div><span className="lab">in 2</span></div>
                <div className="row"><div className="lvl" style={{ width: "70%" }}></div><span className="lab">in 3</span></div>
                <div className="row"><div className="lvl" style={{ width: "100%" }}></div><span className="lab">in 4</span></div>
              </div>
            </div>
          </div>

          <div className="reveal feat span12" id="manifesto">
            <div className="feat-num">State Watch and Teacher Reports</div>
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr 1fr", gap: 32 }}>
              <div>
                <h3 style={{ fontSize: 36, lineHeight: 1.05 }}>
                  A step before the professional toolchain.
                  <br />
                  <span style={{ color: "var(--accent)", fontStyle: "italic" }}>Not a replacement for it.</span>
                </h3>
                <p style={{ marginTop: 16 }}>
                  CodeUp is not trying to replace VS Code, GitHub, screen readers,
                  Braille workflows, or coding agents. It is a stepping stone
                  for learning code structure first.
                </p>
              </div>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 12, fontFamily: "var(--font-mono)", fontSize: 13 }}>
                <li>State Watch</li>
                <li>Project Map</li>
                <li>Error Trace Narration</li>
                <li>Safe Apply and Reject</li>
                <li>Teacher Reports</li>
              </ul>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 12, fontFamily: "var(--font-mono)", fontSize: 13 }}>
                <li>loops and indentation</li>
                <li>input and output</li>
                <li>variables while code runs</li>
                <li>project structure</li>
                <li>Audio Blocks</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Stats() {
  return (
    <section className="stats">
      <div className="stats-grid">
        <div className="reveal stat">
          <div className="num">01<span className="accent">.</span></div>
          <div className="lbl">Python Code Mode</div>
        </div>
        <div className="reveal delay-1 stat">
          <div className="num">02<span className="accent">.</span></div>
          <div className="lbl">Audio Blocks</div>
        </div>
        <div className="reveal delay-2 stat">
          <div className="num">03<span className="accent">.</span></div>
          <div className="lbl">Project Map</div>
        </div>
        <div className="reveal delay-3 stat">
          <div className="num">04<span className="accent">.</span></div>
          <div className="lbl">State Watch</div>
        </div>
        <div className="reveal stat">
          <div className="num">05<span className="accent">.</span></div>
          <div className="lbl">Teacher Reports</div>
        </div>
      </div>
    </section>
  );
}

function StartHere() {
  const steps = [
    'Open CodeUp.',
    'Allow microphone permission if you want to talk to CodeUp.',
    'No microphone? Use the command box. Type a command and press Enter.',
    'Try: "what can I do here".',
    'Try: "start tutorial" to begin the guided lessons.',
    'Try: "insert a for loop that prints the first 3 whole numbers".',
    'Try: "run" to run your program and hear the output.',
    'Try: "explain this code" to hear how your program works.',
  ];
  return (
    <section id="start-here" className="start-here" aria-labelledby="start-here-heading"
             style={{ padding: "72px 0", borderTop: "1px solid var(--line, rgba(0,0,0,0.12))" }}>
      <div className="wrap reveal">
        <span className="eyebrow">New here? Start in five minutes</span>
        <h2 id="start-here-heading" style={{ marginTop: 8 }}>
          A plain place to learn <span className="accent">Python basics</span>.
        </h2>
        <p style={{ maxWidth: 760, lineHeight: 1.7 }}>
          Most coding tools still treat sight like the default. Screen readers
          can read code, but beginners still need help understanding where a
          loop starts, where indentation changes, what line broke, and what
          changed after a fix.
        </p>
        <div className="start-here-grid"
             style={{ display: "flex", flexWrap: "wrap", gap: 32, marginTop: 24 }}>
          <div style={{ flex: "1 1 320px", minWidth: 280 }}>
            <h3 style={{ marginBottom: 12 }}>How to start</h3>
            <ol style={{ lineHeight: 1.8, paddingLeft: 22 }}>
              {steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </div>
          <div style={{ flex: "1 1 280px", minWidth: 260 }}>
            <h3 style={{ marginBottom: 12 }}>Before you begin</h3>
            <ul style={{ lineHeight: 1.8, paddingLeft: 22 }}>
              <li>Put on headphones, or turn on your speaker, so you can hear CodeUp.</li>
              <li>CodeUp works best on Google Chrome. Some browsers, especially Brave or privacy-heavy browsers, may block the microphone or speech. If voice does not work, open this page in Chrome.</li>
              <li>Allow the microphone if you want voice. You can always type commands in the command box instead.</li>
              <li>Say "help" any time, or "stop everything" to stop the talking.</li>
            </ul>
          </div>
        </div>
        <div className="cta-row" style={{ marginTop: 28 }}>
          <a className="btn btn-primary" href="/ide">
            Open CodeUp <span className="arrow">→</span>
          </a>
        </div>
      </div>
    </section>
  );
}

function CTA() {
  return (
    <section className="cta">
      <div className="reveal">
        <h2>
          Learn the shape.
          <br />
          <span className="accent">Then move outward.</span>
        </h2>
        <p>
          CodeUp currently focuses on English beginner Python workflows. It is
          for building structure and confidence before VS Code, GitHub, screen
          readers, Braille workflows, and coding agents.
        </p>
        <div className="cta-row">
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
    </section>
  );
}

function Foot() {
  return (
    <footer>
      <div>
        © 2025 · <a href="https://github.com/da-taki" target="_blank" rel="noreferrer">@da-taki</a> · MIT
      </div>
      <div className="meta">
        <span>v0.8.0 · coffee</span>
        <span>built with monaco · web audio · web speech</span>
        <a href="https://github.com/da-taki/Code_Up">github.com/da-taki/Code_Up</a>
      </div>
    </footer>
  );
}

window.Features = Features;
window.Stats = Stats;
window.StartHere = StartHere;
window.CTA = CTA;
window.Foot = Foot;
