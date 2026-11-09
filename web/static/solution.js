/* ============================================================
 * 方案详情页 /kb/solution/{sol_id}
 * 大华云商风格：点方案 → 进详情页，四块：
 *   方案详情 / 方案配单 / 相关资料 / 相关内容
 * 全程不展示观看数。
 * ============================================================ */
(function () {
  "use strict";

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
  function $(id) { return document.getElementById(id); }

  var solId = (location.pathname.split("/").pop() || "").trim();
  var tab = "detail";

  function switchTab(t) {
    tab = t;
    document.querySelectorAll(".sol-tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === t);
    });
    ["detail", "bom", "materials", "related"].forEach(function (k) {
      var sec = $("sec-" + k);
      if (sec) sec.hidden = k !== t;
    });
  }

  function renderHero(sol) {
    document.title = sol.name + " · 大华解决方案助手";
    $("sol-name").textContent = sol.name || "（未命名方案）";
    $("sol-scene").textContent = sol.scene_name ? "SCENE · " + sol.scene_name : "SOLUTION";
    var meta = [];
    if (sol.release_time) meta.push("发布 " + esc(sol.release_time));
    if (sol.source) meta.push(esc(sol.source));
    $("sol-meta").innerHTML = meta.map(function (m) {
      return '<span class="pill">' + m + "</span>";
    }).join(" ");
    if (sol.tags && sol.tags.length) {
      $("sol-tags").innerHTML = sol.tags.map(function (t) {
        return '<span class="sol-tag scene">' + esc(t) + "</span>";
      }).join("");
    }
    var cover = $("sol-cover");
    if (sol.cover) {
      cover.src = sol.cover;
      cover.onerror = function () { cover.style.display = "none"; };
    } else {
      cover.style.display = "none";
    }
    if (sol.video_url) {
      var v = $("sol-video");
      v.hidden = false;
      v.innerHTML = '<a class="btn ghost" href="' + esc(sol.video_url) +
        '" target="_blank" rel="noopener">▶ 观看方案视频 ↗</a>';
    }
  }

  function renderDetail(sol) {
    if (sol.summary) {
      $("sol-summary").innerHTML = '<div class="sol-summary-box">' + esc(sol.summary) + "</div>";
    }
    var rich = $("sol-rich");
    if (sol.detail_html && sol.detail_html.trim()) {
      rich.innerHTML = sol.detail_html; // 云商原站图文详情（远程图离线可能不显示）
    } else if (sol.summary) {
      rich.innerHTML = '<div class="muted">该方案暂无更多图文详情。</div>';
    } else {
      rich.innerHTML = '<div class="muted">该方案暂无详情内容。</div>';
    }
  }

  function renderBom(bom) {
    $("bom-count").textContent = "共 " + fmt(bom.total) + " 条";
    $("bom-note").textContent = bom.note || "";
    var body = $("bom-body");
    if (!bom.items || !bom.items.length) {
      body.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:30px;color:#6b7280">该方案暂无云商配单明细。</td></tr>';
      return;
    }
    body.innerHTML = bom.items.map(function (it) {
      return "<tr><td>" + esc(it.category || "—") + "</td><td>" + esc(it.product_name || "—") +
        "</td><td>" + esc(it.model || "—") + "</td><td>" + esc(it.spec || "—") +
        '</td><td class="num">' + (it.qty == null ? "—" : fmt(it.qty)) +
        '</td><td class="num">' + (it.unit_price == null ? "—" : "¥" + fmt(it.unit_price)) +
        "</td><td>" + esc(it.price_basis || "—") + "</td></tr>";
    }).join("");
  }

  function renderMaterials(materials) {
    var grid = $("materials-grid");
    if (!materials || !materials.length) {
      grid.innerHTML = '<div class="kb-empty">配单涉及的设备暂未收录进本地产品库，无资料卡片。</div>';
      return;
    }
    grid.innerHTML = materials.map(function (m) {
      var img = m.img
        ? '<img class="prod-img" src="' + esc(m.img) + '" loading="lazy" alt="" onerror="this.style.opacity=0.15">'
        : '<div class="prod-img none">无图</div>';
      var price = m.price != null && Number(m.price) > 0 ? "¥" + fmt(m.price) : "面议";
      var desc = m.prod_desc ? '<div class="mat-desc">' + esc(m.prod_desc.slice(0, 80)) +
        (m.prod_desc.length > 80 ? "…" : "") + "</div>" : "";
      var link = m.url ? '<a class="mat-link" href="' + esc(m.url) +
        '" target="_blank" rel="noopener">官网参数 ↗</a>' : "";
      return '<div class="mat-card">' + img +
        '<div class="prod-info"><div class="pn">' + esc(m.class_name || "设备") + "</div>" +
        '<div class="nm">' + esc(m.product_name || m.model) + "</div>" +
        '<div class="row"><span class="cls">' + esc(m.model || "") + "</span>" +
        '<span class="pr">' + price + "</span></div>" + desc + link + "</div></div>";
    }).join("");
  }

  function renderRelated(related, scene) {
    $("related-sub").textContent = scene ? "同场景（" + scene + "）其它方案" : "更多方案";
    var grid = $("related-grid");
    if (!related || !related.length) {
      grid.innerHTML = '<div class="kb-empty">暂无同场景的其它方案。</div>';
      return;
    }
    grid.innerHTML = related.map(function (s) {
      var img = s.detail_img ? '<img class="sol-img" src="' + esc(s.detail_img) +
        '" loading="lazy" alt="" onerror="this.style.opacity=0.15">' : "";
      var meta = '<span class="sol-badge' + (s.source === "官网方案" ? " plain" : "") + '">' +
        esc(s.source) + "</span>";
      if (s.item_count) meta += '<span class="sol-tag">配单 ' + fmt(s.item_count) + " 条</span>";
      return '<div class="sol-card" data-sol="' + esc(s.sol_id) + '">' + img +
        '<div class="sol-body"><div class="sol-name">' + esc(s.name) + "</div>" +
        '<div class="sol-meta">' + meta + "</div></div></div>";
    }).join("");
    grid.querySelectorAll(".sol-card").forEach(function (card) {
      card.addEventListener("click", function () {
        location.href = "/kb/solution/" + card.dataset.sol;
      });
    });
  }

  async function load() {
    if (!solId) {
      $("sol-error").hidden = false;
      $("sol-error").textContent = "缺少方案 ID。";
      return;
    }
    try {
      var res = await fetch("/api/kb/solutions/" + encodeURIComponent(solId));
      if (!res.ok) throw new Error("HTTP " + res.status);
      var d = await res.json();
      var sol = d.solution || {};
      renderHero(sol);
      renderDetail(sol);
      renderBom(d.bom || { items: [] });
      renderMaterials(d.materials || []);
      renderRelated(d.related || [], sol.scene_name);
    } catch (err) {
      $("sol-error").hidden = false;
      $("sol-error").textContent = "加载失败：" + err.message;
    }
  }

  function init() {
    document.getElementById("sol-tabs").addEventListener("click", function (e) {
      var t = e.target.closest(".sol-tab");
      if (t) switchTab(t.dataset.tab);
    });
    load();
  }

  init();
})();
