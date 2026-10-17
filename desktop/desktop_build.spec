# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把桌面版打包成免安装的 exe 文件夹（onedir）。

构建（在 PDFMathTranslate\\.venv 中执行）：
    python -m PyInstaller --noconfirm --clean desktop_build.spec

产物：dist\\PDF翻译工具\\PDF翻译工具.exe

说明：
- gradio / pymupdf / babeldoc 没有官方 PyInstaller hook，这里手动收集数据文件与子模块；
  onnxruntime、cv2、webview、clr 等由 pyinstaller-hooks-contrib 处理。
- 排除 gradio\\templates\\node（SSR 专用，本应用强制客户端渲染）和所有 source map，约省 100 MB。
- 模型与字体不入包：babeldoc 首次运行时下载到 %USERPROFILE%\\.cache\\babeldoc。
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPEC))
SRC = os.path.dirname(ROOT)

sys.path.insert(0, ROOT)
from desktop_version import PUBLISHER, PUBLISHER_URL  # noqa: E402
from desktop_version import VERSION as APP_VERSION  # noqa: E402

# 引擎版本：pdf2zh 直接读模块常量，babeldoc 读包元数据（都不触发重依赖导入）
import pdf2zh  # noqa: E402

ENGINE_VERSION = pdf2zh.__version__
try:
    from importlib.metadata import version as _pkg_version

    BABELDOC_VERSION = _pkg_version("babeldoc")
except Exception:
    BABELDOC_VERSION = ""


def _version_tuple(text):
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


VERSION_TUPLE = _version_tuple(APP_VERSION)

# 直接用 PyInstaller 的版本结构（避免手写文本格式易错）
from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=VERSION_TUPLE,
        prodvers=VERSION_TUPLE,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "080404B0",  # 中文（简体）+ Unicode
                    [
                        StringStruct(
                            "Comments",
                            f"引擎 pdf2zh {ENGINE_VERSION}"
                            + (f" · BabelDOC {BABELDOC_VERSION}" if BABELDOC_VERSION else "")
                            + f"；源码：{PUBLISHER_URL}",
                        ),
                        StringStruct("CompanyName", PUBLISHER),
                        StringStruct("FileDescription", f"PDF 翻译工具（{PUBLISHER} 发行版）"),
                        StringStruct("FileVersion", f"{APP_VERSION}.0"),
                        StringStruct("InternalName", "PDF翻译工具"),
                        StringStruct(
                            "LegalCopyright", f"AGPL-3.0 · {PUBLISHER}（基于 PDFMathTranslate 项目）"
                        ),
                        StringStruct("OriginalFilename", "PDF翻译工具.exe"),
                        StringStruct("ProductName", "PDF 翻译工具"),
                        StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [2052, 1200])]),
    ],
)


def _filter(entries, drop_tokens=(), drop_suffixes=()):
    kept = []
    for source, target in entries:
        if any(token in source for token in drop_tokens):
            continue
        if any(source.endswith(suffix) for suffix in drop_suffixes):
            continue
        kept.append((source, target))
    return kept


datas = [(os.path.join(ROOT, "desktop.ico"), ".")]
datas += collect_data_files("pdf2zh")  # reader 前端页面、upload.css
datas += collect_data_files("safehttpx")  # version.txt（导入时读取）
datas += collect_data_files("groovy")  # version.txt（导入时读取）
datas += collect_data_files("rapidocr_onnxruntime")  # config.yaml + 默认 OCR 模型
datas += _filter(
    # 需要 .py 源码：gradio 导入组件类时会读源码生成 .pyi 类型提示
    collect_data_files("gradio", include_py_files=True),
    drop_tokens=(os.sep + "templates" + os.sep + "node" + os.sep,),
    drop_suffixes=(".map",),
)
datas += _filter(collect_data_files("gradio_pdf", include_py_files=True), drop_suffixes=(".map",))
datas += _filter(collect_data_files("gradio_client"), drop_suffixes=(".map",))
datas += collect_data_files("pdfminer")  # CID 字体映射表（cmap）
datas += collect_data_files("babeldoc")

binaries = collect_dynamic_libs("pymupdf")

hiddenimports = []
for package in ("pdf2zh", "babeldoc", "gradio", "gradio_client", "gradio_pdf", "peewee", "playhouse"):
    hiddenimports += collect_submodules(package)
hiddenimports += ["webview.platforms.edgechromium", "clr", "clr_loader"]

a = Analysis(
    [os.path.join(ROOT, "desktop_app.py")],
    pathex=[SRC],  # pdf2zh 是 editable 安装，显式指向源码目录
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["IPython", "notebook", "pytest", "matplotlib"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF翻译工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "desktop.ico"),
    version=VERSION_INFO,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PDF翻译工具",
)

# 在产物目录里放一份给使用者的说明（onedir 模式下只有 exe 在根目录，其余都在 _internal）
_readme = os.path.join(DISTPATH, "PDF翻译工具", "使用说明.txt")
with open(_readme, "w", encoding="utf-8") as fh:
    fh.write(
        f"""PDF 翻译工具（免安装版）v{APP_VERSION}
=====================================
发行：{PUBLISHER}
源码：{PUBLISHER_URL}
引擎：pdf2zh {ENGINE_VERSION}"""
        + (f" · BabelDOC {BABELDOC_VERSION}" if BABELDOC_VERSION else "")
        + f"""（AGPL-3.0，基于 PDFMathTranslate 项目）

双击「PDF翻译工具.exe」启动；首次启动加载模型需要几秒。
第一次翻译时程序会自动下载版面分析模型和字体（约 160 MB，需联网），
缓存在 %USERPROFILE%\\.cache\\babeldoc；与源码版共用同一份缓存。

- 每次翻译单独保存在 data/translations/<任务ID>/，同名文档也不会互相覆盖
- 默认 data 在本文件夹；安装版或本文件夹不可写时，使用 %LOCALAPPDATA%\\PDFMathTranslate\\data
- 可用 PDF2ZH_DATA_DIR 指定 data 根目录，已有设置会保留
- "最近译文"显示历史列表，点击条目可继续阅读；窄窗口中可折叠展开
- 旧 pdf2zh_files 仍可发现：项目根目录、PDFMathTranslate 源码目录和
  %LOCALAPPDATA%\\PDFMathTranslate 下的旧目录；旧 PDF 留在原位，不自动搬移
- 不同目录的同名记录会保留并标注来源；只去掉相同实际路径的重复记录
- 本次只整理翻译运行数据；日志、配置和模型/翻译缓存保留原位置
- 界面里点"下载译文 PDF / 双语 PDF"会弹出 Windows"另存为"对话框
- 关闭窗口即退出程序；翻译过程中关窗会中止本次翻译
- 重复双击会把已打开的窗口带到前台（最小化时会自动还原）
- 运行日志：desktop.log（出问题时先看这里）

拷贝到别的电脑：
- 必须整个文件夹一起拷贝（exe 依赖同目录的 _internal 文件夹），不要只拷 exe
- 要求 Windows 10/11（64 位）；需要 WebView2 运行时（Win11 与较新的 Win10 自带）。
  若系统没有装，程序会自动改用 Edge 窗口打开；也可到微软官网安装后恢复原生窗口：
  https://developer.microsoft.com/microsoft-edge/webview2/
- 需要联网下载翻译模型；使用在线翻译服务时按各服务要求联网并配置密钥
"""
    )
print(f"README written to {_readme}")
