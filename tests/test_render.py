"""渲染冒烟测试：Spec → 拓扑 PNG → Word（依赖真实知识库，不存在则跳过）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from app.builder import build_spec
from app.topology import render_topology
from app.word import render_docx
from config.settings import DB_PATH

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


@unittest.skipUnless(DB_PATH.exists(), "知识库不存在，跳过渲染冒烟测试")
class RenderTest(unittest.TestCase):
    def test_render_png_and_docx(self):
        spec = build_spec(TEXT)
        with tempfile.TemporaryDirectory() as tmp:
            png = render_topology(spec, Path(tmp) / "topo.png")
            docx = render_docx(spec, Path(tmp) / "plan.docx", topology_png=png)

            self.assertGreater(png.stat().st_size, 20_000)
            self.assertGreater(docx.stat().st_size, 20_000)

            doc = Document(str(docx))
            # 需求表 / 设备表 / 第八章「设备选型来源」表
            self.assertGreaterEqual(len(doc.tables), 3)
            self.assertEqual(len(doc.inline_shapes), 1)
            self.assertIn("资料来源", [c.text for c in doc.tables[-1].rows[0].cells])
            headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
            self.assertTrue(any("数据来源与测算口径" in h for h in headings))


if __name__ == "__main__":
    unittest.main()
