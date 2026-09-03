// Classroom (cohort/assignment/guided-project/curriculum) integration for
// the IDE page. A no-op unless the page was opened with ?assignment=<id>,
// ?project=<id>, or ?module=<id> (set when a learner opens one from
// /classroom/learner or /classroom/curriculum). Talks to app.js only
// through the same optional-global-hook convention app.js already uses for
// the tutorial overlay (window._tutorialOnRunSuccess etc.), so the base IDE
// is completely unaffected when this script finds nothing to do.
(function () {
  'use strict';

  const params = new URLSearchParams(window.location.search);
  const assignmentId = params.get('assignment');
  const projectId = params.get('project');
  const moduleId = params.get('module');
  // No query param -> the IDE is being opened plainly (or a joined learner
  // just navigated back to it). This is now the primary learner workflow
  // (see spec "IDE should be the learner home"): render the always-on
  // Classroom panel (join form, or the classroom dashboard) instead of a
  // no-op, while the item-specific flows below stay exactly as they were.
  const mode = assignmentId ? 'assignment' : (projectId ? 'project' : (moduleId ? 'module' : 'dashboard'));
  const id = assignmentId || projectId || moduleId;
  let contextData = null;
  let submitting = false;
  let versionHistory = []; // [{label, code}], most recent last, capped
  let lastAiChange = null; // {before, after}

  // ---- small accessible-dialog helper (focus trap + restoration) ----------
  // Reused by the AI-change-review dialog below. Mirrors the same
  // role=dialog/aria-modal pattern already used by the IDE's own modals
  // (API key modal, command palette) so keyboard/screen-reader behavior is
  // consistent with the rest of the app rather than inventing a new pattern.
  const FocusTrap = (function () {
    let activeDialog = null;
    let returnFocusTo = null;
    let keyHandler = null;

    function focusableIn(container) {
      return Array.prototype.slice.call(
        container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
      ).filter(function (n) { return !n.disabled && n.offsetParent !== null; });
    }

    // Is `el` still a safe place to actually move focus to, right now? The
    // recorded opener is snapshotted when the dialog *opens*, but the app
    // keeps running while it's up - the opener can be removed, hidden, or
    // buried inside a disclosure that got collapsed again before the user
    // closes the dialog (confirmed live, Pass 5C classroom AI dialog check:
    // .focus() on a since-hidden element silently no-ops, leaving focus
    // wherever it happened to fall - often <body> or another hidden node).
    function isUsable(el) {
      return !!el && typeof el.focus === 'function' && el.isConnected
        && el !== document.body && !el.disabled && el.offsetParent !== null;
    }

    // Last-resort target when neither the recorded opener nor any of its
    // natural replacements are usable: the main heading, focusable via a
    // temporary tabindex. Always present, always visible, never hidden
    // inside a disclosure - this is what "somewhere useful, never body"
    // degrades to when nothing more specific is available.
    function safeFallback() {
      const editorInput = document.querySelector('.monaco-editor textarea');
      if (isUsable(editorInput)) return editorInput;
      const fixBtn = document.getElementById('fixBtn');
      if (isUsable(fixBtn)) return fixBtn;
      const heading = document.getElementById('pageTitle') || document.querySelector('h1');
      if (heading) {
        if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
        return heading;
      }
      return null;
    }

    function open(dialogEl, opts) {
      opts = opts || {};
      returnFocusTo = opts.returnFocusTo || document.activeElement;
      activeDialog = dialogEl;
      dialogEl.hidden = false;
      dialogEl.setAttribute('role', 'dialog');
      dialogEl.setAttribute('aria-modal', 'true');
      const focusables = focusableIn(dialogEl);
      (opts.initialFocus || focusables[0] || dialogEl).focus();

      keyHandler = function (evt) {
        if (evt.key === 'Escape' && opts.onEscape) {
          evt.preventDefault();
          opts.onEscape();
          return;
        }
        if (evt.key !== 'Tab') return;
        const items = focusableIn(dialogEl);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (evt.shiftKey && document.activeElement === first) {
          evt.preventDefault();
          last.focus();
        } else if (!evt.shiftKey && document.activeElement === last) {
          evt.preventDefault();
          first.focus();
        }
      };
      dialogEl.addEventListener('keydown', keyHandler);
    }

    function close() {
      if (!activeDialog) return;
      activeDialog.hidden = true;
      activeDialog.removeEventListener('keydown', keyHandler);
      activeDialog = null;
      const target = isUsable(returnFocusTo) ? returnFocusTo : safeFallback();
      if (target) target.focus();
      returnFocusTo = null;
    }

    return { open: open, close: close };
  }());

  function announce(text, opts) {
    opts = opts || {};
    // speak() is always sr:false here so Screen Reader Safe mode routes
    // through the explicit srAnnounce() call below instead of speak()'s own
    // fallback - but in CodeUp Voice mode speak() already narrates this
    // audibly, so srAnnounce() must be skipped there or every one of this
    // function's 40+ callers (assignment status, help requests, guided
    // lessons, submissions, quizzes...) would speak twice at once.
    var isCodeupVoice = typeof _speechMode !== 'undefined' && _speechMode === 'codeup-voice';
    if (typeof window.speak === 'function') window.speak(text, { sr: false });
    if (!isCodeupVoice && typeof window.srAnnounce === 'function') window.srAnnounce(text, opts.priority || 'polite');
  }

  // Progressive disclosure lives entirely in native <details>/<summary>
  // (matches the existing .cu-disclosure pattern already used elsewhere in
  // the IDE - see core.css). Commands that focus a target inside a collapsed
  // section (NAV_TARGETS / focus_hint in ide_commands.py) still need that
  // target to actually be focusable, so this walks up from the target and
  // opens every collapsed <details> ancestor before app.js calls .focus().
  window._classroomReveal = function (targetId) {
    const target = document.getElementById(targetId);
    if (!target) return false;
    let node = target.parentElement;
    while (node) {
      if (node.tagName === 'DETAILS' && !node.open) node.open = true;
      node = node.parentElement;
    }
    return true;
  };

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.assign(node, props || {});
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function pushVersion(label, code) {
    versionHistory.push({ label: label, code: code });
    if (versionHistory.length > 10) versionHistory.shift();
    renderVersionHistory();
  }

  function buildPanel() {
    const main = document.getElementById('mainContent');
    if (!main) return null;
    const section = el('section', {
      id: 'classroomPanel',
      className: 'cu-panel cu-classroom-panel',
    });
    section.setAttribute('aria-labelledby', 'classroomPanelHeading');
    main.insertBefore(section, main.firstChild);
    return section;
  }

  // ---- capability-aware UI (assessment mode / granular AI toggles) --------

  const CAPABILITY_BUTTONS = { explain: ['analyzeBtn'], fix: ['fixBtn'] };

  function applyCapabilitySettings(settings) {
    settings = settings || {};
    Object.keys(CAPABILITY_BUTTONS).forEach(function (cap) {
      const allowed = settings[cap] !== false;
      CAPABILITY_BUTTONS[cap].forEach(function (btnId) {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        btn.setAttribute('aria-disabled', allowed ? 'false' : 'true');
        btn.title = allowed ? '' : 'Turned off by your instructor for this assignment.';
      });
    });
  }

  // ---- help widget (shared by assignment/project/module panels) ----------

  function appendHelpWidget(panel, opts) {
    opts = opts || {};
    const heading = el('h3', { textContent: 'Ask your instructor for help' });
    if (opts.headingId) heading.id = opts.headingId;

    if (opts.currentHelpRequest) {
      // Already compact (heading + one status line + a cancel button) -
      // nothing to collapse further.
      const hr = opts.currentHelpRequest;
      const stateText = hr.status === 'helping' ? 'Your instructor is helping you now.' : 'Your help request is waiting for your instructor.';
      const hrStatus = el('p', { textContent: stateText });
      const cancelBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Cancel help request' });
      cancelBtn.addEventListener('click', function () {
        fetch('/classroom/help-requests/' + hr.id + '/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
          .then(function (res) { return res.json(); })
          .then(function (data) {
            if (data && data.success) {
              announce('Your help request was cancelled.');
              if (typeof window._classroomRefreshDashboard === 'function') window._classroomRefreshDashboard();
            }
          });
      });
      panel.appendChild(el('div', { className: 'cu-field' }, [heading, hrStatus, cancelBtn]));
      return;
    }

    // No request in flight: the form defaults to collapsed behind a single
    // compact "Request instructor help" control (see the accessibility
    // audit's progressive-disclosure pass) - the permanent textarea used to
    // take up a lot of vertical space for something rarely used.
    const label = el('label', { htmlFor: 'classroomHelpMessage', textContent: 'What do you need help with? (optional)' });
    const textarea = el('textarea', { id: 'classroomHelpMessage', rows: 2 });
    // No local live region here: this paragraph was sr-only (nothing visible
    // to justify keeping it) and every branch already calls the centralized
    // announce() below - a second aria-live region on invisible text would
    // only double the announcement, never add information.
    const status = el('p', { id: 'classroomHelpStatus', className: 'sr-only' });
    const sendBtn = el('button', { type: 'button', className: 'cu-button cu-button-primary', textContent: 'Send' });
    const cancelBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Cancel' });
    sendBtn.addEventListener('click', function () {
      const message = textarea.value;
      fetch('/classroom/help-requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message, assignment_id: assignmentId || null }),
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.success) {
            textarea.value = '';
            status.textContent = 'Help request sent to your instructor.';
            announce('Help request sent to your instructor.');
          } else {
            status.textContent = 'Could not send the help request. You can still keep working.';
            announce('Could not send the help request.');
          }
        })
        .catch(function () {
          status.textContent = 'Could not send the help request. You can still keep working.';
          announce(status.textContent);
        });
    });

    const details = el('details', { className: 'cu-disclosure' });
    const summary = el('summary', { className: 'cu-disclosure-summary', textContent: 'Request instructor help' });
    const actions = el('div', { role: 'group', ariaLabel: 'Help request actions' }, [sendBtn, cancelBtn]);
    const body = el('div', { className: 'cu-disclosure-body' }, [label, textarea, actions, status]);
    cancelBtn.addEventListener('click', function () {
      textarea.value = '';
      status.textContent = '';
      details.open = false;
      summary.focus();
    });
    details.appendChild(summary);
    details.appendChild(body);

    panel.appendChild(el('div', { className: 'cu-field' }, [heading, details]));
  }

  // ---- guided AI learning (hint ladder) - only when the capability allows it ----

  function appendGuidedAiWidget(panel, settings) {
    settings = settings || {};
    const actions = [
      { key: 'hint', label: 'Give me a hint', mode: 'tiny_hint' },
      { key: 'hint', label: "I'm stuck", mode: 'bigger_hint' },
      { key: 'explain', label: 'Explain the concept', mode: 'concept' },
      { key: 'explain', label: 'Show a small example', mode: 'concept' },
      { key: 'generate', label: 'Show the solution', mode: 'exact_fix' },
    ].filter(function (a) { return settings[a.key] !== false; });
    if (!actions.length) return;

    const heading = el('h3', { textContent: 'Ask for guidance' });
    // Visible status text, but not an independent live region: the reply is
    // already announced once via the centralized announce() below.
    const status = el('p', { id: 'classroomGuidedStatus' });
    const buttonRow = el('div', { role: 'group', ariaLabel: 'Guided AI learning actions' });
    actions.forEach(function (a) {
      const btn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: a.label });
      btn.addEventListener('click', function () { requestGuidance(a.mode, status); });
      buttonRow.appendChild(btn);
    });
    panel.appendChild(el('div', { className: 'cu-field' }, [heading, buttonRow, status]));
  }

  function requestGuidance(guidanceMode, statusEl) {
    const code = typeof window.getCode === 'function' ? window.getCode() : '';
    statusEl.textContent = 'Asking...';
    fetch('/mentor/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: code, message: 'Help me with this.', mode: guidanceMode,
        assignment_id: assignmentId || null, language: 'en',
      }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        const reply = (data && (data.reply || data.error)) || 'No response.';
        statusEl.textContent = reply;
        announce(reply);
      })
      .catch(function () {
        statusEl.textContent = 'Could not reach the AI mentor right now.';
        announce(statusEl.textContent);
      });
  }

  // ---- AI change review + undo + lightweight version history --------------

  function buildReviewDialog() {
    let dialog = document.getElementById('classroomReviewDialog');
    if (dialog) return dialog;
    dialog = el('div', { id: 'classroomReviewDialog', className: 'cu-notice' });
    dialog.hidden = true;
    dialog.style.position = 'fixed';
    dialog.style.top = '10%';
    dialog.style.left = '50%';
    dialog.style.transform = 'translateX(-50%)';
    dialog.style.maxWidth = '90%';
    dialog.style.width = '640px';
    dialog.style.zIndex = '1000';
    dialog.setAttribute('aria-labelledby', 'classroomReviewHeading');
    document.body.appendChild(dialog);
    return dialog;
  }

  function isFocusable(el) {
    return !!el && typeof el.focus === 'function' && el !== document.body && !el.disabled && el.offsetParent !== null;
  }

  function reviewAiFix(before, after, explanation) {
    // Capture the real opener *before* anything else can shift focus - the
    // fallback used to be `document.getElementById('fixBtn') || ...`, but
    // fixBtn always exists in the DOM (it just lives inside the collapsible
    // "More tools" disclosure, so it's frequently not actually visible),
    // and that `||` never fell through to the real opener anyway since
    // getElementById never itself returns a falsy value. If the Fix action
    // was reached via voice/keyboard, or the disclosure isn't open, the old
    // code silently failed to move focus anywhere - confirmed live (Pass 5C
    // classroom AI dialog check) as focus being left stranded on the
    // dialog's own (now-hidden) button. Fall all the way back to the editor,
    // matching every other dialog in this app, rather than risk that again.
    const fixBtn = document.getElementById('fixBtn');
    const editorInput = document.querySelector('.monaco-editor textarea');
    const opener = isFocusable(document.activeElement)
      ? document.activeElement
      : (isFocusable(fixBtn) ? fixBtn : editorInput);
    return new Promise(function (resolve) {
      const dialog = buildReviewDialog();
      dialog.innerHTML = '';
      dialog.appendChild(el('h2', { id: 'classroomReviewHeading', textContent: 'Review suggested change' }));
      dialog.appendChild(el('p', { textContent: explanation || 'The AI suggests a change to your code. Review it before it is applied.' }));

      const applyBtn = el('button', { type: 'button', className: 'cu-button cu-button-primary', textContent: 'Apply change' });
      const rejectBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Reject change' });
      const explainBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Explain change again' });
      dialog.appendChild(el('div', { role: 'group', ariaLabel: 'Change review actions' }, [applyBtn, rejectBtn, explainBtn]));

      function finish(applied) {
        FocusTrap.close();
        if (applied) {
          pushVersion('Before AI fix', before);
          if (typeof window.setCode === 'function') window.setCode(after, { preserveSpeech: true });
          pushVersion('After AI fix', after);
          lastAiChange = { before: before, after: after };
          renderUndoButton();
          announce('Change applied.');
        } else {
          announce('Change rejected. Your code was not changed.');
        }
        resolve(applied);
      }

      applyBtn.addEventListener('click', function () { finish(true); });
      rejectBtn.addEventListener('click', function () { finish(false); });
      explainBtn.addEventListener('click', function () { announce(explanation || 'No further explanation available.'); });

      FocusTrap.open(dialog, {
        returnFocusTo: opener,
        initialFocus: rejectBtn, // safer default than the primary action
        onEscape: function () { finish(false); },
      });
      announce('Review suggested change. ' + (explanation || ''));
    });
  }

  function renderUndoButton() {
    let btn = document.getElementById('classroomUndoAiBtn');
    if (!lastAiChange) {
      if (btn) btn.hidden = true;
      return;
    }
    const panel = document.getElementById('classroomPanel');
    if (!panel) return;
    if (!btn) {
      btn = el('button', { type: 'button', id: 'classroomUndoAiBtn', className: 'cu-button cu-button-secondary' });
      btn.addEventListener('click', function () {
        if (!lastAiChange) return;
        if (typeof window.setCode === 'function') window.setCode(lastAiChange.before, { preserveSpeech: true });
        pushVersion('Undo AI change', lastAiChange.before);
        announce('Undid the last AI change.');
        lastAiChange = null;
        renderUndoButton();
      });
      panel.appendChild(btn);
    }
    btn.hidden = false;
    btn.textContent = 'Undo last AI change';
  }

  function renderVersionHistory() {
    let container = document.getElementById('classroomVersionHistory');
    const panel = document.getElementById('classroomPanel');
    if (!panel) return;
    let body;
    if (!container) {
      container = el('details', { id: 'classroomVersionHistory', className: 'cu-disclosure' });
      container.appendChild(el('summary', { className: 'cu-disclosure-summary', textContent: 'Recent versions' }));
      body = el('div', { className: 'cu-disclosure-body' });
      container.appendChild(body);
      panel.appendChild(container);
    } else {
      body = container.querySelector('.cu-disclosure-body');
    }
    const list = body.querySelector('ul');
    if (list) list.remove();
    const ul = el('ul');
    versionHistory.slice().reverse().forEach(function (v, idx) {
      const btn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Restore: ' + v.label });
      btn.addEventListener('click', function () {
        if (typeof window.setCode === 'function') window.setCode(v.code, { preserveSpeech: true });
        announce('Restored version: ' + v.label);
      });
      ul.appendChild(el('li', {}, [btn]));
    });
    body.appendChild(ul);
  }

  // ---- assignment panel ----------------------------------------------------

  function renderAssignmentPanel(panel, data) {
    const a = data.assignment;
    const p = data.progress;
    panel.innerHTML = '';
    panel.appendChild(el('h2', { id: 'classroomPanelHeading', textContent: 'Assignment: ' + a.title }));

    // Plain text, not a live region: this is set once at render time and
    // never mutated afterward, so role="status" here would never actually
    // announce anything - it's read naturally in document order instead.
    const statusText = 'Status: ' + String(p.status || 'not_started').replace(/_/g, ' ') + (a.due_date ? ('. Due ' + a.due_date) : '');
    const statusEl = el('p', { id: 'classroomStatus', textContent: statusText });
    panel.appendChild(statusEl);

    const details = el('details', { className: 'cu-disclosure', open: true });
    details.appendChild(el('summary', { className: 'cu-disclosure-summary', textContent: 'Instructions' }));
    details.appendChild(el('div', { className: 'cu-disclosure-body' }, [
      el('p', { textContent: a.instructions || '(no instructions given)' }),
    ]));
    panel.appendChild(details);

    if (a.policy_summary) {
      panel.appendChild(el('p', { className: 'cu-notice', id: 'classroomPolicySummary', textContent: a.policy_summary }));
    }
    applyCapabilitySettings(a.capability_settings);

    // Visible status text, but not an independent live region: submit
    // outcomes are already announced once via the centralized announce().
    const submitStatus = el('p', { id: 'classroomSubmitStatus' });

    const submitBtn = el('button', {
      type: 'button', className: 'cu-button cu-button-primary',
      textContent: p.status === 'submitted' ? 'Re-submit assignment' : 'Submit assignment',
    });
    submitBtn.addEventListener('click', function () { submitAssignment(submitBtn, submitStatus); });
    panel.appendChild(submitBtn);
    panel.appendChild(submitStatus);

    // Obvious way back to the plain IDE once there's something to come back
    // from - visible immediately if already submitted (e.g. reopening this
    // page), and revealed live by submitAssignment() on a fresh submit.
    // Plain navigation (not a timer): never cuts off the submission speech,
    // never touches the classroom cookie/membership.
    const backLink = el('a', {
      id: 'classroomBackToIdeLink', className: 'cu-button cu-button-secondary',
      href: '/ide', textContent: 'Back to CodeUp', hidden: p.status !== 'submitted',
    });
    panel.appendChild(backLink);

    appendGuidedAiWidget(panel, a.capability_settings);
    appendHelpWidget(panel);

    if (p.code && typeof window.setCode === 'function') {
      window.setCode(p.code, { preserveSpeech: true, allowNonPython: false });
    }
    pushVersion('Opened assignment', p.code || a.starter_code || '');
    renderUndoButton();

    // Read the assignment once, right when it opens - title + instructions
    // (or a concise fallback if none were given), through the existing
    // centralized announce(). renderAssignmentPanel only ever runs once per
    // page load (see fetchContextAndRender's single 'assignment' branch;
    // background classroom sync never touches this panel - see
    // applyClassroomSync's `mode === 'dashboard'` guard), so this can never
    // repeat from polling or an unrelated DOM update.
    announce(a.title + '. ' + (a.instructions || 'No instructions were given for this assignment.'));
    // Announce the permission summary separately, still once.
    if (a.policy_summary) announce(a.policy_summary);
  }

  // ---- guided project panel -------------------------------------------------

  function renderProjectPanel(panel, data) {
    const project = data.project;
    const progress = data.progress;
    panel.innerHTML = '';
    panel.appendChild(el('h2', { id: 'classroomPanelHeading', textContent: 'Guided project: ' + project.title }));
    panel.appendChild(el('p', { textContent: project.description || '' }));
    panel.appendChild(el('p', { id: 'classroomProjectIntro', textContent: data.intro || '' }));

    const list = el('ul', { id: 'classroomCheckpointList' });
    const completed = (progress.checkpoints_completed || []);
    project.checkpoints.forEach(function (cp) {
      const done = completed.indexOf(cp.id) !== -1;
      list.appendChild(el('li', {
        textContent: (done ? 'Done: ' : 'Not yet: ') + cp.label,
      }));
    });
    panel.appendChild(el('h3', { textContent: 'Checkpoints' }));
    panel.appendChild(list);

    appendHelpWidget(panel);

    const code = progress.code || project.starter_code;
    if (code && typeof window.setCode === 'function') {
      window.setCode(code, { preserveSpeech: true, allowNonPython: false });
    }

    // Introduction/returning-progress speech - once per panel load, not
    // repeated on every checkpoint save. "repeat the project introduction"
    // (see ide_commands.py) re-fetches and re-speaks the same text on demand.
    if (data.intro) announce(data.intro);
  }

  // Visual-only checkpoint list refresh. Deliberately does NOT speak -
  // autosave (fired on every debounced keystroke save) must stay silent;
  // only a real Run announces progress, via speakProjectFeedback below.
  function updateCheckpointList(newlyCompleted, allCompleted) {
    const list = document.getElementById('classroomCheckpointList');
    if (!list || !contextData || !contextData.project) return;
    const items = list.querySelectorAll('li');
    contextData.project.checkpoints.forEach(function (cp, idx) {
      const done = (allCompleted || []).indexOf(cp.id) !== -1;
      if (items[idx]) items[idx].textContent = (done ? 'Done: ' : 'Not yet: ') + cp.label;
    });
  }

  // Humane, deterministic checkpoint feedback (see
  // codeup.classroom.learner_context) - spoken once, right after a Run.
  function speakProjectFeedback(feedback) {
    if (feedback) announce(feedback, { priority: 'polite' });
  }

  // ---- curriculum module / instructor lesson panel ---------------------------

  function renderModulePanel(panel, data) {
    const lesson = data.lesson;
    const progress = data.progress;
    panel.innerHTML = '';
    panel.appendChild(el('h2', { id: 'classroomPanelHeading', textContent: lesson.title }));

    // Plain text, not a live region - same reasoning as the assignment
    // panel's status line above: set once, never mutated afterward.
    const statusEl = el('p', { id: 'classroomStatus', textContent: 'Status: ' + (progress.status || 'not_started').replace(/_/g, ' ') });
    panel.appendChild(statusEl);

    panel.appendChild(el('h3', { textContent: 'Concept' }));
    panel.appendChild(el('p', { textContent: lesson.concept || '' }));

    if (lesson.example_code) {
      const exDetails = el('details', { className: 'cu-disclosure', open: true });
      exDetails.appendChild(el('summary', { className: 'cu-disclosure-summary', textContent: 'Example' }));
      const loadBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Load example into the editor' });
      loadBtn.addEventListener('click', function () {
        if (typeof window.setCode === 'function') window.setCode(lesson.example_code, { preserveSpeech: true });
        announce('Example loaded into the editor.');
      });
      exDetails.appendChild(el('div', { className: 'cu-disclosure-body' }, [
        el('pre', { className: 'cu-code-view', textContent: lesson.example_code }),
        loadBtn,
      ]));
      panel.appendChild(exDetails);
    }

    panel.appendChild(el('h3', { textContent: 'Your turn' }));
    panel.appendChild(el('p', { textContent: lesson.instructions || '' }));
    if (lesson.hints && lesson.hints.length) {
      const hintDetails = el('details', { className: 'cu-disclosure' });
      hintDetails.appendChild(el('summary', { className: 'cu-disclosure-summary', textContent: 'Hints' }));
      const hl = el('ul');
      lesson.hints.forEach(function (h) { hl.appendChild(el('li', { textContent: h })); });
      hintDetails.appendChild(el('div', { className: 'cu-disclosure-body' }, [hl]));
      panel.appendChild(hintDetails);
    }

    // Visible status text, but not an independent live region: feedback is
    // already announced once via the centralized announce() below.
    const attemptStatus = el('p', { id: 'classroomAttemptStatus' });
    const checkBtn = el('button', { type: 'button', className: 'cu-button cu-button-primary', textContent: 'Check my attempt' });
    checkBtn.addEventListener('click', function () { checkAttempt(attemptStatus); });
    panel.appendChild(checkBtn);
    panel.appendChild(attemptStatus);

    const attempted = (progress.completed_stages || []).indexOf('attempt') !== -1;
    if (attempted && lesson.challenge) {
      panel.appendChild(el('h3', { textContent: 'Challenge' }));
      panel.appendChild(el('p', { textContent: lesson.challenge }));
      const challengeStatus = el('p', { id: 'classroomChallengeStatus' });
      const challengeBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Check challenge' });
      challengeBtn.addEventListener('click', function () { checkChallenge(challengeStatus); });
      panel.appendChild(challengeBtn);
      panel.appendChild(challengeStatus);
    }

    if (lesson.quiz_question) {
      const quizLink = el('a', {
        className: 'cu-button cu-button-secondary',
        href: '/classroom/curriculum/' + encodeURIComponent(moduleId) + '/quiz',
        textContent: 'Take the quiz for this module',
      });
      panel.appendChild(el('p', {}, [quizLink]));
    }

    if (data.next_module_id) {
      const nextLink = el('a', {
        className: 'cu-button cu-button-secondary',
        href: '/classroom/curriculum/' + encodeURIComponent(data.next_module_id) + '/open',
        textContent: 'Next module',
      });
      panel.appendChild(el('p', {}, [nextLink]));
    }

    const backLink = el('a', { className: 'cu-button cu-button-secondary', href: '/classroom/curriculum', textContent: 'Back to course' });
    panel.appendChild(el('p', {}, [backLink]));

    appendHelpWidget(panel);

    if (lesson.example_code && (!progress.completed_stages || !progress.completed_stages.length) && typeof window.setCode === 'function') {
      window.setCode('', { preserveSpeech: true });
    }
  }

  function checkAttempt(statusEl) {
    const code = typeof window.getCode === 'function' ? window.getCode() : '';
    statusEl.textContent = 'Checking...';
    fetch('/classroom/curriculum/' + encodeURIComponent(moduleId) + '/attempt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        const feedback = (data && data.feedback) || 'Could not check your attempt.';
        statusEl.textContent = feedback;
        announce(feedback, { priority: data && data.passed ? 'polite' : 'assertive' });
        if (data && data.passed) {
          // Reload the panel so the Challenge section appears.
          fetchContextAndRender();
        }
      })
      .catch(function () {
        statusEl.textContent = 'Could not check your attempt right now.';
        announce(statusEl.textContent);
      });
  }

  function checkChallenge(statusEl) {
    const code = typeof window.getCode === 'function' ? window.getCode() : '';
    statusEl.textContent = 'Checking...';
    fetch('/classroom/curriculum/' + encodeURIComponent(moduleId) + '/challenge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        const feedback = (data && data.feedback) || 'Could not check the challenge.';
        statusEl.textContent = feedback;
        announce(feedback);
      })
      .catch(function () {
        statusEl.textContent = 'Could not check the challenge right now.';
        announce(statusEl.textContent);
      });
  }

  // ---- submit / autosave / run hooks ---------------------------------------

  function submitAssignment(button, statusEl) {
    if (submitting) return;
    submitting = true;
    const code = typeof window.getCode === 'function' ? window.getCode() : '';
    // Tell the server the submitted_at this tab last saw, so a tab that has
    // been open since before another tab (or device) already submitted
    // gets rejected instead of silently overwriting the newer submission
    // with stale code (Pass 5B multi-tab closure - see the matching
    // known_submitted_at check in routes.submit_assignment).
    const knownSubmittedAt = contextData && contextData.progress ? contextData.progress.submitted_at : null;
    fetch('/classroom/assignments/' + encodeURIComponent(assignmentId) + '/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code, known_submitted_at: knownSubmittedAt }),
    })
      .then(function (res) { return res.json().then(function (data) { return { status: res.status, data: data }; }); })
      .then(function (result) {
        submitting = false;
        const data = result.data;
        if (data && data.success) {
          if (contextData && contextData.progress) contextData.progress.submitted_at = data.submitted_at;
          statusEl.textContent = 'Assignment submitted.';
          button.textContent = 'Re-submit assignment';
          announce('Assignment submitted.');
          const backLink = document.getElementById('classroomBackToIdeLink');
          if (backLink) backLink.hidden = false;
        } else if (result.status === 409 && data && data.error === 'stale_submission') {
          // Deliberately do NOT adopt data.submitted_at here - doing so
          // would let a bare retry of the same (still-stale) code succeed
          // on the next click. Only an actual reload (re-fetching /context)
          // should be able to clear this warning.
          statusEl.textContent = data.message;
          announce(data.message, { priority: 'assertive' });
        } else {
          statusEl.textContent = 'Could not submit. Your work is still saved; you can try again.';
          announce('Could not submit the assignment. Your work is still saved.');
        }
      })
      .catch(function () {
        submitting = false;
        statusEl.textContent = 'Could not submit. Your work is still saved; you can try again.';
        announce(statusEl.textContent);
      });
  }

  function postJsonWithRetry(url, body, attemptsLeft) {
    attemptsLeft = attemptsLeft == null ? 1 : attemptsLeft;
    return fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).catch(function (err) {
      if (attemptsLeft > 0) return postJsonWithRetry(url, body, attemptsLeft - 1);
      throw err;
    });
  }

  function onAutosave(code) {
    if (mode === 'assignment') {
      postJsonWithRetry('/classroom/assignments/' + encodeURIComponent(id) + '/autosave', { code: code })
        .catch(function () { /* local autosave already has this covered */ });
    } else if (mode === 'project') {
      postJsonWithRetry('/classroom/projects/' + encodeURIComponent(id) + '/save', { code: code })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.success) updateCheckpointList(data.newly_completed, data.checkpoints_completed);
        })
        .catch(function () {});
    }
  }

  function onRunResult(code, ranOk, errorText) {
    if (mode === 'assignment') {
      postJsonWithRetry('/classroom/assignments/' + encodeURIComponent(id) + '/run-result', {
        code: code, ran_ok: !!ranOk, error: errorText || '',
      }).catch(function () {});
    } else if (mode === 'project') {
      // Same save endpoint as onAutosave, but a real Run also speaks the
      // returned humane feedback - autosave-while-typing stays silent.
      postJsonWithRetry('/classroom/projects/' + encodeURIComponent(id) + '/save', { code: code })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.success) {
            updateCheckpointList(data.newly_completed, data.checkpoints_completed);
            speakProjectFeedback(data.feedback);
          }
        })
        .catch(function () {});
    } else if (mode === 'module') {
      const statusEl = document.getElementById('classroomAttemptStatus');
      if (statusEl) checkAttempt(statusEl);
    }
  }

  // ---- classroom dashboard (plain /ide entry - the primary learner workflow) --
  //
  // Renders one of two states, depending on /classroom/ide/summary:
  //   joined: false  -> an accessible Join Classroom form (native inputs,
  //                      reaches the same learner_actions.join_cohort_by_code
  //                      the classic /classroom/join page and the "join
  //                      <code>" voice/typed command both use)
  //   joined: true   -> a compact classroom panel (current learning,
  //                      assignments, guided projects, help) - never a
  //                      second, competing "classroom page"
  //
  // Welcome/orientation text is spoken from data.welcome_message /
  // data.orientation_message, computed server-side in
  // codeup.classroom.learner_context, so the panel's visible text and
  // CodeUp's spoken announcement share the exact same wording.

  function describeAssignmentCounts(counts) {
    if (!counts || !counts.remaining) return 'You have no assignments left.';
    let text = 'You have ' + counts.remaining + ' assignment' + (counts.remaining === 1 ? '' : 's') + ' left';
    const extras = [];
    if (counts.new) extras.push(counts.new + ' new');
    if (counts.overdue) extras.push(counts.overdue + ' overdue');
    if (extras.length) text += ', including ' + extras.join(' and ');
    return text + '.';
  }

  // The compact label doubles as the single Classroom disclosure's <summary>
  // text (see renderJoinPanel/renderDashboardPanel below) and is refreshed
  // in place by patchDashboardPanel() during background sync - anonymous
  // "Classroom", joined "Classroom · <cohort> · N pending"/"All caught up".
  function summaryLineText(data) {
    if (!data || !data.joined) return 'Classroom';
    const cohortName = (data.cohort && data.cohort.name) || (data.learner && data.learner.display_name) || '';
    const remaining = (data.assignment_counts && data.assignment_counts.remaining) || 0;
    const countText = remaining ? (remaining + ' pending') : 'All caught up';
    return 'Classroom · ' + cohortName + ' · ' + countText;
  }

  function fillContainer(container, nodes) {
    container.innerHTML = '';
    nodes.forEach(function (n) { container.appendChild(n); });
  }

  function buildCourseBodyNodes(data) {
    if (data.module) {
      const progText = (data.module.index && data.module.total)
        ? ('Module ' + data.module.index + ' of ' + data.module.total + ': ' + data.module.title)
        : data.module.title;
      return [
        el('p', { textContent: progText }),
        el('p', {}, [el('a', {
          className: 'cu-button cu-button-secondary',
          href: '/classroom/curriculum/' + encodeURIComponent(data.module.module_id) + '/open',
          textContent: 'Continue',
        })]),
      ];
    }
    return [
      el('p', { textContent: "You haven't started the course yet." }),
      el('p', {}, [el('a', { className: 'cu-button cu-button-secondary', href: '/classroom/curriculum', textContent: 'Start the course' })]),
    ];
  }

  function buildAssignmentsBodyNodes(data, keepOpen) {
    const nodes = [el('p', { textContent: describeAssignmentCounts(data.assignment_counts) })];
    if (data.assignments && data.assignments.length) {
      const assignmentDetails = el('details', { className: 'cu-disclosure', open: !!keepOpen });
      assignmentDetails.appendChild(el('summary', { className: 'cu-disclosure-summary', textContent: 'Show all assignments (' + data.assignments.length + ')' }));
      const list = el('ul');
      data.assignments.forEach(function (a) {
        const item = el('li');
        item.appendChild(el('a', { href: a.open_url, textContent: a.title + ' — ' + a.state.replace(/_/g, ' ') }));
        list.appendChild(item);
      });
      assignmentDetails.appendChild(el('div', { className: 'cu-disclosure-body' }, [list]));
      assignmentDetails.addEventListener('toggle', function () {
        if (assignmentDetails.open) {
          fetch('/classroom/ide/assignments-seen', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(function () {});
        }
      });
      nodes.push(assignmentDetails);
    } else {
      nodes.push(el('p', { textContent: 'No assignments yet.' }));
    }
    return nodes;
  }

  function buildProjectsBodyNodes(data) {
    if (data.projects && data.projects.length) {
      const list = el('ul');
      data.projects.forEach(function (p) {
        const item = el('li');
        const progress = p.total_checkpoints ? (p.done_checkpoints + ' of ' + p.total_checkpoints + ' checkpoints') : '';
        item.appendChild(el('a', { href: p.open_url, textContent: p.title + (progress ? ' — ' + progress : '') }));
        list.appendChild(item);
      });
      return [list];
    }
    return [el('p', { textContent: 'No guided projects available yet.' })];
  }

  function renderJoinPanel(panel, data) {
    panel.innerHTML = '';

    // One compact Classroom disclosure, collapsed by default - matches the
    // existing "CodeUp display and accessibility settings" disclosure
    // pattern (.cu-disclosure/.cu-disclosure-summary) instead of a second
    // visual language. Anonymous + collapsed shows nothing but this label;
    // "join a classroom"/"join a cohort" and the classroomJoinHeading/Code/
    // Name focus targets in ide_commands.py open it via window._classroomReveal.
    const details = el('details', { id: 'classroomDetails', className: 'cu-disclosure' });
    const summary = el('summary', { id: 'classroomPanelHeading', className: 'cu-disclosure-summary', textContent: summaryLineText(data) });
    details.appendChild(summary);

    // Visible status text, but not an independent live region: every branch
    // below already calls the centralized announce() with the same message.
    const status = el('p', { id: 'classroomJoinStatus' });

    const heading = el('h2', { id: 'classroomJoinHeading', textContent: 'Join a classroom' });
    const codeField = el('div', { className: 'cu-field' }, [
      el('label', { htmlFor: 'classroomJoinCode', textContent: 'Classroom code' }),
      el('input', { id: 'classroomJoinCode', type: 'text', autocomplete: 'off' }),
    ]);
    const nameField = el('div', { className: 'cu-field' }, [
      el('label', { htmlFor: 'classroomJoinName', textContent: 'Your name' }),
      el('input', { id: 'classroomJoinName', type: 'text', autocomplete: 'name' }),
    ]);
    const codeInput = codeField.querySelector('input');
    const nameInput = nameField.querySelector('input');
    const joinBtn = el('button', { type: 'button', className: 'cu-button cu-button-primary', textContent: 'Join classroom' });

    let joining = false;
    function doJoin() {
      if (joining) return; // guards double-click / repeated Enter
      const code = codeInput.value.trim();
      const name = nameInput.value.trim();
      if (!code || !name) {
        status.textContent = 'Enter a classroom code and your name.';
        announce(status.textContent);
        return;
      }
      joining = true;
      joinBtn.disabled = true;
      status.textContent = 'Joining...';
      fetch('/classroom/join-api', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ join_code: code, display_name: name }),
      })
        .then(function (res) { return res.json(); })
        .then(function (result) {
          if (result && result.success) {
            announce(result.message || 'Joined.');
            fetchContextAndRender(); // panel re-renders as joined; no need to re-enable the old button
          } else {
            joining = false;
            joinBtn.disabled = false;
            status.textContent = (result && result.message) || 'Could not join. Check the code and try again.';
            announce(status.textContent);
          }
        })
        .catch(function () {
          joining = false;
          joinBtn.disabled = false;
          status.textContent = 'Could not join right now. Check your connection and try again.';
          announce(status.textContent);
        });
    }
    joinBtn.addEventListener('click', doJoin);
    [codeInput, nameInput].forEach(function (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); doJoin(); }
      });
    });

    // The Join button always gets its own row (never crammed beside the name
    // input) - see .cu-classroom-join-actions in classroom.css.
    const actionsRow = el('div', { className: 'cu-classroom-join-actions' }, [joinBtn]);

    const body = el('div', { className: 'cu-disclosure-body' }, [
      heading, codeField, nameField, actionsRow, status,
      el('p', { className: 'cu-command-tip', textContent: 'You can also type "join a classroom" in the command box.' }),
    ]);
    details.appendChild(body);
    panel.appendChild(details);

    if (!sessionStorage.getItem('codeupIdeNoCohortOrientationSpoken')) {
      sessionStorage.setItem('codeupIdeNoCohortOrientationSpoken', '1');
      announce(data.orientation_message || '');
    }
  }

  function renderDashboardPanel(panel, data) {
    panel.innerHTML = '';

    // Same single Classroom disclosure as the anonymous state, just with a
    // status-bearing summary line and different body content - never a
    // second, visually separate "classroom app" bolted onto the IDE.
    const details = el('details', { id: 'classroomDetails', className: 'cu-disclosure' });
    const summary = el('summary', { id: 'classroomPanelHeading', className: 'cu-disclosure-summary', textContent: summaryLineText(data) });
    details.appendChild(summary);

    const body = el('div', { className: 'cu-disclosure-body' });

    body.appendChild(el('p', { className: 'cu-classroom-leave' }, [el('a', { className: 'cu-button cu-button-secondary', href: '/classroom/leave/confirm', textContent: 'Leave this classroom' })]));

    body.appendChild(el('h3', { id: 'classroomCourseHeading', textContent: 'Current learning' }));
    body.appendChild(el('div', { id: 'classroomCourseBody' }, buildCourseBodyNodes(data)));

    body.appendChild(el('h3', { id: 'classroomAssignmentsHeading', textContent: 'Assignments' }));
    body.appendChild(el('div', { id: 'classroomAssignmentsBody' }, buildAssignmentsBodyNodes(data, false)));

    body.appendChild(el('h3', { id: 'classroomProjectsHeading', textContent: 'Guided projects' }));
    body.appendChild(el('div', { id: 'classroomProjectsBody' }, buildProjectsBodyNodes(data)));

    const helpContainer = el('div', { id: 'classroomHelpContainer' });
    appendHelpWidget(helpContainer, { headingId: 'classroomHelpHeading', currentHelpRequest: data.help_request });
    body.appendChild(helpContainer);

    details.appendChild(body);
    // Opening the disclosure is a natural moment to catch up on anything the
    // last background poll missed - see requestClassroomSync() below. This
    // never opens the disclosure itself, only reacts to the learner doing so.
    details.addEventListener('toggle', function () {
      if (details.open) requestClassroomSync({ immediate: true });
    });
    panel.appendChild(details);

    if (!data.ide_orientation_shown) {
      fetch('/classroom/ide/orientation-seen', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(function () {});
      announce(data.orientation_message || data.welcome_message || '');
    } else {
      announce(data.welcome_message || '');
    }
  }

  // Only the specific sub-section whose fingerprint changed gets rebuilt -
  // patchDashboardPanel() never touches panel.innerHTML, so the outer
  // <details>'s open state, keyboard focus, and any in-progress help-request
  // text all survive a background sync untouched (see requestClassroomSync).
  function patchDashboardPanel(previous, data) {
    const summaryEl = document.getElementById('classroomPanelHeading');
    if (summaryEl) {
      const newLine = summaryLineText(data);
      if (summaryEl.textContent !== newLine) summaryEl.textContent = newLine;
    }

    if (moduleFingerprint(previous && previous.module) !== moduleFingerprint(data.module)) {
      const courseBody = document.getElementById('classroomCourseBody');
      if (courseBody) fillContainer(courseBody, buildCourseBodyNodes(data));
    }

    if (assignmentsFingerprint(previous && previous.assignments) !== assignmentsFingerprint(data.assignments)) {
      const assignmentsBody = document.getElementById('classroomAssignmentsBody');
      if (assignmentsBody) {
        const existingDetails = assignmentsBody.querySelector('details');
        const keepOpen = existingDetails ? existingDetails.open : false;
        fillContainer(assignmentsBody, buildAssignmentsBodyNodes(data, keepOpen));
      }
    }

    if (projectsFingerprint(previous && previous.projects) !== projectsFingerprint(data.projects)) {
      const projectsBody = document.getElementById('classroomProjectsBody');
      if (projectsBody) fillContainer(projectsBody, buildProjectsBodyNodes(data));
    }

    if (helpFingerprint(previous && previous.help_request) !== helpFingerprint(data.help_request)) {
      const helpContainer = document.getElementById('classroomHelpContainer');
      if (helpContainer) {
        helpContainer.innerHTML = '';
        appendHelpWidget(helpContainer, { headingId: 'classroomHelpHeading', currentHelpRequest: data.help_request });
      }
    }
  }

  // ---- live classroom sync --------------------------------------------------
  //
  // Reuses the existing GET /classroom/ide/summary (no new endpoint, no
  // WebSocket/SSE) so a joined learner's Classroom panel picks up instructor
  // changes - a newly published assignment, a help request being answered -
  // without a reload. One shared timer, one in-flight guard: there is never
  // more than one summary request outstanding at a time, and the loop pauses
  // entirely while the tab is hidden. Every tick is silent by default -
  // announce() only fires for a genuinely new event (see applyClassroomSync),
  // never "classroom refreshed" chatter, and DOM updates go through
  // patchDashboardPanel's targeted per-section diff, never a full rebuild.

  const SYNC_INTERVAL_MS = 9000;
  const MIN_SYNC_GAP_MS = 1500;

  const syncState = {
    active: false,
    timer: null,
    inFlight: false,
    lastSyncAt: 0,
    lastSummary: null,
    announcedAssignmentIds: null, // {id: true}
    lastHelpStatus: null,
  };

  function moduleFingerprint(m) { return m ? JSON.stringify([m.module_id, m.index, m.total, m.title]) : 'none'; }
  function assignmentsFingerprint(list) { return JSON.stringify((list || []).map(function (a) { return [a.id, a.state, a.title]; })); }
  function projectsFingerprint(list) { return JSON.stringify((list || []).map(function (p) { return [p.id, p.done_checkpoints, p.total_checkpoints]; })); }
  function helpFingerprint(hr) { return hr ? JSON.stringify([hr.id, hr.status]) : 'none'; }

  // Assignments already present the moment sync starts are "already known,"
  // not a live event - only an id that shows up AFTER this point is a real
  // announcement candidate. This is client-side, session-only bookkeeping
  // (cleared on reload) layered on top of the server's authoritative new/
  // pending/seen state, never a replacement for it - see
  // codeup.classroom.db.mark_assignments_seen, which this never calls.
  function seedSyncBaseline(data) {
    syncState.lastSummary = data;
    const seen = {};
    (data.assignments || []).forEach(function (a) { seen[a.id] = true; });
    syncState.announcedAssignmentIds = seen;
    syncState.lastHelpStatus = data.help_request ? data.help_request.status : null;
  }

  function stopClassroomSync() {
    syncState.active = false;
    if (syncState.timer) { clearInterval(syncState.timer); syncState.timer = null; }
  }

  function startClassroomSync() {
    if (syncState.active) return;
    syncState.active = true;
    if (!document.hidden && !syncState.timer) {
      syncState.timer = setInterval(function () { requestClassroomSync(); }, SYNC_INTERVAL_MS);
    }
  }

  function requestClassroomSync(opts) {
    opts = opts || {};
    if (!syncState.active || document.hidden || syncState.inFlight) return;
    const now = Date.now();
    if (now - syncState.lastSyncAt < MIN_SYNC_GAP_MS) return; // de-dupe back-to-back triggers (focus + visibilitychange, etc.)
    syncState.inFlight = true;
    fetch('/classroom/ide/summary')
      .then(function (res) { return res.json(); })
      .then(function (data) {
        syncState.inFlight = false;
        syncState.lastSyncAt = Date.now();
        if (!data || !data.success) return; // silent - next opportunity retries automatically
        applyClassroomSync(data);
      })
      .catch(function () {
        syncState.inFlight = false;
        syncState.lastSyncAt = Date.now();
        // Background sync failure is silent and non-destructive - never
        // replaces the panel with an error, never announces anything.
      });
  }

  function applyClassroomSync(data) {
    const previous = syncState.lastSummary;

    if (!data.joined) {
      const wasJoined = !!(previous && previous.joined);
      syncState.lastSummary = data;
      if (wasJoined) {
        // Learner was removed/left via another tab or command - this is a
        // real structural transition (joined <-> anonymous), not a routine
        // poll, so a full re-render (not a targeted patch) is correct here.
        stopClassroomSync();
        if (mode === 'dashboard') fetchContextAndRender();
      }
      return;
    }

    const announced = syncState.announcedAssignmentIds || {};
    (data.assignments || []).forEach(function (a) {
      if (!announced[a.id]) {
        announced[a.id] = true;
        if (a.state === 'new') announce('New assignment: ' + a.title + '.');
      }
    });
    syncState.announcedAssignmentIds = announced;

    const newHelpStatus = data.help_request ? data.help_request.status : null;
    if (newHelpStatus === 'helping' && syncState.lastHelpStatus !== 'helping') {
      announce('Your instructor is helping you now.');
    }
    syncState.lastHelpStatus = newHelpStatus;

    // Outside the dashboard panel (an assignment/project/lesson is the
    // active content) there is nothing to patch - the active panel is never
    // replaced by background sync - but the announcement above still fires.
    if (mode === 'dashboard') patchDashboardPanel(previous, data);

    syncState.lastSummary = data;
  }

  // Registered once at module scope (harmless no-ops until startClassroomSync
  // has run) so the demo's teacher-tab/learner-tab switching gets an
  // immediate catch-up instead of waiting out the full interval.
  document.addEventListener('visibilitychange', function () {
    if (!syncState.active) return;
    if (document.hidden) {
      if (syncState.timer) { clearInterval(syncState.timer); syncState.timer = null; }
    } else {
      requestClassroomSync({ immediate: true });
      if (!syncState.timer) syncState.timer = setInterval(function () { requestClassroomSync(); }, SYNC_INTERVAL_MS);
    }
  });
  window.addEventListener('focus', function () {
    if (syncState.active && !document.hidden) requestClassroomSync({ immediate: true });
  });

  // ---- init ------------------------------------------------------------------

  function fetchContextAndRender() {
    const panel = document.getElementById('classroomPanel') || buildPanel();
    if (!panel) return;
    panel.innerHTML = '';
    panel.appendChild(el('p', { textContent: 'Loading...' }));

    if (mode === 'dashboard') {
      // Registered unconditionally (not just once joined) so a classroom
      // command that succeeds while the Join panel is still showing (e.g.
      // typed/spoken "join ABC123") can refresh straight to the joined
      // dashboard - see app.js's generic `classroom_refresh` check.
      window._classroomRefreshDashboard = fetchContextAndRender;
      // Shares syncState.inFlight with requestClassroomSync so a manual
      // refresh (join success, help-cancel) and the background poll never
      // both have a /classroom/ide/summary request in flight at once.
      syncState.inFlight = true;
      fetch('/classroom/ide/summary')
        .then(function (res) { return res.json(); })
        .then(function (data) {
          syncState.inFlight = false;
          if (!data || !data.success) {
            panel.innerHTML = '';
            panel.appendChild(el('p', {
              className: 'cu-notice cu-notice--error',
              textContent: 'Could not load the classroom panel. You can still use CodeUp normally.',
            }));
            return;
          }
          contextData = data;
          if (data.joined) {
            renderDashboardPanel(panel, data);
            seedSyncBaseline(data);
            startClassroomSync();
          } else {
            renderJoinPanel(panel, data);
            stopClassroomSync();
          }
        })
        .catch(function () {
          syncState.inFlight = false;
          panel.innerHTML = '';
          panel.appendChild(el('p', {
            className: 'cu-notice cu-notice--error',
            textContent: 'Could not load the classroom panel. You can still use CodeUp normally.',
          }));
        });
      return;
    }

    const url = mode === 'assignment' ? '/classroom/assignments/' + encodeURIComponent(id) + '/context'
      : mode === 'project' ? '/classroom/projects/' + encodeURIComponent(id) + '/context'
      : '/classroom/curriculum/' + encodeURIComponent(id) + '/context';

    fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !data.success) {
          panel.innerHTML = '';
          panel.appendChild(el('p', {
            className: 'cu-notice cu-notice--error',
            textContent: 'Could not load this ' + mode + '. You can still use CodeUp normally.',
          }));
          return;
        }
        contextData = data;
        if (mode === 'assignment') renderAssignmentPanel(panel, data);
        else if (mode === 'project') renderProjectPanel(panel, data);
        else renderModulePanel(panel, data);
      })
      .catch(function () {
        panel.innerHTML = '';
        panel.appendChild(el('p', {
          className: 'cu-notice cu-notice--error',
          textContent: 'Could not load this ' + mode + '. You can still use CodeUp normally.',
        }));
      });
  }

  function init() {
    fetchContextAndRender();
    window._classroomOnAutosave = onAutosave;
    window._classroomOnRunResult = onRunResult;
    if (mode === 'assignment') {
      window._classroomReviewFix = reviewAiFix;
    }
    if (mode !== 'dashboard') {
      // The dashboard branch of fetchContextAndRender seeds the sync
      // baseline itself (reusing that same fetch); other modes need one
      // dedicated /classroom/ide/summary call so a newly published
      // assignment can still be announced while an assignment/project/
      // lesson panel is the active content (see applyClassroomSync).
      fetch('/classroom/ide/summary')
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (!data || !data.success || !data.joined) return;
          seedSyncBaseline(data);
          startClassroomSync();
        })
        .catch(function () {});
    }
  }

  window._classroomInit = init;
})();
