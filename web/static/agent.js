/* ============================================================
 * Agent 工作台：LangGraph 的 start / state / resume 三动作接前端
 * - 思考流 5 步随状态高亮
 * - 缺口回问（gap）/ Spec 审阅（review）两种中断分别处理
 * - 审阅页改结构化参数 → patch 写回 state → 条件回边重算
 * - thread_id 存 localStorage，刷新或重启服务后可断点续跑
 * ============================================================ */
(function () {
  "use strict";

  var DEMO = "某智慧园区综合安防项目：园区周界80路，出入口20路，主干道30路，室内大厅40路，存储30天。";
  var TEMPLATES = [
    "某智慧园区综合安防项目：园区周界80路，出入口20路，主干道30路，室内大厅40路，存储30天。",
    "某智慧校园安防项目：周界30路，出入口20路，教学楼室内60路，操场公共区域40路，食堂15路，存储45天。",
    "某办公楼宇安防项目：谈话室20路，办案区周界40路，电梯15路，走廊30路，大厅20路，机房10路，存储90天。",
    "某商业综合体安防项目：地下停车场80路，出入口25路，电梯30路，公共区域40路，存储30天。",
  ];
  var KEY = "dh_agent_thread";
  var st = { thread_id: null, data: null, busy: false, spec: null, files: null, mode: "simple" };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(n) { return n == null || isNaN(n) ? "—" : Number(n).toLocaleString("zh-CN"); }
  function intOr(v) {
    if (v == null || String(v).trim() === "") return null;
    var n = Number.parseInt(String(v).trim(), 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  async function api(url, opts) {
    var r = await fetch(url, opts);
    var d = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error((d.detail && d.detail.message) || ("HTTP " + r.status));
    return d;
  }
  function post(url, body) {
    return api(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
  }
  function busy(on, btn) {
    st.busy = on;
    document.querySelectorAll(".agent-actions .btn, #btn-start, #btn-restore").forEach(function (b) {
      b.disabled = on;
    });
    if (btn && on) btn.dataset.old = btn.textContent, btn.textContent = "处理中…";
    if (btn && !on && btn.dataset.old) { btn.textContent = btn.dataset.old; delete btn.dataset.old; }
  }
  function alertMsg(msg) {
    var el = $("alert");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  function step(n) {
    document.querySelectorAll("#agent-steps .step").forEach(function (b) {
      var i = Number(b.dataset.step);
      b.classList.toggle("active", i <= n);
      b.classList.toggle("done", i < n);
    });
  }

  function specHtml(s) {
    if (!s) return '<div class="muted">暂无 Spec</div>';
    var reqs = s.requirements || [];
    var total = reqs.reduce(function (a, r) { return a + (Number(r.lanes) || 0); }, 0);
    var devs = s.devices || [];
    var qty = devs.reduce(function (a, d) { return a + (Number(d.qty) || 0); }, 0);
    var cells = [
      ["项目", s.project && s.project.name],
      ["行业", s.project && s.project.industry],
      ["总点位", total + " 路"],
      ["存储", (s.storage && s.storage.retention_days) + " 天 / " +
        fmt(s.storage && s.storage.with_redundancy_tb) + " TB"],
      ["上行带宽", fmt(s.network && s.network.uplink_mbps) + " Mbps"],
      ["设备", devs.length + " 类 / " + qty + " 台"],
    ];
    var html = '<div class="spec-grid">' + cells.map(function (c) {
      return '<div class="spec-cell"><div class="k">' + esc(c[0]) + '</div><div class="v">' +
        esc(c[1]) + "</div></div>";
    }).join("") + "</div>";

    html += '<table class="mini-table"><thead><tr><th>场景</th><th>建设目标</th><th>点位数</th><th>码流</th></tr></thead><tbody>' +
      reqs.map(function (r) {
        return "<tr><td>" + esc(r.target) + "</td><td>" + esc(r.intent) + "</td><td>" +
          fmt(r.lanes) + "</td><td>" + fmt(r.bitrate_mbps) + " Mbps</td></tr>";
      }).join("") + "</tbody></table>";

    html += '<table class="mini-table"><thead><tr><th>类别</th><th>型号</th><th>数量</th><th>单价</th></tr></thead><tbody>' +
      devs.map(function (d) {
        return "<tr><td>" + esc(d.category) + "</td><td>" + esc(d.model) + "</td><td>" +
          fmt(d.qty) + "</td><td>" + (d.unit_price == null ? "—" : "¥" + fmt(d.unit_price)) + "</td></tr>";
      }).join("") + "</tbody></table>";

    var gaps = s.gaps || [];
    if (gaps.length) {
      html += '<div class="muted" style="font-size:12px">待确认：' +
        gaps.map(esc).join("；") + "</div>";
    }
    return html;
  }

  function fillReviewEditors(s) {
    if (!s) return;
    var reqs = s.requirements || [];
    $("ed-lanes").value = reqs.reduce(function (a, r) { return a + (Number(r.lanes) || 0); }, 0);
    $("ed-days").value = (s.storage && s.storage.retention_days) || "";
    $("ed-name").value = (s.project && s.project.name) || "";
  }

  /* ---------- 工具核实（LLM 自主调工具，结果由确定性函数算出） ---------- */
  var TOOL_LABELS = {
    search_kb: "产品库检索", calc_storage: "存储测算",
    calc_bandwidth: "带宽测算", compare_pair: "竞品逐字段对比",
  };

  function parseToolResult(c) {
    try { return JSON.parse(c.result); } catch (e) { return null; }
  }

  function summarizeTool(c) {
    var r = parseToolResult(c);
    if (!c.ok) return "失败：" + (r && r.error ? r.error : "调用未成功");
    if (!r) return "已完成";
    if (c.tool === "search_kb") {
      if (!r.found) return r.note || "没有匹配型号";
      var items = (r.items || []).slice(0, 3).map(function (it) {
        return it.model + (it.price_stand != null ? "（¥" + it.price_stand + "）" : "");
      });
      return "命中 " + r.found + " 款：" + items.join("、") + (r.found > 3 ? " 等" : "");
    }
    if (c.tool === "calc_storage") {
      return "裸容量 " + r.raw_tb + " TB · 含冗余 " + r.with_redundancy_tb + " TB（冗余系数 " + r.redundancy + "）";
    }
    if (c.tool === "calc_bandwidth") return "上行带宽 " + r.uplink_mbps + " Mbps";
    if (c.tool === "compare_pair") {
      var cov = r.coverage || {};
      return (r.dahua_model || "?") + " vs " + (r.hik_model || "?") +
        " · 字段对齐 " + (cov.aligned || 0) + "/" + (cov.total || 0) + " 项";
    }
    return "已完成";
  }

  function renderResearch(d) {
    var el = $("research-note");
    var rz = d.research || {};
    var calls = rz.calls || [];
    if (!calls.length) { el.hidden = true; el.innerHTML = ""; return; }
    var rows = calls.map(function (c, i) {
      var args = Object.keys(c.args || {}).map(function (k) {
        return k + "=" + c.args[k];
      }).join("，");
      return "<li><b>" + esc(TOOL_LABELS[c.tool] || c.tool) + "</b>" +
        (args ? ' <span class="tool-args">(' + esc(args) + ")</span>" : "") +
        ' → <span class="' + (c.ok ? "" : "tool-bad") + '">' + esc(summarizeTool(c)) + "</span></li>";
    });
    var engineNote = { llm: "大模型自主决定调用", offline: "离线降级", rule: "本地模式" }[rz.engine] || "";
    var html = "<b>工具核实</b>（" + esc(engineNote) +
      "，数字全部由确定性工具算出，入参如上可审计；仅作核实记录，不改变方案计算口径）：<ul class='tool-list'>" +
      rows.join("") + "</ul>";
    if (rz.warnings && rz.warnings.length) {
      html += "<div class='muted' style='font-size:11.5px'>" + esc(rz.warnings.join("；")) + "</div>";
    }
    el.hidden = false;
    el.innerHTML = html;
  }

  var STATUS_LABEL = {
    awaiting_input: "等待人工输入",
    done: "已完成",
    aborted: "已终止（缺口超出轮次）",
    running: "运行中",
  };

  function render(d) {
    st.data = d;
    st.thread_id = d.thread_id;
    try { localStorage.setItem(KEY, d.thread_id); } catch (e) { /* 隐私模式忽略 */ }
    $("run-card").hidden = false;
    $("tid").textContent = d.thread_id;
    $("run-status").textContent = STATUS_LABEL[d.status] || d.status;
    $("rounds").textContent = fmt(d.rounds);

    var p = d.parser || {};
    var engineText = {
      llm: "LLM 语义解析（数字仍由确定性算法核算）",
      rule: "本地规则解析（未配置大模型）",
      offline: "本地规则解析（离线降级）",
    }[p.engine] || "本地规则解析";
    var note = "<b>解析引擎</b>：" + esc(engineText) +
      (p.scenes && p.scenes.length ? " · 生效场景：" + esc(p.scenes.join("、")) : "");
    if (p.normalized_scenes && p.normalized_scenes.length) {
      note += "<br><b>LLM 补写场景</b>：" + esc(p.normalized_scenes.join("、")) +
        "（口语归一化，仅追加场景词，不含数字）";
    }
    if (p.number_normalized && p.number_normalized.length) {
      note += "<br><b>本地数量词转换</b>：" + esc(p.number_normalized.join("、")) +
        "（数字来自原文，非大模型产出）";
    }
    if (p.industry_suggestion) {
      note += "<br><b>LLM 行业建议</b>：" + esc(p.industry_suggestion) + "（仅提示，未改动分类口径）";
    }
    if (p.dropped_scenes && p.dropped_scenes.length) {
      note += "<br><b>已丢弃的不合规输出</b>：" + esc(p.dropped_scenes.join("、"));
    }
    if (p.warnings && p.warnings.length) note += "<br>" + esc(p.warnings.join("；"));
    $("parser-note").innerHTML = note;
    renderResearch(d);

    $("gap-panel").hidden = true;
    $("review-panel").hidden = true;
    $("done-panel").hidden = true;
    alertMsg("");

    if (d.status === "awaiting_input" && d.interrupt_type === "gap") {
      step(3);
      $("gap-panel").hidden = false;
      var ip = d.interrupt || {};
      var gaps = ip.gaps || d.gate_gaps || [];
      var qs = ip.questions || [];
      $("gap-list").innerHTML = qs.length
        ? qs.map(function (x) {
            return "<li><b>" + esc(x.question) + "</b>" +
              (x.gap ? "<div class='gap-origin'>系统提示：" + esc(x.gap) + "</div>" : "") +
              "</li>";
          }).join("")
        : (gaps.length
            ? gaps.map(function (g) { return "<li>" + esc(g) + "</li>"; }).join("")
            : "<li>方案信息不足，请补充</li>");
      var qe = { llm: "大模型改写追问（不含数字）", offline: "本地追问模板（离线降级）",
                 rule: "本地追问模板" }[ip.question_engine] || "本地追问模板";
      var ge = $("gap-engine");
      if (ge) ge.innerHTML = " · 追问措辞：" + esc(qe);
      $("gap-input").value = "";
    } else if (d.status === "awaiting_input" && d.interrupt_type === "review") {
      step(4);
      $("review-panel").hidden = false;
      $("spec-summary").innerHTML = specHtml(d.spec);
      fillReviewEditors(d.spec);
      $("review-feedback").value = "";
    } else if (d.status === "done") {
      step(5);
      st.spec = d.spec;
      st.files = d.files;
      st.mode = "simple";
      $("done-panel").hidden = false;
      drawTopo();
      var png = d.files && d.files.png;
      var docx = d.files && d.files.docx;
      $("dl-png").href = png ? png.url : "#";
      $("dl-png-detail").href = (d.files && d.files.png_detail) ? d.files.png_detail.url : "#";
      $("dl-docx").href = docx ? docx.url : "#";
    } else if (d.status === "aborted") {
      step(3);
      alertMsg("缺口回问超过上限，未生成半成品。缺口：" + (d.gaps || []).join("；"));
    } else {
      step(2);
    }
    $("run-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function start(btn) {
    var text = $("demand").value.trim();
    if (!text) { alertMsg("请先填写需求描述"); return; }
    busy(true, btn);
    alertMsg("");
    try {
      var d = await post("/api/agent/start", {
        text: text,
        name: $("in-name").value.trim() || null,
        lanes: intOr($("in-lanes").value),
        retention_days: intOr($("in-days").value),
      });
      render(d);
    } catch (e) { alertMsg("启动失败：" + e.message); }
    finally { busy(false, btn); }
  }

  async function resume(answer, btn) {
    if (!st.thread_id) { alertMsg("没有进行中的会话"); return; }
    busy(true, btn);
    alertMsg("");
    try {
      var d = await post("/api/agent/" + encodeURIComponent(st.thread_id) + "/resume",
        { answer: answer });
      render(d);
    } catch (e) { alertMsg("继续失败：" + e.message); }
    finally { busy(false, btn); }
  }

  async function restore(tid, silent) {
    if (!tid) { if (!silent) alertMsg("没有可恢复的会话"); return; }
    try {
      var d = await api("/api/agent/" + encodeURIComponent(tid) + "/state");
      render(d);
    } catch (e) {
      try { localStorage.removeItem(KEY); } catch (x) { /* ignore */ }
      if (!silent) alertMsg("恢复失败：" + e.message);
    }
  }

  function drawTopo() {
    var host = $("topo-host");
    if (!host || !st.spec) return;
    document.querySelectorAll(".topo-mode").forEach(function (b) {
      b.classList.toggle("active", b.dataset.mode === st.mode);
    });
    if (window.DahuaTopology) DahuaTopology.render(host, st.spec, st.mode);
  }

  function init() {
    $("btn-demo").addEventListener("click", function () { $("demand").value = DEMO; });

    // 快速体验模板：点击填入典型需求
    document.querySelectorAll(".tpl-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var i = parseInt(chip.dataset.tpl, 10);
        if (TEMPLATES[i] != null) $("demand").value = TEMPLATES[i];
      });
    });

    // 使用教程弹窗
    function openTut() { $("tut-mask").hidden = false; }
    function closeTut() { $("tut-mask").hidden = true; }
    $("btn-tutorial").addEventListener("click", openTut);
    $("tut-close").addEventListener("click", closeTut);
    $("tut-mask").addEventListener("click", function (e) {
      if (e.target === $("tut-mask")) closeTut();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("tut-mask").hidden) closeTut();
    });

    // 拓扑双模式切换
    document.querySelectorAll(".topo-mode").forEach(function (b) {
      b.addEventListener("click", function () {
        st.mode = b.dataset.mode;
        drawTopo();
      });
    });

    // 导出当前正在查看的拓扑图为 PNG（客户端，离线可用）
    $("dl-png-svg").addEventListener("click", function () {
      if (window.DahuaTopology) {
        DahuaTopology.exportPNG($("topo-host"), st.mode === "detailed" ? "拓扑图-详细.png" : "拓扑图-简略.png");
      }
    });
    $("btn-start").addEventListener("click", function () { start(this); });
    $("btn-restore").addEventListener("click", function () {
      var tid = null;
      try { tid = localStorage.getItem(KEY); } catch (e) { /* ignore */ }
      restore(tid, false);
    });
    $("btn-gap").addEventListener("click", function () {
      var v = $("gap-input").value.trim();
      if (!v) { alertMsg("请先填写补充内容"); return; }
      resume({ text: v }, this);
    });
    $("btn-approve").addEventListener("click", function () {
      resume({ decision: "approve" }, this);
    });
    $("btn-revise").addEventListener("click", function () {
      var s = st.data && st.data.spec;
      if (!s) return;
      var patch = {};
      var lanes = intOr($("ed-lanes").value);
      var days = intOr($("ed-days").value);
      var name = $("ed-name").value.trim();
      var curLanes = (s.requirements || []).reduce(function (a, r) { return a + (Number(r.lanes) || 0); }, 0);
      var curDays = s.storage && s.storage.retention_days;
      var curName = s.project && s.project.name;
      if (lanes != null && lanes !== curLanes) patch.lanes = lanes;
      if (days != null && days !== curDays) patch.retention_days = days;
      if (name && name !== curName) patch.name = name;
      var feedback = $("review-feedback").value.trim();
      if (!Object.keys(patch).length && !feedback) {
        alertMsg("没有检测到修改，若已确认请点「通过并生成」");
        return;
      }
      var answer = { decision: "revise", feedback: feedback || "按审阅意见调整" };
      if (Object.keys(patch).length) answer.patch = patch;
      resume(answer, this);
    });
    $("btn-new").addEventListener("click", function () {
      $("run-card").hidden = true;
      st.thread_id = null; st.data = null;
      try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
      $("demand").focus();
      step(1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });

    api("/api/agent/status").then(function (d) {
      var b = $("engine-badge");
      var t = d.tools || {};
      b.textContent = d.label + (t.online ? " · 工具核实已启用" : "");
      b.classList.toggle("offline", d.engine !== "llm");
    }).catch(function () { $("engine-badge").textContent = "引擎状态读取失败"; });

    var tid = null;
    try { tid = localStorage.getItem(KEY); } catch (e) { /* ignore */ }
    if (tid) restore(tid, true);
  }

  init();
})();
