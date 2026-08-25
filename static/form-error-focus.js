// Tiny, generic post-validation-error focus/announce helper, shared by
// every instructor form that re-renders itself with a server-side
// accessible error (create cohort, rename cohort, create assignment,
// create lesson, create project - see codeup/classroom/routes.py). These
// are traditional full-page POST forms, not an AJAX app, so the error
// itself is server-rendered HTML (aria-invalid + aria-describedby on the
// field, a visible <p> the description points at) - this script only adds
// the two things a fresh page load can't do on its own: move keyboard
// focus to the invalid field, and mirror its error text into the existing
// #srAlert region once, so it's announced even though nothing on this
// fresh page load otherwise would (a plain page load doesn't fire a focus
// event a screen reader announces the way an in-page update would).
//
// Never validates anything itself, never touches values, never creates a
// new live region - purely "the field the server flagged is `<page load>`;
// make sure a keyboard/screen-reader user actually lands there."
(function () {
  'use strict';
  var invalid = document.querySelector('[data-focus-on-error]');
  if (!invalid) return;
  var describedBy = invalid.getAttribute('aria-describedby');
  var errorEl = describedBy && document.getElementById(describedBy);
  var alertEl = document.getElementById('srAlert');
  if (alertEl && errorEl) alertEl.textContent = errorEl.textContent;
  invalid.focus();
})();
