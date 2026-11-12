/* ============================================================
 * 竞品对比 v6 · 交互重构
 * 类目 → 大华 / 海康产品池 → 各选一款 → 逐字段对比 → DOCX
 * 支持三类组成「一套典型监控系统」整组对比。
 * 不再默认展示两款固定摄像头；缺参数明确标注，不推断。
 * ============================================================ */
(function () {
  "use strict";

  // 缓存兜底：保证 hidden 在旧版 styles.css 缓存下仍然生效
  (function ensureHidden() {
    var s = document.createElement("style");
    s.textContent = "[hidden]{display:none!important}";
    document.head.appendChild(s);
  })();

  var CATS = [];
  var current = "";
  // selections[category] = { dahua: model, hik: model }
  var selections = {};
  var lists = {
    "大华": { items: [], total: 0, offset: 0, q: "", loading: false },
    "海康": { items: [], total: 0, offset: 0, q: "", loading: false },
  };
  var LIMIT = 20;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function fmt(n) { return n == null || isNaN(n) ? "—" : Number(n).toLocaleString("zh-CN"); }
  function debounce(fn, ms) { var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); }; }
  function el(id) { return document.getElementById(id); }
  function sel(cat) { return selections[cat] || (selections[cat] = { dahua: "", hik: "" }); }

  function renderCats() {
    var host = el("cats");
    host.innerHTML = CATS.map(function (c) {
      var s = selections[c.key] || {};
      var picked = (s.dahua && s.hik) ? '<span class="pick">✓ 已配对</span>' : "";
      return '<button class="cmp-cat' + (c.key === current ? " active" : "") +
        '" data-cat="' + esc(c.key) + '">' +
        '<div class="cn">' + esc(c.name) + "</div>" +
        '<div class="cs">' + esc(c.hint) + "</div>" +
        '<div class="cc">大华 ' + fmt(c.dahua_products) + " 款（" + fmt(c.dahua_with_spec) +
        " 款有参数） · 海康 " + fmt(c.hik_models) + " 款</div>" + picked + "</button>";
    }).join("");
    host.querySelectorAll("[data-cat]").forEach(function (b) {
      b.addEventListener("click", function () { switchCat(b.dataset.cat); });
    });
  }

  function switchCat(cat) {
    current = cat;
    renderCats();
    el("sel-cat").textContent = cat;
    el("result").hidden = true;
    el("group-result").innerHTML = "";
    el("empty").hidden = false;
    renderSelected();
    loadList("大华", true);
    loadList("海康", true);
  }

  function renderSelected() {
    var s = sel(current);
    el("sel-dahua").textContent = "大华：" + (s.dahua || "未选择");
    el("sel-hik").textContent = "海康：" + (s.hik || "未选择");
    var ready = !!(s.dahua && s.hik);
    el("btn-pair").disabled = !ready;
    var pairs = CATS.filter(function (c) {
      var x = selections[c.key] || {};
      return x.dahua && x.hik;
    });
    el("btn-group").disabled = pairs.length < 2;
    el("btn-group").textContent = "整组对比（已配 " + pairs.length + "/3 类）";
  }

  async function loadList(brand, reset) {
    var st = lists[brand];
    if (st.loading || !current) return;
    st.loading = true;
    if (reset) { st.offset = 0; st.items = []; }
    var host = el(brand === "大华" ? "list-dahua" : "list-hik");
    var more = el(brand === "大华" ? "more-dahua" : "more-hik");
    if (reset) host.innerHTML = '<div class="cmp-empty" style="padding:18px">加载中…</div>';
    try {
      var url = "/api/compare/products?category=" + encodeURIComponent(current) +
        "&brand=" + encodeURIComponent(brand) + "&q=" + encodeURIComponent(st.q) +
        "&offset=" + st.offset + "&limit=" + LIMIT;
      var r = await fetch(url);
      if (!r.ok) throw new Error("HTTP " + r.status);
      var d = await r.json();
      if (reset) { st.items = d.items || []; st.total = d.total || 0; host.innerHTML = ""; }
      else { st.items = st.items.concat(d.items || []); st.total = d.total || st.total; }
      st.offset = st.items.length;
      host.innerHTML = st.items.map(function (p) { return itemHtml(brand, p); }).join("") ||
        '<div class="cmp-empty" style="padding:18px">没有匹配的产品。</div>';
      more.hidden = st.offset >= st.total;
      host.querySelectorAll("[data-model]").forEach(function (b) {
        b.addEventListener("click", function () {
          sel(current)[brand === "大华" ? "dahua" : "hik"] = b.dataset.model;
          renderSelected(); renderCats();
          host.querySelectorAll(".cmp-item").forEach(function (x) { x.classList.remove("sel"); });
          b.classList.add("sel");
        });
      });
      var picked = sel(current)[brand === "大华" ? "dahua" : "hik"];
      if (picked) {
        var hit = host.querySelector('[data-model="' + CSS.escape(picked) + '"]');
        if (hit) hit.classList.add("sel");
      }
      el(brand === "大华" ? "dh-sub" : "hk-sub").textContent =
        "共 " + fmt(st.total) + " 款可选" +
        (brand === "海康" ? " · 官网公开参数机型" : " · 有技术参数的已排前");
    } catch (e) {
      host.innerHTML = '<div class="cmp-empty" style="padding:18px">加载失败：' + esc(e.message) + "</div>";
    } finally { st.loading = false; }
  }

  function itemHtml(brand, p) {
    var img = p.img_url
      ? '<img class="cmp-thumb" src="' + esc(p.img_url) + '" loading="lazy" alt="" onerror="this.style.display=\'none\'">'
      : '<div class="cmp-thumb">' + (brand === "海康" ? "HIK" : "DH") + "</div>";
    var price = p.price_stand ? " · ¥" + fmt(p.price_stand) : "";
    return '<button class="cmp-item" data-model="' + esc(p.model) + '">' + img +
      '<div class="ii"><div class="mm">' + esc(p.model) + "</div>" +
      '<div class="nn">' + esc(p.display_name || p.class_name || "") + "</div>" +
      '<div class="ff">' + esc(p.fact_note || "") + price + "</div></div>" +
      '<span class="tick">✓</span></button>';
  }

  var LABELS = { same: "同档", dahua: "大华占优", hik: "海康占优",
    qualitative: "口径不同", only_dahua: "仅大华公开", only_hik: "仅海康公开" };

  function statsHtml(result) {
    var s = result.summary;
    var cards = [["可比字段", result.rows.length], ["同档", s.same], ["大华占优", s.dahua],
      ["海康占优", s.hik], ["口径不同", s.qualitative], ["仅一方公开", s.only_dahua + s.only_hik]];
    return '<div class="stats">' + cards.map(function (c) {
      return '<div class="stat"><div class="label">' + esc(c[0]) +
        '</div><div class="value">' + fmt(c[1]) + "<small>项</small></div></div>";
    }).join("") + "</div>";
  }

  function rowsHtml(result) {
    return '<div class="table-wrap"><table><thead><tr><th>字段</th><th>大华（归一化）</th>' +
      "<th>海康（归一化）</th><th>判定</th><th>差异 / 口径说明</th></tr></thead><tbody>" +
      result.rows.map(function (r) {
        return "<tr><td><strong>" + esc(r.field) + "</strong></td><td>" + esc(r.dahua_value) +
          "</td><td>" + esc(r.hik_value) + '</td><td><span class="badge ' + esc(r.status) + '">' +
          esc(LABELS[r.status] || r.status) + "</span></td><td class=\"muted\">" +
          esc([r.delta, r.note].filter(Boolean).join("；") || "—") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function talksHtml(result) {
    return '<div class="table-wrap"><table><thead><tr><th>字段</th><th>话术</th>' +
      "<th>技术密度</th></tr></thead><tbody>" + result.rows.map(function (r) {
        return "<tr><td>" + esc(r.field) + "</td><td>" + esc(r.talking_point) +
          '</td><td class="mono">' + Number(r.density_score).toFixed(2) + " · " +
          esc(r.density_kind) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function renderResult(result) {
    el("empty").hidden = true;
    el("group-result").innerHTML = "";
    var host = el("result");
    host.hidden = false;
    var pairParams = "category=" + encodeURIComponent(result.category) +
      "&dahua=" + encodeURIComponent(result.dahua_model) +
      "&hik=" + encodeURIComponent(result.hik_model);
    var cov = result.coverage || {};
    var covText = "参数覆盖：两侧都能对齐的字段 " + fmt(cov.aligned) + "/" +
      fmt(cov.total) + " 项；未公开的字段标『官网未公开』，不推断、不补数。";
    if (cov.total && cov.aligned / cov.total < 0.5) {
      covText += " <b>覆盖率偏低</b>：该机型官网参数页的字段口径与对比模板差异较大，结论仅供定性参考。";
    }
    host.innerHTML =
      '<div class="card-head flex"><div><span class="eyebrow">' + esc(result.category) +
      " · " + (result.mode === "official_pair" ? "官网代表款精对比" : "自定义配对") + "</span>" +
      "<h2>大华 " + esc(result.dahua_model) + " <span class=\"hl\">vs</span> 海康 " +
      esc(result.hik_model) + "</h2><p>" + esc(result.dahua_name) + " ｜ " + esc(result.hik_name) + "</p></div>" +
      '<a class="btn primary" href="/api/compare/docx?' + pairParams + '">下载本组 DOCX</a></div>' +
      '<p class="cmp-coverage">' + covText + "</p>" +
      statsHtml(result) +
      '<div class="block"><div class="block-head"><h3>逐字段对照表</h3>' +
      '<span class="muted">数值先归一化再比较；口径不同会显式标注</span></div>' + rowsHtml(result) + "</div>" +
      '<div class="block"><div class="block-head"><h3>销售话术</h3></div>' + talksHtml(result) + "</div>" +
      '<div class="gaps"><h3>数据来源与依据</h3><ul>' +
      "<li>大华：<a class=\"mono\" href=\"" + esc(result.dahua_source) +
      '" target="_blank" rel="noopener">' + esc(result.dahua_source) + "</a></li>" +
      "<li>海康：<a class=\"mono\" href=\"" + esc(result.hik_source) +
      '" target="_blank" rel="noopener">' + esc(result.hik_source) + "</a></li>" +
      "<li>抓取时间：" + esc(result.retrieved_at) + "</li>" +
      "<li>" + esc(result.disclaimer) + "</li></ul></div>";
    host.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function pairsReady() {
    return CATS.map(function (c) {
      var s = selections[c.key] || {};
      return { category: c.key, dahua: s.dahua, hik: s.hik };
    }).filter(function (p) { return p.dahua && p.hik; });
  }

  async function runPair() {
    var s = sel(current);
    if (!s.dahua || !s.hik) return;
    var btn = el("btn-pair");
    btn.disabled = true; btn.textContent = "生成中…";
    var alert = el("empty");
    alert.hidden = false;
    alert.innerHTML = '<div class="big">正在对齐官网参数…</div>';
    try {
      var url = "/api/compare/pair?category=" + encodeURIComponent(current) +
        "&dahua=" + encodeURIComponent(s.dahua) + "&hik=" + encodeURIComponent(s.hik);
      var r = await fetch(url);
      var d = await r.json();
      if (!r.ok) throw new Error((d.detail && d.detail.message) || "HTTP " + r.status);
      renderResult(d);
    } catch (e) {
      alert.innerHTML = '<div class="big">对比失败</div><div>' + esc(e.message) +
        "<br>请确认两侧型号都存在于本地知识库，或换一款带官网参数的产品。</div>";
    } finally { btn.disabled = false; btn.textContent = "生成对比表"; renderSelected(); }
  }

  async function runGroup() {
    var pairs = pairsReady();
    if (pairs.length < 2) return;
    var btn = el("btn-group");
    btn.disabled = true; btn.textContent = "整组生成中…";
    var host = el("group-result");
    host.innerHTML = '<div class="cmp-empty" style="margin-top:18px">正在逐类对齐参数…</div>';
    el("result").hidden = true;
    try {
      var results = [];
      for (var i = 0; i < pairs.length; i++) {
        var p = pairs[i];
        var r = await fetch("/api/compare/pair?category=" + encodeURIComponent(p.category) +
          "&dahua=" + encodeURIComponent(p.dahua) + "&hik=" + encodeURIComponent(p.hik));
        var d = await r.json();
        if (!r.ok) throw new Error(p.category + "：" + ((d.detail && d.detail.message) || r.status));
        results.push(d);
      }
      el("empty").hidden = true;
      host.innerHTML = '<div class="card-head flex" style="margin-top:22px"><div>' +
        '<span class="eyebrow">GROUP BENCHMARK</span><h2>典型监控系统 · 整组对比（' +
        results.length + ' 类）</h2><p>三类设备各选一款组成一条链路，下面按类目汇总结果。</p></div>' +
        '<button class="btn primary" id="dl-group">下载整组 DOCX</button></div>' +
        results.map(function (x) {
          return '<div class="cmp-group"><h3>' + esc(x.category) + " · 大华 " + esc(x.dahua_model) +
            " vs 海康 " + esc(x.hik_model) + "（同档 " + x.summary.same + " / 大华占优 " +
            x.summary.dahua + " / 海康占优 " + x.summary.hik + "）</h3>" +
            '<div class="inner">' + statsHtml(x) + rowsHtml(x) + "</div></div>";
        }).join("");
      el("dl-group").addEventListener("click", function () { downloadGroup(pairs); });
      host.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      host.innerHTML = '<div class="cmp-empty" style="margin-top:18px"><div class="big">整组对比失败</div><div>' +
        esc(e.message) + "</div></div>";
    } finally { btn.disabled = false; renderSelected(); }
  }

  async function downloadGroup(pairs) {
    try {
      var r = await fetch("/api/compare/group/docx", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairs: pairs }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      var blob = await r.blob();
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "竞品对比-整组-" + new Date().toISOString().slice(0, 10) + ".docx";
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    } catch (e) { window.alert("导出失败：" + e.message); }
  }

  function init() {
    el("q-dahua").addEventListener("input", debounce(function () {
      lists["大华"].q = el("q-dahua").value.trim(); loadList("大华", true);
    }, 300));
    el("q-hik").addEventListener("input", debounce(function () {
      lists["海康"].q = el("q-hik").value.trim(); loadList("海康", true);
    }, 300));
    el("more-dahua").addEventListener("click", function () { loadList("大华", false); });
    el("more-hik").addEventListener("click", function () { loadList("海康", false); });
    el("btn-pair").addEventListener("click", runPair);
    el("btn-group").addEventListener("click", runGroup);

    fetch("/api/compare/categories").then(function (r) { return r.json(); }).then(function (d) {
      CATS = d.items || [];
      if (!CATS.length) throw new Error("类目为空");
      current = CATS[0].key;

      var params = new URLSearchParams(location.search);
      var preDahua = params.get("dahua") || "";
      var preHik = params.get("hik") || "";
      var preCat = params.get("category") || "";
      if (preCat && CATS.some(function (c) { return c.key === preCat; })) current = preCat;
      if (preDahua) sel(current).dahua = preDahua;
      if (preHik) sel(current).hik = preHik;

      var totalDahua = CATS.reduce(function (a, c) { return a + c.dahua_products; }, 0);
      var totalSpec = CATS.reduce(function (a, c) { return a + c.dahua_with_spec; }, 0);
      var hikModels = CATS.reduce(function (a, c) { return a + c.hik_models; }, 0);
      var hikFacts = CATS.reduce(function (a, c) { return a + c.hik_facts; }, 0);
      el("data-note").innerHTML = "数据口径：大华侧按类目可从本地产品库选出约 " +
        fmt(totalDahua) + " 款（其中 " + fmt(totalSpec) +
        " 款带官网技术参数，已排在列表最前）；海康侧已补采 " + fmt(hikModels) +
        " 款官网产品，共 " + fmt(hikFacts) +
        " 条参数事实，每一条都可回溯到官网参数页。" +
        "<b>参数条数不等于产品款数</b>；两侧缺项一律标『官网未公开』，不推断、不补数。" +
        "若某款标注「仅目录型号」，说明尚未采集逐字段参数，选中时会明确提示而不是生成空表。";

      renderCats();
      el("sel-cat").textContent = current;
      renderSelected();
      loadList("大华", true);
      loadList("海康", true);
      if (preDahua && preHik) runPair();
    }).catch(function (e) {
      el("cats").innerHTML = '<div class="cmp-empty">类目加载失败：' + esc(e.message) + "</div>";
    });
  }

  init();
})();
