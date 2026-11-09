"""MCP Server 薄包装测试：工具清单 / 计算透传 / 产品检索 / 渲染落盘。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server
from app.builder import build_spec
from app.calc import calc_bandwidth, calc_storage
from app.models import Requirement
from config.settings import DB_PATH

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


EXPORT_NAMES = {"calc_storage", "calc_bandwidth", "search_kb", "compare_pair",
                "render_topology", "gen_docx"}


class McpSchemaTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_list_matches_exports(self):
        tools = await mcp_server.server.list_tools()
        self.assertEqual({t.name for t in tools}, EXPORT_NAMES)
        storage_tool = next(t for t in tools if t.name == "calc_storage")
        self.assertIn("requirements", storage_tool.input_schema["properties"])

    async def test_calc_storage_goes_through_calc_module(self):
        result = await mcp_server.server.call_tool(
            "calc_storage",
            {"requirements": [{"lanes": 100, "bitrate_mbps": 4.0}], "retention_days": 30},
        )
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0].text)
        expected = calc_storage([Requirement(target="点位1", intent="视频监控", lanes=100, bitrate_mbps=4.0)], 30)
        self.assertEqual(payload, expected)


@unittest.skipUnless(DB_PATH.exists(), "知识库不存在，跳过 MCP 冒烟测试")
class McpToolTest(unittest.TestCase):
    def test_calc_bandwidth_matches_calc_module(self):
        reqs = [mcp_server.RequirementIn(lanes=100, bitrate_mbps=4.0)]
        self.assertEqual(mcp_server.calc_bandwidth(reqs), calc_bandwidth(
            [Requirement(target="点位1", intent="视频监控", lanes=100, bitrate_mbps=4.0)]
        ))

    def test_search_kb_returns_real_models(self):
        rows = mcp_server.search_kb(keyword="400万", class_name="网络摄像机", limit=3)
        self.assertTrue(rows)
        self.assertEqual(len(rows), 3)
        self.assertIn("model", rows[0])

    def test_compare_pair_official_models(self):
        from app.compare import REPRESENTATIVES
        d_model, h_model = REPRESENTATIVES["NVR"]
        res = mcp_server.compare_pair("NVR", d_model, h_model)
        self.assertEqual(res["category"], "NVR")
        self.assertGreater(res["coverage"]["aligned"], 0)

    def test_render_and_docx_write_files(self):
        spec = build_spec(TEXT)
        with tempfile.TemporaryDirectory() as tmp, patch.object(mcp_server, "EXPORT_DIR", Path(tmp)):
            png = mcp_server.render_topology(spec.model_dump(), "mcp-topo")["path"]
            docx = mcp_server.gen_docx(spec.model_dump(), "mcp-plan", topology_png=png)["path"]
            self.assertTrue(Path(png).is_file())
            self.assertTrue(Path(docx).is_file())


if __name__ == "__main__":
    unittest.main()
