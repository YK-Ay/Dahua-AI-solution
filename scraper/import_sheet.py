"""把 sheet_full.json（DOM 提取，每组 text 含多个产品，/ 分隔）解析入库。"""
import sqlite3, json, re, sys, os, datetime

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

DB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'db', 'dahua_kb.sqlite'))
SOL_ID = '66f3df7122d447f7ac8688728af8d769'
SOL_INFO = {
    'name': '云联智居-智慧小区综合管理解决方案',
    'scene_id': '50e37352b1364dc49c6ccf5e848a3a0a',
    'scene_name': '小区社区',
    'detail_img': 'https://obs-prmfile.dahuatech.com/prm/prod/2026/3/27/970/a01253305c5e4d63b97adeb1b5d60550.png',
    'release_time': '2026-10-10',
    'view_count': 3398,
}

DEFAULT_SHEET = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'sheet_full.json'))
path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHEET
if not os.path.exists(path):
    raise SystemExit(f'找不到配单数据文件：{path}\n用法：python scraper/import_sheet.py <sheet_full.json 路径>')
with open(path, encoding='utf-8-sig') as f:
    groups = json.load(f)

con = sqlite3.connect(DB)
con.execute('PRAGMA journal_mode=MEMORY')
now = datetime.datetime.now().isoformat(timespec='seconds')

con.execute('''INSERT INTO solutions(sol_id,name,scene_id,scene_name,detail_img,release_time,view_count,collected_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(sol_id) DO UPDATE SET collected_at=excluded.collected_at''',
            (SOL_ID, SOL_INFO['name'], SOL_INFO['scene_id'], SOL_INFO['scene_name'],
             SOL_INFO['detail_img'], SOL_INFO['release_time'], SOL_INFO['view_count'], now))
con.execute('DELETE FROM solution_items WHERE sol_id=?', (SOL_ID,))

SKIP = {'产品描述', '替换', '/'}
n = 0
for g in groups:
    m = re.match(r'(\d+)\s*[、\.．]\s*(.+)', g['group'])
    grp_no = int(m.group(1)) if m else n + 1
    grp_name = m.group(2).strip() if m else g['group']

    # 按 / 切分产品块
    blocks = [b.strip() for b in re.split(r'\n/\n', g['text']) if b.strip() and b.strip() != '/']
    for b in blocks:
        lines = [l.strip() for l in b.split('\n') if l.strip() and l.strip() not in SKIP]
        # 去掉分组名行本身（如果第一行是 "1、xxx"）
        if lines and re.match(r'^\d+\s*[、\.．]', lines[0]):
            lines = lines[1:]
        if not lines:
            continue
        model = next((l for l in lines if re.match(r'^(DH-|ST\d|ASF|WBD)', l)), None)
        price_m = re.search(r'¥([\d.]+)', b)
        price = float(price_m.group(1)) if price_m else None
        # 展示名 = 第一行非型号非价格
        display = next((l for l in lines if not re.match(r'^(DH-|ST\d|ASF|WBD)', l)
                        and not l.startswith('¥') and 'mm' not in l and '|' not in l), lines[0])
        spec = next((l for l in lines if 'mm' in l or '|' in l), '')

        con.execute('''INSERT INTO solution_items(sol_id,grp_no,grp_name,model,display_name,spec,price)
                       VALUES(?,?,?,?,?,?,?)''',
                    (SOL_ID, grp_no, grp_name, model, display, spec, price))
        n += 1

con.commit()
print(f"入库产品总数: {n}")
for row in con.execute('SELECT grp_no,grp_name,model,price FROM solution_items WHERE sol_id=? ORDER BY grp_no', (SOL_ID,)):
    print(row)
con.close()
