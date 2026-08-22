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
  if (!assignmentId && !projectId && !moduleId) return;

  const mode = assignmentId ? 'assignment' : (projectId ? 'project' : 'module');
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
      if (returnFocusTo && typeof returnFocusTo.focus === 'function') returnFocusTo.focus();
      returnFocusTo = null;
    }

    return { open: open, close: close };
  }());

  function announce(text, opts) {
    opts = opts || {};
    if (typeof window.speak === 'function') window.speak(text, { sr: false });
    if (typeof window.srAnnounce === 'function') window.srAnnounce(text, opts.priority || 'polite');
  }

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

  function appendHelpWidget(panel) {
    const heading = el('h3', { textContent: 'Ask your instructor for help' });
    const label = el('label', { htmlFor: 'classroomHelpMessage', textContent: 'What do you need help with? (optional)' });
    const textarea = el('textarea', { id: 'classroomHelpMessage', rows: 2 });
    const status = el('p', { id: 'classroomHelpStatus', className: 'sr-only' });
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const button = el('button', {
      type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Request help',
    });
    button.addEventListener('click', function () {
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
        });
    });
    panel.appendChild(el('div', { className: 'cu-field' }, [heading, label, textarea, button, status]));
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
    const status = el('p', { id: 'classroomGuidedStatus' });
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
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
      .catch(function () { statusEl.textContent = 'Could not reach the AI mentor right now.'; });
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

  function reviewAiFix(before, after, explanation) {
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
        returnFocusTo: document.getElementById('fixBtn') || document.activeElement,
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
    if (!container) {
      container = el('details', { id: 'classroomVersionHistory' });
      container.appendChild(el('summary', { textContent: 'Recent versions' }));
      panel.appendChild(container);
    }
    const list = container.querySelector('ul');
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
    container.appendChild(ul);
  }

  // ---- assignment panel ----------------------------------------------------

  function renderAssignmentPanel(panel, data) {
    const a = data.assignment;
    const p = data.progress;
    panel.innerHTML = '';
    panel.appendChild(el('h2', { id: 'classroomPanelHeading', textContent: 'Assignment: ' + a.title }));

    const statusText = 'Status: ' + String(p.status || 'not_started').replace(/_/g, ' ') + (a.due_date ? ('. Due ' + a.due_date) : '');
    const statusEl = el('p', { id: 'classroomStatus', textContent: statusText });
    statusEl.setAttribute('role', 'status');
    panel.appendChild(statusEl);

    const details = el('details', { open: true });
    details.appendChild(el('summary', { textContent: 'Instructions' }));
    details.appendChild(el('p', { textContent: a.instructions || '(no instructions given)' }));
    panel.appendChild(details);

    if (a.policy_summary) {
      panel.appendChild(el('p', { className: 'cu-notice', id: 'classroomPolicySummary', textContent: a.policy_summary }));
    }
    applyCapabilitySettings(a.capability_settings);

    const submitStatus = el('p', { id: 'classroomSubmitStatus' });
    submitStatus.setAttribute('role', 'status');
    submitStatus.setAttribute('aria-live', 'polite');

    const submitBtn = el('button', {
      type: 'button', className: 'cu-button cu-button-primary',
      textContent: p.status === 'submitted' ? 'Re-submit assignment' : 'Submit assignment',
    });
    submitBtn.addEventListener('click', function () { submitAssignment(submitBtn, submitStatus); });
    panel.appendChild(submitBtn);
    panel.appendChild(submitStatus);

    appendGuidedAiWidget(panel, a.capability_settings);
    appendHelpWidget(panel);

    if (p.code && typeof window.setCode === 'function') {
      window.setCode(p.code, { preserveSpeech: true, allowNonPython: false });
    }
    pushVersion('Opened assignment', p.code || a.starter_code || '');
    renderUndoButton();

    // Announce the permission summary once, not on every action.
    if (a.policy_summary) announce(a.policy_summary);
  }

  // ---- guided project panel -------------------------------------------------

  function renderProjectPanel(panel, data) {
    const project = data.project;
    const progress = data.progress;
    panel.innerHTML = '';
    panel.appendChild(el('h2', { id: 'classroomPanelHeading', textContent: 'Guided project: ' + project.title }));
    panel.appendChild(el('p', { textContent: project.description || '' }));

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
  }

  function updateCheckpointList(newlyCompleted, allCompleted) {
    if (!newlyCompleted || !newlyCompleted.length) return;
    const list = document.getElementById('classroomCheckpointList');
    if (list && contextData && contextData.project) {
      const items = list.querySelectorAll('li');
      contextData.project.checkpoints.forEach(function (cp, idx) {
        const done = allCompleted.indexOf(cp.id) !== -1;
        if (items[idx]) items[idx].textContent = (done ? 'Done: ' : 'Not yet: ') + cp.label;
      });
    }
    newlyCompleted.forEach(function (cpId) {
      const cp = (contextData.project.checkpoints || []).find(function (c) { return c.id === cpId; });
      if (cp) announce('Checkpoint complete: ' + cp.label, { priority: 'polite' });
    });
  }

  // ---- curriculum module / instructor lesson panel ---------------------------

  function renderModulePanel(panel, data) {
    const lesson = data.lesson;
    const progress = data.progress;
    panel.innerHTML = '';
    panel.appendChild(el('h2', { id: 'classroomPanelHeading', textContent: lesson.title }));

    const statusEl = el('p', { id: 'classroomStatus', textContent: 'Status: ' + (progress.status || 'not_started').replace(/_/g, ' ') });
    statusEl.setAttribute('role', 'status');
    panel.appendChild(statusEl);

    panel.appendChild(el('h3', { textContent: 'Concept' }));
    panel.appendChild(el('p', { textContent: lesson.concept || '' }));

    if (lesson.example_code) {
      const exDetails = el('details', { open: true });
      exDetails.appendChild(el('summary', { textContent: 'Example' }));
      exDetails.appendChild(el('pre', { className: 'cu-code-view', textContent: lesson.example_code }));
      const loadBtn = el('button', { type: 'button', className: 'cu-button cu-button-secondary', textContent: 'Load example into the editor' });
      loadBtn.addEventListener('click', function () {
        if (typeof window.setCode === 'function') window.setCode(lesson.example_code, { preserveSpeech: true });
        announce('Example loaded into the editor.');
      });
      exDetails.appendChild(loadBtn);
      panel.appendChild(exDetails);
    }

    panel.appendChild(el('h3', { textContent: 'Your turn' }));
    panel.appendChild(el('p', { textContent: lesson.instructions || '' }));
    if (lesson.hints && lesson.hints.length) {
      const hintDetails = el('details');
      hintDetails.appendChild(el('summary', { textContent: 'Hints' }));
      const hl = el('ul');
      lesson.hints.forEach(function (h) { hl.appendChild(el('li', { textContent: h })); });
      hintDetails.appendChild(hl);
      panel.appendChild(hintDetails);
    }

    const attemptStatus = el('p', { id: 'classroomAttemptStatus' });
    attemptStatus.setAttribute('role', 'status');
    attemptStatus.setAttribute('aria-live', 'polite');
    const checkBtn = el('button', { type: 'button', className: 'cu-button cu-button-primary', textContent: 'Check my attempt' });
    checkBtn.addEventListener('click', function () { checkAttempt(attemptStatus); });
    panel.appendChild(checkBtn);
    panel.appendChild(attemptStatus);

    const attempted = (progress.completed_stages || []).indexOf('attempt') !== -1;
    if (attempted && lesson.challenge) {
      panel.appendChild(el('h3', { textContent: 'Challenge' }));
      panel.appendChild(el('p', { textContent: lesson.challenge }));
      const challengeStatus = el('p', { id: 'classroomChallengeStatus' });
      challengeStatus.setAttribute('role', 'status');
      challengeStatus.setAttribute('aria-live', 'polite');
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
      .catch(function () { statusEl.textContent = 'Could not check your attempt right now.'; });
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
      .catch(function () { statusEl.textContent = 'Could not check the challenge right now.'; });
  }

  // ---- submit / autosave / run hooks ---------------------------------------

  function submitAssignment(button, statusEl) {
    if (submitting) return;
    submitting = true;
    const code = typeof window.getCode === 'function' ? window.getCode() : '';
    fetch('/classroom/assignments/' + encodeURIComponent(assignmentId) + '/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        submitting = false;
        if (data && data.success) {
          statusEl.textContent = 'Assignment submitted.';
          button.textContent = 'Re-submit assignment';
          announce('Assignment submitted.');
        } else {
          statusEl.textContent = 'Could not submit. Your work is still saved; you can try again.';
          announce('Could not submit the assignment. Your work is still saved.');
        }
      })
      .catch(function () {
        submitting = false;
        statusEl.textContent = 'Could not submit. Your work is still saved; you can try again.';
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
      onAutosave(code);
    } else if (mode === 'module') {
      const statusEl = document.getElementById('classroomAttemptStatus');
      if (statusEl) checkAttempt(statusEl);
    }
  }

  // ---- init ------------------------------------------------------------------

  function fetchContextAndRender() {
    const panel = document.getElementById('classroomPanel') || buildPanel();
    if (!panel) return;
    panel.innerHTML = '';
    panel.appendChild(el('p', { textContent: 'Loading...' }));

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
  }

  window._classroomInit = init;
})();
