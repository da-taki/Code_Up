// Classroom (cohort/assignment/guided-project) integration for the IDE page.
// A no-op unless the page was opened with ?assignment=<id> or ?project=<id>
// (set when a learner opens one from /classroom/learner). Talks to app.js
// only through the same optional-global-hook convention app.js already uses
// for the tutorial overlay (window._tutorialOnRunSuccess etc.), so the base
// IDE is completely unaffected when this script finds nothing to do.
(function () {
  'use strict';

  const params = new URLSearchParams(window.location.search);
  const assignmentId = params.get('assignment');
  const projectId = params.get('project');
  if (!assignmentId && !projectId) return;

  const mode = assignmentId ? 'assignment' : 'project';
  const id = assignmentId || projectId;
  let contextData = null;
  let submitting = false;

  function announce(text, opts) {
    opts = opts || {};
    if (typeof window.speak === 'function') window.speak(text, { sr: false });
    if (typeof window.srAnnounce === 'function') window.srAnnounce(text, opts.priority || 'polite');
  }

  function policyExplanation(policy) {
    const labels = {
      EXPLANATIONS_ONLY: 'The AI can explain code and errors, but will not write or fix code for you.',
      HINTS_ONLY: 'The AI can give hints, but will not write code or explain errors directly.',
      ERROR_HELP_ONLY: 'The AI can explain what an error means, but will not write or fix code.',
      ASSESSMENT: 'AI help is turned off while this assignment is in assessment mode. You can still edit, run, save and submit.',
      OFF: 'AI help is turned off for this assignment. You can still edit, run, save and submit.',
    };
    return labels[policy] || '';
  }

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.assign(node, props || {});
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
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

  function applyPolicyToButtons(policy) {
    const map = { EXPLANATIONS_ONLY: [], HINTS_ONLY: ['analyzeBtn', 'fixBtn'], ERROR_HELP_ONLY: ['analyzeBtn', 'fixBtn'], ASSESSMENT: ['analyzeBtn', 'fixBtn'], OFF: ['analyzeBtn', 'fixBtn'] };
    const explainBlocked = policy !== 'FULL' && policy !== 'EXPLANATIONS_ONLY';
    const generateBlocked = policy !== 'FULL';
    const analyzeBtn = document.getElementById('analyzeBtn');
    if (analyzeBtn) {
      analyzeBtn.setAttribute('aria-disabled', explainBlocked ? 'true' : 'false');
      analyzeBtn.title = explainBlocked ? 'Turned off by your instructor for this assignment.' : '';
    }
    const fixBtn = document.getElementById('fixBtn');
    if (fixBtn) {
      fixBtn.setAttribute('aria-disabled', generateBlocked ? 'true' : 'false');
      fixBtn.title = generateBlocked ? 'Turned off by your instructor for this assignment.' : '';
    }
  }

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

    if (a.ai_policy && a.ai_policy !== 'FULL') {
      panel.appendChild(el('p', {
        className: 'cu-notice',
        textContent: 'AI policy: ' + a.ai_policy.replace(/_/g, ' ') + '. ' + policyExplanation(a.ai_policy),
      }));
    }
    applyPolicyToButtons(a.ai_policy || 'FULL');

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

    appendHelpWidget(panel);

    if (p.code && typeof window.setCode === 'function') {
      window.setCode(p.code, { preserveSpeech: true, allowNonPython: false });
    }
  }

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

  function onAutosave(code) {
    if (mode === 'assignment') {
      fetch('/classroom/assignments/' + encodeURIComponent(id) + '/autosave', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code }),
      }).catch(function () { /* local autosave already has this covered */ });
    } else {
      fetch('/classroom/projects/' + encodeURIComponent(id) + '/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code }),
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.success) updateCheckpointList(data.newly_completed, data.checkpoints_completed);
        })
        .catch(function () {});
    }
  }

  function onRunResult(code, ranOk, errorText) {
    if (mode === 'assignment') {
      fetch('/classroom/assignments/' + encodeURIComponent(id) + '/run-result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, ran_ok: !!ranOk, error: errorText || '' }),
      }).catch(function () {});
    } else {
      onAutosave(code);
    }
  }

  function init() {
    const panel = buildPanel();
    if (!panel) return;
    panel.appendChild(el('p', { textContent: 'Loading...' }));

    const url = mode === 'assignment'
      ? '/classroom/assignments/' + encodeURIComponent(id) + '/context'
      : '/classroom/projects/' + encodeURIComponent(id) + '/context';

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
        else renderProjectPanel(panel, data);
      })
      .catch(function () {
        panel.innerHTML = '';
        panel.appendChild(el('p', {
          className: 'cu-notice cu-notice--error',
          textContent: 'Could not load this ' + mode + '. You can still use CodeUp normally.',
        }));
      });

    window._classroomOnAutosave = onAutosave;
    window._classroomOnRunResult = onRunResult;
  }

  window._classroomInit = init;
})();
