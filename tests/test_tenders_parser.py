"""ccgp 中标公告解析器单测（不联网，内联 HTML 片段）。"""
from __future__ import annotations

from scraper.collect_tenders import parse_detail

HTML = """
<html><body>
<table><tr><td>采购人信息 名 称：某县行政审批服务管理局</td></tr></table>
<table><tr><td>采购代理机构信息 名 称：某建设项目管理有限公司</td></tr></table>
<p>总中标金额</p><table><tr><td>￥364.000000 万元（人民币）</td></tr></table>
<table>
  <tr><td>序号</td><td>标项名称</td><td>标的名称</td><td>品牌</td><td>规格型号</td><td>数量</td><td>单价</td></tr>
  <tr><td>1</td><td>某项目</td><td>交换机</td><td>信锐</td><td>NS5320-54X-SI-48T</td><td>20</td><td>9250</td></tr>
  <tr><td>2</td><td>某项目</td><td>※综合安防管理平台</td><td>大华</td><td>DH-ICC-B8900-U-PRO</td><td>1</td><td>155000</td></tr>
  <tr><td>3</td><td>某项目</td><td>网络摄像机</td><td>大华</td><td>DH-IPC-HFW2649M-A-LED</td><td>40</td><td>860</td></tr>
</table>
</body></html>
"""

URL = "http://www.ccgp.gov.cn/cggg/dfgg/zbgg/202610/t20261029_27585688.htm"


def test_only_dahua_rows_kept():
    t = parse_detail(URL, "某政务服务中心安防采购项目结果公告", "2026-10-29", HTML)
    models = [it["model"] for it in t["items"]]
    assert models == ["DH-ICC-B8900-U-PRO", "DH-IPC-HFW2649M-A-LED"]


def test_platform_row_fields():
    t = parse_detail(URL, "某政务服务中心安防采购项目结果公告", "2026-10-29", HTML)
    plat = t["items"][0]
    assert plat["category"] == "平台"
    assert plat["qty"] == 1
    assert plat["unit_price"] == 155000


def test_meta_fields():
    t = parse_detail(URL, "某政务服务中心安防采购项目结果公告", "2026-10-29", HTML)
    assert t["tender_id"] == "t20261029_27585688"
    assert t["purchaser"] == "某县行政审批服务管理局"
    assert t["agency"] == "某建设项目管理有限公司"
    assert t["amount"] == 3_640_000  # 万元折算为元
    assert t["industry"] == "政务"


CONCAT_HTML = """
<html><body><p>四、主要标的信息 货物类</p>
<p>名称：智能综合管理平台品牌（如有）：大华规格型号：DH-ICC-B8900-U-PRO数量：1个单价：2700.00</p>
<p>名称：24口千兆交换机品牌（如有）：华为规格型号：S5735R-24T4SQA2数量：3台单价：3826.00元</p>
</body></html>
"""


def test_concatenated_block_format():
    """公告常把名称/品牌/型号/数量/单价连写在同一行。"""
    t = parse_detail(URL, "某医院智慧安消平台项目中标公告", "2026-02-26", CONCAT_HTML)
    assert len(t["items"]) == 1
    it = t["items"][0]
    assert it["model"] == "DH-ICC-B8900-U-PRO"
    assert it["qty"] == 1
    assert it["unit_price"] == 2700
