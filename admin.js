(function () {
  'use strict';
  var h = ES.h, root = document.getElementById('app'), view, tabs;
  var state = { tab: 'exams', examId: null, es: null };

  function closeLive() { if (state.es) { state.es.close(); state.es = null; } }
  function errBox(box, e) { ES.mount(box, ES.msg(e.message, 'err')); }

  // ---------- login ----------
  function loginView() {
    var u = h('input', { value: 'admin', autocomplete: 'username' }), p = h('input', { type: 'password', autocomplete: 'current-password' }), out = h('div');
    function go() {
      ES.api('POST', '/api/auth/admin/login', { username: u.value, password: p.value }).then(shell).catch(function (e) { errBox(out, e); });
    }
    p.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
    ES.mount(root, h('div', {}, ES.header('ExamShield - Examiner'), h('main', {}, h('div', { 'class': 'card' }, h('h2', {}, 'Sign in'),
      h('label', {}, 'Username'), u, h('label', {}, 'Password'), p, out, h('div', { 'class': 'row' }, h('button', { onclick: go }, 'Login'), h('a', { 'class': 'btn secondary', href: '/' }, 'Student page'))))));
  }

  // ---------- shell ----------
  function shell() {
    view = h('main');
    tabs = h('div', { 'class': 'tabs' });
    var logout = h('button', { 'class': 'secondary', onclick: function () { closeLive(); ES.api('POST', '/api/auth/logout', {}).then(loginView); } }, 'Logout');
    ES.mount(root, h('div', {}, ES.header('ExamShield - Examiner', logout), view));
    view.appendChild(tabs); view.appendChild(h('div', { id: 'tabbody' }));
    show(state.tab);
  }
  function show(tab, examId) {
    closeLive(); state.tab = tab; if (examId) state.examId = examId;
    ES.clear(tabs);
    [['questions', 'Question bank'], ['exams', 'Exams'], ['live', 'Live'], ['results', 'Results']].forEach(function (t) {
      tabs.appendChild(h('button', { 'class': state.tab === t[0] ? 'active' : '', onclick: function () { show(t[0]); } }, t[1]));
    });
    var body = document.getElementById('tabbody'); ES.clear(body);
    ({ questions: questionsTab, exams: examsTab, live: liveTab, results: resultsTab })[tab](body);
  }

  // ---------- question bank ----------
  function questionsTab(body) {
    var list = h('div'), form = h('div');
    function load() {
      ES.api('GET', '/api/admin/questions').then(function (d) {
        ES.mount(list, h('div', { 'class': 'card' }, h('h2', {}, 'Questions (' + d.questions.length + ')'), h('div', { 'class': 'table-wrap' }, h('table', {},
          h('tr', {}, ['#', 'Question', 'Type', 'Category', 'Difficulty', ''].map(function (c) { return h('th', {}, c); })),
          d.questions.map(function (q) {
            return h('tr', {}, h('td', {}, q.id), h('td', {}, q.text), h('td', {}, q.type), h('td', {}, q.category), h('td', {}, q.difficulty),
              h('td', {}, h('button', { 'class': 'secondary', onclick: function () { edit(q.id); } }, 'Edit'), ' ',
                h('button', { 'class': 'danger', onclick: function () { if (confirm('Delete question?')) ES.api('DELETE', '/api/admin/questions/' + q.id).then(load).catch(function (e) { alert(e.message); }); } }, 'Delete')));
          })))));
      });
    }
    function edit(id) {
      (id ? ES.api('GET', '/api/admin/questions/' + id) : Promise.resolve({ type: 'mcq', difficulty: 'medium', category: 'General', text: '', options: [{ text: '', is_correct: 0 }, { text: '', is_correct: 0 }], blank_answer: '' })).then(function (q) {
        var text = h('textarea', { rows: '2', value: q.text }), cat = h('input', { value: q.category }), out = h('div');
        var type = h('select', {}, ['mcq', 'multi', 'tf', 'fill'].map(function (t) { return h('option', { value: t, selected: t === q.type }, t); }));
        var diff = h('select', {}, ['easy', 'medium', 'hard'].map(function (t) { return h('option', { value: t, selected: t === q.difficulty }, t); }));
        var blank = h('input', { value: q.blank_answer || '', placeholder: 'Accepted answers, separate with |' });
        var tf = h('select', {}, h('option', { value: 'true', selected: q.type === 'tf' && q.options[0] && q.options[0].is_correct }, 'True'), h('option', { value: 'false', selected: q.type === 'tf' && q.options[1] && q.options[1].is_correct }, 'False'));
        var optBox = h('div'), opts = q.type === 'tf' || q.type === 'fill' ? [{ text: '', is_correct: 0 }, { text: '', is_correct: 0 }] : q.options.map(function (o) { return { text: o.text, is_correct: o.is_correct }; });
        function paintOpts() {
          ES.clear(optBox);
          var multi = type.value === 'multi';
          opts.forEach(function (o, i) {
            var t = h('input', { value: o.text, placeholder: 'Option ' + (i + 1) });
            t.addEventListener('input', function () { o.text = t.value; });
            var c = h('input', { type: multi ? 'checkbox' : 'radio', name: 'correct', checked: !!o.is_correct });
            c.addEventListener('change', function () { if (!multi) opts.forEach(function (x) { x.is_correct = 0; }); o.is_correct = c.checked ? 1 : 0; });
            optBox.appendChild(h('div', { 'class': 'row' }, c, h('span', { 'class': 'grow' }, t), h('button', { 'class': 'secondary', type: 'button', onclick: function () { opts.splice(i, 1); paintOpts(); } }, 'x')));
          });
          optBox.appendChild(h('button', { 'class': 'secondary', type: 'button', onclick: function () { opts.push({ text: '', is_correct: 0 }); paintOpts(); } }, 'Add option'));
        }
        function layout() {
          optBox.classList.toggle('hidden', type.value === 'tf' || type.value === 'fill'); blank.classList.toggle('hidden', type.value !== 'fill'); tf.classList.toggle('hidden', type.value !== 'tf'); paintOpts();
        }
        type.addEventListener('change', layout);
        var save = h('button', { onclick: function () {
          var body = { text: text.value, type: type.value, category: cat.value, difficulty: diff.value };
          if (type.value === 'fill') body.blank_answer = blank.value;
          else if (type.value === 'tf') body.tf_answer = tf.value === 'true';
          else body.options = opts.map(function (o) { return { text: o.text, correct: !!o.is_correct }; });
          ES.api(id ? 'PUT' : 'POST', '/api/admin/questions' + (id ? '/' + id : ''), body).then(function () { ES.clear(form); load(); }).catch(function (e) { errBox(out, e); });
        } }, 'Save question');
        ES.mount(form, h('div', { 'class': 'card' }, h('h2', {}, id ? 'Edit question #' + id : 'New question'), h('label', {}, 'Question text'), text,
          h('div', { 'class': 'grid2' }, h('div', {}, h('label', {}, 'Type'), type), h('div', {}, h('label', {}, 'Difficulty'), diff)), h('label', {}, 'Category'), cat,
          h('label', {}, 'Correct answer / options'), optBox, blank, tf, out, h('div', { 'class': 'row' }, save, h('button', { 'class': 'secondary', onclick: function () { ES.clear(form); } }, 'Cancel'))));
        layout();
      });
    }
    body.appendChild(h('div', { 'class': 'row' }, h('button', { onclick: function () { edit(null); } }, 'New question')));
    body.appendChild(form); body.appendChild(list); load();
  }

  // ---------- exams ----------
  function examsTab(body) {
    var list = h('div'), form = h('div');
    function load() {
      ES.api('GET', '/api/admin/exams').then(function (d) {
        ES.mount(list, h('div', { 'class': 'card' }, h('h2', {}, 'Exams'), h('div', { 'class': 'table-wrap' }, h('table', {},
          h('tr', {}, ['Code', 'Title', 'Status', 'Q', 'Attempts', 'Actions'].map(function (c) { return h('th', {}, c); })),
          d.exams.map(function (e) {
            var st = e.status === 'open' ? ['Close', 'closed'] : ['Open', 'open'];
            return h('tr', {}, h('td', {}, h('strong', {}, e.code)), h('td', {}, e.title), h('td', {}, ES.badge(e.status)), h('td', {}, e.question_count), h('td', {}, e.attempt_count),
              h('td', {}, h('div', { 'class': 'row' },
                h('button', { 'class': 'secondary', onclick: function () { ES.api('POST', '/api/admin/exams/' + e.id + '/status', { status: st[1] }).then(load).catch(function (x) { alert(x.message); }); } }, st[0]),
                h('button', { 'class': 'secondary', onclick: function () { edit(e.id); } }, 'Edit / notes'),
                h('button', { 'class': 'secondary', onclick: function () { show('live', e.id); } }, 'Live'),
                h('button', { 'class': 'secondary', onclick: function () { show('results', e.id); } }, 'Results'),
                h('a', { 'class': 'btn secondary', href: '/api/admin/exams/' + e.id + '/qr.png?download=1' }, 'QR'))));
          })))));
      });
    }
    function edit(id) {
      Promise.all([ES.api('GET', '/api/admin/questions'), id ? ES.api('GET', '/api/admin/exams/' + id) : Promise.resolve(null)]).then(function (r) {
        var bank = r[0].questions, e = r[1] || { title: '', topic: '', time_limit_min: 20, pass_mark: 50, shuffle: 1, reveal_answers: 0, one_at_a_time: 1, send_parent: 1, notes_visibility: 'after', questions: [], notes: [] };
        var picked = {}; e.questions.forEach(function (q) { picked[q.id] = q.points; });
        var title = h('input', { value: e.title }), topic = h('input', { value: e.topic || '', placeholder: 'e.g. Cybersecurity basics' });
        var time = h('input', { type: 'number', min: '1', max: '600', value: String(e.time_limit_min) }), pass = h('input', { type: 'number', min: '0', max: '100', value: String(e.pass_mark) });
        function chk(label, v) { var c = h('input', { type: 'checkbox', checked: !!v }); return [c, h('label', { 'class': 'inline' }, c, label)]; }
        var shuffle = chk('Shuffle questions/options', e.shuffle), reveal = chk('Reveal correct answers after submit', e.reveal_answers),
          one = chk('One question at a time', e.one_at_a_time), parent = chk('E-mail result to parent/guardian on submit', e.send_parent);
        var vis = h('select', {}, h('option', { value: 'after', selected: e.notes_visibility === 'after' }, 'After the student submits'), h('option', { value: 'before', selected: e.notes_visibility === 'before' }, 'Before the exam starts (and after)'));
        var qbox = h('div', { 'class': 'table-wrap' }, h('table', {}, bank.map(function (q) {
          var c = h('input', { type: 'checkbox', checked: picked[q.id] !== undefined });
          var pts = h('input', { type: 'number', min: '1', max: '100', value: String(picked[q.id] || 1) });
          c.addEventListener('change', function () { if (c.checked) picked[q.id] = Number(pts.value) || 1; else delete picked[q.id]; });
          pts.addEventListener('change', function () { if (picked[q.id] !== undefined) picked[q.id] = Number(pts.value) || 1; });
          return h('tr', {}, h('td', {}, c), h('td', {}, q.text, h('div', { 'class': 'muted' }, q.type + ' - ' + q.category + ' - ' + q.difficulty)), h('td', {}, pts));
        })));
        var out = h('div'), notesBox = h('div');
        function paintNotes() {
          if (!id) return ES.mount(notesBox, h('p', { 'class': 'muted' }, 'Save the exam first, then upload study notes here.'));
          ES.api('GET', '/api/admin/exams/' + id + '/notes').then(function (d) {
            var file = h('input', { type: 'file', accept: '.pdf,.txt,.md,.csv,.png,.jpg,.jpeg,.docx,.pptx' }), nt = h('input', { placeholder: 'Topic label (optional)', value: e.topic || '' }), nout = h('div');
            var up = h('button', { type: 'button', onclick: function () {
              var f = file.files[0]; if (!f) return errBox(nout, new Error('Choose a file first'));
              f.arrayBuffer().then(function (buf) {
                return ES.api('POST', '/api/admin/exams/' + id + '/notes', buf, { 'X-Filename': encodeURIComponent(f.name), 'X-Topic': encodeURIComponent(nt.value) });
              }).then(function () { paintNotes(); }).catch(function (x) { errBox(nout, x); });
            } }, 'Upload notes');
            ES.mount(notesBox, h('div', {}, d.notes.length ? d.notes.map(function (n) {
              return h('div', { 'class': 'note-item' }, h('a', { href: '/api/admin/notes/' + n.id + '/download' }, n.filename), h('span', { 'class': 'muted' }, (n.topic || '') + ' - ' + Math.max(1, Math.round(n.size / 1024)) + ' KB'),
                h('button', { 'class': 'danger', type: 'button', onclick: function () { if (confirm('Delete this file?')) ES.api('DELETE', '/api/admin/notes/' + n.id).then(paintNotes); } }, 'Delete'));
            }) : h('p', { 'class': 'muted' }, 'No notes uploaded yet.'), h('div', { 'class': 'grid2' }, file, nt), nout, up, h('p', { 'class': 'muted' }, 'Allowed: pdf, txt, md, csv, png, jpg, docx, pptx. Students never see notes during the exam.')));
          });
        }
        var save = h('button', { type: 'button', onclick: function () {
          var body = { title: title.value, topic: topic.value, time_limit_min: Number(time.value), pass_mark: Number(pass.value), shuffle: shuffle[0].checked, reveal_answers: reveal[0].checked,
            one_at_a_time: one[0].checked, send_parent: parent[0].checked, notes_visibility: vis.value,
            questions: Object.keys(picked).map(function (k) { return { id: Number(k), points: picked[k] }; }) };
          ES.api(id ? 'PUT' : 'POST', '/api/admin/exams' + (id ? '/' + id : ''), body).then(function (saved) { if (!id) { edit(saved.id); } else { ES.clear(form); load(); } }).catch(function (x) { errBox(out, x); });
        } }, id ? 'Save changes' : 'Create exam');
        ES.mount(form, h('div', { 'class': 'card' }, h('h2', {}, id ? 'Edit exam ' + e.code : 'New exam'),
          id ? h('div', { 'class': 'row' }, h('img', { 'class': 'qr', src: '/api/admin/exams/' + id + '/qr.png', alt: 'Join QR code' }), h('div', {}, h('div', { 'class': 'big' }, e.code), h('div', { 'class': 'muted' }, e.join_url))) : null,
          h('label', {}, 'Title'), title, h('label', {}, 'Topic'), topic,
          h('div', { 'class': 'grid2' }, h('div', {}, h('label', {}, 'Time limit (minutes)'), time), h('div', {}, h('label', {}, 'Pass mark (%)'), pass)),
          shuffle[1], reveal[1], one[1], parent[1], h('label', {}, 'Study notes visible'), vis,
          h('h3', {}, 'Questions'), qbox, out, h('div', { 'class': 'row' }, save, h('button', { 'class': 'secondary', onclick: function () { ES.clear(form); } }, 'Close')),
          h('h3', {}, 'Study notes (upload files related to the topic)'), notesBox));
        paintNotes();
      });
    }
    body.appendChild(h('div', { 'class': 'row' }, h('button', { onclick: function () { edit(null); } }, 'New exam')));
    body.appendChild(form); body.appendChild(list); load();
  }

  // ---------- exam picker ----------
  function pickExam(body, then) {
    ES.api('GET', '/api/admin/exams').then(function (d) {
      if (!d.exams.length) return body.appendChild(ES.msg('Create an exam first.'));
      if (!state.examId || !d.exams.some(function (e) { return e.id === state.examId; })) state.examId = d.exams[0].id;
      var sel = h('select', {}, d.exams.map(function (e) { return h('option', { value: String(e.id), selected: e.id === state.examId }, e.code + ' - ' + e.title); }));
      sel.addEventListener('change', function () { state.examId = Number(sel.value); show(state.tab); });
      body.appendChild(h('div', { 'class': 'card' }, sel));
      var area = h('div'); body.appendChild(area); then(area, state.examId);
    });
  }

  // ---------- live ----------
  function liveTab(body) {
    pickExam(body, function (area, eid) {
      var tbl = h('div');
      area.appendChild(h('div', { 'class': 'card' }, h('h2', {}, 'Live proctor dashboard'), h('p', { 'class': 'muted' }, 'Updates over Server-Sent Events. Signals are indicators, not proof.'), tbl));
      state.es = new EventSource('/api/admin/exams/' + eid + '/live');
      state.es.onmessage = function (m) {
        var d = JSON.parse(m.data);
        ES.mount(tbl, h('div', { 'class': 'table-wrap' }, h('table', {}, h('tr', {}, ['Student', 'Status', 'Progress', 'Time left', 'Risk', 'Focus loss', 'Strikes'].map(function (c) { return h('th', {}, c); })),
          d.attempts.map(function (a) {
            var bar = h('div', { 'class': 'bar' }, h('span')); bar.firstChild.style.width = a.progress + '%';
            return h('tr', {}, h('td', {}, a.name, h('div', { 'class': 'muted' }, a.student_id)),
              h('td', {}, a.status === 'submitted' ? ES.badge(a.auto_submitted ? 'auto-submitted' : 'submitted', a.auto_submitted ? 'high' : 'pass') : ES.badge(a.active ? 'active' : 'idle', a.active ? 'low' : 'medium')),
              h('td', {}, bar, h('span', { 'class': 'muted' }, a.answered + '/' + a.total)), h('td', {}, a.status === 'in_progress' ? ES.fmtTime(a.time_left_s) : '-'),
              h('td', {}, ES.badge(a.risk, a.risk)), h('td', {}, a.focus_loss), h('td', {}, a.strikes));
          }))));
        if (!d.attempts.length) ES.mount(tbl, h('p', { 'class': 'muted' }, 'No students have started yet.'));
      };
    });
  }

  // ---------- results ----------
  function resultsTab(body) {
    pickExam(body, function (area, eid) {
      ES.api('GET', '/api/admin/exams/' + eid + '/results').then(function (d) {
        var detail = h('div');
        function showDetail(id) {
          ES.api('GET', '/api/admin/attempts/' + id).then(function (a) {
            var s = a.summary, mailOut = h('div'), email = h('input', { type: 'email', value: (a.parent && a.parent.parent_email) || '', placeholder: 'parent@example.com' });
            ES.mount(detail, h('div', { 'class': 'card' }, h('h2', {}, s.name + ' (' + s.student_id + ')'),
              h('p', {}, 'Score ' + s.score + '/' + s.max_score + ' (' + s.percent + '%) ', ES.badge(s.passed ? 'PASS' : 'FAIL', s.passed ? 'pass' : 'fail'), ' risk ', ES.badge(s.risk), s.auto_submitted ? ' - auto-submitted: ' + s.auto_reason : ''),
              h('div', { 'class': 'row' }, h('a', { 'class': 'btn secondary', href: '/api/admin/attempts/' + id + '/pdf' }, 'Download PDF')),
              h('h3', {}, 'Parent / guardian e-mail'),
              h('div', { 'class': 'row' }, h('span', { 'class': 'grow' }, email), h('button', { type: 'button', onclick: function () {
                ES.api('POST', '/api/admin/attempts/' + id + '/send-parent', { email: email.value.trim() || undefined }).then(function (r) { ES.mount(mailOut, ES.msg('Result mail: ' + (r.status ? r.status.status : 'queued') + (r.status && r.status.error ? ' (' + r.status.error + ')' : ''), 'ok')); })
                  .catch(function (x) { errBox(mailOut, x); });
              } }, 'Send / resend to parent')), mailOut,
              a.parent_emails.length ? h('div', { 'class': 'muted' }, a.parent_emails.map(function (m) { return ES.fmtDate(m.created_at * 1000) + ' -> ' + (m.to_addr || '-') + ': ' + m.status + (m.error ? ' (' + m.error + ')' : ''); }).join(' | ')) : null,
              h('h3', {}, 'Answers'), a.answers.map(function (x, i) {
                return h('div', { 'class': x.is_correct ? 'review-ok' : 'review-bad' }, h('strong', {}, (i + 1) + '. ' + x.text), h('div', {}, 'Answer: ' + x.given + (x.flagged_fast ? '  (too fast)' : '')), x.is_correct ? null : h('div', { 'class': 'muted' }, 'Correct: ' + x.correct_answer));
              }),
              h('h3', {}, 'Event timeline'), a.events.length ? a.events.map(function (ev) { return h('div', { 'class': 'muted' }, ES.fmtDate(ev.created_at) + '  ' + ev.kind + (ev.detail ? ' - ' + ev.detail : '') + '  (+' + ev.weight + ')'); }) : h('p', { 'class': 'muted' }, 'No events.')));
          });
        }
        area.appendChild(h('div', { 'class': 'card' }, h('h2', {}, 'Results - ' + d.exam.title),
          h('div', { 'class': 'row' }, h('a', { 'class': 'btn secondary', href: '/api/admin/exams/' + eid + '/results.csv' }, 'CSV'), h('a', { 'class': 'btn secondary', href: '/api/admin/exams/' + eid + '/pdf.zip' }, 'All PDFs (zip)'),
            h('button', { type: 'button', 'class': 'secondary', onclick: function () { if (confirm('Send results to every parent with an e-mail on file?')) ES.api('POST', '/api/admin/exams/' + eid + '/send-parents', {}).then(function (r) { alert('Queued ' + r.queued + ' e-mail(s)'); show('results'); }); } }, 'E-mail all parents')),
          h('div', { 'class': 'table-wrap' }, h('table', {}, h('tr', {}, ['#', 'Student', 'Score', '%', 'Result', 'Risk', 'Strikes', 'Parent mail', ''].map(function (c) { return h('th', {}, c); })),
            d.rows.map(function (r) {
              return h('tr', {}, h('td', {}, r.rank), h('td', {}, r.name, h('div', { 'class': 'muted' }, r.student_id)), h('td', {}, r.status === 'submitted' ? r.score + '/' + r.max_score : '-'), h('td', {}, r.percent === null ? '-' : r.percent),
                h('td', {}, r.status === 'submitted' ? ES.badge(r.passed ? 'PASS' : 'FAIL', r.passed ? 'pass' : 'fail') : ES.badge('in progress', 'medium')), h('td', {}, ES.badge(r.risk), ' ' + r.risk_score), h('td', {}, r.strikes),
                h('td', {}, ES.badge(r.parent_email_status)), h('td', {}, h('button', { 'class': 'secondary', onclick: function () { showDetail(r.attempt_id); } }, 'Details')));
            })))));
        area.appendChild(detail);
      });
    });
  }

  ES.api('GET', '/api/auth/me').then(function (m) { if (m.role === 'admin') shell(); else loginView(); }).catch(loginView);
})();
