/* ============================================================
 * 大华解决方案助手 · 交互式拓扑（红白风，纯 SVG，离线可用）
 * 两种模式：
 *   mode="simple"   简略分层版（前端→传输→存储→平台→显示），
 *                    摄像机节点标注「使用地点 + 建设目标」，按场景分组
 *   mode="detailed" 超详细逻辑连线版：场景摄像机 → 交换机 → NVR → 硬盘
 *                    → 平台 → 监视器 的真实互连
 * 用法：DahuaTopology.render(容器El, spec, mode)
 *       DahuaTopology.exportPNG(容器El, 文件名)  // 客户端导出 PNG
 * ============================================================ */
(function () {
  "use strict";

  var RED = "#e60012";
  var RED2 = "#ff3b43";
  var INK = "#1a1d24";
  var MUTED = "#6b7280";
  var BG = "#ffffff";
  var PANEL = "rgba(246,247,249,0.95)";
  var PANEL_LINE = "rgba(226,229,234,1)";

  var COLORS = {
    camera: "#0091be",
    switch: "#16966e",
    recorder: "#c87800",
    disk: "#7850be",
    monitor: "#d6336c",
    platform: "#e60012",
    box: "#606e82",
  };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function iconType(category) {
    if (category === "网络摄像机") return "camera";
    if (category.indexOf("交换机") >= 0 || category === "PoE交换机") return "switch";
    if (category === "NVR" || category.indexOf("录像机") >= 0) return "recorder";
    if (category.indexOf("硬盘") >= 0 || category.indexOf("存储") >= 0) return "disk";
    if (category === "监视器" || category.indexOf("拼接屏") >= 0) return "monitor";
    if (category.indexOf("平台") >= 0 || category.indexOf("解码") >= 0) return "platform";
    return "box";
  }
  function colorOf(category, dashed) { return dashed ? COLORS.box : (COLORS[iconType(category)] || COLORS.box); }
  function truncate(text, limit) {
    text = String(text || "");
    return text.length <= limit ? text : text.slice(0, limit - 1) + "…";
  }
  // 按像素宽度估算（中文按 1em、西文按 0.56em），避免固定字数截断造成溢出/重叠
  function measure(text, fs) {
    var w = 0;
    text = String(text == null ? "" : text);
    for (var i = 0; i < text.length; i++) {
      w += text.charCodeAt(i) < 128 ? fs * 0.56 : fs;
    }
    return w;
  }
  function fit(text, maxPx, fs) {
    text = String(text == null ? "" : text);
    if (!maxPx || measure(text, fs) <= maxPx) return text;
    var out = "";
    for (var i = 0; i < text.length; i++) {
      if (measure(out + text[i] + "…", fs) > maxPx) break;
      out += text[i];
    }
    return out + "…";
  }
  function intentMap(spec) {
    var m = {};
    (spec.requirements || []).forEach(function (r) { if (r.target) m[r.target] = r.intent || ""; });
    return m;
  }

  function iconSVG(type, cx, cy, s, color) {
    var g =
      '<g transform="translate(' + cx + "," + cy + ')">' +
      '<rect x="' + (-s * 0.62) + '" y="' + (-s * 0.48) + '" width="' + s * 1.24 +
      '" height="' + s * 0.96 + '" rx="7" fill="#ffffff" stroke="' + color + '" stroke-width="2"/>';
    if (type === "camera") {
      g +=
        '<circle cx="0" cy="0" r="' + s * 0.34 + '" fill="none" stroke="' + color + '" stroke-width="2.4"/>' +
        '<circle cx="0" cy="0" r="' + s * 0.16 + '" fill="' + color + '"/>' +
        '<rect x="' + s * 0.2 + '" y="' + -s * 0.62 + '" width="' + s * 0.32 + '" height="' + s * 0.2 + '" rx="3" fill="' + color + '"/>';
    } else if (type === "switch") {
      for (var i = 0; i < 4; i++) {
        var px = -s * 0.5 + i * (s * 0.26);
        g += '<rect x="' + px + '" y="' + s * 0.16 + '" width="' + s * 0.13 + '" height="' + s * 0.18 + '" rx="1.5" fill="' + color + '"/>';
      }
      g += '<circle cx="0" cy="' + -s * 0.2 + '" r="' + s * 0.1 + '" fill="' + color + '"/>';
    } else if (type === "recorder") {
      g +=
        '<rect x="' + -s * 0.48 + '" y="' + -s * 0.3 + '" width="' + s * 0.96 + '" height="' + s * 0.22 + '" rx="3" fill="none" stroke="' + color + '" stroke-width="2"/>' +
        '<rect x="' + -s * 0.48 + '" y="' + s * 0.04 + '" width="' + s * 0.96 + '" height="' + s * 0.22 + '" rx="3" fill="none" stroke="' + color + '" stroke-width="2"/>' +
        '<circle cx="' + s * 0.42 + '" cy="' + -s * 0.28 + '" r="' + s * 0.06 + '" fill="' + color + '"/>';
    } else if (type === "disk") {
      g +=
        '<rect x="' + -s * 0.5 + '" y="' + -s * 0.32 + '" width="' + s * 1.0 + '" height="' + s * 0.64 + '" rx="' + s * 0.1 + '" fill="#ffffff" stroke="' + color + '" stroke-width="2"/>' +
        '<line x1="' + -s * 0.5 + '" y1="' + -s * 0.1 + '" x2="' + s * 0.5 + '" y2="' + -s * 0.1 + '" stroke="' + color + '" stroke-width="1.5"/>' +
        '<line x1="' + -s * 0.5 + '" y1="' + s * 0.1 + '" x2="' + s * 0.5 + '" y2="' + s * 0.1 + '" stroke="' + color + '" stroke-width="1.5"/>' +
        '<circle cx="' + s * 0.22 + '" cy="0" r="' + s * 0.12 + '" fill="' + color + '"/>';
    } else if (type === "monitor") {
      g +=
        '<rect x="' + -s * 0.56 + '" y="' + -s * 0.46 + '" width="' + s * 1.12 + '" height="' + s * 0.72 + '" rx="6" fill="#ffffff" stroke="' + color + '" stroke-width="2"/>' +
        '<rect x="' + -s * 0.42 + '" y="' + -s * 0.3 + '" width="' + s * 0.84 + '" height="' + s * 0.42 + '" rx="2" fill="' + color + '" opacity="0.18"/>' +
        '<rect x="' + -s * 0.12 + '" y="' + s * 0.26 + '" width="' + s * 0.24 + '" height="' + s * 0.16 + '" fill="' + color + '"/>' +
        '<rect x="' + -s * 0.28 + '" y="' + s * 0.4 + '" width="' + s * 0.56 + '" height="' + s * 0.09 + '" rx="2" fill="' + color + '"/>';
    } else if (type === "platform") {
      for (var k = 0; k < 3; k++) {
        var yy = -s * 0.3 + k * s * 0.3;
        g +=
          '<rect x="' + -s * 0.56 + '" y="' + yy + '" width="' + s * 1.12 + '" height="' + s * 0.2 + '" rx="3" fill="none" stroke="' + color + '" stroke-width="2"/>' +
          '<circle cx="' + s * 0.44 + '" cy="' + (yy + s * 0.1) + '" r="' + s * 0.045 + '" fill="' + color + '"/>';
      }
    } else {
      g += '<circle cx="0" cy="0" r="' + s * 0.2 + '" fill="none" stroke="' + color + '" stroke-width="2.4"/>';
      g += '<line x1="' + -s * 0.2 + '" y1="0" x2="' + s * 0.2 + '" y2="0" stroke="' + color + '" stroke-width="2"/>';
    }
    return g + "</g>";
  }

  // =======================================================================
  // 简略分层版（带场景 / 使用地点 / 建设目标）
  // =======================================================================
  var SIM_LAYERS = [
    { key: "前端", cats: ["网络摄像机"] },
    { key: "传输", cats: ["PoE交换机", "交换机", "数通产品"] },
    { key: "存储", cats: ["NVR", "通用存储录像机", "机械硬盘"] },
    { key: "平台", cats: ["综合平台", "综合管理平台", "平台授权", "平台服务器"] },
    { key: "显示", cats: ["监视器", "拼接屏", "拼接支架", "解码器"] },
  ];

  function renderSimple(container, spec) {
    if (!container) return;
    spec = spec || {};
    var W = 1180, MARGIN_X = 60, LABEL_W = 168, TOP0 = 150, BAND_GAP = 52;
    var CARD_H = 128, CARD_GAP = 20, CARD_W_MAX = 280;
    var areaX = MARGIN_X + LABEL_W;
    var areaW = W - MARGIN_X - areaX;
    var im = intentMap(spec);

    var layers = SIM_LAYERS.map(function (L) {
      var items = (spec.devices || [])
        .filter(function (d) { return L.cats.indexOf(d.category) >= 0; })
        .map(function (d) {
          return {
            category: d.category, model: d.model, display_name: d.display_name || "",
            qty: d.qty || 0, scene: d.scene || "",
            intent: (d.category === "网络摄像机") ? (im[d.scene] || "") : "",
            dashed: false,
          };
        });
      if (!items.length) {
        items = [{ category: L.key + "设备", model: "待选型", display_name: "", qty: 0, scene: "", intent: "", dashed: true }];
      }
      return { key: L.key, items: items };
    });

    var bandRects = [];
    var top = TOP0;
    layers.forEach(function (L) {
      var n = L.items.length;
      var perRow = Math.max(1, Math.min(n, Math.floor((areaW + CARD_GAP) / (CARD_W_MAX + CARD_GAP))));
      var rows = Math.max(1, Math.ceil(n / perRow));
      var h = Math.max(140, rows * CARD_H + (rows - 1) * CARD_GAP + 24);
      bandRects.push({ top: top, h: h, items: L.items, key: L.key });
      top += h + BAND_GAP;
    });
    var H = top + 36;

    var svg =
      '<svg class="topo-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" ' +
      'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="系统拓扑图（简略）">' +
      "<defs>" +
      '<linearGradient id="topoLink" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + RED + '" stop-opacity="0.2"/>' +
      '<stop offset="0.5" stop-color="' + RED + '" stop-opacity="0.9"/>' +
      '<stop offset="1" stop-color="' + RED2 + '" stop-opacity="0.2"/></linearGradient>' +
      '<filter id="topoGlow" x="-30%" y="-30%" width="160%" height="160%">' +
      '<feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
      "<style>" +
      ".topo-card{transition:transform .18s ease, filter .18s ease;}" +
      ".topo-card:hover{transform:translateY(-3px);}" +
      ".topo-card:hover .tc-box{stroke-width:2.4;filter:url(#topoGlow);}" +
      ".tc-title{font:600 13px/1.2 'Microsoft YaHei','PingFang SC',sans-serif;fill:#1a1d24;}" +
      ".tc-model{font:600 12px/1.2 Consolas,'JetBrains Mono',monospace;fill:#4a5160;}" +
      ".tc-sub{font:400 10.5px/1.2 'Microsoft YaHei',sans-serif;fill:#6b7280;}" +
      ".tc-intent{font:700 10.5px/1.2 'Microsoft YaHei',sans-serif;}" +
      ".tc-loc{font:700 11px/1.2 'Microsoft YaHei',sans-serif;}" +
      ".tc-qty{font:700 13px/1 Consolas,monospace;fill:#ffffff;}" +
      ".topo-head{font:800 15px/1 'Microsoft YaHei',sans-serif;fill:#ffffff;letter-spacing:.08em;}" +
      ".topo-head-sub{font:500 11px/1 Consolas,monospace;fill:rgba(255,255,255,0.82);}" +
      ".topo-flow{stroke-dasharray:8 12;animation:topoDash 1.1s linear infinite;}" +
      "@keyframes topoDash{to{stroke-dashoffset:-20;}}" +
      "</style></defs>";

    svg += '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#ffffff"/>';
    svg += '<text x="' + (W / 2) + '" y="44" text-anchor="middle" font="800 32px sans-serif" fill="#1a1d24">系统拓扑图（简略）</text>';
    var sub = (spec.project ? spec.project.name : "方案") + " · 前端 → 存储 → 平台 → 显示";
    svg += '<text x="' + (W / 2) + '" y="78" text-anchor="middle" font="500 16px sans-serif" fill="#6b7280">' + esc(sub) + "</text>";
    svg += '<line x1="' + MARGIN_X + '" y1="112" x2="' + (W - MARGIN_X) + '" y2="112" stroke="' + RED + '" stroke-width="3"/>';

    var centerX = areaX + areaW / 2;
    bandRects.forEach(function (B, idx) {
      svg += '<rect x="' + MARGIN_X + '" y="' + B.top + '" width="' + (W - 2 * MARGIN_X) + '" height="' + B.h +
        '" rx="14" fill="' + PANEL + '" stroke="' + PANEL_LINE + '" stroke-width="1"/>';
      svg += '<rect x="' + MARGIN_X + '" y="' + B.top + '" width="' + LABEL_W + '" height="' + B.h +
        '" rx="14" fill="' + RED + '" stroke="' + "#a30a14" + '" stroke-width="2"/>';
      svg += '<rect x="' + MARGIN_X + '" y="' + B.top + '" width="5" height="' + B.h + '" rx="2" fill="' + RED + '"/>';
      svg += '<text class="topo-head" x="' + (MARGIN_X + LABEL_W / 2) + '" y="' + (B.top + B.h / 2 - 6) + '" text-anchor="middle">' + esc(B.key) + " 层</text>";
      svg += '<text class="topo-head-sub" x="' + (MARGIN_X + LABEL_W / 2) + '" y="' + (B.top + B.h / 2 + 16) + '" text-anchor="middle">' + B.items.length + " 类设备</text>";

      var n = B.items.length;
      var perRow = Math.max(1, Math.min(n, Math.floor((areaW + CARD_GAP) / (CARD_W_MAX + CARD_GAP))));
      var rows = Math.max(1, Math.ceil(n / perRow));
      var cardW = Math.min(CARD_W_MAX, (areaW - (perRow - 1) * CARD_GAP) / perRow);
      var totalH = rows * CARD_H + (rows - 1) * CARD_GAP;
      var startY = B.top + (B.h - totalH) / 2;
      var idx2 = 0;
      for (var r = 0; r < rows; r++) {
        var rowN = Math.min(perRow, B.items.length - idx2);
        var rowW = rowN * cardW + (rowN - 1) * CARD_GAP;
        var startX = areaX + (areaW - rowW) / 2;
        var ry = startY + r * (CARD_H + CARD_GAP) + CARD_H / 2;
        for (var c = 0; c < rowN; c++) {
          var it = B.items[idx2++];
          var cardX = startX + c * (cardW + CARD_GAP);
          var cy = ry;
          var icon = iconType(it.category);
          var col = it.dashed ? COLORS.box : colorOf(it.category);
          var boxFill = it.dashed ? "#f2f3f5" : "#ffffff";
          var dashAttr = it.dashed ? ' stroke-dasharray="6 5"' : "";
          svg += '<g class="topo-card">';
          svg += '<title>' + esc(it.category) + (it.dashed ? "（待选型）" : "　地点 " + esc(it.scene) + "　型号 " + esc(it.model) + "　×" + it.qty) + "</title>";
          svg += '<rect class="tc-box" x="' + cardX + '" y="' + (cy - CARD_H / 2) + '" width="' + cardW + '" height="' + CARD_H +
            '" rx="12" fill="' + boxFill + '" stroke="' + col + '" stroke-width="1.6"' + dashAttr + "/>";
          svg += '<rect x="' + cardX + '" y="' + (cy - CARD_H / 2) + '" width="4" height="' + CARD_H + '" rx="2" fill="' + col + '"/>';
          svg += iconSVG(icon, cardX + 38, cy, 26, col);
          var tx = cardX + 72;
          var right = cardX + cardW - 14;
          var textW = right - tx;
          // 第一行：类别 + 数量胶囊（胶囊占右侧固定宽度，标题据此让位）
          var qtyW = it.qty > 0 && !it.dashed ? 48 : 0;
          svg += '<text class="tc-title" x="' + tx + '" y="' + (cy - 40) + '">' +
            esc(fit(it.category, textW - qtyW - 6, 13)) + "</text>";
          if (qtyW) {
            var qx = right - qtyW, qy = cy - 53;
            svg += '<rect x="' + qx + '" y="' + qy + '" width="' + qtyW + '" height="22" rx="11" fill="' + col + '"/>';
            svg += '<text class="tc-qty" x="' + (qx + qtyW / 2) + '" y="' + (qy + 15.5) + '" text-anchor="middle">×' + it.qty + "</text>";
          }
          if (!it.dashed) {
            svg += '<text class="tc-model" x="' + tx + '" y="' + (cy - 18) + '">' +
              esc(fit(it.model, textW, 12)) + "</text>";
            svg += '<text class="tc-sub" x="' + tx + '" y="' + (cy + 2) + '">' +
              esc(fit(it.display_name, textW, 10.5)) + "</text>";
            // 底部信息条：左侧场景胶囊，右侧目标；双方按剩余宽度各自截断
            var hasScene = !!it.scene, hasIntent = !!it.intent;
            if (hasScene || hasIntent) {
              var sceneTxt = hasScene ? fit(it.scene, textW * 0.52 - 16, 10.5) : "";
              var sceneW = sceneTxt ? measure(sceneTxt, 10.5) + 18 : 0;
              var intentTxt = "";
              if (hasIntent) {
                var rest = textW - sceneW - (sceneW ? 8 : 0);
                intentTxt = fit("目标 · " + it.intent, rest, 10.5);
              }
              if (sceneW) {
                svg += '<rect x="' + tx + '" y="' + (cy + 20) + '" width="' + sceneW + '" height="20" rx="10" fill="#fdecee"/>';
                svg += '<text class="tc-loc" x="' + (tx + 9) + '" y="' + (cy + 34) + '" fill="#a30a14">' + esc(sceneTxt) + "</text>";
              }
              if (intentTxt) {
                svg += '<text class="tc-loc" x="' + right + '" y="' + (cy + 34) + '" text-anchor="end" fill="#a30a14">' + esc(intentTxt) + "</text>";
              }
            }
          } else {
            svg += '<text class="tc-sub" x="' + tx + '" y="' + (cy + 2) + '">待选型</text>';
          }
          svg += "</g>";
        }
      }

      if (idx < bandRects.length - 1) {
        var y0 = B.top + B.h + 4, y1 = bandRects[idx + 1].top - 4;
        svg += '<line x1="' + centerX + '" y1="' + y0 + '" x2="' + centerX + '" y2="' + y1 + '" stroke="url(#topoLink)" stroke-width="3"/>';
        svg += '<line class="topo-flow" x1="' + centerX + '" y1="' + y0 + '" x2="' + centerX + '" y2="' + y1 + '" stroke="' + RED2 + '" stroke-width="2"/>';
        var ah = 8;
        svg += '<polygon points="' + centerX + "," + y1 + " " + (centerX - ah * 0.6) + "," + (y1 - ah) + " " + (centerX + ah * 0.6) + "," + (y1 - ah) + '" fill="' + RED + '"/>';
      }
    });

    svg += "</svg>";
    container.innerHTML = svg;
  }

  // =======================================================================
  // 超详细逻辑连线版
  // =======================================================================
  function renderDetailed(container, spec) {
    if (!container) return;
    spec = spec || {};
    var im = intentMap(spec);
    var cameras = (spec.devices || []).filter(function (d) { return d.category === "网络摄像机"; });
    var scenes = {};
    cameras.forEach(function (d) { (scenes[d.scene || "未分类"] = scenes[d.scene || "未分类"] || []).push(d); });
    var sceneItems = Object.keys(scenes).map(function (sc) {
      var ds = scenes[sc];
      return { scene: sc, intent: im[sc] || "", model: ds[0].model, dn: ds[0].display_name || "", qty: ds.reduce(function (a, x) { return a + (x.qty || 0); }, 0) };
    });

    var switches = (spec.devices || []).filter(function (d) { return d.category === "PoE交换机"; });
    var nvrs = (spec.devices || []).filter(function (d) { return d.category === "NVR" || d.category.indexOf("录像机") >= 0; });
    var disks = (spec.devices || []).filter(function (d) { return d.category === "机械硬盘"; });
    var platforms = (spec.devices || []).filter(function (d) { return d.category.indexOf("平台") >= 0; });
    var monitors = (spec.devices || []).filter(function (d) { return d.category === "监视器"; });

    var M = 56, SCENE_W = 320, SCENE_H = 96, SCENE_GAP = 16;
    var SPINE_W = 250, SPINE_H = 96, SPINE_GAP = 22, HDD_W = 200;
    var n_sc = Math.max(1, sceneItems.length);
    var scenesBandH = n_sc * SCENE_H + (n_sc - 1) * SCENE_GAP;

    var spineNodes = [];
    if (switches.length) spineNodes.push({ t: "接入交换机", model: switches[0].model, sub: switches[0].display_name || "PoE 接入", qty: switches.reduce(function (a, x) { return a + (x.qty || 0); }, 0), color: COLORS.switch });
    if (nvrs.length) spineNodes.push({ t: "NVR 录像机", model: nvrs[0].model, sub: "存储与录像", qty: nvrs.reduce(function (a, x) { return a + (x.qty || 0); }, 0), color: COLORS.recorder });
    if (platforms.length) spineNodes.push({ t: "综合管理平台", model: platforms[0].model, sub: "统一管理与上墙", qty: platforms.reduce(function (a, x) { return a + (x.qty || 0); }, 0), color: COLORS.platform });
    if (monitors.length) spineNodes.push({ t: "监视器", model: monitors[0].model, sub: "监控中心显示", qty: monitors.reduce(function (a, x) { return a + (x.qty || 0); }, 0), color: COLORS.monitor });
    var n_sp = Math.max(1, spineNodes.length);
    var spineBandH = n_sp * SPINE_H + (n_sp - 1) * SPINE_GAP;

    var SCENE_X = M;
    var SPINE_X = SCENE_X + SCENE_W + 130;
    var HDD_X = SPINE_X + SPINE_W + 60;
    var bandH = Math.max(scenesBandH, spineBandH);
    var TOP = 150;
    var W = HDD_X + HDD_W + M;
    var H = TOP + bandH + 210;

    var sceneTop = TOP + (bandH - scenesBandH) / 2;
    var spineTop = TOP + (bandH - spineBandH) / 2;

    var svg =
      '<svg class="topo-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" ' +
      'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="系统拓扑图（详细）">' +
      "<defs><style>" +
      ".dt-title{font:800 15px/1 'Microsoft YaHei',sans-serif;}" +
      ".dt-model{font:600 12px/1.2 Consolas,monospace;}" +
      ".dt-sub{font:400 11px/1.2 'Microsoft YaHei',sans-serif;}" +
      ".dt-qty{font:700 14px/1 Consolas,monospace;}" +
      ".dt-legend{font:500 12px/1.4 'Microsoft YaHei',sans-serif;fill:#6b7280;}" +
      ".dt-cap{font:700 12px/1 'Microsoft YaHei',sans-serif;fill:#a30a14;}" +
      ".dt-flow{stroke-dasharray:7 9;animation:dtDash 1.1s linear infinite;}" +
      "@keyframes dtDash{to{stroke-dashoffset:-16;}}" +
      "</style></defs>";

    svg += '<text x="' + (W / 2) + '" y="40" text-anchor="middle" font="800 30px sans-serif" fill="#1a1d24">系统拓扑图（详细 · 逻辑连线）</text>';
    var pname = (spec.project ? spec.project.name : "方案") + " · " + (spec.project ? spec.project.industry : "");
    svg += '<text x="' + (W / 2) + '" y="74" text-anchor="middle" font="500 15px sans-serif" fill="#8a94a6">' + esc(pname) + " · 全配单互连示意</text>";
    svg += '<line x1="' + M + '" y1="110" x2="' + (W - M) + '" y2="110" stroke="' + RED + '" stroke-width="3"/>';

    // 场景节点
    var sy = sceneTop, sceneCenters = [];
    sceneItems.forEach(function (it) {
      var box = { x: SCENE_X, y: sy, w: SCENE_W, h: SCENE_H };
      svg += detailedNode(svg, box, it.scene, it.model, (it.intent ? it.intent + " · " : "") + it.dn, it.qty, COLORS.camera);
      sceneCenters.push({ x: box.x + box.w, y: box.y + box.h / 2 });
      sy += SCENE_H + SCENE_GAP;
    });

    // spine 节点
    var spy = spineTop, spineBoxes = [];
    spineNodes.forEach(function (nd) {
      var box = { x: SPINE_X, y: spy, w: SPINE_W, h: SPINE_H };
      svg += detailedNode(svg, box, nd.t, nd.model, nd.sub, nd.qty, nd.color);
      spineBoxes.push(box);
      spy += SPINE_H + SPINE_GAP;
    });

    // 连线：场景 → 交换机（扇形）
    if (spineBoxes.length) {
      var swb = spineBoxes[0];
      sceneCenters.forEach(function (c, i) {
        var ty = swb.y + (i + 1) / (sceneCenters.length + 1) * SPINE_H;
        svg += arrow(c.x, c.y, swb.x, ty);
      });
      svg += '<text class="dt-cap" x="' + (swb.x - 12) + '" y="' + (swb.y - 10) + '" text-anchor="end">网线/PoE 接入</text>';
    }
    // spine 内部顺序连线
    var spineLabels = ["视频流汇聚", "统一管理", "上墙显示"];
    for (var i = 0; i < spineBoxes.length - 1; i++) {
      var b0 = spineBoxes[i], b1 = spineBoxes[i + 1];
      svg += arrow((b0.x + b0.w) / 2, b0.y + b0.h, (b1.x + b1.w) / 2, b1.y, spineLabels[i]);
    }
    // NVR → 硬盘
    if (nvrs.length && disks.length && spineBoxes.length >= 2) {
      var nvrB = spineBoxes[1] || spineBoxes[0];
      var hddQty = disks.reduce(function (a, x) { return a + (x.qty || 0); }, 0);
      var hddBox = { x: HDD_X, y: nvrB.y, w: HDD_W, h: SPINE_H };
      svg += detailedNode(svg, hddBox, "监控硬盘", disks[0].model, "录像存储", hddQty, COLORS.disk);
      svg += arrow(nvrB.x + nvrB.w, nvrB.y + nvrB.h / 2, hddBox.x, hddBox.y + hddBox.h / 2, "录像存储");
    }

    // 图例
    var ly = TOP + bandH + 40;
    svg += '<text class="dt-title" x="' + M + '" y="' + ly + '" fill="#1a1d24">连线含义</text>';
    var legend = [
      "摄像机（按使用地点分组）→ 接入交换机（网线 / PoE）",
      "交换机 → NVR（视频流汇聚与录像）→ 综合平台（统一管理）→ 监视器（上墙显示）",
      "NVR → 监控硬盘（录像存储，按冗余系数折算块数）",
      "注：本图为逻辑连接示意；实际工程按点位就近接入交换机，并按区域 / 机柜位置部署。型号与数量来自选型结果。",
    ];
    var yy = ly + 30;
    legend.forEach(function (line) {
      svg += '<text class="dt-legend" x="' + (M + 10) + '" y="' + yy + '">• ' + esc(line) + "</text>";
      yy += 28;
    });

    svg += "</svg>";
    container.innerHTML = svg;

    function detailedNode(svg, box, title, model, sub, qty, color) {
      var s = "";
      s += '<g>';
      s += '<title>' + esc(title) + "　型号 " + esc(model) + "　×" + qty + "</title>";
      s += '<rect x="' + box.x + '" y="' + box.y + '" width="' + box.w + '" height="' + box.h + '" rx="14" fill="#ffffff" stroke="' + color + '" stroke-width="2"/>';
      s += '<rect x="' + box.x + '" y="' + box.y + '" width="6" height="' + box.h + '" rx="3" fill="' + color + '"/>';
      s += iconSVG(iconType(title.indexOf("摄像机") >= 0 ? "camera" : (title.indexOf("交换机") >= 0 ? "switch" : (title.indexOf("NVR") >= 0 ? "recorder" : (title.indexOf("硬盘") >= 0 ? "disk" : (title.indexOf("平台") >= 0 ? "platform" : (title.indexOf("监视器") >= 0 ? "monitor" : "box")))))), box.x + 40, box.y + box.h / 2, 24, color);
      var titleW = box.w - 74 - (qty > 0 ? 62 : 16);
      var bodyW = box.w - 74 - 18;
      s += '<text class="dt-title" x="' + (box.x + 74) + '" y="' + (box.y + 26) + '" fill="' + color + '">' + esc(fit(title, titleW, 15)) + "</text>";
      s += '<text class="dt-model" x="' + (box.x + 74) + '" y="' + (box.y + 52) + '">' + esc(fit(model, bodyW, 12)) + "</text>";
      s += '<text class="dt-sub" x="' + (box.x + 74) + '" y="' + (box.y + 74) + '">' + esc(fit(sub, bodyW, 11)) + "</text>";
      if (qty > 0) {
        s += '<circle cx="' + (box.x + box.w - 26) + '" cy="' + (box.y + 24) + '" r="16" fill="' + color + '"/>';
        s += '<text class="dt-qty" x="' + (box.x + box.w - 26) + '" y="' + (box.y + 29) + '" text-anchor="middle" fill="#fff">×' + qty + "</text>";
      }
      s += "</g>";
      return s;
    }
    function arrow(x0, y0, x1, y1, label) {
      var ang = Math.atan2(y1 - y0, x1 - x0), head = 12;
      var ax = x1 - head * Math.cos(ang), ay = y1 - head * Math.sin(ang);
      var s = '<line x1="' + x0 + '" y1="' + y0 + '" x2="' + x1 + '" y2="' + y1 + '" stroke="' + RED + '" stroke-width="3"/>';
      s += '<line class="dt-flow" x1="' + x0 + '" y1="' + y0 + '" x2="' + ax + '" y2="' + ay + '" stroke="' + RED2 + '" stroke-width="1.6"/>';
      s += '<polygon points="' + x1 + "," + y1 + " " + (x1 - 11 * Math.cos(ang - 0.5)) + "," + (y1 - 11 * Math.sin(ang - 0.5)) + " " + (x1 - 11 * Math.cos(ang + 0.5)) + "," + (y1 - 11 * Math.sin(ang + 0.5)) + '" fill="' + RED + '"/>';
      if (label) {
        var mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
        s += '<rect x="' + (mx - 44) + '" y="' + (my - 11) + '" width="88" height="20" rx="6" fill="#ffffff"/>';
        s += '<text x="' + mx + '" y="' + (my + 4) + '" text-anchor="middle" font="700 11px sans-serif" fill="#a30a14">' + esc(label) + "</text>";
      }
      return s;
    }
  }

  function render(container, spec, mode) {
    if (mode === "detailed") return renderDetailed(container, spec);
    return renderSimple(container, spec);
  }

  function exportPNG(container, filename) {
    var svg = container.querySelector("svg");
    if (!svg) return;
    var xml = new XMLSerializer().serializeToString(svg);
    var url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
    var img = new Image();
    img.onload = function () {
      var vb = (svg.getAttribute("viewBox") || "0 0 1000 800").split(" ").map(Number);
      var scale = 2;
      var canvas = document.createElement("canvas");
      canvas.width = vb[2] * scale;
      canvas.height = vb[3] * scale;
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0,0);
      canvas.toBlob(function (blob) {
        if (!blob) return;
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename || "拓扑图.png";
        a.click();
      });
    };
    img.src = url;
  }

  window.DahuaTopology = { render: render, exportPNG: exportPNG };
})();
