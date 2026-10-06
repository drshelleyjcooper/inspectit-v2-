(function () {
  'use strict';
  var d = document;

  // Opening sequence: logo appears, fades to the Hero. Plays once per visit; skipped for reduced motion.
  var splash = d.getElementById('splash');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var seen = false;
  try { seen = sessionStorage.getItem('inspectit-splash') === '1'; } catch (e) {}
  function endSplash() {
    if (!splash || splash.classList.contains('is-hidden')) return;
    splash.classList.add('is-hidden');
    setTimeout(function () { splash.classList.add('is-gone'); }, 1100);
    try { sessionStorage.setItem('inspectit-splash', '1'); } catch (e) {}
    ['keydown', 'click', 'touchstart'].forEach(function (t) { d.removeEventListener(t, endSplash); });
  }
  if (splash) {
    if (reduce || seen) { splash.classList.add('is-gone'); }
    else {
      setTimeout(endSplash, 2600);
      ['keydown', 'click', 'touchstart'].forEach(function (t) { d.addEventListener(t, endSplash); });
    }
  }

  // Mobile menu
  var toggle = d.querySelector('.nav-toggle');
  var nav = d.getElementById('site-nav');
  function setMenu(open) {
    nav.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
  }
  toggle.addEventListener('click', function () { setMenu(toggle.getAttribute('aria-expanded') !== 'true'); });
  nav.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
  d.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') { setMenu(false); toggle.focus(); }
  });

  // Move focus to the target section after a nav jump, so keyboard and screen reader users land there
  d.addEventListener('click', function (e) {
    if (e.defaultPrevented) return;
    var a = e.target.closest('a[href^="#"]');
    if (!a || a.getAttribute('href') === '#') return;
    var t = d.getElementById(a.getAttribute('href').slice(1));
    if (t) { if (!t.hasAttribute('tabindex')) t.setAttribute('tabindex', '-1'); setTimeout(function () { t.focus({ preventScroll: true }); }, 0); }
  });

  // Demo video dialog (no autoplay; focus returns to the opener on close)
  var dlg = d.getElementById('video-dialog');
  var frame = d.getElementById('video-frame');
  var opener = null;
  d.querySelectorAll('[data-open-video]').forEach(function (b) {
    b.addEventListener('click', function () {
      opener = b; setMenu(false);
      // Load the player only now, from YouTube's privacy-enhanced domain. No autoplay.
      if (!frame.firstChild) {
        var f = d.createElement('iframe');
        f.src = 'https://www.youtube-nocookie.com/embed/' + frame.dataset.videoId + '?rel=0&hl=en&cc_lang_pref=en';
        f.title = 'Inspectit.app demo video';
        f.allow = 'encrypted-media; picture-in-picture; fullscreen';
        f.allowFullscreen = true;
        frame.appendChild(f);
      }
      dlg.showModal();
    });
  });
  d.querySelector('[data-close-video]').addEventListener('click', function () { dlg.close(); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener('close', function () { frame.textContent = ''; if (opener) opener.focus(); });

  // Forms: client-side checks with text errors, then POST to the Inspectit backend.
  // Same-origin by default (the backend serves this site). To point a separately hosted
  // copy at the API, set window.INSPECTIT_API = 'https://inspectit.app' before this script.
  var API = (window.INSPECTIT_API || '').replace(/\/$/, '');
  var MSG = {
    required: 'Please fill in this field.',
    email: 'Please enter a valid email address.',
    password: 'Your password needs at least 8 characters.',
    exists: 'That email already has an account. Sign in instead?',
    busy: 'Too many attempts. Please wait a moment and try again.',
    network: 'We could not reach the server. Please check your connection and try again.',
    server: 'Something went wrong on our side. Please try again in a few minutes.'
  };
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
  function validate(form) {
    var first = null;
    form.querySelectorAll('input[required]').forEach(function (inp) {
      var msg = '';
      if (!inp.value.trim()) msg = MSG.required;
      else if (inp.type === 'email' && !inp.checkValidity()) msg = MSG.email;
      else if (inp.type === 'password' && inp.value.length < 8) msg = MSG.password;
      fieldError(inp, msg);
      if (msg && !first) first = inp;
    });
    return first;
  }
  function post(path, payload) {
    return fetch(API + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return { status: res.status, data: data }; });
    });
  }
  function wire(formId, statusId, build, path, onOk, onFail) {
    var f = d.getElementById(formId); if (!f) return;
    var btn = f.querySelector('button[type="submit"]'), label = btn.textContent;
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var st = d.getElementById(statusId), bad = validate(f);
      if (bad) { st.textContent = 'Please fix the fields marked with an error.'; bad.focus(); return; }
      st.textContent = ''; btn.disabled = true; btn.textContent = 'Sending...'; f.setAttribute('aria-busy', 'true');
      post(path, build(f)).then(function (r) {
        if (r.status >= 200 && r.status < 300) return onOk(f, st);
        var msg = r.status === 429 ? MSG.busy : r.status >= 500 ? MSG.server : '';
        if (!msg && onFail) msg = onFail(f, r);
        st.textContent = msg || MSG.server;
      }).catch(function () { st.textContent = MSG.network; })
        .then(function () { btn.disabled = false; btn.textContent = label; f.removeAttribute('aria-busy'); });
    });
  }
  function val(f, name) { var el = f.elements[name]; return el ? String(el.value || '').trim() : ''; }

  wire('demo-form', 'demo-status', function (f) {
    var c = parseInt(val(f, 'count'), 10);
    return { name: val(f, 'name'), email: val(f, 'email'), count: isNaN(c) ? null : c, website: val(f, 'website') };
  }, '/public/demo-requests', function (f, st) {
    f.reset(); st.textContent = 'Thank you. We received your request and will be in touch soon.';
  }, function (f, r) {
    if (r.status === 422) { var i = f.elements.email; fieldError(i, MSG.email); i.focus(); return 'Please fix the fields marked with an error.'; }
  });

  wire('signup-form', 'signup-status', function (f) {
    var who = f.querySelector('input[name="who"]:checked');
    return { name: val(f, 'name'), email: val(f, 'email'), password: f.elements.password.value,
             track: val(f, 'track') || null, who: who ? who.value : null,
             org: who && who.value === 'org' ? val(f, 'org') || null : null, size: val(f, 'size') || null };
  }, '/auth/trial-signup', function (f) {
    f.hidden = true; var done = d.getElementById('signup-done'); done.hidden = false; done.focus();
  }, function (f, r) {
    if (r.status === 409) { var i = f.elements.email; fieldError(i, MSG.exists); i.focus(); return 'Please fix the fields marked with an error.'; }
    if (r.status === 422) {
      var e = f.elements.email; fieldError(e, MSG.email); e.focus(); return 'Please fix the fields marked with an error.';
    }
  });

  // Sign-up dialog: every "Start Free Trial" button opens it
  var sdlg = d.getElementById('signup-dialog'), sOpener = null;
  d.querySelectorAll('[data-open-signup]').forEach(function (b) {
    b.addEventListener('click', function (e) { e.preventDefault(); sOpener = b; setMenu(false); sdlg.showModal(); });
  });
  d.querySelector('[data-close-signup]').addEventListener('click', function () { sdlg.close(); });
  sdlg.addEventListener('click', function (e) { if (e.target === sdlg) sdlg.close(); });
  sdlg.addEventListener('close', function () { if (sOpener) sOpener.focus(); });
  d.getElementById('su-show').addEventListener('change', function (e) {
    d.getElementById('su-pass').type = e.target.checked ? 'text' : 'password';
  });
  var orgWrap = d.getElementById('su-org-wrap');
  d.querySelectorAll('input[name="who"]').forEach(function (r) {
    r.addEventListener('change', function () { orgWrap.hidden = d.getElementById('su-org-radio').checked === false; });
  });

  // Sticky mobile bar: tuck away while a page button for the same action is on screen
  var sticky = d.getElementById('sticky-cta');
  if (sticky && 'IntersectionObserver' in window) {
    var seenNow = new Set();
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { e.isIntersecting ? seenNow.add(e.target) : seenNow.delete(e.target); });
      sticky.classList.toggle('is-tucked', seenNow.size > 0);
    });
    d.querySelectorAll('.hero__cta, .cta-strip, #free-trial, #call-to-action').forEach(function (el) { io.observe(el); });
  }

  var y = d.getElementById('year'); if (y) y.textContent = new Date().getFullYear();
})();
