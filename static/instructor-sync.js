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
    learnerRowsById: {}, // learner id (string) -> <tr> already in the table
    assignmentRowsById: {}, // assignment id (string) -> <tr> already in the table
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

  // Generic "diff a server list against a <tbody>'s current rows" used by
  // both the learners table and the assignments table below. Items are
  // server-sorted (alphabetically for learners, newest-first for
  // assignments), so a new arrival can belong anywhere in the list, not
  // just the end - this walks the new order against the current DOM order
  // and moves only the rows that are genuinely out of place.
  // insertBefore() on a node already in the document repositions it
  // without destroying it, so a moved row keeps its focus/selection state
  // if it happened to have any; an unchanged row is never touched at all.
  function reconcileTable(opts) {
    var wrap = document.getElementById(opts.wrapId);
    if (!wrap) return;

    if (!opts.items || !opts.items.length) {
      if (document.getElementById(opts.tableId)) {
        wrap.innerHTML = '';
        var p = document.createElement('p');
        p.textContent = opts.emptyMessage;
        wrap.appendChild(p);
      }
      Object.keys(opts.rowsById).forEach(function (id) { delete opts.rowsById[id]; });
      return;
    }

    var table = document.getElementById(opts.tableId);
    if (!table) {
      wrap.innerHTML = '';
      table = opts.buildTable();
      wrap.appendChild(table);
      Object.keys(opts.rowsById).forEach(function (id) { delete opts.rowsById[id]; });
    }
    var tbody = table.querySelector('tbody');

    var seenIds = {};
    var cursor = tbody.firstChild;
    opts.items.forEach(function (item) {
      var id = opts.idOf(item);
      seenIds[id] = true;
      var row = opts.rowsById[id];
      if (row && row.parentNode === tbody) {
        opts.updateRow(row, item);
      } else {
        row = opts.buildRow(item);
        opts.rowsById[id] = row;
      }
      if (cursor !== row) {
        tbody.insertBefore(row, cursor);
      } else {
        cursor = cursor.nextSibling;
      }
    });

    // An item that no longer appears - move focus off its row before
    // removing it so the instructor never silently loses keyboard position
    // to <body>, and say why (when the caller wants that; the assignments
    // table never actually loses rows in this app, so it skips this).
    Object.keys(opts.rowsById).forEach(function (id) {
      if (seenIds[id]) return;
      var row = opts.rowsById[id];
      if (row.parentNode === tbody) {
        if (opts.onRemoveIfFocused && row.contains(document.activeElement)) {
          opts.onRemoveIfFocused(row);
        }
        tbody.removeChild(row);
      }
      delete opts.rowsById[id];
    });
  }

  function reconcileLearnersTable(data) {
    var heading = document.getElementById('learnersHeading');
    if (heading && state.lastLearnerCount !== data.learner_count) {
      heading.textContent = 'Learners (' + data.learner_count + ')';
      state.lastLearnerCount = data.learner_count;
    }
    reconcileTable({
      wrapId: 'learnersTableWrap', tableId: 'learnersTable',
      emptyMessage: 'No learners have joined this cohort yet. Share the join code above.',
      buildTable: buildLearnersTable, buildRow: buildLearnerRow, updateRow: updateLearnerRow,
      items: data.learners, idOf: function (l) { return l.id; },
      rowsById: state.learnerRowsById,
      onRemoveIfFocused: function (row) {
        var name = (row.querySelector('a') || {}).textContent || 'A learner';
        if (heading) {
          if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
          heading.focus();
        }
        announce(name + ' is no longer in this cohort.');
      },
    });
  }

  // ---- assignments table: same targeted patch, no full rebuild ----------
  //
  // Optional per the hardening pass's own scope note: only added because
  // it reuses the exact same reconcileTable() the learners table already
  // proved safe - a second, separate polling mechanism would not have been
  // worth the added complexity, but one more call into the existing one is.

  function buildAssignmentRow(a) {
    var tr = document.createElement('tr');
    tr.dataset.assignmentId = String(a.id);
    var titleTd = document.createElement('td');
    var link = document.createElement('a');
    link.href = a.detail_url;
    link.textContent = a.title;
    titleTd.appendChild(link);
    tr.appendChild(titleTd);

    var statusTd = document.createElement('td');
    var badge = document.createElement('span');
    badge.className = 'cu-badge cu-badge--' + a.status;
    badge.textContent = a.status;
    statusTd.appendChild(badge);
    tr.appendChild(statusTd);

    tr.appendChild(textTd(a.ai_policy));
    tr.appendChild(textTd(a.due_date));
    return tr;
  }

  function updateAssignmentRow(tr, a) {
    var link = tr.cells[0].querySelector('a');
    if (link.textContent !== a.title) link.textContent = a.title;
    if (link.getAttribute('href') !== a.detail_url) link.setAttribute('href', a.detail_url);
    var badge = tr.cells[1].querySelector('span');
    var badgeClass = 'cu-badge cu-badge--' + a.status;
    if (badge.textContent !== a.status) badge.textContent = a.status;
    if (badge.className !== badgeClass) badge.className = badgeClass;
    if (tr.cells[2].textContent !== a.ai_policy) tr.cells[2].textContent = a.ai_policy;
    if (tr.cells[3].textContent !== a.due_date) tr.cells[3].textContent = a.due_date;
  }

  function buildAssignmentsTable() {
    var table = document.createElement('table');
    table.className = 'cu-table';
    table.id = 'assignmentsTable';
    var caption = document.createElement('caption');
    caption.className = 'sr-only';
    caption.textContent = 'Assignments in this cohort';
    table.appendChild(caption);
    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    ['Title', 'Status', 'AI policy', 'Due date'].forEach(function (label) {
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

  function reconcileAssignmentsTable(data) {
    reconcileTable({
      wrapId: 'assignmentsTableWrap', tableId: 'assignmentsTable',
      emptyMessage: 'No assignments yet.',
      buildTable: buildAssignmentsTable, buildRow: buildAssignmentRow, updateRow: updateAssignmentRow,
      items: data.assignments, idOf: function (a) { return a.id; },
      rowsById: state.assignmentRowsById,
      // No onRemoveIfFocused: this app has no "delete assignment" action
      // (only archive/lock, which still show a row), so a row disappearing
      // here is not a real scenario worth handling.
    });
  }

  function seedExistingRows() {
    var learnersTable = document.getElementById('learnersTable');
    if (learnersTable) {
      var learnerRows = learnersTable.querySelectorAll('tbody tr[data-learner-id]');
      for (var i = 0; i < learnerRows.length; i++) {
        state.learnerRowsById[learnerRows[i].dataset.learnerId] = learnerRows[i];
      }
    }
    var assignmentsTable = document.getElementById('assignmentsTable');
    if (assignmentsTable) {
      var assignmentRows = assignmentsTable.querySelectorAll('tbody tr[data-assignment-id]');
      for (var j = 0; j < assignmentRows.length; j++) {
        state.assignmentRowsById[assignmentRows[j].dataset.assignmentId] = assignmentRows[j];
      }
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
    reconcileAssignmentsTable(data);
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
