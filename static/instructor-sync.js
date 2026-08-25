// Lightweight near-live sync for the instructor cohort dashboard
// (templates/classroom/cohort_dashboard.html). Polls the existing-shape
// GET /classroom/cohorts/<id>/live-summary (added alongside this file -
// same DB reads cohort_dashboard's own render already does, just
// serialized as JSON for a targeted patch) roughly every 7s while the tab
// is visible, plus an immediate sync on visibility/focus regain. Only the
// learner table and the help-queue count are ever touched - the
// "Create an assignment" form (and everything else on the page) is a
// separate section this script never reaches into, so a poll can never
// discard something the instructor is mid-typing. No WebSocket/SSE, no new
// infrastructure: one guarded fetch loop, purely visual - no
// announcements, no new aria-live region (the learner-side sync in
// static/classroom.js is the only classroom feature that speaks).
(function () {
  'use strict';

  var scriptEl = document.currentScript;
  var cohortId = scriptEl && scriptEl.dataset ? scriptEl.dataset.cohortId : '';
  if (!cohortId) return;

  var POLL_INTERVAL_MS = 7000;
  var MIN_SYNC_GAP_MS = 1500;

  var state = { timer: null, inFlight: false, lastSyncAt: 0, lastSummary: null };

  function learnersFingerprint(list) {
    return JSON.stringify((list || []).map(function (l) {
      return [l.id, l.live_status, l.last_active_at, l.modules_completed,
        l.assignments_submitted, l.concepts_demonstrated];
    }));
  }

  function textTd(text) {
    var td = document.createElement('td');
    td.textContent = text;
    return td;
  }

  function buildLearnerRow(l) {
    var tr = document.createElement('tr');
    var nameTd = document.createElement('td');
    var link = document.createElement('a');
    link.href = l.detail_url;
    link.textContent = l.display_name;
    nameTd.appendChild(link);
    tr.appendChild(nameTd);

    var statusTd = document.createElement('td');
    var badge = document.createElement('span');
    badge.className = 'cu-badge';
    badge.textContent = l.live_status;
    statusTd.appendChild(badge);
    tr.appendChild(statusTd);

    tr.appendChild(textTd(l.last_active_at));
    tr.appendChild(textTd(l.modules_completed + ' / ' + l.modules_total));
    tr.appendChild(textTd(l.assignments_submitted + ' / ' + l.assignments_total));
    tr.appendChild(textTd(l.concepts_demonstrated + ' / ' + l.concepts_total));
    return tr;
  }

  function buildLearnersTable() {
    var table = document.createElement('table');
    table.className = 'cu-table';
    table.id = 'learnersTable';
    var caption = document.createElement('caption');
    caption.className = 'sr-only';
    caption.textContent = 'Learners in this cohort and their progress';
    table.appendChild(caption);
    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    ['Learner', 'Status', 'Last active', 'Course modules', 'Assignments submitted', 'Concepts demonstrated']
      .forEach(function (label) {
        var th = document.createElement('th');
        th.scope = 'col';
        th.textContent = label;
        headRow.appendChild(th);
      });
    thead.appendChild(headRow);
    table.appendChild(thead);
    table.appendChild(document.createElement('tbody'));
    return table;
  }

  function patchLearnersTable(data) {
    var heading = document.getElementById('learnersHeading');
    if (heading) heading.textContent = 'Learners (' + data.learner_count + ')';

    var wrap = document.getElementById('learnersTableWrap');
    if (!wrap) return;

    if (!data.learners || !data.learners.length) {
      if (!document.getElementById('learnersTable')) return; // already showing the empty state
      wrap.innerHTML = '';
      var p = document.createElement('p');
      p.textContent = 'No learners have joined this cohort yet. Share the join code above.';
      wrap.appendChild(p);
      return;
    }

    var table = document.getElementById('learnersTable');
    if (!table) {
      wrap.innerHTML = '';
      table = buildLearnersTable();
      wrap.appendChild(table);
    }
    var tbody = table.querySelector('tbody');
    tbody.innerHTML = '';
    data.learners.forEach(function (l) { tbody.appendChild(buildLearnerRow(l)); });
  }

  function patchHelpQueueLink(data) {
    var link = document.getElementById('helpQueueLink');
    if (link) link.textContent = 'Help queue (' + data.open_help_count + ')';
  }

  function applySync(data) {
    var previous = state.lastSummary;
    if (!previous || previous.learner_count !== data.learner_count ||
        learnersFingerprint(previous.learners) !== learnersFingerprint(data.learners)) {
      patchLearnersTable(data);
    }
    if (!previous || previous.open_help_count !== data.open_help_count) {
      patchHelpQueueLink(data);
    }
    state.lastSummary = data;
  }

  function requestSync() {
    if (document.hidden || state.inFlight) return;
    var now = Date.now();
    if (now - state.lastSyncAt < MIN_SYNC_GAP_MS) return;
    state.inFlight = true;
    fetch('/classroom/cohorts/' + encodeURIComponent(cohortId) + '/live-summary')
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.inFlight = false;
        state.lastSyncAt = Date.now();
        if (!data || !data.success) return; // silent - next opportunity retries automatically
        applySync(data);
      })
      .catch(function () {
        state.inFlight = false;
        state.lastSyncAt = Date.now();
        // Background sync failure is silent and non-destructive.
      });
  }

  function startPolling() {
    if (!document.hidden && !state.timer) {
      state.timer = setInterval(requestSync, POLL_INTERVAL_MS);
    }
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (state.timer) { clearInterval(state.timer); state.timer = null; }
    } else {
      requestSync();
      startPolling();
    }
  });
  window.addEventListener('focus', function () {
    if (!document.hidden) requestSync();
  });

  startPolling();
})();
