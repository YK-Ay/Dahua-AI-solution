"""方案包测试：Word 章节、报价单/点位表 Excel、ZIP 内容。"""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from app.builder import build_spec
from app.package import build_deliverables
from config.settings import DB_PATH

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


@unittest.skipUnless(DB_PATH.exists(), "知识库不存在，跳过方案包测试")
class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = build_spec(TEXT, name="方案包测试项目")
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.out = build_deliverables(cls.spec, Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_and_zip(self):
        for key in ("docx", "pptx", "png", "png_detail", "quote", "points", "package"):
            self.assertTrue(Path(self.out[key]).is_file(), f"缺少 {key}")
        with zipfile.ZipFile(self.out["package"]) as zf:
            names = set(zf.namelist())
        self.assertEqual(names, {
            "方案书.docx", "报价单.xlsx", "点位表.xlsx",
            "系统拓扑.png", "系统拓扑-详细.png", "方案汇报.pptx", "交付说明.txt",
            "工程审查清单.xlsx",
        })

    def test_docx_chapters_and_no_ai(self):
        from docx import Document

        doc = Document(self.out["docx"])
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertNotIn("AI 生成", text)
        self.assertNotIn("草稿", text)
        for title in ("项目概述", "需求分析", "设计依据与标准", "总体设计",
                      "点位设计", "设备清单与选型说明", "存储与带宽测算",
                      "系统拓扑", "施工组织与进度", "售后服务与培训",
                      "交付边界与风险假设", "缺口与待确认", "数据来源与测算口径"):
            self.assertIn(title, text)
        headers = [[c.text for c in t.rows[0].cells] for t in doc.tables]
        self.assertIn(["版本", "日期", "修订内容", "编制", "审核"], headers)
        self.assertIn(["章节", "主要内容"], headers)

    def test_quote_xlsx(self):
        ws = load_workbook(self.out["quote"]).active
        values = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
        self.assertIn("型号", values)
        self.assertIn("小计(元)", values)
        self.assertTrue(any("合计" in v for v in values))

    def test_point_table_xlsx(self):
        ws = load_workbook(self.out["points"]).active
        values = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
        self.assertIn("点位数", values)
        self.assertIn("所需容量(TB)", values)
        self.assertTrue(any("合计" in v for v in values))


if __name__ == "__main__":
    unittest.main()
