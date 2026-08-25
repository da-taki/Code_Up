// Lightweight near-live sync for the instructor cohort dashboard
// (templates/classroom/cohort_dashboard.html). Polls the existing-shape
// GET /classroom/cohorts/<id>/live-summary (same DB reads cohort_dashboard's
// own render already does, plus a small allowlisted recent-events feed -
// see codeup.classroom.routes._LIVE_SUMMARY_EVENT_KINDS) roughly every 7s
// while the tab is visible, plus an immediate sync on visibility/focus
// regain. No WebSocket/SSE, no new infrastructure: one guarded fetch loop.
//
// Two things a routine poll must never do to an instructor operating this
// page by keyboard/screen reader:
//   1. Steal focus - the learner table is patched row-by-row, cell-by-cell
//      (see reconcileLearnersTable/updateLearnerRow below), never torn down
//      and rebuilt, so a screen-reader user's position on an existing row's
//      link survives every poll untouched. The "Create an assignment" form
//      (and everything else on the page) is a separate section this script
//      never reaches into, so a poll can't discard a half-typed draft.
//   2. Stay silent about something the instructor actually needs to know -
//      a new join, help request, or submission - if they can't see the
//      screen. See announceNewEvents(): each event has a stable id, so a
//      genuinely new one announces exactly once via the existing
//      #srAnnouncer polite live region already on every classroom page
//      (templates/classroom/_base.html) - never a new live region, and
//      routine polling itself (last-active timestamps, "dashboard
//      refreshed") is never announced.
(function () {
  'use strict';

  var scriptEl = document.currentScript;
  var cohortId = scriptEl && scriptEl.dataset ? scriptEl.dataset.cohortId : '';
  if (!cohortId) return;

  var POLL_INTERVAL_MS = 7000;
  var MIN_SYNC_GAP_MS = 1500;

  var state = {
    timer: null,
    inFlight: false,
    lastSyncAt: 0,
    lastLearnerCount: null,
    lastOpenHelpCount: null,
    lastSeenEventId: null, // null until the first successful sync seeds it
    rowsById: {}, // learner id (string) -> <tr> already in the table
  };

  // ---- accessible event announcements ----------------------------------

  function announce(text) {
    var el = document.getElementById('srAnnouncer');
    if (el) el.textContent = text;
  }

  function eventAnnouncement(evt) {
    if (evt.kind === 'learner_joined') return evt.learner_name + ' joined the cohort.';
    if (evt.kind === 'help_requested') return evt.learner_name + ' requested instructor help.';
    if (evt.kind === 'assignment_submitted') {
      return evt.assignment_title
        ? evt.learner_name + ' submitted ' + evt.assignment_title + '.'
        : evt.learner_name + ' submitted an assignment.';
    }
    return null;
  }

  // Dedupes on the event's own database id (not a text/snapshot diff),
  // so a re-fetch of the same event never announces twice and a re-
  // submission genuinely creates a new event with a new id (which SHOULD
  // announce again - the instructor cares about a fresh submission).
  function announceNewEvents(events) {
    if (!events || !events.length) return;
    if (state.lastSeenEventId === null) {
      // First sync of this page load - these already existed, not a live
      // arrival; seed the watermark without announcing any of them.
      state.lastSeenEventId = events.reduce(function (max, e) { return Math.max(max, e.id); }, 0);
      return;
    }
    var fresh = events.filter(function (e) { return e.id > state.lastSeenEventId; });
    if (!fresh.length) return;
    fresh.sort(function (a, b) { return a.id - b.id; }); // oldest first, so multiple arrivals read in order
    fresh.forEach(function (e) {
      var text = eventAnnouncement(e);
      if (text) announce(text);
    });
    state.lastSeenEventId = fresh[fresh.length - 1].id;
  }

  // ---- learner table: targeted row/cell patch, never a full rebuild ----

  function textTd(text) {
    var td = document.createElement('td');
    td.textContent = text;
    return td;
  }

  function buildLearnerRow(l) {
    var tr = document.createElement('tr');
    tr.dataset.learnerId = String(l.id);
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

  // Only the specific cell whose text actually changed gets a new
  // textContent - the row element itself, and every cell that didn't
  // change, is left completely alone. If a screen-reader user's virtual
  // cursor or keyboard focus is on this row's link, it is never touched
  // here (link text/href only change if the learner's own name/url did).
  function updateLearnerRow(tr, l) {
    var link = tr.cells[0].querySelector('a');
    if (link.textContent !== l.display_name) link.textContent = l.display_name;
    if (link.getAttribute('href') !== l.detail_url) link.setAttribute('href', l.detail_url);
    var badge = tr.cells[1].querySelector('span');
    if (badge.textContent !== l.live_status) badge.textContent = l.live_status;
    if (tr.cells[2].textContent !== l.last_active_at) tr.cells[2].textContent = l.last_active_at;
    var modulesText = l.modules_completed + ' / ' + l.modules_total;
    if (tr.cells[3].textContent !== modulesText) tr.cells[3].textContent = modulesText;
    var assignText = l.assignments_submitted + ' / ' + l.assignments_total;
    if (tr.cells[4].textContent !== assignText) tr.cells[4].textContent = assignText;
    var conceptsText = l.concepts_demonstrated + ' / ' + l.concepts_total;
    if (tr.cells[5].textContent !== conceptsText) tr.cells[5].textContent = conceptsText;
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

  // Learners are server-sorted alphabetically (see
  // codeup.classroom.db.list_learners_for_cohort), so a newly-joined
  // learner can belong in the middle of the table, not just at the end.
  // This walks the new order against the current DOM order and moves only
  // the rows that are genuinely out of place - insertBefore() on a node
  // already in the document repositions it without destroying it, so a
  // moved row keeps its focus/selection state if it happened to have any.
  function reconcileLearnersTable(data) {
    var heading = document.getElementById('learnersHeading');
    if (heading && state.lastLearnerCount !== data.learner_count) {
      heading.textContent = 'Learners (' + data.learner_count + ')';
      state.lastLearnerCount = data.learner_count;
    }

    var wrap = document.getElementById('learnersTableWrap');
    if (!wrap) return;

    if (!data.learners || !data.learners.length) {
      if (document.getElementById('learnersTable')) {
        wrap.innerHTML = '';
        var p = document.createElement('p');
        p.textContent = 'No learners have joined this cohort yet. Share the join code above.';
        wrap.appendChild(p);
      }
      state.rowsById = {};
      return;
    }

    var table = document.getElementById('learnersTable');
    if (!table) {
      wrap.innerHTML = '';
      table = buildLearnersTable();
      wrap.appendChild(table);
      state.rowsById = {};
    }
    var tbody = table.querySelector('tbody');

    var seenIds = {};
    var cursor = tbody.firstChild;
    data.learners.forEach(function (l) {
      seenIds[l.id] = true;
      var row = state.rowsById[l.id];
      if (row && row.parentNode === tbody) {
        updateLearnerRow(row, l);
      } else {
        row = buildLearnerRow(l);
        state.rowsById[l.id] = row;
      }
      if (cursor !== row) {
        tbody.insertBefore(row, cursor);
      } else {
        cursor = cursor.nextSibling;
      }
    });

    // A learner who no longer appears (removed from the cohort) - move
    // focus off their row before removing it so the instructor never
    // silently loses keyboard position to <body>, and say why.
    Object.keys(state.rowsById).forEach(function (id) {
      if (seenIds[id]) return;
      var row = state.rowsById[id];
      if (row.parentNode === tbody) {
        if (row.contains(document.activeElement)) {
          var name = (row.querySelector('a') || {}).textContent || 'A learner';
          if (heading) {
            if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
            heading.focus();
          }
          announce(name + ' is no longer in this cohort.');
        }
        tbody.removeChild(row);
      }
      delete state.rowsById[id];
    });
  }

  function seedExistingRows() {
    var table = document.getElementById('learnersTable');
    if (!table) return;
    var rows = table.querySelectorAll('tbody tr[data-learner-id]');
    for (var i = 0; i < rows.length; i++) {
      state.rowsById[rows[i].dataset.learnerId] = rows[i];
    }
  }

  function patchHelpQueueLink(data) {
    if (state.lastOpenHelpCount === data.open_help_count) return;
    var link = document.getElementById('helpQueueLink');
    if (link) link.textContent = 'Help queue (' + data.open_help_count + ')';
    state.lastOpenHelpCount = data.open_help_count;
  }

  function applySync(data) {
    reconcileLearnersTable(data);
    patchHelpQueueLink(data);
    announceNewEvents(data.events);
  }

  // ---- polling loop: one timer, one in-flight guard ---------------------

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

  seedExistingRows();
  startPolling();
})();
