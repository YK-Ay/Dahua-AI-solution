"""招标文件导入测试：硬信息、★ 参数、章节切段、Word 读取。"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from app.tender import TenderError, analyze, read_text

TENDER = """郑州某学院智慧校园建设项目招标文件
项目编号：ZZ-2026-001
受郑州某学院委托，就郑州某学院智慧校园建设项目（项目编号：ZZ-2026-001）进行公开招标。
采购代理机构：河南某招标有限公司
地址：郑州市金水区某路 1 号
采购预算：187.9558 万元
投标截止时间：2026年11月9日9时30分
开标时间：2026年11月9日9时30分
签订合同后90天（日历日）内完成供货、安装与验收。

三、投标人资格要求
1. 具有独立承担民事责任的能力；
2. 近三年内无重大违法记录；
3. 未被列入失信被执行人名单。
四、获取招标文件

第二章 具体参数要求
1、支持 GB/T 28181 联网，支持 Onvif 接入
★2、支持将搜索记录和点位保存为模板，可点击模板发起文搜（提供第三方检测报告）
3、支持 H.265 编码，支持双码流
★4、硬盘上的视音频信息取出存储后，第三方设备无法播放
第三章 商务要求

评分标准
投标报价
（35%）
35
满足招标文件要求且报价最低者为基准价
商务部分
（25%）
25
业绩每提供一份同类合同得 1 分
技术部分
（40%）
40
★ 参数每不满足一项扣 1 分

第五章 无效投标条款
（一）未按招标文件要求签字盖章的；
（二）投标报价超过最高限价的；
（三）投标文件出现两个以上报价的。
第六章 采购需求

第七章 投标文件格式
一、投标函；
二、开标一览表；
三、技术规格偏离表。
附件：资格承诺声明函
"""


class AnalyzeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = analyze(TENDER)

    def test_project_info(self):
        p = self.data["project"]
        self.assertEqual(p["name"], "郑州某学院智慧校园建设项目")
        self.assertEqual(p["code"], "ZZ-2026-001")
        self.assertEqual(p["buyer"], "郑州某学院")
        self.assertEqual(p["agency"], "河南某招标有限公司")
        self.assertIn("187.9558", p["budget"])
        self.assertEqual(p["deadline"], "2026-11-09 09:30")
        self.assertIn("90", p["duration"])

    def test_stars(self):
        stars = self.data["stars"]
        self.assertEqual(len(stars), 2)
        self.assertIn("搜索记录", stars[0]["text"])
        self.assertTrue(stars[0]["need_proof"])
        self.assertFalse(stars[1]["need_proof"])

    def test_clauses_keep_star_flag(self):
        rows = self.data["clauses"]
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[1]["star"])
        self.assertFalse(rows[0]["star"])

    def test_invalid_and_qualification(self):
        self.assertEqual(len(self.data["qualification"]), 3)
        self.assertEqual(len(self.data["invalid"]), 3)
        self.assertIn("最高限价", self.data["invalid"][1])

    def test_formats(self):
        formats = self.data["formats"]
        self.assertEqual(len(formats), 3)
        self.assertIn("投标函", formats[0])

    def test_scoring(self):
        pairs = [(x["label"], x["weight"], x["score"]) for x in self.data["scoring"]]
        self.assertIn(("投标报价", "35%", "35"), pairs)
        self.assertIn(("技术部分", "40%", "40"), pairs)

    def test_short_text_warns(self):
        data = analyze("随便一段文字，不是招标文件。")
        self.assertTrue(any("正文太短" in w for w in data["warnings"]))


class ReadFileTest(unittest.TestCase):
    def test_read_docx(self):
        doc = Document()
        doc.add_paragraph("项目编号：DOC-1")
        doc.add_paragraph("采购预算：100 万元")
        buf = io.BytesIO()
        doc.save(buf)

        text = read_text("招标文件.docx", buf.getvalue())
        self.assertIn("DOC-1", text)
        self.assertIn("100 万元", text)

    def test_unknown_suffix_rejected(self):
        with self.assertRaises(TenderError):
            read_text("招标文件.xls", b"whatever")

    def test_broken_pdf_rejected(self):
        with self.assertRaises(TenderError):
            read_text("坏文件.pdf", b"not a pdf")


if __name__ == "__main__":
    unittest.main()
