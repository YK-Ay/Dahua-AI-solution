-- 大华安防解决方案助手 · 知识库 schema（W1）
-- 单库 SQLite，后续 chunks / products / competitor 表随 W2-W4 增量

-- 案例页：官网每个方案/案例详情页一条
CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT UNIQUE NOT NULL,      -- 详情页 URL
    industry      TEXT NOT NULL,             -- 主行业：智慧园区/智慧交管/智慧校园
    title         TEXT,
    fetched_at    TEXT,                      -- ISO 时间
    raw_html_path TEXT,                      -- 本地 HTML 缓存路径
    content_text  TEXT,                      -- 清洗后的纯文本正文
    content_len   INTEGER DEFAULT 0,         -- 正文字数
    -- 技术密度标注（W1 启发式）
    density_score  REAL DEFAULT 0,           -- 0~1，技术词密度
    tech_hits     INTEGER DEFAULT 0,        -- 命中技术词数
    market_hits   INTEGER DEFAULT 0,        -- 命中营销词数
    page_kind     TEXT DEFAULT 'unknown'    -- tech_page / marketing_page / mixed
);

-- 预留：W2 分块
-- CREATE TABLE IF NOT EXISTS chunks (...);

-- W2：产品库（来源：大华云商 mall.dahuatech.com 匿名接口）
CREATE TABLE IF NOT EXISTS products (
    prod_id      TEXT PRIMARY KEY,          -- "1@24768799435"
    model        TEXT,                      -- 订货型号 DH-LM22-F200
    display_name  TEXT,                     -- 展示名
    price_stand   REAL,                     -- 标准价（元）
    pi_sysj       TEXT,                     -- 上架时间 2026-09-02 ...
    year_new      INTEGER,                  -- 上架年份（便于筛 2026 主推）
    pi_status     TEXT,                     -- 正常销售/停产 等
    class_name    TEXT,                     -- 类目名（网络摄像机/道闸...）
    catalog_ids   TEXT,                     -- JSON 数组
    img_url       TEXT,
    part_num      TEXT,
    direction     TEXT,                     -- 归属方向：园区楼宇 / 交管 / 其他
    collected_at  TEXT
);

-- W4：竞品事实（来源：双方官网公开产品参数页，逐字段一条）
CREATE TABLE IF NOT EXISTS competitor_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    brand         TEXT NOT NULL,            -- 大华 / 海康
    category      TEXT NOT NULL,            -- 网络摄像机 / NVR / PoE交换机
    model         TEXT NOT NULL,            -- 官网型号
    display_name  TEXT,                     -- 官网展示名
    section       TEXT,                     -- 官网参数分区（基础参数/视频/网络...）
    field_label   TEXT NOT NULL,            -- 官网原始字段名
    raw_value     TEXT NOT NULL,            -- 官网原始字段值（不改写）
    source_url    TEXT NOT NULL,            -- 产品参数页 URL
    page_kind     TEXT DEFAULT 'tech_page', -- 技术密度标注
    evidence      TEXT,                     -- 原文片段（field_label｜raw_value）
    retrieved_at  TEXT NOT NULL,            -- 抓取时间 ISO
    UNIQUE (brand, model, field_label)
);

-- W2：方案主表（来源：大华云商 /smb/api/detail/getSchemeNew）
CREATE TABLE IF NOT EXISTS solutions (
    sol_id        TEXT PRIMARY KEY,         -- 方案 ID（hash）
    name          TEXT,                     -- 方案名
    scene_id      TEXT,                     -- 场景分类 ID
    scene_name    TEXT,                     -- 小区社区/工厂园区/企业办公/智能交通
    detail_img    TEXT,                     -- 详情长图 URL
    release_time  TEXT,
    view_count    INTEGER,
    raw_json      TEXT,                     -- 原始响应
    collected_at  TEXT
);

-- W2：方案配单项（场景分组 → 设备，来源：DOM 提取）
CREATE TABLE IF NOT EXISTS solution_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sol_id        TEXT NOT NULL,
    grp_no        INTEGER,                  -- 分组序号（1、2、3...）
    grp_name      TEXT,                     -- 周界围墙/小区大门-出入口...
    model         TEXT,                     -- 订货型号 DH-IPC-HFW2649M-A-LED
    display_name  TEXT,                     -- 展示名
    spec          TEXT,                     -- 3.6mm| 网络摄像机
    price         REAL,                     -- 单价
    FOREIGN KEY (sol_id) REFERENCES solutions(sol_id)
);
