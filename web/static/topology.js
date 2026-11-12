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

  // 设备线稿图标：枪机 / 半球 / 交换机 / NVR / 硬盘 / 服务器 / 监视器
  // 全部为纯 SVG 矢量，导出 PNG 时同样有效，不依赖任何外链图片。
  function iconSVG(type, cx, cy, s, color) {
    var g = '<g transform="translate(' + cx + "," + cy + ')" stroke="' + color +
      '" stroke-linecap="round" stroke-linejoin="round" fill="none">';
    if (type === "camera") {
      // 枪机：壁装支架 + 机身 + 遮阳罩 + 镜头
      g += '<path d="M' + (-s * 0.34) + ',' + (s * 0.56) + ' L' + (-s * 0.34) + ',' + (s * 0.18) +
        '" stroke-width="' + (s * 0.1) + '"/>';
      g += '<path d="M' + (-s * 0.54) + ',' + (s * 0.56) + ' L' + (-s * 0.14) + ',' + (s * 0.56) +
        '" stroke-width="' + (s * 0.1) + '"/>';
      g += '<rect x="' + (-s * 0.46) + '" y="' + (-s * 0.24) + '" width="' + (s * 0.94) + '" height="' + (s * 0.42) +
        '" rx="' + (s * 0.09) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<path d="M' + (-s * 0.52) + ',' + (-s * 0.22) + ' L' + (s * 0.5) + ',' + (-s * 0.22) +
        '" stroke-width="' + (s * 0.13) + '"/>';
      g += '<circle cx="' + (s * 0.33) + '" cy="' + (-s * 0.03) + '" r="' + (s * 0.15) +
        '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<circle cx="' + (s * 0.33) + '" cy="' + (-s * 0.03) + '" r="' + (s * 0.06) +
        '" fill="' + color + '" stroke="none"/>';
      g += '<circle cx="' + (-s * 0.24) + '" cy="' + (-s * 0.06) + '" r="' + (s * 0.045) +
        '" fill="' + color + '" stroke="none"/>';
    } else if (type === "dome") {
      // 半球：球罩 + 底座 + 镜头
      g += '<path d="M' + (-s * 0.5) + ',' + (s * 0.24) + ' Q0,' + (-s * 0.56) + ' ' + (s * 0.5) + ',' + (s * 0.24) +
        '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<rect x="' + (-s * 0.58) + '" y="' + (s * 0.24) + '" width="' + (s * 1.16) + '" height="' + (s * 0.22) +
        '" rx="' + (s * 0.06) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<circle cx="0" cy="' + (s * 0.04) + '" r="' + (s * 0.16) +
        '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<circle cx="0" cy="' + (s * 0.04) + '" r="' + (s * 0.065) + '" fill="' + color + '" stroke="none"/>';
    } else if (type === "switch") {
      // 交换机：机身 + 6 个网口 + 指示灯 + 上联口
      g += '<rect x="' + (-s * 0.58) + '" y="' + (-s * 0.23) + '" width="' + (s * 1.16) + '" height="' + (s * 0.46) +
        '" rx="' + (s * 0.07) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      for (var i = 0; i < 6; i++) {
        g += '<rect x="' + (-s * 0.48 + i * s * 0.16) + '" y="' + (s * 0.01) + '" width="' + (s * 0.11) +
          '" height="' + (s * 0.14) + '" rx="' + (s * 0.02) + '" fill="' + color +
          '" opacity="' + (i < 2 ? 0.4 : 1) + '" stroke="none"/>';
      }
      g += '<circle cx="' + (-s * 0.42) + '" cy="' + (-s * 0.1) + '" r="' + (s * 0.045) +
        '" fill="' + color + '" stroke="none"/>';
      g += '<circle cx="' + (-s * 0.27) + '" cy="' + (-s * 0.1) + '" r="' + (s * 0.045) +
        '" fill="' + color + '" opacity="0.45" stroke="none"/>';
      g += '<rect x="' + (s * 0.3) + '" y="' + (-s * 0.13) + '" width="' + (s * 0.18) + '" height="' + (s * 0.1) +
        '" rx="' + (s * 0.02) + '" stroke-width="' + (s * 0.05) + '"/>';
    } else if (type === "recorder") {
      // NVR：机身 + 硬盘仓 + 指示灯
      g += '<rect x="' + (-s * 0.58) + '" y="' + (-s * 0.3) + '" width="' + (s * 1.16) + '" height="' + (s * 0.6) +
        '" rx="' + (s * 0.07) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      for (var j = 0; j < 3; j++) {
        g += '<rect x="' + (-s * 0.46) + '" y="' + (-s * 0.19 + j * s * 0.14) + '" width="' + (s * 0.5) +
          '" height="' + (s * 0.08) + '" rx="' + (s * 0.02) + '" fill="' + color +
          '" opacity="0.35" stroke="none"/>';
      }
      g += '<circle cx="' + (s * 0.34) + '" cy="' + (-s * 0.12) + '" r="' + (s * 0.06) +
        '" fill="' + color + '" stroke="none"/>';
      g += '<circle cx="' + (s * 0.34) + '" cy="' + (s * 0.04) + '" r="' + (s * 0.06) +
        '" stroke-width="' + (s * 0.05) + '"/>';
    } else if (type === "disk") {
      // 硬盘：三层盘位
      for (var k = 0; k < 3; k++) {
        var yy = -s * 0.42 + k * s * 0.28;
        g += '<rect x="' + (-s * 0.5) + '" y="' + yy + '" width="' + (s * 1.0) + '" height="' + (s * 0.2) +
          '" rx="' + (s * 0.04) + '" fill="#fff" stroke-width="' + (s * 0.06) + '"/>';
        g += '<circle cx="' + (s * 0.36) + '" cy="' + (yy + s * 0.1) + '" r="' + (s * 0.035) +
          '" fill="' + color + '" stroke="none"/>';
      }
    } else if (type === "platform") {
      // 平台/服务器：机架 + 4 个 U 位
      g += '<rect x="' + (-s * 0.34) + '" y="' + (-s * 0.52) + '" width="' + (s * 0.68) + '" height="' + (s * 1.04) +
        '" rx="' + (s * 0.07) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      for (var m = 0; m < 4; m++) {
        g += '<rect x="' + (-s * 0.24) + '" y="' + (-s * 0.42 + m * s * 0.24) + '" width="' + (s * 0.48) +
          '" height="' + (s * 0.12) + '" rx="' + (s * 0.02) + '" fill="' + color +
          '" opacity="' + (0.25 + m * 0.2) + '" stroke="none"/>';
      }
    } else if (type === "monitor") {
      // 监视器：屏幕 + 底座
      g += '<rect x="' + (-s * 0.58) + '" y="' + (-s * 0.46) + '" width="' + (s * 1.16) + '" height="' + (s * 0.7) +
        '" rx="' + (s * 0.06) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<rect x="' + (-s * 0.46) + '" y="' + (-s * 0.34) + '" width="' + (s * 0.92) + '" height="' + (s * 0.46) +
        '" rx="' + (s * 0.03) + '" fill="' + color + '" opacity="0.16" stroke="none"/>';
      g += '<path d="M' + (-s * 0.16) + ',' + (s * 0.24) + ' L' + (-s * 0.16) + ',' + (s * 0.4) + ' L' +
        (s * 0.16) + ',' + (s * 0.4) + ' L' + (s * 0.16) + ',' + (s * 0.24) + '" stroke-width="' + (s * 0.07) + '"/>';
      g += '<line x1="' + (-s * 0.3) + '" y1="' + (s * 0.46) + '" x2="' + (s * 0.3) + '" y2="' + (s * 0.46) +
        '" stroke-width="' + (s * 0.07) + '"/>';
    } else {
      // 解码器 / 其它网络设备
      g += '<rect x="' + (-s * 0.5) + '" y="' + (-s * 0.26) + '" width="' + (s * 1.0) + '" height="' + (s * 0.52) +
        '" rx="' + (s * 0.06) + '" fill="#fff" stroke-width="' + (s * 0.07) + '"/>';
      g += '<path d="M' + (-s * 0.18) + ',' + (-s * 0.11) + ' L' + (s * 0.16) + ',0 L' + (-s * 0.18) + ',' +
        (s * 0.11) + ' Z" fill="' + color + '" opacity="0.7" stroke="none"/>';
      g += '<circle cx="' + (s * 0.34) + '" cy="0" r="' + (s * 0.05) + '" fill="' + color + '" stroke="none"/>';
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
      'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="系统拓扑图（建议）">' +
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
    svg += '<text x="' + (W / 2) + '" y="44" text-anchor="middle" font-size="26" font-weight="800" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#1a1d24">系统拓扑图（建议方案）</text>';
    var sub = (spec.project ? spec.project.name : "方案") + " · 前端 → 存储 → 平台 → 显示";
    svg += '<text x="' + (W / 2) + '" y="74" text-anchor="middle" font-size="12" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#6b7280">' + esc(sub) + "</text>";
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
  // 工程拓扑：三列分区（① 前端采集 / ② 传输与存储 / ③ 平台与显示）
  //   · 平台按「软件 N 套 + 通道授权 N 路 + 服务器 N 台」分别标注，
  //     不把 170 路通道授权折算成 170 个平台
  //   · 专业设备线稿图标 + 圆角正交连线
  // =======================================================================
  var ENG = {
    W: 1256,
    TOP: 128,
    HEAD: 48,
    PAD: 14,
    LINK: "#93A1B5",
    COLS: [
      { x: 36, w: 356, color: "#0B6FB8", no: "1", title: "前端采集" },
      { x: 438, w: 336, color: "#0F8A6A", no: "2", title: "传输与存储" },
      { x: 820, w: 400, color: "#C8102E", no: "3", title: "平台与显示" }
    ]
  };

  function renderDetailed(container, spec) {
    if (!container) return;
    spec = spec || {};
    var devices = spec.devices || [];
    var im = intentMap(spec);
    var W = ENG.W, TOP = ENG.TOP, PAD = ENG.PAD, M = ENG.COLS[0].x;
    var contentTop = TOP + ENG.HEAD + 10;
    var CARD_H = 84, LGAP = 10, MGAP = 20, RGAP = 34;

    function pick(cats) {
      return devices.filter(function (d) { return cats.indexOf(d.category) >= 0; });
    }
    function sumQty(list) {
      return list.reduce(function (a, x) { return a + (x.qty || 0); }, 0);
    }
    function modelOf(list) { return list.length ? (list[0].model || "") : ""; }
    function nameOf(list) { return list.length ? (list[0].display_name || "") : ""; }

    // ---------- 选型结果分组 ----------
    var camList = pick(["网络摄像机"]);
    var swList = pick(["PoE交换机", "交换机"]);
    var nvrList = pick(["NVR", "通用存储录像机"]);
    var diskList = pick(["机械硬盘"]);
    var softList = pick(["综合平台", "综合管理平台"]);
    var licList = pick(["平台授权"]);
    var srvList = pick(["平台服务器"]);
    var scrList = pick(["监视器", "拼接屏"]);
    var accList = pick(["拼接支架"]);
    var decList = pick(["解码器"]);
    var lanes = sumQty(camList);

    // 摄像机按使用地点分组
    var sceneMap = {}, sceneKeys = [];
    camList.forEach(function (d) {
      var k = d.scene || "未分类";
      if (!sceneMap[k]) { sceneMap[k] = []; sceneKeys.push(k); }
      sceneMap[k].push(d);
    });

    // ---------- ① 前端采集 ----------
    var c1 = ENG.COLS[0];
    var lx = c1.x + PAD, lw = c1.w - PAD * 2, ly = contentTop;
    var leftCards = [];
    sceneKeys.forEach(function (k) {
      var ds = sceneMap[k];
      leftCards.push({
        box: { x: lx, y: ly, w: lw, h: CARD_H },
        icon: /HDBW|DOME|半球/i.test(ds[0].model || "") ? "dome" : "camera",
        color: COLORS.camera,
        title: k,
        model: ds[0].model,
        sub: ds[0].display_name || "网络摄像机",
        qty: sumQty(ds),
        chip: im[k] ? "目标 " + im[k] : ""
      });
      ly += CARD_H + LGAP;
    });
    if (!leftCards.length) {
      leftCards.push({
        box: { x: lx, y: contentTop, w: lw, h: CARD_H },
        icon: "camera", color: COLORS.box, title: "前端摄像机",
        model: "待选型", sub: "", qty: 0, dashed: true
      });
    }
    var leftH = leftCards[leftCards.length - 1].box.y + CARD_H - contentTop;

    // ---------- ② 传输与存储 ----------
    var c2 = ENG.COLS[1];
    var mx = c2.x + PAD, mw = c2.w - PAD * 2, my = contentTop;
    var midCards = [];
    function pushMid(icon, color, title, model, sub, q) {
      var card = {
        box: { x: mx, y: my, w: mw, h: CARD_H },
        icon: icon, color: color, title: title, model: model, sub: sub, qty: q
      };
      midCards.push(card);
      my += CARD_H + MGAP;
      return card;
    }
    var swCard = swList.length
      ? pushMid("switch", COLORS.switch, "PoE 接入交换机", modelOf(swList), nameOf(swList) || "前端就近接入与供电", sumQty(swList))
      : null;
    var nvrCard = nvrList.length
      ? pushMid("recorder", COLORS.recorder, "NVR 录像机", modelOf(nvrList), "视频流汇聚与集中录像", sumQty(nvrList))
      : null;
    var diskCard = diskList.length
      ? pushMid("disk", COLORS.disk, "监控硬盘", modelOf(diskList), "按冗余系数折算盘位", sumQty(diskList))
      : null;
    if (!midCards.length) pushMid("switch", COLORS.box, "传输设备", "待选型", "", 0);
    var midH = midCards[midCards.length - 1].box.y + CARD_H - contentTop;

    // ---------- ③ 平台与显示 ----------
    var c3 = ENG.COLS[2];
    var rx = c3.x + PAD, rw = c3.w - PAD * 2, ry = contentTop;
    var srvCard = null, platCard = null, dispCard = null;
    if (srvList.length) {
      srvCard = {
        box: { x: rx, y: ry, w: rw, h: CARD_H }, icon: "platform", color: "#3B4A63",
        title: "平台服务器", model: modelOf(srvList), sub: "承载 ICC 平台运行", qty: sumQty(srvList)
      };
      ry += CARD_H + RGAP;
    }
    if (softList.length) {
      var chips = [{ text: "软件 " + sumQty(softList) + " 套", tone: "red" }];
      if (licList.length) chips.push({ text: "通道授权 " + sumQty(licList) + " 路", tone: "amber" });
      platCard = {
        box: { x: rx, y: ry, w: rw, h: 132 }, icon: "platform", color: COLORS.platform,
        title: "综合管理平台", model: modelOf(softList), sub: nameOf(softList),
        chips: chips, note: "统一管理 / 电子地图 / 解码上墙", qty: 0
      };
      ry += 132 + RGAP;
    }
    if (scrList.length) {
      var extras = [];
      if (accList.length) extras.push("支架 " + sumQty(accList) + " 套");
      if (decList.length) extras.push("解码器 " + sumQty(decList) + " 台");
      var isWall = scrList[0].category === "拼接屏";
      dispCard = {
        box: { x: rx, y: ry, w: rw, h: CARD_H }, icon: "monitor", color: COLORS.monitor,
        title: isWall ? "拼接电视墙" : "监控监视器", model: modelOf(scrList),
        sub: (isWall ? sumQty(scrList) + " 块拼接" : "监控中心显示") +
          (extras.length ? " · " + extras.join(" · ") : ""),
        qty: sumQty(scrList)
      };
    }
    var rightH = Math.max(
      srvCard ? srvCard.box.y + srvCard.box.h : 0,
      platCard ? platCard.box.y + platCard.box.h : 0,
      dispCard ? dispCard.box.y + dispCard.box.h : 0
    ) - contentTop;
    if (rightH < 0) rightH = 0;

    var panelContentH = Math.max(leftH, midH, rightH, 180);
    // 各列在分区内垂直居中，避免列高差异造成上重下空
    var offL = (panelContentH - leftH) / 2, offM = (panelContentH - midH) / 2;
    leftCards.forEach(function (b) { b.box.y += offL; });
    midCards.forEach(function (b) { b.box.y += offM; });
    var panelH = ENG.HEAD + 10 + panelContentH + 16;
    var panelBottom = TOP + panelH;
    var fy = panelBottom + 34;
    var H = fy + 62;

    // ---------- 绘制工具 ----------
    function elbow(x0, y0, x1, y1) {
      if (Math.abs(y1 - y0) < 1.5) return "M" + x0 + "," + y0 + " H" + x1;
      var r = Math.min(10, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2);
      var mid = (x0 + x1) / 2;
      var dir = y1 > y0 ? 1 : -1;
      return "M" + x0 + "," + y0 +
        " H" + (mid - r) +
        " Q" + mid + "," + y0 + " " + mid + "," + (y0 + dir * r) +
        " V" + (y1 - dir * r) +
        " Q" + mid + "," + y1 + " " + (mid + r) + "," + y1 +
        " H" + x1;
    }
    function tag(cx, cy, text) {
      var tw = measure(text, 9) + 14;
      return '<rect x="' + (cx - tw / 2) + '" y="' + (cy - 8) + '" width="' + tw + '" height="16" rx="8" fill="#FFFFFF" stroke="#E4E8EE" stroke-width="1"/>' +
        '<text x="' + cx + '" y="' + (cy + 3) + '" text-anchor="middle" font-size="9" font-weight="500" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#5B6878">' + esc(text) + "</text>";
    }
    // 竖线右侧贴标签，避免标签压住连线
    function tagNear(xLine, cy, text) {
      return tag(xLine + 7 + (measure(text, 9) + 14) / 2, cy, text);
    }
    function link(path, dashed) {
      return '<path d="' + path + '" fill="none" stroke="' + ENG.LINK + '" stroke-width="1.4"' +
        (dashed ? ' stroke-dasharray="4 4"' : "") + ' marker-end="url(#engArrow)"/>';
    }
    function card(b) {
      var col = b.color || COLORS.box;
      var x = b.box.x, y = b.box.y, w = b.box.w, h = b.box.h;
      var s = "<g>";
      s += "<title>" + esc(b.title) + (b.model ? "　" + esc(b.model) : "") + (b.qty ? "　×" + b.qty : "") + "</title>";
      s += '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="10" fill="#FFFFFF" stroke="' +
        (b.dashed ? "#D7DDE6" : "#E4E8EE") + '" stroke-width="1.1"' + (b.dashed ? ' stroke-dasharray="6 5"' : "") + "/>";
      s += '<rect x="' + x + '" y="' + (y + 9) + '" width="3.5" height="' + (h - 18) + '" rx="2" fill="' + col + '" opacity="' + (b.dashed ? 0.4 : 1) + '"/>';
      s += iconSVG(b.icon || "box", x + 30, y + (b.chips ? 38 : h / 2 + 2), 22, col);
      var tx = x + 57, bodyW = w - 57 - 12;
      var pillW = b.qty ? measure("×" + b.qty, 9.5) + 16 : 0;
      s += '<text x="' + tx + '" y="' + (y + 22) + '" font-size="12" font-weight="700" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="' + col + '">' +
        esc(fit(b.title, bodyW - (pillW ? pillW + 8 : 0), 12)) + "</text>";
      s += '<text x="' + tx + '" y="' + (y + 40) + '" font-size="9.5" font-weight="500" font-family="Consolas, JetBrains Mono, monospace" fill="#3E4655">' +
        esc(fit(b.model || "—", bodyW, 9.5)) + "</text>";
      s += '<text x="' + tx + '" y="' + (y + 55) + '" font-size="9" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#76808F">' +
        esc(fit(b.sub || "", bodyW, 9)) + "</text>";
      if (pillW) {
        var px = x + w - 10 - pillW;
        s += '<rect x="' + px + '" y="' + (y + 10) + '" width="' + pillW + '" height="18" rx="9" fill="' + col + '"/>';
        s += '<text x="' + (px + pillW / 2) + '" y="' + (y + 23) + '" text-anchor="middle" font-size="9.5" font-weight="700" font-family="Consolas, monospace" fill="#FFFFFF">×' + b.qty + "</text>";
      }
      if (b.chips && b.chips.length) {
        var cx0 = tx;
        b.chips.forEach(function (ch) {
          var tone = ch.tone === "amber"
            ? { bg: "#FFF7E6", bd: "#F0D29B", fg: "#8A5B00" }
            : { bg: "#FDECEE", bd: "#F3C7CC", fg: "#A30A14" };
          var cw = measure(ch.text, 9) + 18;
          s += '<rect x="' + cx0 + '" y="' + (y + 66) + '" width="' + cw + '" height="22" rx="11" fill="' + tone.bg + '" stroke="' + tone.bd + '"/>';
          s += '<text x="' + (cx0 + 9) + '" y="' + (y + 81) + '" font-size="9" font-weight="600" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="' + tone.fg + '">' + esc(ch.text) + "</text>";
          cx0 += cw + 8;
        });
        if (b.note) {
          s += '<text x="' + tx + '" y="' + (y + 104) + '" font-size="9" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#8A94A6">' +
            esc(fit(b.note, bodyW, 9)) + "</text>";
        }
      } else if (b.chip) {
        var chW = Math.min(measure(b.chip, 9) + 18, bodyW);
        s += '<rect x="' + tx + '" y="' + (y + 61) + '" width="' + chW + '" height="16" rx="8" fill="#FDECEE"/>';
        s += '<text x="' + (tx + 9) + '" y="' + (y + 72) + '" font-size="9" font-weight="600" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#A30A14">' +
          esc(b.chip) + "</text>";
      }
      return s + "</g>";
    }

    // ---------- 画布 ----------
    var svg = '<svg class="topo-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" ' +
      'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="系统拓扑图（工程）">';
    svg += "<defs>";
    svg += '<marker id="engArrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5" markerHeight="5" orient="auto">' +
      '<path d="M0,0 L10,5 L0,10 z" fill="' + ENG.LINK + '"/></marker>';
    svg += "</defs>";
    svg += '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#FFFFFF"/>';
    svg += '<text x="' + (W / 2) + '" y="42" text-anchor="middle" font-size="26" font-weight="800" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#1A1D24">系统拓扑图（工程建议）</text>';
    var pname = spec.project ? (spec.project.name || "方案") : "方案";
    var ind = spec.project ? (spec.project.industry || "") : "";
    var meta = [pname, ind, lanes ? lanes + " 路接入" : ""].filter(function (x) { return !!x; }).join(" · ");
    svg += '<text x="' + (W / 2) + '" y="68" text-anchor="middle" font-size="12" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#8A94A6">' + esc(meta) + "</text>";
    svg += '<line x1="' + M + '" y1="90" x2="' + (W - M) + '" y2="90" stroke="' + RED + '" stroke-width="2.4"/>';

    // 分区面板
    [[c1, lanes ? lanes + " 路 · " + sceneKeys.length + " 个区域" : "按使用地点分组"],
     [c2, "PoE 接入 · 集中录像"],
     [c3, "统一管理 · 解码上墙"]].forEach(function (pair) {
      var c = pair[0], subTxt = pair[1];
      svg += '<rect x="' + c.x + '" y="' + TOP + '" width="' + c.w + '" height="' + panelH + '" rx="12" fill="#F8F9FB" stroke="#E4E8EE" stroke-width="1.1"/>';
      svg += '<circle cx="' + (c.x + 24) + '" cy="' + (TOP + 24) + '" r="11" fill="' + c.color + '"/>';
      svg += '<text x="' + (c.x + 24) + '" y="' + (TOP + 28) + '" text-anchor="middle" font-size="11" font-weight="700" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#FFFFFF">' + c.no + "</text>";
      svg += '<text x="' + (c.x + 42) + '" y="' + (TOP + 28) + '" font-size="13" font-weight="700" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#1F2733">' + esc(c.title) + "</text>";
      svg += '<text x="' + (c.x + c.w - 14) + '" y="' + (TOP + 27) + '" text-anchor="end" font-size="9.5" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#8A94A6">' + esc(subTxt) + "</text>";
      svg += '<line x1="' + (c.x + 14) + '" y1="' + (TOP + 40) + '" x2="' + (c.x + c.w - 14) + '" y2="' + (TOP + 40) + '" stroke="#E4E8EE" stroke-width="1"/>';
    });

    // 设备卡片
    leftCards.forEach(function (b) { svg += card(b); });
    midCards.forEach(function (b) { svg += card(b); });
    [srvCard, platCard, dispCard].forEach(function (b) { if (b) svg += card(b); });

    // 连线
    var hub = swCard || nvrCard;
    if (hub) {
      var hb = hub.box, nL = leftCards.length;
      leftCards.forEach(function (c, i) {
        if (c.dashed) return;
        var y0 = c.box.y + c.box.h / 2;
        var y1 = nL === 1 ? hb.y + hb.h / 2 : hb.y + hb.h * (i + 1) / (nL + 1);
        svg += link(elbow(c.box.x + c.box.w, y0, hb.x, y1));
      });
    }
    if (swCard && nvrCard) {
      var ax = swCard.box.x + swCard.box.w / 2;
      svg += link("M" + ax + "," + (swCard.box.y + swCard.box.h) + " V" + nvrCard.box.y);
      svg += tagNear(ax, (swCard.box.y + swCard.box.h + nvrCard.box.y) / 2, "视频流汇聚");
    }
    if (nvrCard && diskCard) {
      var bx = nvrCard.box.x + nvrCard.box.w / 2;
      svg += link("M" + bx + "," + (nvrCard.box.y + nvrCard.box.h) + " V" + diskCard.box.y);
      svg += tagNear(bx, (nvrCard.box.y + nvrCard.box.h + diskCard.box.y) / 2, "录像存储");
    }
    if (nvrCard && platCard) {
      var nx1 = nvrCard.box.x + nvrCard.box.w, ny = nvrCard.box.y + nvrCard.box.h / 2;
      svg += link("M" + nx1 + "," + ny + " H" + platCard.box.x);
      svg += tag((nx1 + platCard.box.x) / 2, ny - 14, "视频流");
    }
    if (srvCard && platCard) {
      var sx = srvCard.box.x + srvCard.box.w / 2;
      svg += link("M" + sx + "," + (srvCard.box.y + srvCard.box.h) + " V" + platCard.box.y, true);
      svg += tagNear(sx, (srvCard.box.y + srvCard.box.h + platCard.box.y) / 2, "承载");
    }
    if (platCard && dispCard) {
      var px2 = platCard.box.x + platCard.box.w / 2;
      svg += link("M" + px2 + "," + (platCard.box.y + platCard.box.h) + " V" + dispCard.box.y);
      svg += tagNear(px2, (platCard.box.y + platCard.box.h + dispCard.box.y) / 2, "上墙");
    }

    // 图例与说明
    svg += '<line x1="' + M + '" y1="' + fy + '" x2="' + (M + 38) + '" y2="' + fy + '" stroke="' + ENG.LINK + '" stroke-width="1.4" marker-end="url(#engArrow)"/>';
    var legTxt = "PoE / 网络视频流（摄像机 → 交换机 → NVR → 平台 → 上墙）";
    svg += '<text x="' + (M + 46) + '" y="' + (fy + 3.5) + '" font-size="9.5" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#5B6878">' + esc(legTxt) + '</text>';
    var dx0 = M + 46 + measure(legTxt, 9.5) + 34;
    svg += '<line x1="' + dx0 + '" y1="' + fy + '" x2="' + (dx0 + 38) + '" y2="' + fy + '" stroke="' + ENG.LINK + '" stroke-width="1.4" stroke-dasharray="4 4"/>';
    svg += '<text x="' + (dx0 + 46) + '" y="' + (fy + 3.5) + '" font-size="9.5" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#5B6878">承载关系（平台软件运行于服务器）</text>';
    svg += '<text x="' + M + '" y="' + (fy + 28) + '" font-size="10" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#8A94A6">读图：摄像机就近接入 PoE 交换机并供电，视频流汇聚到 NVR 集中录像，再由综合管理平台统一管理、解码上墙。</text>';
    svg += '<text x="' + M + '" y="' + (fy + 46) + '" font-size="10" font-weight="400" font-family="Microsoft YaHei, PingFang SC, sans-serif" fill="#8A94A6">说明：本图为工程建议拓扑，实际按点位就近接入交换机、按区域 / 机柜部署；型号与数量来自选型结果。</text>';
    svg += "</svg>";

    container.innerHTML = svg;
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
