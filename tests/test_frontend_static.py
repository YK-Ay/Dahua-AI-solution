"""前端静态契约测试：不引浏览器，只守住三类最容易出错的契约。

1. JS 语法：node --check（CI 装了 node；本地没有则跳过）
2. 资源版本号：agent.html 引用的脚本必须带 ?v=，避免浏览器缓存旧脚本
3. DOM 契约：agent.js 里 $("id") / getElementById("id") 用到的 id，
   agent.html 必须真的存在——少一个 id 就会在运行时静默报 null 错误
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"
JS_FILES = ["agent.js", "topology.js", "compare.js", "kb.js", "rag.js", "solution.js"]


class FrontendStaticTest(unittest.TestCase):
    def test_js_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("未安装 node，跳过 JS 语法检查")
        for name in JS_FILES:
            path = STATIC / name
            if not path.is_file():
                continue
            proc = subprocess.run([node, "--check", str(path)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             f"{name} 语法错误：{proc.stderr.strip()}")

    def test_agent_assets_are_versioned(self):
        html = (STATIC / "agent.html").read_text(encoding="utf-8")
        for name in ("agent.js", "topology.js"):
            matches = re.findall(rf'/static/{re.escape(name)}\?v=([0-9A-Za-z\-]+)', html)
            self.assertTrue(matches, f"agent.html 未给 {name} 加版本号")

    def test_dom_ids_used_by_agent_js_exist(self):
        html = (STATIC / "agent.html").read_text(encoding="utf-8")
        js = (STATIC / "agent.js").read_text(encoding="utf-8")
        used = set(re.findall(r'\$\("([A-Za-z0-9_\-]+)"\)', js))
        used |= set(re.findall(r'getElementById\("([A-Za-z0-9_\-]+)"\)', js))
        declared = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', html))
        missing = sorted(used - declared)
        self.assertEqual(missing, [], f"agent.html 缺少这些 id：{missing}")


if __name__ == "__main__":
    unittest.main()
