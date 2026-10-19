(function () {
  'use strict';
  var ES = window.ES = {};
  // DOM builder: strings become text nodes (textContent semantics) - never raw HTML.
  ES.h = function (tag, attrs) {
    var el = document.createElement(tag), kids = Array.prototype.slice.call(arguments, 2);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') el.className = v;
      else if (k.indexOf('on') === 0) el.addEventListener(k.slice(2), v);
      else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    });
    (function add(list) {
      list.forEach(function (c) {
        if (c === null || c === undefined || c === false) return;
        if (Array.isArray(c)) return add(c);
        el.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
      });
    })(kids);
    return el;
  };
  ES.clear = function (el) { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  ES.mount = function (root, node) { ES.clear(root).appendChild(node); };
  ES.api = function (method, path, body, extraHeaders) {
    var headers = Object.assign({ 'X-Requested-With': 'ExamShield' }, extraHeaders || {});
    var opts = { method: method, headers: headers, credentials: 'same-origin' };
    if (body !== undefined && body !== null) {
      if (body instanceof ArrayBuffer || body instanceof Blob) { opts.body = body; headers['Content-Type'] = 'application/octet-stream'; }
      else { opts.body = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { var e = new Error(data.error || ('HTTP ' + r.status)); e.status = r.status; e.code = data.code; e.data = data; throw e; }
        return data;
      });
    });
  };
  ES.fmtTime = function (s) { s = Math.max(0, Math.floor(s)); var m = Math.floor(s / 60); return m + ':' + String(s % 60).padStart(2, '0'); };
  ES.fmtDate = function (ms) { return ms ? new Date(ms).toLocaleString() : '-'; };
  ES.badge = function (text, cls) { return ES.h('span', { 'class': 'badge ' + (cls || text) }, text); };
  ES.msg = function (text, kind) { return ES.h('div', { 'class': 'msg ' + (kind || '') }, text); };
  ES.themeButton = function () {
    return ES.h('button', { 'class': 'secondary', title: 'Toggle light/dark', onclick: function () {
      var cur = document.documentElement.getAttribute('data-theme');
      var dark = cur ? cur === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
      var next = dark ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('es-theme', next); } catch (e) { /* ignore */ }
    } }, 'Theme');
  };
  ES.header = function (title, extra) {
    return ES.h('header', { 'class': 'top' }, ES.h('h1', {}, title), extra || null, ES.themeButton());
  };
})();
