"""PDF 导出：把已生成的 Word 方案转成 PDF。

Windows 上走 Microsoft Word COM（与 docx 排版完全一致）；未安装 Word 或
非 Windows 环境时明确报错，不静默产出损坏文件。
"""
from __future__ import annotations

import sys
from pathlib import Path


def docx_to_pdf(docx_path: Path | str, pdf_path: Path | str) -> Path:
    src = Path(docx_path)
    out = Path(pdf_path)
    if not src.is_file():
        raise FileNotFoundError(f"Word 文件不存在：{src}")
    if sys.platform != "win32":
        raise RuntimeError("当前系统不支持 Word 转 PDF（需要 Windows + Microsoft Word）")
    try:
        import win32com.client  # noqa: PLC0415 —— 仅 Windows 需要
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，无法调用 Word 转 PDF") from exc

    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(src.resolve()), ReadOnly=True)
        try:
            doc.SaveAs(str(out.resolve()), FileFormat=17)   # 17 = wdFormatPDF
        finally:
            doc.Close(False)
    except Exception as exc:  # noqa: BLE001 —— COM 异常统一转成可读错误
        raise RuntimeError(f"Word 转 PDF 失败：{exc}") from exc
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:  # noqa: BLE001
                pass
    if not out.is_file():
        raise RuntimeError("Word 未产出 PDF 文件")
    return out
