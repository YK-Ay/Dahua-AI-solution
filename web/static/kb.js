/* ============================================================
 * 知识库可视化 v6（/kb）
 * - 四个 Tab 互斥替换整块内容（配合 [hidden]{display:none!important}）
 * - 产品中心：类目树 / 搜索 / 分页 / +对比 / +配单 真正可用
 * - 解决方案：53 条官网方案 + 云商配单，点卡片展开配单明细
 * - 行业案例：标注官网原文抓取时间，不冒充总结
 * - 竞品：按机型聚合，默认展示海康
 * - 同步：展示真实增量结果与失败原因
 * ============================================================ */
(function () {
  "use strict";

  // 缓存兜底：外部 styles.css 可能还是旧版（旧版 .pc{display:grid} 会盖掉 [hidden]），
  // 这里无条件注入一条最高优先级规则，保证 hidden 在任何缓存组合下都生效。
  (function ensureHidden() {
    var s = document.createElement("style");
    s.textContent = "[hidden]{display:none!important}";
    document.head.appendChild(s);
  })();

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function fmt(n) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toLocaleString("zh-CN");
  }
  function setText(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }
  function debounce(fn, ms) { var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); }; }

  var state = {
    tab: "products", catName: "", offset: 0, total: 0, loading: false,
    stats: null, brand: "海康", gridItems: [], gridLoading: false,
    compView: "models",
    facts: {
      items: [], total: 0, offset: 0, loading: false, filters: null,
      brand: "海康", category: "", model: "", q: "",
    },
  };
  var LIMIT = 20;
  var KEY_COMPARE = "dh_kb_compare_basket";
  var KEY_BOM = "dh_kb_bom_basket";
  var baskets = { compare: load(KEY_COMPARE), bom: load(KEY_BOM) };
  var basketKind = "bom";

  function load(key) {
    try { var v = JSON.parse(localStorage.getItem(key) || "[]"); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  function save(kind) {
    try { localStorage.setItem(kind === "compare" ? KEY_COMPARE : KEY_BOM,
      JSON.stringify(baskets[kind])); } catch (e) { /* 隐私模式忽略 */ }
    updateBasketCounts();
  }
  function updateBasketCounts() {
    setText("n-compare", baskets.compare.length);
    setText("n-bom", baskets.bom.length);
    document.querySelectorAll(".pc-btn[data-kind]").forEach(function (b) {
      var found = baskets[b.dataset.kind].some(function (x) { return x.model === b.dataset.m; });
      b.classList.toggle("added", found);
      b.textContent = found ? "✓ 已加入" : (b.dataset.kind === "compare" ? "＋ 对比" : "＋ 配单");
    });
  }
  function addToBasket(kind, item) {
    var arr = baskets[kind];
    if (kind === "compare" && arr.length >= 2) {
      arr.shift(); // 对比最多两款，新的顶掉最早的
    }
    if (!arr.some(function (x) { return x.model === item.model; })) arr.push(item);
    save(kind);
    toast(kind === "compare" ? "已加入对比篮，可在竞品对比页继续选型" : "已加入配单篮");
  }

  function toast(msg) {
    var t = document.getElementById("toast");
    if (!t) {
      t = document.createElement("div"); t.id = "toast";
      t.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:120;" +
        "background:#1a1d24;color:#fff;padding:10px 18px;border-radius:999px;font-size:12.5px;" +
        "box-shadow:0 8px 26px rgba(0,0,0,.22);opacity:0;transition:opacity .2s";
      document.body.appendChild(t);
    }
    t.textContent = msg; t.style.opacity = "1";
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.style.opacity = "0"; }, 1900);
  }

  /* ---------------- 统计卡 / 图谱 / 分布 ---------------- */
  function renderStats(s) {
    state.stats = s;
    setText("st-products", fmt(s.products));
    setText("st-solutions", fmt(s.solutions));
    setText("st-cases", fmt(s.cases));
    setText("st-competitor", fmt(s.competitor));
    setText("st-products-sub", "云商公开池 / 官网目录");
    setText("st-solutions-sub", "含 " + fmt(s.solutions_with_bom) + " 条官方配单");
    setText("st-cases-sub", "官网原文 · " + String(s.cases_fetched_at || "").slice(0, 10));
    setText("st-competitor-sub", fmt(s.competitor_models) + " 款海康机型 · " +
      fmt(s.competitor_hik) + " 条参数");
    var gsub = document.getElementById("graph-sub");
    if (gsub) gsub.textContent = "四大实体按体量关联；竞品节点用的是参数事实条数（" +
      fmt(s.competitor) + " 条），对应 " + fmt(s.competitor_models) + " 款机型。";
    renderGraph(s);
    renderDist(s.product_categories);
    updateNote();
  }

  function renderDist(rows) {
    var host = document.getElementById("kb-dist-list");
    if (!host) return;
    if (!rows || !rows.length) { host.innerHTML = '<div class="muted">暂无数据</div>'; return; }
    var max = Math.max.apply(null, rows.map(function (r) { return r.c; }));
    host.innerHTML = rows.map(function (r) {
      var pct = max ? Math.round((r.c / max) * 100) : 0;
      return '<div class="dist-row"><div class="dist-name">' + esc(r.name) + '</div>' +
        '<div class="dist-bar"><div class="dist-fill" style="width:' + pct + '%"></div></div>' +
        '<div class="dist-c">' + fmt(r.c) + "</div></div>";
    }).join("");
  }

  function renderGraph(s) {
    var svg = document.getElementById("kb-graph");
    if (!svg) return;
    var W = 760, H = 460, cx = W / 2, cy = H / 2;
    var entities = [
      { name: "产品", val: s.products, color: "#e60012", x: cx, y: 80 },
      { name: "解决方案", val: s.solutions, color: "#d98a00", x: cx + 250, y: cy },
      { name: "行业案例", val: s.cases, color: "#1a9e6b", x: cx, y: H - 80 },
      { name: "竞品参数", val: s.competitor, color: "#4a5160", x: cx - 250, y: cy },
    ];
    var maxV = Math.max.apply(null, entities.map(function (e) { return e.val || 1; }));
    var out = '<defs><filter id="kg" x="-40%" y="-40%" width="180%" height="180%">' +
      '<feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>';
    out += '<circle cx="' + cx + '" cy="' + cy + '" r="46" fill="#fff" stroke="#e60012" stroke-width="2.5" filter="url(#kg)"/>' +
      '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" font-family="sans-serif" font-weight="800" font-size="15" fill="#e60012">大华</text>' +
      '<text x="' + cx + '" y="' + (cy + 15) + '" text-anchor="middle" font-family="sans-serif" font-weight="600" font-size="10" fill="#a30a14">知识库</text>';
    entities.forEach(function (e) {
      var r = 30 + 34 * Math.sqrt((e.val || 1) / maxV);
      out += '<line x1="' + cx + '" y1="' + cy + '" x2="' + e.x + '" y2="' + e.y + '" stroke="' + e.color + '" stroke-width="1.4" opacity="0.45"/>';
      out += '<circle cx="' + e.x + '" cy="' + e.y + '" r="' + r + '" fill="#fff" stroke="' + e.color + '" stroke-width="2.2" filter="url(#kg)"/>' +
        '<text x="' + e.x + '" y="' + (e.y - r - 8) + '" text-anchor="middle" font-family="sans-serif" font-weight="700" font-size="13" fill="#1a1d24">' + esc(e.name) + "</text>" +
        '<text x="' + e.x + '" y="' + e.y + '" text-anchor="middle" font-family="monospace" font-weight="800" font-size="17" fill="' + e.color + '">' + fmt(e.val) + "</text>";
    });
    svg.innerHTML = out;
  }

  /* ---------------- Tab 视图说明 ---------------- */
  function updateNote() {
    var s = state.stats || {};
    var host = document.getElementById("view-note");
    if (!host) return;
    var html = "";
    if (state.tab === "products") {
      html = "<b>产品中心</b> · 共 " + fmt(s.products) + " 款大华产品（云商公开池、官网目录与配单溯源型号）。" +
        "最近同步 " + esc(String(s.last_product_sync || "").slice(0, 16)) +
        "<span class='sep'>|</span>点「＋ 对比」放进对比篮、「＋ 配单」放进配单篮，右上角可查看。";
    } else if (state.tab === "solutions") {
      html = "<b>解决方案</b> · " + fmt(s.solutions) + " 条 = 官网方案 + 大华云商官方配单（" +
        fmt(s.boms) + " 份配单 / " + fmt(s.bom_items) + " 条明细）。点击卡片展开配单明细。" +
        "<span class='sep'>|</span>同一分类下多个型号是<b>可替换候选</b>，不能累加成采购总价。";
    } else if (state.tab === "cases") {
      html = "<b>行业案例</b> · " + fmt(s.cases) + " 条，全部来自 " +
        esc(String(s.fetched_from || s.cases_fetched_at || "").slice(0, 10)) +
        " 抓取的大华官网案例详情页<b>原文</b>，不是 AI 总结、也不是最新行业研究；如需最新资料请用智能问答检索方案知识库。";
    } else {
      html = "<b>竞品</b> · " + fmt(s.competitor_models) + " 款海康机型，对应 " +
        fmt(s.competitor) + " 条官网参数事实。统计卡里的数字是<b>参数条数</b>不是产品款数。" +
        "<span class='sep'>|</span>点「按参数明细查看」可逐条查全部 " + fmt(s.competitor) +
        " 条并回溯官网来源；默认展示海康。";
    }
    host.innerHTML = html;
  }

  /* ---------------- 产品中心：类目树 ---------------- */
  function renderCategories(cats, total) {
    var host = document.getElementById("pc-cats");
    if (!host) return;
    var all = total != null ? total : state.total;
    var html = '<div class="pc-cat' + (state.catName ? "" : " active") +
      '" data-cat=""><span class="c-name"><span class="dotc"></span>全部产品</span>' +
      '<span class="count" id="cat-all-count">' + fmt(all) + "</span></div>";
    html += (cats || []).map(function (c) {
      return '<div class="pc-cat' + (state.catName === c.name ? " active" : "") +
        '" data-cat="' + esc(c.name) + '"><span class="c-name"><span class="dotc"></span>' +
        esc(c.name) + '</span><span class="count">' + fmt(c.c) + "</span></div>";
    }).join("");
    host.innerHTML = html;
    host.querySelectorAll(".pc-cat").forEach(function (el) {
      el.addEventListener("click", function () {
        host.querySelectorAll(".pc-cat").forEach(function (x) { x.classList.remove("active"); });
        el.classList.add("active");
        state.catName = el.dataset.cat || "";
        loadProducts(true);
      });
    });
  }

  /* ---------------- 产品中心：横向列表卡片 ---------------- */
  function productRow(p) {
    var price = p.price_stand != null && Number(p.price_stand) > 0 ? "¥" + fmt(p.price_stand) : "面议";
    var stClass = p.pi_status && p.pi_status.indexOf("停售") >= 0 ? "st-warn" : "st-ok";
    var tags = '<span class="pc-tag price">' + price + "</span>" +
      '<span class="pc-tag ' + stClass + '">' + esc(p.pi_status || "—") + "</span>";
    if (p.direction) tags += '<span class="pc-tag">' + esc(p.direction) + "</span>";
    if (p.year_new) tags += '<span class="pc-tag">' + esc(p.year_new) + " 年</span>";
    if (p.part_num) tags += '<span class="pc-tag">物料号 ' + esc(p.part_num) + "</span>";
    if (p.source === "official_catalog") tags += '<span class="pc-tag">官网产品</span>';
    else if (p.source === "mall_public_search") tags += '<span class="pc-tag">云商资料库</span>';
    var descText = p.prod_desc ? String(p.prod_desc).replace(/\s+/g, " ").trim() : "";
    var desc = descText ? '<div style="font-size:12px;color:#5b6472;line-height:1.55;margin-top:6px;max-width:820px">' +
      esc(descText.slice(0, 120)) + (descText.length > 120 ? "…" : "") + "</div>" : "";
    var spec = p.spec_value ? '<details style="margin-top:6px;font-size:12px;color:#5b6472"><summary style="cursor:pointer;color:#e60012">查看技术参数</summary>' +
      '<pre style="white-space:pre-wrap;word-break:break-all;max-height:240px;overflow:auto;background:#fafafa;padding:8px;border-radius:6px">' +
      esc(String(p.spec_value).slice(0, 4000)) + "</pre></details>" : "";
    var img = p.img_url ? '<img class="pc-thumb" src="' + esc(p.img_url) +
      '" loading="lazy" alt="" onerror="this.style.opacity=0.15">' :
      '<div class="pc-thumb" style="display:grid;place-items:center;color:#9aa1ad">无图</div>';
    var common = ' data-m="' + esc(p.model) + '" data-name="' + esc(p.display_name || p.model) +
      '" data-price="' + esc(p.price_stand == null ? "" : p.price_stand) +
      '" data-cls="' + esc(p.class_name || "") + '"';
    return '<div class="pc-item">' + img +
      '<div class="pc-body"><div class="pc-name">' + esc(p.display_name || p.model) + "</div>" +
      '<div class="pc-model">' + esc(p.model || "") + (p.class_name ? " · " + esc(p.class_name) : "") + "</div>" +
      '<div class="pc-tags">' + tags + "</div>" + desc + spec + "</div>" +
      '<div class="pc-actions">' +
      '<button class="pc-btn compare" data-kind="compare"' + common + '>＋ 对比</button>' +
      '<button class="pc-btn" data-kind="bom"' + common + '>＋ 配单</button>' +
      "</div></div>";
  }

  async function loadProducts(reset) {
    if (state.loading) return;
    state.loading = true;
    var listEl = document.getElementById("pc-list");
    var metaEl = document.getElementById("pc-meta");
    var more = document.getElementById("pc-more");
    var q = document.getElementById("pc-q").value.trim();
    if (reset) { state.offset = 0; listEl.innerHTML = ""; }
    var url = "/api/kb/products?q=" + encodeURIComponent(q) +
      "&class_name=" + encodeURIComponent(state.catName) +
      "&offset=" + state.offset + "&limit=" + LIMIT;
    try {
      var res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      var data = await res.json();
      var items = data.items || [];
      if (reset) state.total = data.total != null ? data.total : items.length;
      if (!items.length && state.offset === 0) {
        listEl.innerHTML = '<div class="kb-empty">未找到相关产品，换个关键词或类目试试。</div>';
        more.hidden = true;
      } else {
        listEl.innerHTML += items.map(productRow).join("");
        state.offset += items.length;
        more.hidden = state.offset >= state.total;
      }
      metaEl.innerHTML = "共 <b>" + fmt(state.total) + "</b> 个产品" +
        (state.catName ? " · 类目：" + esc(state.catName) : "") + (q ? " · 已筛选" : "");
      updateBasketCounts();
    } catch (err) {
      if (state.offset === 0) listEl.innerHTML = '<div class="kb-empty">加载失败：' + esc(err.message) + "</div>";
    } finally { state.loading = false; }
  }

  /* ---------------- 其它 tab 的网格 ---------------- */
  function gridCard(o) {
    var img = o.img ? '<img class="prod-img" src="' + esc(o.img) +
      '" loading="lazy" alt="" onerror="this.style.opacity=0.15">' : "";
    return '<div class="prod-card ' + (o.cls || "") + '">' + img +
      '<div class="prod-info">' +
      (o.pn ? '<div class="pn">' + esc(o.pn) + "</div>" : "") +
      '<div class="nm">' + esc(o.nm) + "</div>" +
      '<div class="row">' + (o.tag ? '<span class="cls">' + esc(o.tag) + "</span>" : "") +
      (o.pr ? '<span class="pr">' + esc(o.pr) + "</span>" : "") + "</div>" +
      (o.extra || "") + "</div></div>";
  }

  function solCard(s) {
    var img = s.detail_img ? '<img class="sol-img" src="' + esc(s.detail_img) +
      '" loading="lazy" alt="" onerror="this.style.opacity=0.15">' : "";
    var meta = '<span class="sol-badge' + (s.source === "官网方案" ? " plain" : "") + '">' +
      esc(s.source) + "</span>";
    if (s.industry || s.scene_name) meta += '<span class="sol-tag scene">' + esc(s.industry || s.scene_name) + "</span>";
    if (s.item_count) meta += '<span class="sol-tag">配单 ' + fmt(s.item_count) + " 条</span>";
    if (s.release_time) meta += '<span class="sol-tag">' + esc(s.release_time) + "</span>";
    var desc = s.summary ? '<div class="sol-desc">' + esc(s.summary) + "</div>" : "";
    return '<div class="sol-card" data-sol="' + esc(s.sol_id) + '">' + img +
      '<div class="sol-body"><div class="sol-name">' + esc(s.name) + "</div>" + desc +
      '<div class="sol-meta">' + meta + "</div></div></div>";
  }

  function renderGridItems() {
    var grid = document.getElementById("kb-grid");
    var meta = document.getElementById("kb-meta");
    var q = document.getElementById("kb-q").value.trim().toLowerCase();
    var items = state.gridItems.filter(function (x) {
      if (state.tab === "competitors" && state.brand && x.brand !== state.brand) return false;
      if (!q) return true;
      return JSON.stringify(x).toLowerCase().indexOf(q) >= 0;
    });
    var label = { solutions: "解决方案", cases: "行业案例", competitors: "竞品机型" }[state.tab];
    var html;
    if (state.tab === "solutions") {
      html = items.map(solCard).join("");
    } else if (state.tab === "cases") {
      html = items.map(function (c) {
        return gridCard({ pn: c.industry || "行业案例", nm: c.title || "(无标题)",
          tag: "官网原文", pr: c.content_len ? fmt(c.content_len) + " 字" : "—",
          extra: '<div class="case-source">' + esc(c.source_name) + " · 抓取于 " +
            esc(String(c.fetched_at || "").replace("T", " ")) + "<br>" +
            '<a href="' + esc(c.url) + '" target="_blank" rel="noopener" style="color:#e60012">查看原文 ↗</a></div>' });
      }).join("");
    } else {
      html = items.map(function (c) {
        var hik = c.brand === "海康";
        return gridCard({ cls: "comp-card" + (hik ? " hik" : ""),
          pn: c.category || "", nm: c.model || "",
          tag: c.brand, pr: fmt(c.fact_count) + " 条参数",
          extra: '<div class="extra"><span class="comp-brand">' + esc(c.brand) + "</span> " +
            esc(c.display_name || "") + "</div>" +
            '<div class="case-source">官网参数页 · ' + esc(String(c.retrieved_at || "").slice(0, 10)) +
            (c.source_url ? '<br><a href="' + esc(c.source_url) + '" target="_blank" rel="noopener" style="color:#e60012">参数来源 ↗</a>' : "") +
            "</div>" });
      }).join("");
    }
    grid.innerHTML = html || '<div class="kb-empty">没有匹配的' + label + "。</div>";
    meta.innerHTML = "共 <b>" + fmt(items.length) + "</b> 条" + label +
      (q ? "（已筛选）" : "") +
      (state.tab === "competitors" && state.brand ? " · 品牌：" + esc(state.brand) : "");
    document.getElementById("kb-more").hidden = true;
    if (state.tab === "solutions") {
      grid.querySelectorAll(".sol-card").forEach(function (card) {
        card.addEventListener("click", function () {
          location.href = "/kb/solution/" + encodeURIComponent(card.dataset.sol);
        });
      });
    }
  }

  function renderBrandFilter() {
    var host = document.getElementById("brand-filter");
    var opts = ["海康", ""];
    host.innerHTML = opts.map(function (b) {
      return '<button data-brand="' + esc(b) + '" class="' + (state.brand === b ? "active" : "") + '">' +
        (b || "全部品牌") + "</button>";
    }).join("");
    host.querySelectorAll("button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.brand = btn.dataset.brand;
        renderBrandFilter(); renderGridItems();
      });
    });
  }

  /* ---------------- 竞品：参数明细视图（358 条逐条开放） ---------------- */
  function renderCompToolbar() {
    var isComp = state.tab === "competitors";
    var isFacts = isComp && state.compView === "facts";
    document.getElementById("comp-view-switch").hidden = !isComp;
    document.getElementById("brand-filter").hidden = !(isComp && !isFacts);
    document.getElementById("facts-filter").hidden = !isFacts;
    document.getElementById("kb-grid").hidden = isFacts;
    document.getElementById("facts-wrap").hidden = !isFacts;
    if (!isComp) return;
    document.querySelectorAll("#comp-view-switch button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.view === state.compView);
    });
    if (!isFacts) renderBrandFilter();
  }

  function renderFactFilters() {
    var f = state.facts;
    var fl = f.filters || { brands: [], categories: [], models: [] };
    function fill(id, current, allLabel, list, val, label) {
      var sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML = '<option value="">' + allLabel + "</option>" +
        list.map(function (x) {
          var v = val(x);
          return '<option value="' + esc(v) + '"' + (String(current) === String(v) ? " selected" : "") +
            ">" + esc(label(x)) + "</option>";
        }).join("");
    }
    fill("fact-brand", f.brand, "全部品牌", fl.brands,
      function (x) { return x.brand; },
      function (x) { return x.brand + "（" + fmt(x.c) + " 条）"; });
    fill("fact-category", f.category, "全部类目", fl.categories,
      function (x) { return x.category; },
      function (x) { return x.category + "（" + fmt(x.c) + " 条）"; });
    // 选了类目后，机型下拉只列该类目的，避免补采后几百个型号挤在一起
    var models = f.category
      ? fl.models.filter(function (x) { return x.category === f.category; })
      : fl.models;
    if (f.model && !models.some(function (x) { return x.model === f.model; })) {
      f.model = ""; // 切换类目后清空失效的机型筛选
    }
    fill("fact-model", f.model, "全部机型", models,
      function (x) { return x.model; },
      function (x) { return x.model + "（" + fmt(x.c) + " 条）"; });
  }

  function renderFacts() {
    var f = state.facts;
    var body = document.getElementById("facts-body");
    body.innerHTML = f.items.map(function (x) {
      return "<tr>" +
        '<td><span class="f-brand' + (x.brand === "海康" ? " hik" : "") + '">' + esc(x.brand) + "</span></td>" +
        "<td>" + esc(x.category || "—") + "</td>" +
        '<td class="f-model">' + esc(x.model) + "</td>" +
        "<td>" + esc(x.section || "—") + "</td>" +
        '<td class="f-field">' + esc(x.field_label) + "</td>" +
        '<td class="f-value">' + esc(x.raw_value || "—") + "</td>" +
        "<td>" + (x.source_url ? '<a class="f-link" href="' + esc(x.source_url) +
          '" target="_blank" rel="noopener">官网 ↗</a>' : "—") + "</td></tr>";
    }).join("") || '<tr><td colspan="7" style="text-align:center;padding:36px;color:#6b7280">没有匹配的参数，换个筛选条件试试。</td></tr>';
    document.getElementById("kb-meta").innerHTML = "共 <b>" + fmt(f.total) + "</b> 条官网参数事实" +
      "（已显示 " + fmt(f.items.length) + " 条）" +
      (f.brand ? " · 品牌：" + esc(f.brand) : "") +
      (f.category ? " · 类目：" + esc(f.category) : "") +
      (f.model ? " · 机型：" + esc(f.model) : "") +
      (f.q ? " · 关键字：" + esc(f.q) : "");
    document.getElementById("kb-more").hidden = f.offset >= f.total;
  }

  async function loadFacts(reset) {
    var f = state.facts;
    if (f.loading) return;
    f.loading = true;
    if (reset) { f.offset = 0; f.items = []; }
    var p = new URLSearchParams({
      brand: f.brand, category: f.category, model: f.model, q: f.q,
      offset: f.offset, limit: 40,
    });
    try {
      var r = await fetch("/api/kb/competitors/facts?" + p.toString());
      if (!r.ok) throw new Error("HTTP " + r.status);
      var d = await r.json();
      f.filters = d.filters || f.filters;
      f.total = d.total || 0;
      f.items = f.items.concat(d.items || []);
      f.offset = f.items.length;
      renderFactFilters();
      renderFacts();
    } catch (e) {
      document.getElementById("facts-body").innerHTML =
        '<tr><td colspan="7" style="text-align:center;padding:36px">加载失败：' + esc(e.message) + "</td></tr>";
    } finally { f.loading = false; }
  }

  async function loadGrid() {
    if (state.gridLoading) return;
    state.gridLoading = true;
    var grid = document.getElementById("kb-grid");
    grid.innerHTML = '<div class="kb-empty">加载中…</div>';
    var url = { solutions: "/api/kb/solutions", cases: "/api/kb/cases",
      competitors: "/api/kb/competitors?limit=400" }[state.tab];
    try {
      var res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      var d = await res.json();
      state.gridItems = d.items || [];
      if (state.tab === "cases") {
        var note = document.getElementById("view-note");
        if (note && d.source_note && note.innerHTML.indexOf("来源说明") < 0) {
          note.innerHTML += "<br><span class='sep'>来源说明：</span>" + esc(d.source_note);
        }
      }
      renderGridItems();
    } catch (err) {
      grid.innerHTML = '<div class="kb-empty">加载失败：' + esc(err.message) + "</div>";
    } finally { state.gridLoading = false; }
  }

  /* ---------------- 对比篮 / 配单篮 ---------------- */
  function renderBasket() {
    var kind = basketKind, arr = baskets[kind];
    setText("basket-title", kind === "compare" ? "⚔️ 对比篮（最多 2 款）" : "🧾 配单篮");
    var body = document.getElementById("basket-body");
    var foot = document.getElementById("basket-foot");
    if (!arr.length) {
      body.innerHTML = '<div class="kb-empty">还没有加入' + (kind === "compare" ? "对比" : "配单") +
        "产品。到「产品中心」点产品卡片右侧的按钮即可加入。</div>";
      foot.innerHTML = "";
      return;
    }
    body.innerHTML = arr.map(function (x, i) {
      return '<div class="basket-item"><div class="bi-main">' +
        '<div class="bi-name">' + esc(x.model) + "</div>" +
        '<div class="bi-sub">' + esc(x.name || "") + (x.cls ? " · " + esc(x.cls) : "") +
        (x.price ? " · ¥" + fmt(x.price) : "") + "</div></div>" +
        '<button class="bi-x" data-i="' + i + '" aria-label="移除">✕</button></div>';
    }).join("");
    body.querySelectorAll(".bi-x").forEach(function (b) {
      b.addEventListener("click", function () {
        arr.splice(Number(b.dataset.i), 1); save(kind); renderBasket();
      });
    });
    var sum = arr.reduce(function (a, x) { return a + (Number(x.price) || 0); }, 0);
    foot.innerHTML = (kind === "bom"
      ? '<div class="basket-sum"><span>参考价小计（单件各 1 件）</span><b>¥' + fmt(sum) + "</b></div>" +
        '<div class="case-source" style="margin-bottom:10px">仅为选型清单：候选择一、数量与工程配置未核算，不可直接当报价单。</div>'
      : '<div class="case-source" style="margin-bottom:10px">带着已选型号去竞品对比页，系统会自动填到大华一侧。</div>') +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
      (kind === "compare"
        ? '<a class="btn primary" id="go-compare" href="/compare?dahua=' +
          encodeURIComponent(arr[0].model) + '">去竞品对比 →</a>'
        : '<button class="btn primary" id="export-bom">导出清单 CSV</button>') +
      '<button class="btn ghost" id="clear-basket">清空</button></div>';
    var clr = document.getElementById("clear-basket");
    if (clr) clr.addEventListener("click", function () {
      baskets[kind] = []; save(kind); renderBasket();
    });
    var exp = document.getElementById("export-bom");
    if (exp) exp.addEventListener("click", exportBomCsv);
  }

  function exportBomCsv() {
    var head = ["型号", "产品名称", "类目", "参考单价", "来源"];
    var lines = [head.join(",")].concat(baskets.bom.map(function (x) {
      return [x.model, x.name, x.cls, x.price || "", "大华本地产品库"]
        .map(function (v) { return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"'; })
        .join(",");
    }));
    var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "大华选型清单-" + new Date().toISOString().slice(0, 10) + ".csv";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function openBasket(kind) {
    basketKind = kind;
    document.getElementById("basket").hidden = false;
    renderBasket();
  }

  /* ---------------- 同步流程（真实结果） ---------------- */
  var syncing = false;
  async function startSync() {
    if (syncing) return;
    syncing = true;
    var btn = document.getElementById("btn-sync");
    var log = document.getElementById("kb-log");
    btn.disabled = true; btn.textContent = "⟳ 同步中…";
    log.hidden = false;
    log.textContent = "▶ 已发起同步：匿名游客接口拉取云商公开产品 / 53 条方案清单，并做增量入库…\n";
    try {
      var res = await fetch("/api/kb/sync", { method: "POST" });
      if (!res.ok) throw new Error("同步请求失败 HTTP " + res.status);
      var data = await res.json();
      if (!data.job_id) throw new Error("未返回任务 ID");
      poll(data.job_id, btn, log);
    } catch (err) {
      log.textContent += "\n✗ " + err.message;
      finishSync(btn);
    }
  }
  async function poll(jobId, btn, log) {
    try {
      var res = await fetch("/api/kb/sync/" + encodeURIComponent(jobId));
      if (!res.ok) throw new Error("进度查询失败");
      var d = await res.json();
      log.textContent = d.log || "";
      log.scrollTop = log.scrollHeight;
      if (d.status === "running") {
        setTimeout(function () { poll(jobId, btn, log); }, 1200);
        return;
      }
      var r = d.result || {};
      if (d.status === "done") {
        var p = r.products || {}, s = r.solutions || {}, sc = r.schemes || {};
        log.textContent += "\n—— 同步结果 ——" +
          "\n产品：线上 " + fmt(p.fetched) + " 条 / 新增 " + fmt(p.added) +
          " 条 / 刷新 " + fmt(p.refreshed) + " 条" +
          "\n方案：补齐 solutions " + fmt(s.added) + " 条（现共 " + fmt(s.total) + " 条）" +
          "\n清单：线上方案 " + fmt(sc.online) + " 个 / 新增 " + fmt(sc.new) + " 个" +
          "\n配单接口自检：" + (sc.probe && sc.probe.ok ? "正常" : "异常") +
          "\n✔ 数据已真实写入本地知识库。";
        if (d.stats) renderStats(d.stats);
        refreshAll();
      } else {
        log.textContent += "\n✗ 同步失败：" + esc((r.error) || "脚本返回非 0，详见上方日志");
      }
      finishSync(btn);
    } catch (err) {
      log.textContent += "\n✗ " + err.message;
      finishSync(btn);
    }
  }
  function finishSync(btn) {
    syncing = false; btn.disabled = false;
    btn.textContent = "⟳ 同步最新大华产品与方案";
  }
  function refreshAll() {
    fetch("/api/kb/categories").then(function (r) { return r.json(); }).then(function (d) {
      renderCategories(d.items, d.total);
      var c = document.getElementById("cat-all-count");
      if (c) c.textContent = fmt(d.total);
    }).catch(function () {});
    if (state.tab === "products") loadProducts(true); else loadGrid();
  }

  /* ---------------- 事件绑定 ---------------- */
  function applyView(isProduct) {
    var pc = document.getElementById("pc-view");
    var gv = document.getElementById("grid-view");
    // hidden 属性 + 行内 display 双保险：行内样式不受外部 CSS 缓存影响
    pc.hidden = !isProduct;
    gv.hidden = isProduct;
    pc.style.display = isProduct ? "" : "none";
    gv.style.display = isProduct ? "none" : "block";
  }

  function switchTab(tab) {
    state.tab = tab;
    var isProduct = tab === "products";
    applyView(isProduct);
    updateNote();
    renderCompToolbar();
    if (isProduct) loadProducts(true);
    else if (state.tab === "competitors" && state.compView === "facts") loadFacts(true);
    else loadGrid();
    var tabs = document.getElementById("kb-tabs");
    if (tabs) window.scrollTo({ top: Math.max(0, tabs.getBoundingClientRect().top + window.scrollY - 80),
      behavior: "smooth" });
  }

  function init() {
    document.getElementById("kb-tabs").addEventListener("click", function (e) {
      var t = e.target.closest(".kb-tab");
      if (!t) return;
      document.querySelectorAll(".kb-tab").forEach(function (b) { b.classList.remove("active"); });
      t.classList.add("active");
      switchTab(t.dataset.tab);
    });
    document.getElementById("pc-q").addEventListener("input", debounce(function () { loadProducts(true); }, 350));
    document.getElementById("pc-more").addEventListener("click", function () { loadProducts(false); });
    document.getElementById("kb-q").addEventListener("input", debounce(function () {
      if (state.tab === "competitors" && state.compView === "facts") {
        state.facts.q = document.getElementById("kb-q").value.trim();
        loadFacts(true);
      } else renderGridItems();
    }, 300));
    document.getElementById("kb-more").addEventListener("click", function () {
      if (state.tab === "competitors" && state.compView === "facts") loadFacts(false);
    });
    document.getElementById("comp-view-switch").addEventListener("click", function (e) {
      var b = e.target.closest("[data-view]");
      if (!b) return;
      state.compView = b.dataset.view;
      state.facts.q = "";
      document.getElementById("kb-q").value = "";
      renderCompToolbar();
      if (state.compView === "facts") loadFacts(true); else loadGrid();
    });
    ["fact-brand", "fact-category", "fact-model"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", function () {
        var prevCat = state.facts.category;
        state.facts.brand = document.getElementById("fact-brand").value;
        state.facts.category = document.getElementById("fact-category").value;
        state.facts.model = document.getElementById("fact-model").value;
        if (prevCat !== state.facts.category) state.facts.model = "";
        loadFacts(true);
      });
    });
    document.getElementById("fact-reset").addEventListener("click", function () {
      state.facts.brand = "海康";
      state.facts.category = "";
      state.facts.model = "";
      state.facts.q = "";
      document.getElementById("kb-q").value = "";
      loadFacts(true);
    });
    document.getElementById("btn-sync").addEventListener("click", startSync);
    document.getElementById("open-compare").addEventListener("click", function () { openBasket("compare"); });
    document.getElementById("open-bom").addEventListener("click", function () { openBasket("bom"); });
    document.getElementById("basket-close").addEventListener("click", function () {
      document.getElementById("basket").hidden = true;
    });
    document.getElementById("pc-list").addEventListener("click", function (e) {
      var b = e.target.closest(".pc-btn[data-kind]");
      if (!b) return;
      addToBasket(b.dataset.kind, {
        model: b.dataset.m, name: b.dataset.name, price: b.dataset.price, cls: b.dataset.cls,
      });
    });

    applyView(true); // 初始就在产品页，显式隐藏其它视图
    renderCompToolbar();
    updateBasketCounts();
    fetch("/api/kb/stats").then(function (r) { return r.json(); }).then(renderStats).catch(function () {});
    fetch("/api/kb/categories").then(function (r) { return r.json(); }).then(function (d) {
      state.total = d.total;
      renderCategories(d.items, d.total);
    }).catch(function () {});
    loadProducts(true);
    updateNote();
  }

  init();
})();
