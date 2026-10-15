"""Local, on-demand PDF rendering for the side-by-side reader."""

from collections import OrderedDict
from html import escape
from pathlib import Path
from threading import RLock, Semaphore
from uuid import uuid4

import pymupdf
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.routing import Mount

ASSETS = Path(__file__).parent / "reader"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_documents = OrderedDict()
_lock = RLock()
_render_slots = Semaphore(2)


def _describe(path):
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("请先解锁加密 PDF，再上传。")
        return [{"width": page.rect.width, "height": page.rect.height} for page in doc]


def reader_html(original=None, translated=None):
    """Register only files supplied by the GUI, never accept paths from HTTP."""
    token = "empty"
    if original:
        source = Path(original).resolve()
        if source.suffix.lower() != ".pdf":
            return '<div class="reader-note">Word 文件将在翻译后显示 PDF 对照预览。</div>'
        try:
            target = Path(translated).resolve() if translated else None
            entry = {
                "original": source,
                "translated": target,
                "name": source.name,
                "pages": {"original": _describe(source)},
            }
            if target:
                entry["pages"]["translated"] = _describe(target)
                dual = target.with_name(target.name.removesuffix("-mono.pdf") + "-dual.pdf")
                entry["dual"] = dual if dual.is_file() else None
        except (RuntimeError, ValueError, OSError) as exc:
            return f'<div class="reader-note">无法预览 PDF：{escape(str(exc))}</div>'
        token = uuid4().hex
        with _lock:
            _documents[token] = entry
            while len(_documents) > 128:
                _documents.popitem(last=False)
    return (
        f'<iframe src="compare/view/{token}" title="原文与译文对照阅读器" '
        'style="width:100%;height:80vh;min-height:640px;border:0;border-radius:12px" '
        'allow="fullscreen"></iframe>'
    )


def latest_pair(directory):
    directory = Path(directory)
    for target in sorted(directory.glob("*-mono.pdf"), key=lambda p: p.stat().st_mtime, reverse=True):
        source = target.with_name(target.name.removesuffix("-mono.pdf") + ".pdf")
        if source.exists():
            return source, target
    return None, None


def _entry(token):
    with _lock:
        entry = _documents.get(token)
    if entry is None:
        raise HTTPException(404, "预览已失效，请重新上传文件。")
    return entry


@app.get("/view/{token}")
def view(token: str):
    return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/assets/{name}")
def asset(name: str):
    if name not in {"reader.js", "theme.css"}:
        raise HTTPException(404)
    return FileResponse(ASSETS / name, headers={"Cache-Control": "no-cache"})


@app.get("/document/{token}")
def document(token: str):
    entry = _entry(token)
    return {
        "name": entry["name"], "pages": entry["pages"],
        "downloads": {"translated": bool(entry["translated"]), "dual": bool(entry.get("dual"))},
    }


@app.get("/download/{token}/{side}")
def download(token: str, side: str):
    entry = _entry(token)
    if side not in {"translated", "dual"} or not entry.get(side):
        raise HTTPException(404, "翻译结果不可用。")
    path = entry[side]
    return FileResponse(path, filename=path.name)


def _page(token, side, page):
    entry = _entry(token)
    if side not in {"original", "translated"} or side not in entry["pages"]:
        raise HTTPException(404, "该文档尚无译文。")
    if not 1 <= page <= len(entry["pages"][side]):
        raise HTTPException(404, "页码超出范围。")
    return entry[side]


@app.get("/page/{token}/{side}/{page}.png")
def page_image(token: str, side: str, page: int):
    path = _page(token, side, page)
    with _render_slots, pymupdf.open(path) as doc:
        data = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False).tobytes("png")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/text/{token}/{side}/{page}")
def page_text(token: str, side: str, page: int):
    path = _page(token, side, page)
    with _render_slots, pymupdf.open(path) as doc:
        spans = []
        for block in doc[page - 1].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    spans.append({"text": span["text"], "bbox": span["bbox"], "size": span["size"]})
    return {"spans": spans}


def launch_kwargs():
    return {"app_kwargs": {"routes": [Mount("/compare", app=app)]}}
