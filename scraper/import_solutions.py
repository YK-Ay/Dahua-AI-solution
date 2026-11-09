"""批量入库方案配单 JSON（浏览器 JS 采集产物）。

用法:
  python scraper/import_solutions.py data/solutions/*.json
  python scraper/import_solutions.py 文件1.json 文件2.json

每个 JSON 文件推荐格式（JS-B 脚本生成）:
{
  "sol_id": "66f3...",
  "name": "云联智居-智慧小区综合管理解决方案",
  "scene_id": "50e3...",
  "scene_name": "小区社区",
  "detail_img": "https://...",
  "release_time": "2026-10-10",
  "view_count": 3398,
  "groups": [ {"group": "1、周界围墙", "text": "/\\n夜视王...¥618.00\\n替换"} ]
}

兼容旧格式：文件直接是 groups 数组，需用 --sol-id/--name/--scene 补充元信息。
"""
import sqlite3, json, re, sys, os, glob, datetime, argparse

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

DB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'db', 'dahua_kb.sqlite'))
SKIP = {'产品描述', '替换', '/'}


def parse_groups(groups):
    """把 groups 列表解析为 [(grp_no, grp_name, model, display, spec, price), ...]"""
    rows = []
    n = 0
    for g in groups:
        m = re.match(r'(\d+)\s*[、\.．]\s*(.+)', g['group'])
        grp_no = int(m.group(1)) if m else n + 1
        grp_name = m.group(2).strip() if m else g['group']

        blocks = [b.strip() for b in re.split(r'\n/\n', g['text']) if b.strip() and b.strip() != '/']
        for b in blocks:
            lines = [l.strip() for l in b.split('\n') if l.strip() and l.strip() not in SKIP]
            if lines and re.match(r'^\d+\s*[、\.．]', lines[0]):
                lines = lines[1:]
            if not lines:
                continue
            model = next((l for l in lines if re.match(r'^(DH-|ST\d|ASF|WBD)', l)), None)
            price_m = re.search(r'¥([\d.]+)', b)
            price = float(price_m.group(1)) if price_m else None
            display = next((l for l in lines if not re.match(r'^(DH-|ST\d|ASF|WBD)', l)
                            and not l.startswith('¥') and 'mm' not in l and '|' not in l), lines[0])
            spec = next((l for l in lines if 'mm' in l or '|' in l), '')
            rows.append((grp_no, grp_name, model, display, spec, price))
            n += 1
    return rows


def main():
    ap = argparse.ArgumentParser(description='批量入库方案配单')
    ap.add_argument('files', nargs='+', help='json 文件或 glob')
    ap.add_argument('--sol-id', help='旧格式时的方案 ID')
    ap.add_argument('--name', help='旧格式时的方案名')
    ap.add_argument('--scene-id', default='', help='旧格式时的 scene_id')
    ap.add_argument('--scene', default='', help='旧格式时的 scene_name')
    ap.add_argument('--detail-img', default='', help='旧格式时的详情图 URL')
    ap.add_argument('--release-time', default='', help='旧格式时的发布时间')
    ap.add_argument('--view-count', type=int, default=0, help='旧格式时的浏览量')
    args = ap.parse_args()

    files = []
    for f in args.files:
        files.extend(glob.glob(f) if any(c in f for c in '*?') else [f])
    if not files:
        print('未找到任何 json 文件'); sys.exit(1)

    con = sqlite3.connect(DB)
    con.execute('PRAGMA journal_mode=MEMORY')
    now = datetime.datetime.now().isoformat(timespec='seconds')
    total_sol = 0
    total_items = 0

    for path in files:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
        # 兼容两种格式
        if isinstance(data, dict) and 'groups' in data:
            sol_id = data.get('sol_id') or args.sol_id
            info = data
            groups = data['groups']
        elif isinstance(data, list):
            sol_id = args.sol_id
            info = {'name': args.name, 'scene_id': args.scene_id, 'scene_name': args.scene,
                    'detail_img': args.detail_img, 'release_time': args.release_time,
                    'view_count': args.view_count}
            groups = data
        else:
            print(f'跳过 {path}: 无法识别的格式'); continue

        if not sol_id:
            print(f'跳过 {path}: 缺少 sol_id（旧格式请用 --sol-id 指定）'); continue

        name = info.get('name') or sol_id
        scene_id = info.get('scene_id') or ''
        scene_name = info.get('scene_name') or ''
        detail_img = info.get('detail_img') or ''
        release_time = info.get('release_time') or ''
        view_count = info.get('view_count') or 0

        con.execute('''INSERT INTO solutions(sol_id,name,scene_id,scene_name,detail_img,release_time,view_count,collected_at)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(sol_id) DO UPDATE SET name=excluded.name, scene_id=excluded.scene_id,
                       scene_name=excluded.scene_name, detail_img=excluded.detail_img,
                       release_time=excluded.release_time, view_count=excluded.view_count,
                       collected_at=excluded.collected_at''',
                    (sol_id, name, scene_id, scene_name, detail_img, release_time, view_count, now))
        con.execute('DELETE FROM solution_items WHERE sol_id=?', (sol_id,))

        rows = parse_groups(groups)
        for grp_no, grp_name, model, display, spec, price in rows:
            con.execute('''INSERT INTO solution_items(sol_id,grp_no,grp_name,model,display_name,spec,price)
                           VALUES(?,?,?,?,?,?,?)''',
                        (sol_id, grp_no, grp_name, model, display, spec, price))
        total_items += len(rows)
        total_sol += 1
        print(f'[OK] {name} [{scene_name}] 配单 {len(rows)} 条  (sol_id={sol_id})')

    con.commit()
    print(f'\n入库完成: 方案 {total_sol} 个, 配单 {total_items} 条')
    con.close()


if __name__ == '__main__':
    main()
