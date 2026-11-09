/* 智能问答页：调 /api/rag/ask，把 [资料N] 渲染成可点击引用。
 * 纯原生 JS，无框架、无打包；Markdown 只实现回答里真正会用到的子集。 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var lastAnswer = '';

  var EXAMPLES = [
    '小区停车场出入口要抓拍车牌，推荐哪些设备，价格多少？',
    'DH-IPC-HFW2649M-A-LED 的参数和价格',
    '智慧小区周界防范需要哪些摄像机？给一份配置建议',
    '中小化工厂安消一体化方案里用了哪些设备？',
    '海康和大华的网络录像机参数对比'
  ];

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function inline(s) {
    var t = esc(s);
    // [资料N] → 可点击引用按钮（先做，避免被后续的粗体替换破坏）
    t = t.replace(/\[资料(\d+)\]/g, function (_, n) {
      return '<button class="ref-chip" data-n="' + n + '">资料' + n + '</button>';
    });
    t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    return t;
  }

  function isTableRow(line) { return /^\s*\|.*\|\s*$/.test(line); }
  function cells(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (x) { return x.trim(); });
  }
  function isSep(line) { return /^\s*\|?[\s:\-|]+\|?\s*$/.test(line) && line.indexOf('-') >= 0; }

  function renderMd(md) {
    var lines = String(md || '').split('\n');
    var out = [], i = 0;
    while (i < lines.length) {
      var ln = lines[i];
      if (isTableRow(ln)) {
        var rows = [];
        while (i < lines.length && isTableRow(lines[i])) { rows.push(lines[i]); i++; }
        var head = cells(rows[0]);
        var body = rows.filter(function (_, k) { return k > 0 && !isSep(rows[k]); });
        var html = '<table><thead><tr>' + head.map(function (c) { return '<th>' + inline(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
        body.forEach(function (r) {
          html += '<tr>' + cells(r).map(function (c) { return '<td>' + inline(c) + '</td>'; }).join('') + '</tr>';
        });
        out.push(html + '</tbody></table>');
        continue;
      }
      if (/^\s*---+\s*$/.test(ln)) { out.push('<hr>'); i++; continue; }
      if (/^\s*#{2,4}\s/.test(ln)) {
        var m = ln.match(/^\s*(#{2,4})\s+(.*)$/);
        var tag = m[1].length === 2 ? 'h2' : 'h3';
        out.push('<' + tag + '>' + inline(m[2]) + '</' + tag + '>'); i++; continue;
      }
      if (/^\s*>\s?/.test(ln)) {
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, '')); i++;
        }
        out.push('<blockquote>' + inline(quote.join(' ')) + '</blockquote>');
        continue;
      }
      if (!ln.trim()) { i++; continue; }
      out.push('<p>' + inline(ln) + '</p>');
      i++;
    }
    return out.join('');
  }

  function renderStatus(st) {
    if (!st || !st.ok) { $('rag-engine').textContent = '索引状态读取失败'; return; }
    var parts = [
      '<span class="eng-item">📚 索引 <b>' + st.total + '</b> 篇资料</span>',
      '<span class="eng-item">🤖 当前引擎 <b>' + esc(st.mode) + '</b></span>',
      '<span class="eng-item">🔀 ' + esc(st.engine.fusion) + '</span>',
      '<span class="eng-item">🛡 ' + esc(st.engine.fallback) + '</span>'
    ];
    if (st.offline) {
      parts.push('<span class="eng-item" style="border-color:var(--line-strong);color:var(--red-dark)">' +
        '🔌 离线模式：检索与摘录全部在本机完成，联网后自动恢复大模型</span>');
    }
    var by = st.by_subset || {};
    var labels = { dahua_solution: '大华方案', competitor_solution: '竞品方案', bom: '官方配单', product: '产品', case: '案例', competitor_fact: '参数事实' };
    var detail = Object.keys(by).map(function (k) { return (labels[k] || k) + ' ' + by[k]; }).join(' · ');
    $('rag-engine').innerHTML = parts.join('') + '<span class="eng-item" style="width:100%;color:var(--muted)">' + esc(detail) + '</span>';
  }

  function renderSources(list) {
    $('rag-src-count').textContent = '（' + list.length + ' 篇，点击答案里的「资料N」可跳转）';
    $('rag-sources-section').hidden = !list.length;
    $('rag-sources').innerHTML = list.map(function (s) {
      var link = s.url
        ? ' · <a href="' + esc(s.url) + '" target="_blank" rel="noopener">打开来源 ↗</a>'
        : '';
      return '<div class="rag-src" id="rag-src-' + s.n + '">' +
        '<div class="rag-src-t">[资料' + s.n + '] ' + esc(s.title) + '</div>' +
        '<div class="rag-src-m">' + esc(s.label) + ' ｜ ' + esc(s.source) +
        ' ｜ 行业：' + esc(s.industry || '未标注') + ' ｜ 相关度 ' + esc(s.score) + link + '</div>' +
        '<div class="rag-src-s">' + esc(s.snippet) + '…</div>' +
        '</div>';
    }).join('');
  }

  function renderWarnings(list) {
    if (!list || !list.length) { $('rag-warn').innerHTML = ''; return; }
    $('rag-warn').innerHTML = '<div class="rag-warn">⚠️ 自动校验提示<ul>' +
      list.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') + '</ul></div>';
  }

  function setLoading(on, text) {
    $('rag-submit').disabled = on;
    $('rag-submit').textContent = on ? '检索并生成中…' : '生成带溯源的回答';
    if (text) { $('rag-meter').textContent = text; }
  }

  function jumpTo(n) {
    var el = $('rag-src-' + n);
    if (!el) { return; }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('flash');
    setTimeout(function () { el.classList.remove('flash'); }, 1600);
  }

  async function submit() {
    var q = $('rag-query').value.trim();
    if (!q) { $('rag-query').focus(); return; }
    var payload = {
      query: q,
      mode: $('rag-mode').value,
      industry: $('rag-industry').value,
      top_k: parseInt($('rag-topk').value, 10) || 8
    };
    // 竞品示例自动切模式，避免用户拿错子集
    if (/海康|竞品|对比/.test(q) && payload.mode === 'solution') {
      payload.mode = 'competitor';
      $('rag-mode').value = 'competitor';
    }
    setLoading(true, '正在检索资料并生成…');
    $('rag-result').hidden = false;
    $('rag-answer').innerHTML = '<div class="rag-loading">检索中：双路召回 → RRF 融合 → 生成带引用的回答…</div>';
    $('rag-warn').innerHTML = '';
    var t0 = Date.now();
    try {
      var res = await fetch('/api/rag/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var data = await res.json();
      if (!res.ok || !data.ok) { throw new Error(data.detail && data.detail.message ? data.detail.message : '请求失败'); }
      lastAnswer = data.answer || '';
      $('rag-answer').innerHTML = renderMd(lastAnswer);
      renderWarnings(data.warnings);
      renderSources(data.sources || []);
      $('rag-disclaimer').textContent = data.disclaimer || '';
      var e = data.engine || {};
      $('rag-meter').textContent =
        '耗时 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's ｜ 模式 ' + data.mode +
        ' ｜ 关键词命中 ' + (e.keyword_hits || 0) + ' / 语义命中 ' + (e.vector_hits || 0) +
        ' ｜ RRF α=' + (e.rrf_alpha != null ? e.rrf_alpha : '-') +
        (e.vector_failed ? ' ｜ ⚠ 语义路已降级为纯关键词' : '') +
        (e.industry_relaxed ? ' ｜ 行业筛选已自动放宽' : '');
    } catch (err) {
      $('rag-answer').innerHTML = '<div class="rag-warn">请求失败：' + esc(err.message) +
        '<br>请确认服务已启动（双击 web.bat），或稍后重试。</div>';
      $('rag-meter').textContent = '';
    } finally {
      setLoading(false);
    }
  }

  function init() {
    $('rag-examples').innerHTML = EXAMPLES.map(function (e) {
      return '<button class="rag-chip" data-q="' + esc(e) + '">' + esc(e) + '</button>';
    }).join('');
    $('rag-examples').addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-q]');
      if (b) { $('rag-query').value = b.getAttribute('data-q'); $('rag-query').focus(); }
    });
    $('rag-answer').addEventListener('click', function (ev) {
      var b = ev.target.closest('.ref-chip');
      if (b) { jumpTo(b.getAttribute('data-n')); }
    });
    $('rag-submit').addEventListener('click', submit);
    $('rag-query').addEventListener('keydown', function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { submit(); }
    });
    $('rag-copy').addEventListener('click', function () {
      if (!lastAnswer) { return; }
      navigator.clipboard.writeText(lastAnswer).then(function () {
        $('rag-copy').textContent = '已复制 ✓';
        setTimeout(function () { $('rag-copy').textContent = '复制答案'; }, 1500);
      });
    });
    fetch('/api/rag/status').then(function (r) { return r.json(); }).then(renderStatus)
      .catch(function () { $('rag-engine').textContent = '索引状态读取失败'; });
    $('rag-query').value = EXAMPLES[0];
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();
