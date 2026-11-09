/* ============================================================
 * 大华云商 · 方案配单批量导出脚本  v3
 * ------------------------------------------------------------
 * 真实接口：POST https://mall.dahuatech.com/product/api/lectotype/products/cloudProds
 * 请求体  ：{ "lectotypeId": "<方案ID>" }
 * 方案 ID ：内嵌 53 条（来自《大华云商方案索引_53个.json》）
 *
 * v3 针对「code=10000 登陆超时」的改进：
 *   1. identifycode 直接从 accesstoken 的 JWT payload 解出，保证两者配对
 *   2. 收集本机所有候选凭证，逐个自检，第一个通过就用它
 *   3. 临时借用「页面自己发出的请求头」（最可信），必要时轻轻触发一次页面请求
 *   4. 全部失败时打印诊断信息（不含任何凭证值），便于定位
 *
 * 用法：登录 mall.dahuatech.com → F12 Console → 手打"允许粘贴"解锁 → 粘贴本文 → 回车
 * 安全：不硬编码任何账号/token/Cookie；凭证只在浏览器内存里使用，不写入文件、不外发。
 * ============================================================ */
(async () => {
  'use strict';

  const BASE  = 'https://mall.dahuatech.com';
  const API   = '/product/api/lectotype/products/cloudProds';
  const PROBE_ID = '714d69110371435fb689c5715eced577';  // 已验证：SMB全云停车方案
  const DELAY = 800;
  const STORE_KEY = 'dahua_bom_progress_v3';
  const SCHEMES = [
  {
    "id": "66f3df7122d447f7ac8688728af8d769",
    "name": "云联智居-智慧小区综合管理解决方案",
    "cat": "小区社区",
    "view": 3622
  },
  {
    "id": "60adba42868d48f087db74a0be019f38",
    "name": "加油加气站安防系统解决方案",
    "cat": "中小化工厂",
    "view": 2276
  },
  {
    "id": "b6533b98972c4bcaae997eef84108fa3",
    "name": "中小工厂安消一体化解决方案",
    "cat": "工厂园区",
    "view": 3194
  },
  {
    "id": "6615c2600da64259b318ad971293cbec",
    "name": "中小化工企业安防系统解决方案",
    "cat": "中小化工厂",
    "view": 2563
  },
  {
    "id": "5c6fdc7a13f846d8b277db1475dfa11d",
    "name": "恶意退货视频追溯解决方案（电商举证监控方案）",
    "cat": "快递驿站",
    "view": 9267
  },
  {
    "id": "fb8d54f93e6f4544a7c38889b2883b25",
    "name": "压缩王利旧改造方案",
    "cat": "监控中心",
    "view": 2673
  },
  {
    "id": "91e35320674b475189cef9226f089b50",
    "name": "储能电站智慧消防解决方案",
    "cat": "中小化工厂",
    "view": 2070
  },
  {
    "id": "3be7d80d9ffd4333b0e8b45fb5df7e42",
    "name": "SMB 社区云对讲解决方案",
    "cat": "小区社区",
    "view": 974
  },
  {
    "id": "eee166067be04765a29b02876a9d4a87",
    "name": "工厂园区综合解决方案",
    "cat": "工厂园区",
    "view": 1760
  },
  {
    "id": "a094ea275bab4cf4945d476a18265a29",
    "name": "SMB智能超低存储解决方案",
    "cat": "监控中心",
    "view": 2688
  },
  {
    "id": "714d69110371435fb689c5715eced577",
    "name": "SMB全云停车方案",
    "cat": "出入口",
    "view": 928
  },
  {
    "id": "6f096c0723924ef0bb4dd54f8e92ca97",
    "name": "防溺水解决方案",
    "cat": "数字乡村",
    "view": 3838
  },
  {
    "id": "873e8d14039244d99b1cfe7e1bde6b3a",
    "name": "高分低存解决方案",
    "cat": "连锁商超",
    "view": 2618
  },
  {
    "id": "2caf5d3752ab418ca8cf23b4f9f05e81",
    "name": "SMB云考勤方案",
    "cat": "企业办公",
    "view": 2483
  },
  {
    "id": "d5db7c0003094431ab96a11c8300215b",
    "name": "SMB智慧民宿解决方案",
    "cat": "酒店民宿",
    "view": 4654
  },
  {
    "id": "ffccabc6447d4108befdc3b498d1be07",
    "name": "客流场景方案",
    "cat": "商业综合体",
    "view": 6663
  },
  {
    "id": "cb5e21e7911740989bfc507dd6af0ac2",
    "name": "夜视王大模型周界防护解决方案",
    "cat": "工厂园区",
    "view": 4576
  },
  {
    "id": "bad5d60b237a41d1bff2f632d1f1cea1",
    "name": "服装厂智慧消防解决方案",
    "cat": "纺织服装",
    "view": 3010
  },
  {
    "id": "9c90a59b9bee4a5696fee723a1da037e",
    "name": "车辆出入口停车云边场景",
    "cat": "出入口",
    "view": 4421
  },
  {
    "id": "0eaec14116b34a83b7d4b470bd346d36",
    "name": "消防通道占用解决方案",
    "cat": "小区社区",
    "view": 2636
  },
  {
    "id": "90aa4ec9868c473a899d2681060ea5dc",
    "name": "企业人员出行方案",
    "cat": "企业办公",
    "view": 3577
  },
  {
    "id": "56a6bc0acf174c43ac04e336000bd721",
    "name": "人员在离岗解决方案",
    "cat": "工厂园区",
    "view": 3899
  },
  {
    "id": "512482d2c67d42e3a562f136141864d9",
    "name": "SMB全景监控方案",
    "cat": "智慧校园",
    "view": 2401
  },
  {
    "id": "d3f6934368e5492c8c0166c82dd7e419",
    "name": "监控中心文搜解决方案",
    "cat": "监控中心",
    "view": 3313
  },
  {
    "id": "ee89df9d72a849ed88f623863134d0c7",
    "name": "自建房/别墅云对讲方案",
    "cat": "别墅自建房",
    "view": 3598
  },
  {
    "id": "cf8b7728a00a4c5e9814b6a29a3703e9",
    "name": "大华云联监控中心解决方案",
    "cat": "监控中心",
    "view": 1854
  },
  {
    "id": "9e4fbd320b6f4659838a62813da3492b",
    "name": "鱼塘无电无网监控解决方案",
    "cat": "种植/养殖",
    "view": 4841
  },
  {
    "id": "58649c37fba74097b5da535e2efeb35e",
    "name": "小区电瓶车入梯管控解决方案",
    "cat": "小区社区",
    "view": 2984
  },
  {
    "id": "7345bb2c2fd141818e13f74141d7a1bd",
    "name": "高价值零售行业服务巡检解决方案",
    "cat": "连锁商超",
    "view": 1792
  },
  {
    "id": "63fb664b78904fdbb63383e29aa49ad2",
    "name": "小区高空抛物管控解决方案",
    "cat": "小区社区",
    "view": 7649
  },
  {
    "id": "867a71ff26ed43eaa72724c140a4bf9f",
    "name": "中小工厂夜视王监控解决方案",
    "cat": "工厂园区",
    "view": 4996
  },
  {
    "id": "d0fb412e7c344eefadc3fa37bfc342fa",
    "name": "娱乐场所夜视王监控解决方案",
    "cat": "休闲娱乐",
    "view": 3640
  },
  {
    "id": "e8aa906e6f6b48479110c3602cd5fecd",
    "name": "园区夜视智能监控解决方案",
    "cat": "工厂园区",
    "view": 3991
  },
  {
    "id": "a40f7dcc283b4810a1db3374aa1c7adf",
    "name": "托育园、幼儿园开放监控解决方案",
    "cat": "智慧校园",
    "view": 3180
  },
  {
    "id": "bc436a0f45054407a8b9dc3c12f8640f",
    "name": "无网有电4G远程监控解决方案",
    "cat": "种植/养殖",
    "view": 5775
  },
  {
    "id": "4fcbb6b5f9fa465c84b05e80c54fa348",
    "name": "中小公园/景区夜视王监控解决方案",
    "cat": "景区场馆",
    "view": 6874
  },
  {
    "id": "1183165e86cd4eb0a7ff9fbc3e2087f8",
    "name": "幼儿园与中小学监控智能改造解决方案",
    "cat": "智慧校园",
    "view": 6780
  },
  {
    "id": "e4ad96284d124a42955c8b24d48f6b15",
    "name": "幼儿园、小学智慧接送解决方案",
    "cat": "智慧校园",
    "view": 2675
  },
  {
    "id": "e2f55e0ebb91412095f500a6a6291183",
    "name": "夜视王监控直播解决方案",
    "cat": "景区场馆",
    "view": 3839
  },
  {
    "id": "1bd6f303aeb847fb9c5bb3a2ba96d42a",
    "name": "河湖轮船夜视王监控解决方案",
    "cat": "智能交通",
    "view": 2944
  },
  {
    "id": "7e55d30a251449ffa56770153db828aa",
    "name": "仓库夜视王监控解决方案",
    "cat": "工厂园区",
    "view": 2692
  },
  {
    "id": "311bc658328747f5968e413fffdf7642",
    "name": "室外网桥解决方案",
    "cat": "传输方案",
    "view": 903
  },
  {
    "id": "9ac1c4a004684987969576627b5d82a4",
    "name": "无线网络覆盖解决方案（商业场景）",
    "cat": "传输方案",
    "view": 765
  },
  {
    "id": "e419af4543d3479ab00bf84ffa03b9fe",
    "name": "无线网络覆盖解决方案（住宅场景）",
    "cat": "传输方案",
    "view": 730
  },
  {
    "id": "55b6ed8d93364823bcb9ce0152f59c46",
    "name": "视频网络云管解决方案",
    "cat": "传输方案",
    "view": 699
  },
  {
    "id": "aeca19105cb44bb1822e5dca7e8a4733",
    "name": "快递驿站“包裹”精准追寻解决方案",
    "cat": "快递驿站",
    "view": 5590
  },
  {
    "id": "3a3a83bc62c342c284fa0de9b9b5ec66",
    "name": "景区/公园精准客流解决方案",
    "cat": "景区场馆",
    "view": 4920
  },
  {
    "id": "39b55a2bc73c424fa4278299618143aa",
    "name": "大华超能监控 | 超能护园（园区）解决方案",
    "cat": "工厂园区",
    "view": 15805
  },
  {
    "id": "85f75f37acf64a49ae6ffd601402c99e",
    "name": "大华超能监控 | 超能护幼（幼儿园）解决方案",
    "cat": "智慧校园",
    "view": 9277
  },
  {
    "id": "e0bf748772bf48f3a7c7376c28cad244",
    "name": "大华超能监控 | 超能护校（中小学）解决方案",
    "cat": "智慧校园",
    "view": 10425
  },
  {
    "id": "84ba4efa46ad40aebcbe7f7e84d62c3b",
    "name": "精准检索解决方案",
    "cat": "工厂园区",
    "view": 3213
  },
  {
    "id": "cb88339e629e4afe960a0b4b022a5e09",
    "name": "商超精准防损解决方案",
    "cat": "连锁商超",
    "view": 2910
  },
  {
    "id": "3826601e0da84ef8a9b1ac3f53d926b9",
    "name": "风电机组消防安全解决方案",
    "cat": "",
    "view": 755
  }
];

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const log   = (...a) => console.log('%c[配单导出v3]', 'color:#0a84ff;font-weight:bold', ...a);
  const warn  = (...a) => console.warn('%c[配单导出v3]', 'color:#ff9500;font-weight:bold', ...a);

  const JWT_RE = /^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
  const HEX32  = /^[0-9a-f]{32}$/i;
  const HEADER_KEYS = ['accesstoken', 'identifycode', 'channel', 'version', 'system',
                       'token', 'authorization', 'x-xsrf-token', 'credential'];

  // ---------- 1. 解 JWT payload（只读，不打印凭证值） ----------
  function decodeJwt(tok) {
    try {
      let p = tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      while (p.length % 4) p += '=';
      const bin = atob(p);
      const bytes = Uint8Array.from(bin, c => c.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_) { return null; }
  }

  // ---------- 2. 收集本机所有候选凭证 ----------
  function collectTokens() {
    const found = [], seen = new Set();
    let localIdentify = null;
    const add = (k, v, from) => {
      if (typeof v !== 'string' || !v) return;
      const kk = String(k || '').toLowerCase();
      if (kk.includes('identify') && HEX32.test(v) && !localIdentify) localIdentify = v;
      if (!JWT_RE.test(v) || seen.has(v)) return;
      seen.add(v);
      let prio = 5;
      if (kk.includes('accesstoken')) prio = 0;
      else if (kk === 'token' || kk === 'access_token') prio = 1;
      else if (kk.includes('token')) prio = 2;
      found.push({ token: v, prio: prio, from: from + ':' + (k || '?'), payload: decodeJwt(v) });
    };
    const walk = (o, depth, from) => {
      if (!o || typeof o !== 'object' || depth > 4) return;
      for (const [k, v] of Object.entries(o)) {
        if (typeof v === 'string') add(k, v, from);
        else if (v && typeof v === 'object') walk(v, depth + 1, from);
      }
    };
    for (const [name, store] of [['local', localStorage], ['session', sessionStorage]]) {
      try {
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i), v = store.getItem(k);
          add(k, v, name);
          if (v && (v[0] === '{' || v[0] === '[')) {
            try { walk(JSON.parse(v), 0, name); } catch (_) {}
          }
        }
      } catch (_) {}
    }
    try {
      document.cookie.split(';').forEach(s => {
        const p = s.trim().split('=');
        add(p[0], decodeURIComponent(p.slice(1).join('=') || ''), 'cookie');
      });
    } catch (_) {}
    found.sort((a, b) => a.prio - b.prio);
    return { tokens: found, localIdentify: localIdentify };
  }

  // ---------- 3. 构造请求头 ----------
  function headersRaw(src) {
    const h = { 'Content-Type': 'application/json' };
    HEADER_KEYS.forEach(k => { if (src[k]) h[k] = src[k]; });
    if (!h.channel) h.channel = 'PC';
    if (!h.version) h.version = '1.0';
    return h;
  }

  // ---------- 4. 借用页面自己的请求头 ----------
  const captured = {};
  let selfCall = false;
  function installHook() {
    const origSet   = XMLHttpRequest.prototype.setRequestHeader;
    const origFetch = window.fetch;
    XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
      captured[String(k).toLowerCase()] = v;
      return origSet.apply(this, arguments);
    };
    window.fetch = function (url, opt) {
      if (!selfCall && opt && opt.headers) {
        const hs = opt.headers;
        if (hs instanceof Headers) hs.forEach((v, k) => { captured[k.toLowerCase()] = v; });
        else if (Array.isArray(hs)) hs.forEach(p => { captured[String(p[0]).toLowerCase()] = p[1]; });
        else Object.entries(hs).forEach(([k, v]) => { captured[String(k).toLowerCase()] = v; });
      }
      return origFetch.apply(this, arguments);
    };
    return function () {
      XMLHttpRequest.prototype.setRequestHeader = origSet;
      window.fetch = origFetch;
    };
  }
  async function nudgePage() {
    // 先试着点一下「方案配单」标签，让页面自己重新发一次请求
    try {
      const nodes = Array.from(document.querySelectorAll('li,a,span,div,button'))
        .filter(e => e.children.length === 0 && (e.textContent || '').trim() === '方案配单');
      if (nodes.length) nodes[0].click();
    } catch (_) {}
    await sleep(1300);
    if (captured.accesstoken) return;
    // 再切换一次方案 ID（hash 路由），触发页面重新拉数据
    try {
      const m = location.hash.match(/solutionDetail\/([0-9a-f]+)/i);
      const other = SCHEMES.find(s => s.id !== PROBE_ID);
      if (m && other) {
        location.hash = location.hash.replace(m[1], other.id);
        await sleep(1000);
        location.hash = location.hash.replace(other.id, m[1]);
        await sleep(1300);
      }
    } catch (_) {}
  }

  // ---------- 5. 调接口 ----------
  async function callBom(lectotypeId, hdrs) {
    let res, txt;
    selfCall = true;
    try {
      res = await fetch(BASE + API, {
        method: 'POST', headers: hdrs, credentials: 'include',
        body: JSON.stringify({ lectotypeId: lectotypeId }),
      });
      txt = await res.text();
    } finally { selfCall = false; }
    let j = null;
    try { j = JSON.parse(txt); }
    catch (_) { return { ok: false, status: res.status, code: null, msg: '响应非JSON', raw: txt.slice(0, 600) }; }
    const data = j && (j.data !== undefined ? j.data : j.result);
    const bad  = j && /登陆超时|登录超时|未登录|未授权|登录失效|重新登陆|重新登录|unauthor/i.test(
      JSON.stringify({ code: j.code, msg: j.msg, message: j.message }));
    const ok = res.status === 200 && data != null && !bad;
    return { ok: ok, status: res.status, code: j && j.code, msg: j && (j.msg || j.message),
             data: j, raw: txt.slice(0, 600) };
  }

  // ---------- 6. 只找"疑似配单数组"用于提示，不做假设 ----------
  function findBomArrays(obj, hits, path) {
    hits = hits || []; path = path || '';
    if (!obj || typeof obj !== 'object') return hits;
    if (Array.isArray(obj)) {
      const sample = obj.find(x => x && typeof x === 'object');
      if (sample) {
        const ks = Object.keys(sample).map(k => k.toLowerCase());
        const hasModel = ks.some(k => ['model','modelname','productmodel','prodmodel','spec','specification','productcode'].indexOf(k) >= 0);
        const hasQty   = ks.some(k => ['qty','quantity','num','count','amount','number','productnum'].indexOf(k) >= 0);
        const hasPrice = ks.some(k => k.indexOf('price') >= 0 || ['cost','priceind'].indexOf(k) >= 0);
        const hasName  = ks.some(k => ['name','productname','prodname','title'].indexOf(k) >= 0);
        if ((hasModel && hasQty) || (hasModel && hasPrice) || (hasName && hasQty && hasPrice))
          hits.push({ path: path, count: obj.length, fields: Object.keys(sample) });
      }
      obj.forEach((v, i) => findBomArrays(v, hits, path + '[' + i + ']'));
    } else {
      for (const [k, v] of Object.entries(obj)) findBomArrays(v, hits, path ? path + '.' + k : k);
    }
    return hits;
  }

  // ---------- 7. 自检：多候选逐个试 ----------
  const info = collectTokens();
  const tokens = info.tokens;
  log('本机找到候选登录凭证 ' + tokens.length + ' 个；开始逐个自检（只发少量请求）…');
  const restore = installHook();
  const tried = [];
  let probe = null, working = null, lastRaw = '';

  const attempts = [];
  tokens.slice(0, 3).forEach(t => {
    const p = t.payload || {};
    attempts.push({ h: headersRaw({ accesstoken: t.token, identifycode: p.identifyCode }),
                    label: t.from + ' + payload内identifyCode' });
    if (p.system) attempts.push({ h: headersRaw({ accesstoken: t.token, identifycode: p.identifyCode, system: p.system }),
                    label: t.from + ' + system(' + p.system + ')' });
    if (info.localIdentify && info.localIdentify !== p.identifyCode)
      attempts.push({ h: headersRaw({ accesstoken: t.token, identifycode: info.localIdentify }),
                      label: t.from + ' + 本机identifycode' });
  });

  for (const a of attempts.slice(0, 9)) {
    const r = await callBom(PROBE_ID, a.h);
    lastRaw = r.raw;
    tried.push(a.label + ' → ' + (r.ok ? '成功' : ('code=' + r.code + ' ' + (r.msg || ''))));
    if (r.ok) { probe = r; working = a.h; break; }
    await sleep(250);
  }

  // 页面自己在脚本运行期间发过请求 → 原样借用
  if (!probe && captured.accesstoken) {
    const h = headersRaw(captured);
    const r = await callBom(PROBE_ID, h);
    lastRaw = r.raw;
    tried.push('页面请求头(原样) → ' + (r.ok ? '成功' : ('code=' + r.code + ' ' + (r.msg || ''))));
    if (r.ok) { probe = r; working = h; }
  }

  // 仍未通过 → 轻推页面重新发一次请求，再借头
  if (!probe) {
    log('本机凭证未通过，尝试借用页面当前正在使用的登录态…');
    await nudgePage();
    if (captured.accesstoken) {
      const h = headersRaw(captured);
      const r = await callBom(PROBE_ID, h);
      lastRaw = r.raw;
      tried.push('借用页面请求头 → ' + (r.ok ? '成功' : ('code=' + r.code + ' ' + (r.msg || ''))));
      if (r.ok) { probe = r; working = h; }
    }
  }
  restore();

  if (!probe) {
    warn('自检全部未通过，已停止（未发起其余 52 个请求）。');
    tried.forEach(t => warn('  尝试：' + t));
    tokens.slice(0, 5).forEach(t => {
      const p = t.payload || {};
      const days = p.exp ? ((p.exp * 1000 - Date.now()) / 86400000).toFixed(1) : '未知';
      warn('  凭证 ' + t.from + '：system=' + (p.system || '?') + '，剩余有效天数=' + days +
           '，payload含identifyCode=' + (!!p.identifyCode));
    });
    warn('本机独立 identifycode：' + (info.localIdentify ? '有' : '无') +
         '；页面请求头捕获：' + (captured.accesstoken ? '成功' : '未捕获到'));
    warn('最后一次响应：' + lastRaw);
    warn('若上面显示「剩余有效天数」为负数 → 请在云商退出并重新登录，然后重新粘贴本脚本。');
    warn('若凭证未过期仍失败 → 把上面这些【诊断行】发我（不含任何凭证值），我继续改。');
    return;
  }

  log('✅ 自检通过！使用的登录态：' + (working.system ? '带system' : '标准头') +
      '；响应顶层键：' + Object.keys(probe.data).join(', '));
  const ph = findBomArrays(probe.data);
  if (ph.length) log('🔍 疑似配单路径：' + ph[0].path + '（' + ph[0].count + ' 条，字段：' + ph[0].fields.join(',') + '）');
  else warn('未自动识别配单数组（没关系）：原始响应会完整保存，交给我适配即可。');

  // ---------- 8. 批量抓取（断点续跑） ----------
  let prog = {};
  try { prog = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (_) {}
  const results = prog.results || {};
  for (let i = 0; i < SCHEMES.length; i++) {
    const s = SCHEMES[i];
    if (results[s.id] && results[s.id]._done) {
      log('(' + (i + 1) + '/' + SCHEMES.length + ') 已完成，跳过：' + s.name);
      continue;
    }
    try {
      log('(' + (i + 1) + '/' + SCHEMES.length + ') 抓取：' + s.name);
      const r = await callBom(s.id, working);
      if (!r.ok) { warn('  失败 code=' + r.code + ' msg=' + (r.msg || '')); continue; }
      results[s.id] = {
        _done: true, scheme_id: s.id, scheme_name: s.name, industry: s.cat,
        view_count: s.view, detail: r.data,
        bom_candidates: findBomArrays(r.data).map(h => ({ path: h.path, count: h.count, fields: h.fields })),
        fetched_at: new Date().toISOString(),
      };
      prog.results = results;
      try { localStorage.setItem(STORE_KEY, JSON.stringify(prog)); }
      catch (_) { warn('  进度缓存空间不足（不影响本次导出，请勿关闭页面）'); }
    } catch (e) { warn('  异常：' + e.message); }
    await sleep(DELAY);
  }

  // ---------- 9. 导出完整原始响应 ----------
  const rows = SCHEMES.map(s => results[s.id]).filter(Boolean);
  const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'dahua_boms.json';
  a.click();
  log('完成：' + rows.length + '/' + SCHEMES.length + ' 个方案，已下载 dahua_boms.json');
  const empty = SCHEMES.filter(s => !results[s.id]).map(s => s.name);
  if (empty.length) warn('未取到：' + empty.join('、'));
  if (ph.length) log('配单字段路径：' + ph[0].path + '（把这个也发我，便于核对解析）');
})();
