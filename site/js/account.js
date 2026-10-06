(function () {
  'use strict';
  var d = document;
  var MSG = {
    required: 'Please fill in this field.',
    email: 'Please enter a valid email address.',
    password: 'Your password needs at least 8 characters.',
    mismatch: 'The two passwords do not match.',
    badLink: 'This reset link is invalid or expired.',
    busy: 'Too many attempts. Please wait a moment and try again.',
    network: 'We could not reach the server. Please check your connection and try again.',
    server: 'Something went wrong on our side. Please try again in a few minutes.'
  };
  var status = d.getElementById('account-status');
  function say(t) { status.textContent = t; }
  function fieldError(inp, msg) {
    var old = inp.parentNode.querySelector('.error'); if (old) old.remove();
    inp.removeAttribute('aria-invalid');
    if (inp.dataset.describe === undefined) inp.dataset.describe = inp.getAttribute('aria-describedby') || '';
    inp.setAttribute('aria-describedby', inp.dataset.describe);
    if (!msg) return;
    var m = d.createElement('span'); m.className = 'error'; m.id = inp.id + '-error'; m.textContent = msg;
    inp.insertAdjacentElement('afterend', m);
    inp.setAttribute('aria-invalid', 'true');
    inp.setAttribute('aria-describedby', (inp.dataset.describe + ' ' + m.id).trim());
  }
  function post(path, payload) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { return { status: r.status, body: b }; }); });
  }
  function busy(form, on, label) {
    var b = form.querySelector('button[type="submit"]');
    if (on) { b.dataset.label = b.textContent; b.textContent = 'Sending...'; } else { b.textContent = b.dataset.label || label; }
    b.disabled = on; form.toggleAttribute('aria-busy', on);
  }
  function generic(r) { return r.status === 429 ? MSG.busy : MSG.server; }
  var y = d.getElementById('year'); if (y) y.textContent = new Date().getFullYear();

  // ---- Forgot password: always shows the same message (no account lookup oracle) ----
  var ff = d.getElementById('forgot-form');
  if (ff) {
    ff.addEventListener('submit', function (e) {
      e.preventDefault();
      var inp = ff.elements.email, v = inp.value.trim(), msg = '';
      if (!v) msg = MSG.required; else if (!inp.checkValidity()) msg = MSG.email;
      fieldError(inp, msg);
      if (msg) { say('Please fix the field marked with an error.'); inp.focus(); return; }
      say(''); busy(ff, true);
      post('/auth/forgot', { email: v }).then(function (r) {
        if (r.status >= 200 && r.status < 300) {
          ff.hidden = true;
          say('If an account exists for ' + v + ', we have sent a link to reset the password. It expires in 60 minutes. If you do not see it, check your spam folder.');
          status.focus();
        } else say(generic(r));
      }).catch(function () { say(MSG.network); }).then(function () { busy(ff, false, 'Send reset link'); });
    });
  }

  // ---- Reset password ----
  var rf = d.getElementById('reset-form');
  if (rf) {
    var qs = new URLSearchParams(location.search);
    var token = qs.get('token') || '';
    var welcome = qs.get('welcome') === '1';          // account created for them by an admin
    if (welcome) {
      d.title = 'Set your password | Inspectit.app';
      d.getElementById('page-title').textContent = 'Welcome to Inspectit.app';
      d.querySelector('#reset-wrap p').textContent = 'Choose a password to finish setting up your account. Use at least 8 characters.';
      d.querySelector('#reset-form button[type="submit"]').textContent = 'Set my password';
    }
    var wrap = d.getElementById('reset-wrap'), next = d.getElementById('account-next');
    function badLink() {
      wrap.hidden = true;
      say(welcome ? 'This setup link is invalid or has expired. Setup links work once and last 7 days.'
                  : MSG.badLink + ' Reset links work once and expire after 60 minutes.');
      next.hidden = false; next.innerHTML = '<a class="btn btn--solid" href="/forgot-password">Request a new link</a>'; status.focus();
    }
    if (!token) { badLink(); return; }
    // keep the secret token out of history/screenshots once read
    try { history.replaceState(null, '', location.pathname); } catch (e) {}
    d.getElementById('rp-show').addEventListener('change', function (e) {
      var t = e.target.checked ? 'text' : 'password';
      d.getElementById('rp-pass').type = t; d.getElementById('rp-pass2').type = t;
    });
    rf.addEventListener('submit', function (e) {
      e.preventDefault();
      var p1 = d.getElementById('rp-pass'), p2 = d.getElementById('rp-pass2'), first = null;
      var m1 = !p1.value ? MSG.required : p1.value.length < 8 ? MSG.password : '';
      var m2 = !p2.value ? MSG.required : (!m1 && p1.value !== p2.value) ? MSG.mismatch : '';
      fieldError(p1, m1); fieldError(p2, m2);
      first = m1 ? p1 : m2 ? p2 : null;
      if (first) { say('Please fix the fields marked with an error.'); first.focus(); return; }
      say(''); busy(rf, true);
      post('/auth/reset', { token: token, password: p1.value }).then(function (r) {
        if (r.status >= 200 && r.status < 300) {
          wrap.hidden = true;
          say(welcome ? 'Your password is set. You can now log in.'
                      : 'Your password has been changed. You were signed out everywhere, so log in with your new password.');
          next.hidden = false; next.innerHTML = '<a class="btn btn--solid btn--lg" href="https://inspectit.app/web/inspectit-app.html">Log in</a>'; status.focus();
        } else if (r.status === 400) badLink();
        else say(generic(r));
      }).catch(function () { say(MSG.network); }).then(function () { busy(rf, false, welcome ? 'Set my password' : 'Change password'); });
    });
  }
})();
