(function () {
  'use strict';
  var h = ES.h, root = document.getElementById('app');
  var code = (new URLSearchParams(location.search).get('code') || '').toUpperCase().slice(0, 6);
  var fCode = h('input', { maxlength: '6', value: code, autocomplete: 'off', placeholder: 'ABC123' });
  var fName = h('input', { maxlength: '80', autocomplete: 'name' });
  var fSid = h('input', { maxlength: '40', autocomplete: 'off' });
  var fPName = h('input', { maxlength: '80', placeholder: 'Optional' });
  var fPMail = h('input', { type: 'email', maxlength: '254', placeholder: 'Optional - e.g. parent@example.com' });
  var out = h('div'), btn = h('button', { type: 'button' }, 'Continue');
  btn.addEventListener('click', function () {
    ES.clear(out); btn.disabled = true;
    var body = { code: fCode.value.trim().toUpperCase(), name: fName.value, student_id: fSid.value };
    if (fPMail.value.trim()) body.parent_email = fPMail.value.trim();
    if (fPName.value.trim()) body.parent_name = fPName.value.trim();
    ES.api('POST', '/api/student/join', body).then(function () { location.href = '/exam'; })
      .catch(function (e) { out.appendChild(ES.msg(e.message, 'err')); btn.disabled = false; });
  });
  ES.mount(root, h('div', {}, ES.header('ExamShield'), h('main', {},
    h('div', { 'class': 'card' }, h('h2', {}, 'Join an exam'),
      h('label', {}, 'Exam code'), fCode, h('label', {}, 'Your full name'), fName, h('label', {}, 'Student ID'), fSid,
      h('h3', {}, 'Parent / guardian (optional)'),
      h('p', { 'class': 'muted' }, 'If you enter an e-mail address, your exam result (score, pass/fail and a PDF summary) will be sent to your parent or guardian after you submit.'),
      h('div', { 'class': 'grid2' }, h('div', {}, h('label', {}, 'Parent name'), fPName), h('div', {}, h('label', {}, 'Parent e-mail'), fPMail)),
      out, h('div', { 'class': 'row' }, btn, h('a', { 'class': 'btn secondary', href: '/admin' }, 'Examiner login'))))));
})();
