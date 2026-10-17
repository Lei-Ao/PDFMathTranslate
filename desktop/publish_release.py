"""把当前安装包发布到 GitHub Releases。

用法（在项目根目录执行）：
    .\\PDFMathTranslate\\.venv\\Scripts\\python.exe publish_release.py
    .\\PDFMathTranslate\\.venv\\Scripts\\python.exe publish_release.py --notes-file 说明.md
    .\\PDFMathTranslate\\.venv\\Scripts\\python.exe publish_release.py --tag v1.0.1 --repo Lei-Ao/PDFMathTranslate

要点：
- 版本号取自 desktop_version.py 的 VERSION；附件文件名用 ASCII
  （GitHub 会把非 ASCII 文件名替换成"."，所以本地中文名在远端转为 PDFTranslate-Setup-<版本>.exe）。
- 凭据取自 git 的凭据管理器（git credential fill），不会写进任何文件，也不打印。
- 默认直连、不走系统代理；若你的网络需要代理访问 GitHub，请先启动代理再运行。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from desktop_version import PUBLISHER_URL, VERSION  # noqa: E402

DEFAULT_REPO = "Lei-Ao/PDFMathTranslate"
API = "https://api.github.com"

NOTES_TEMPLATE = """## {app_name} v{version} · 桌面发行版

基于 [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)（AGPL-3.0）的 Windows 桌面版。

### 安装

1. 下载下方附件 `{asset_name}`（约 {size_mb:.0f} MB；在 Windows 上安装后的程序名为"PDF 翻译工具"）
2. 双击安装。默认装到 `%LOCALAPPDATA%\\Programs\\PDF 翻译工具`，不需要管理员权限；卸载在"设置 → 应用"里完成
3. 首次翻译时自动下载版面分析模型与字体（约 160 MB，需联网）
4. 从浏览器下载的安装包首次运行可能出现"Windows 已保护你的电脑"（未做代码签名），点"更多信息 → 仍要运行"即可

### 系统要求

- Windows 10 / 11（64 位）
- 需要 WebView2 运行时：Win11 与较新的 Win10 自带；缺失时自动改用 Edge 窗口打开
- 翻译与首次模型下载需要联网

### 许可

基于 PDFMathTranslate（作者 Byaidu）二次开发，遵循 AGPL-3.0。源码见本仓库。
"""


def get_token() -> str | None:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return None


class ProgressReader:
    """给 requests 用的上传进度包装：每 10% 打一行。"""

    def __init__(self, path: Path, chunk: int = 1024 * 1024):
        self._handle = path.open("rb")
        self._total = path.stat().st_size
        self._chunk = chunk
        self._sent = 0
        self._next_mark = self._total / 10

    def __iter__(self):
        return self

    def __next__(self):
        data = self._handle.read(self._chunk)
        if not data:
            self._handle.close()
            raise StopIteration
        self._sent += len(data)
        if self._sent >= self._next_mark:
            pct = self._sent / self._total * 100
            print(f"  上传中 {pct:5.1f}%（{self._sent / 1048576:.0f}/{self._total / 1048576:.0f} MB）", flush=True)
            self._next_mark = self._sent + self._total / 10
        return data

    def __len__(self):
        return self._total


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 PDF 翻译工具到 GitHub Releases")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"仓库（默认 {DEFAULT_REPO}）")
    parser.add_argument("--tag", default=f"v{VERSION}", help=f"标签（默认 v{VERSION}）")
    parser.add_argument("--notes-file", help="发布说明 Markdown 文件；默认自动生成")
    parser.add_argument("--target", help="标签指向的提交；默认用远程默认分支最新提交")
    args = parser.parse_args()

    local_installer = ROOT / f"PDF翻译工具安装包-{VERSION}.exe"
    if not local_installer.is_file():
        print(f"找不到安装包：{local_installer}\n请先用 PyInstaller + Inno Setup 打包。")
        return 1
    asset_name = f"PDFTranslate-Setup-{VERSION}.exe"  # ASCII：GitHub 不接受非 ASCII 文件名
    size_mb = local_installer.stat().st_size / 1048576

    token = get_token()
    if not token:
        print("未能从 git 凭据管理器取到 GitHub 凭据（可先执行一次 git push 以完成登录）。")
        return 1

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    session.trust_env = False  # 默认直连；如需代理，请启动代理并删掉这一行

    notes = Path(args.notes_file).read_text(encoding="utf-8") if args.notes_file else NOTES_TEMPLATE.format(
        app_name="PDF 翻译工具", version=VERSION, asset_name=asset_name, size_mb=size_mb,
    )

    print(f"仓库 {args.repo}｜标签 {args.tag}｜附件 {asset_name}（{size_mb:.1f} MB）")
    existing = session.get(f"{API}/repos/{args.repo}/releases/tags/{args.tag}", timeout=60)
    if existing.status_code == 200:
        release = existing.json()
        print(f"该标签的 Release 已存在，直接复用它：{release['html_url']}")
    else:
        payload = {
            "tag_name": args.tag,
            "name": f"PDF 翻译工具 {args.tag}",
            "body": notes,
            "draft": False,
            "prerelease": False,
            "make_latest": "true",
        }
        if args.target:
            payload["target_commitish"] = args.target
        resp = session.post(f"{API}/repos/{args.repo}/releases", json=payload, timeout=120)
        if resp.status_code not in (200, 201):
            print("创建 Release 失败：", resp.status_code, resp.text[:300])
            return 1
        release = resp.json()
        print(f"Release 已创建：{release['html_url']}")

    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            print(f"附件 {asset_name} 已存在（{asset['size'] / 1048576:.1f} MB），跳过上传。")
            return 0

    print("开始上传（网络较慢时可能需要十几分钟）…")
    started = time.time()
    upload_url = release["upload_url"].split("{")[0]
    resp = session.post(upload_url, params={"name": asset_name}, data=ProgressReader(local_installer),
                        headers={"Content-Type": "application/octet-stream"}, timeout=3600)
    if resp.status_code not in (200, 201):
        print("上传失败：", resp.status_code, resp.text[:300])
        return 1
    asset = resp.json()
    print(f"上传完成，用时 {time.time() - started:.0f} 秒")
    print(f"  附件：{asset['name']}｜{asset['size'] / 1048576:.1f} MB｜{asset['state']}")
    print(f"  直链：{asset['browser_download_url']}")
    print(f"  页面：{release['html_url']}")
    print(f"源码来源：{PUBLISHER_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
