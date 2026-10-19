(function () {
  'use strict';
  var h = ES.h, root = document.getElementById('app');
  var S = { endAt: 0, timerEl: null, strikeEl: null, inExam: false, blocked: false, q: null, last: {}, timer: null, pinger: null };
  var channel = ('BroadcastChannel' in window) ? new BroadcastChannel('examshield-tab') : null;

  function fail(e) {
    if (e.status === 401) { location.href = '/'; return; }
    if (e.code === 'session_replaced') return showReplaced();
    ES.mount(root, h('div', {}, ES.header('ExamShield'), h('main', {}, ES.msg(e.message, 'err'), h('a', { 'class': 'btn', href: '/' }, 'Back'))));
  }
  function showReplaced() {
    stopLoops();
    ES.mount(root, h('div', {}, ES.header('ExamShield'), h('main', {}, h('div', { 'class': 'card' }, h('h2', {}, 'Session locked'),
      ES.msg('This exam is now open in another window or device, so this session was locked. The event was recorded.', 'err')))));
  }
  function stopLoops() { S.inExam = false; clearInterval(S.timer); clearInterval(S.pinger); }

  // ---------- anti-cheat signals (deterrents + evidence, not proof) ----------
  function report(kind, detail, debounceMs) {
    if (!S.inExam) return;
    var now = Date.now();
    if (debounceMs && S.last[kind] && now - S.last[kind] < debounceMs) return;
    S.last[kind] = now;
    ES.api('POST', '/api/student/event', { kind: kind, detail: detail || '' }).then(function (r) {
      if (S.strikeEl) S.strikeEl.textContent = 'Strikes ' + r.strikes + '/' + r.max_strikes;
      if (r.submitted) loadResult();
    }).catch(function (e) { if (e.code === 'submitted') loadResult(); else if (e.code === 'session_replaced') showReplaced(); });
  }
  function fsOverlay() {
    var ov = h('div', { 'class': 'overlay', id: 'fs-overlay' }, h('h2', {}, 'Fullscreen required'),
      h('p', {}, 'You left fullscreen. This was recorded as a strike. Return to fullscreen to continue.'),
      h('button', { onclick: function () { enterFs().then(function () { ov.remove(); }); } }, 'Return to fullscreen'));
    document.body.appendChild(ov);
  }
  function enterFs() {
    var el = document.documentElement;
    return (el.requestFullscreen ? el.requestFullscreen() : Promise.resolve()).catch(function () { /* user can retry */ });
  }
  function attachListeners() {
    document.addEventListener('visibilitychange', function () { if (document.hidden) report('tab_switch', 'visibilitychange', 1500); });
    window.addEventListener('blur', function () { report('tab_switch', 'blur', 1500); });
    document.addEventListener('fullscreenchange', function () {
      if (!document.fullscreenElement && S.inExam) { report('fullscreen_exit', '', 1500); if (!document.getElementById('fs-overlay')) fsOverlay(); }
    });
    ['copy', 'paste', 'cut', 'contextmenu'].forEach(function (ev) {
      document.addEventListener(ev, function (e) { if (S.inExam) { e.preventDefault(); report(ev, '', 1000); } });
    });
    document.addEventListener('keydown', function (e) {
      if (!S.inExam) return;
      var k = e.key.toLowerCase(), ctrl = e.ctrlKey || e.metaKey;
      if (e.key === 'F12' || (ctrl && ['c', 'v', 'p', 'u', 's'].indexOf(k) >= 0) || (ctrl && e.shiftKey && ['i', 'j', 'c'].indexOf(k) >= 0)) {
        e.preventDefault(); report('shortcut', (ctrl ? 'Ctrl+' : '') + e.key, 1000);
      }
    });
    window.addEventListener('beforeunload', function (e) { if (S.inExam) { e.preventDefault(); e.returnValue = ''; } });
  }
  // second tab: a newly opened tab asks "ping"; a tab that is running the exam answers "pong"
  function checkSecondTab(cb) {
    if (!channel) return cb(false);
    var blocked = false;
    channel.onmessage = function (m) {
      if (m.data === 'ping' && S.inExam) channel.postMessage('pong');
      if (m.data === 'pong') blocked = true;
    };
    channel.postMessage('ping');
    setTimeout(function () { cb(blocked); }, 450);
  }

  // ---------- views ----------
  function notesList(notes, title) {
    if (!notes || !notes.length) return null;
    return h('div', {}, h('h3', {}, title), notes.map(function (n) {
      return h('div', { 'class': 'note-item' }, h('a', { href: '/api/student/notes/' + n.id }, n.filename),
        n.topic ? h('span', { 'class': 'muted' }, n.topic) : null, h('span', { 'class': 'muted' }, Math.max(1, Math.round(n.size / 1024)) + ' KB'));
    }));
  }
  function rulesView(st) {
    var go = h('button', { type: 'button' }, 'Start exam (fullscreen)');
    var out = h('div');
    go.addEventListener('click', function () {
      go.disabled = true;
      enterFs().then(function () {
        return ES.api('POST', '/api/student/start', { fp: [navigator.userAgent, screen.width + 'x' + screen.height, Intl.DateTimeFormat().resolvedOptions().timeZone].join('|') });
      }).then(loadQuestions).catch(function (e) { go.disabled = false; ES.clear(out).appendChild(ES.msg(e.message, 'err')); });
    });
    var e = st.exam;
    ES.mount(root, h('div', {}, ES.header('ExamShield'), h('main', {}, h('div', { 'class': 'card' },
      h('h2', {}, e.title), h('p', {}, 'Welcome, ' + st.student.name + ' (' + st.student.student_id + ').'),
      h('p', {}, e.question_count + ' questions - ' + e.time_limit_min + ' minutes - pass mark ' + e.pass_mark + '%'),
      h('h3', {}, 'Rules'),
      h('ul', {}, h('li', {}, 'The server keeps the clock. When time runs out your answers are submitted automatically.'),
        h('li', {}, 'Stay in fullscreen and on this tab. Switching tabs/windows or leaving fullscreen is a strike. ' + e.max_strikes + ' strikes auto-submit your exam.'),
        h('li', {}, 'Copy, paste, right-click and developer shortcuts are blocked and reported.'),
        h('li', {}, 'Do not open the exam in a second tab or device.'),
        e.one_at_a_time ? h('li', {}, 'Questions appear one at a time and you cannot go back.') : h('li', {}, 'All questions are shown on one page; answers save as you go.')),
      st.student.parent_will_be_emailed ? ES.msg('Your result will be e-mailed to your parent/guardian (' + st.student.parent_email + ') when you submit.') : null,
      notesList(st.notes, 'Study notes'),
      h('p', { 'class': 'disclaimer' }, 'Proctoring signals come from your browser. They are indicators for the examiner, not proof of misconduct.'),
      out, go))));
  }
  function loadQuestions() {
    return ES.api('GET', '/api/student/questions').then(function (d) {
      if (d.state === 'submitted') return loadResult();
      checkSecondTab(function (isSecond) {
        if (isSecond) {
          S.inExam = true; report('second_tab', 'another tab already has the exam'); S.inExam = false;
          return ES.mount(root, h('div', {}, ES.header('ExamShield'), h('main', {}, h('div', { 'class': 'card' }, h('h2', {}, 'Exam already open in another tab'),
            ES.msg('Close this tab and continue in the original one. This was recorded.', 'err')))));
        }
        S.inExam = true; renderExam(d);
      });
    }).catch(fail);
  }
  function optionInput(q, o, saved, onChange) {
    var multi = q.type === 'multi';
    var checked = saved && (multi ? (saved.choices || []).indexOf(o.id) >= 0 : saved.choice === o.id);
    var input = h('input', { type: multi ? 'checkbox' : 'radio', name: 'q' + q.id, value: String(o.id), checked: !!checked });
    input.addEventListener('change', onChange);
    return h('label', { 'class': 'opt' }, input, h('span', {}, o.text));
  }
  function readResponse(q, box) {
    if (q.type === 'fill') return { text: box.querySelector('input[type=text]').value };
    var ids = Array.prototype.map.call(box.querySelectorAll('input:checked'), function (i) { return Number(i.value); });
    return q.type === 'multi' ? { choices: ids } : { choice: ids[0] };
  }
  function questionCard(q, idx, d, onSaved) {
    var box = h('div', { 'class': 'card' }), status = h('span', { 'class': 'muted' });
    function save() {
      var r = readResponse(q, box);
      if (q.type !== 'multi' && q.type !== 'fill' && r.choice === undefined) return Promise.resolve();
      return ES.api('POST', '/api/student/answer', { question_id: q.id, response: r }).then(function () { status.textContent = 'saved'; if (onSaved) onSaved(); })
        .catch(function (e) { if (e.code === 'time_up') loadResult(); else if (e.code === 'session_replaced') showReplaced(); else status.textContent = e.message; });
    }
    box.appendChild(h('h3', {}, 'Question ' + idx + ' of ' + d.total + ' ', h('span', { 'class': 'muted' }, '(' + q.points + ' pt)')));
    box.appendChild(h('p', {}, q.text));
    if (q.type === 'fill') {
      var inp = h('input', { type: 'text', maxlength: '200', autocomplete: 'off', value: (q.saved && q.saved.text) || '' });
      if (!d.one_at_a_time) inp.addEventListener('change', save);
      box.appendChild(inp);
    } else {
      if (q.type === 'multi') box.appendChild(h('p', { 'class': 'muted' }, 'Select all that apply.'));
      q.options.forEach(function (o) { box.appendChild(optionInput(q, o, q.saved, d.one_at_a_time ? function () {} : save)); });
    }
    box.appendChild(status);
    box._save = save;
    return box;
  }
  function renderExam(d) {
    S.q = d; stopLoopsOnly();
    S.endAt = performance.now() + d.remaining_ms;
    S.timerEl = h('span', { 'class': 'timer' }, '--:--');
    S.strikeEl = h('span', { 'class': 'badge medium' }, 'Strikes ' + d.strikes + '/' + d.max_strikes);
    var body = h('main', {}), cards = [];
    var submitBtn = h('button', { type: 'button', 'class': 'danger' }, 'Submit exam');
    submitBtn.addEventListener('click', function () {
      if (confirm('Submit your exam now? You cannot change answers afterwards.')) doSubmit();
    });
    if (d.one_at_a_time) {
      var q = d.questions[0];
      if (!q) { body.appendChild(h('div', { 'class': 'card' }, h('h2', {}, 'All questions answered'), h('p', {}, 'Press Submit to finish.'), submitBtn)); }
      else {
        var card = questionCard(q, d.answered + 1, d);
        var next = h('button', { type: 'button' }, d.answered + 1 >= d.total ? 'Save answer' : 'Save & next');
        next.addEventListener('click', function () {
          next.disabled = true;
          card._save().then(function () { return loadQuestions(); }).catch(function () { next.disabled = false; });
        });
        body.appendChild(card); body.appendChild(h('div', { 'class': 'row' }, next, submitBtn));
      }
    } else {
      d.questions.forEach(function (q, i) { var c = questionCard(q, i + 1, d); cards.push(c); body.appendChild(c); });
      body.appendChild(submitBtn);
    }
    body.insertBefore(h('div', { 'class': 'bar' }, h('span', { id: 'pbar' })), body.firstChild);
    ES.mount(root, h('div', {}, ES.header('ExamShield', h('span', {}, S.strikeEl, ' ', S.timerEl)), body));
    document.getElementById('pbar').style.width = (d.total ? Math.round(100 * d.answered / d.total) : 0) + '%';
    S.timer = setInterval(tick, 250); tick();
    S.pinger = setInterval(ping, 10000);
  }
  function stopLoopsOnly() { clearInterval(S.timer); clearInterval(S.pinger); }
  function tick() {
    var left = (S.endAt - performance.now()) / 1000;
    if (S.timerEl) { S.timerEl.textContent = ES.fmtTime(left); S.timerEl.classList.toggle('low-time', left < 60); }
    if (left <= 0 && S.inExam) { S.inExam = false; doSubmit(); }
  }
  function ping() {
    ES.api('GET', '/api/student/ping').then(function (r) {
      S.endAt = performance.now() + r.remaining_ms;
      if (S.strikeEl) S.strikeEl.textContent = 'Strikes ' + r.strikes + '/' + S.q.max_strikes;
      if (r.status === 'submitted') loadResult();
    }).catch(fail);
  }
  function doSubmit() {
    stopLoops();
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
    ES.api('POST', '/api/student/submit', {}).then(showResult).catch(fail);
  }
  function loadResult() {
    stopLoops();
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
    var ov = document.getElementById('fs-overlay'); if (ov) ov.remove();
    ES.api('GET', '/api/student/result').then(showResult).catch(fail);
  }
  function showResult(r) {
    var parent = r.parent_email;
    ES.mount(root, h('div', {}, ES.header('ExamShield'), h('main', {}, h('div', { 'class': 'card' },
      h('h2', {}, r.exam), h('div', { 'class': 'big' }, r.score + ' / ' + r.max_score + '  (' + r.percent + '%)'),
      h('p', {}, ES.badge(r.passed ? 'PASS' : 'FAIL', r.passed ? 'pass' : 'fail'), ' pass mark ' + r.pass_mark + '%'),
      r.auto_submitted ? ES.msg('Your exam was submitted automatically (' + r.auto_reason + ').') : null,
      parent ? ES.msg(parent.status === 'sent' ? 'Your result was e-mailed to ' + parent.to + '.' : parent.status === 'failed' ? 'We could not e-mail your parent; your examiner can resend it.' : 'Your result has been prepared for ' + parent.to + '.', 'ok') : null),
      notesList(r.notes, 'Study notes for this topic') ? h('div', { 'class': 'card' }, notesList(r.notes, 'Study notes for this topic')) : null,
      r.review ? h('div', { 'class': 'card' }, h('h2', {}, 'Answer review'), r.review.map(function (x, i) {
        return h('div', { 'class': x.is_correct ? 'review-ok' : 'review-bad' }, h('strong', {}, (i + 1) + '. ' + x.text),
          h('div', {}, 'Your answer: ' + x.given), x.is_correct ? null : h('div', {}, 'Correct: ' + x.correct_answer));
      })) : null)));
  }

  // ---------- boot ----------
  attachListeners();
  ES.api('GET', '/api/student/state').then(function (st) {
    if (st.state === 'replaced') return showReplaced();
    if (st.state === 'ready') return rulesView(st);
    if (st.state === 'submitted') return loadResult();
    loadQuestions();
  }).catch(fail);
})();
