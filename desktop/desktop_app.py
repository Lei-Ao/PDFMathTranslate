"""PDF 翻译工具 · 桌面版启动器。

把 PDFMathTranslate 的本地界面包成桌面软件：在同一进程内启动界面服务、
打开原生窗口，关闭窗口时整个程序退出。不修改 PDFMathTranslate 仓库内的任何代码。

用法（平时由「启动桌面版.vbs」或桌面快捷方式调用）：
    pythonw desktop_app.py                 原生窗口（默认，WebView2）
    pythonw desktop_app.py --app-window    Edge/Chrome 应用窗口（兼容模式）

环境变量：
    PDF2ZH_DESKTOP_DEBUG=1   打开开发者工具与右键菜单（调试用）
"""

from __future__ import annotations

import argparse
import ctypes
import html
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from ctypes import wintypes
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _local_app_data():
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDFMathTranslate"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _app_home():
    """数据目录（日志、翻译结果、缓存）。

    源码版用项目文件夹。打包版默认用 exe 所在文件夹，这两种情况改用
    %LOCALAPPDATA%\\PDFMathTranslate：目录不可写；或目录里有 installed.marker
    （安装包创建），这样卸载程序不会连带删掉翻译结果。
    """
    if not FROZEN:
        return Path(__file__).resolve().parent
    home = Path(sys.executable).resolve().parent
    if (home / "installed.marker").is_file():
        return _local_app_data()
    try:
        probe = home / ".write-test"
        probe.touch()
        probe.unlink()
        return home
    except OSError:
        return _local_app_data()


ROOT = _app_home()
LOG_FILE = ROOT / "desktop.log"
ICON_FILE = (BUNDLE_DIR if FROZEN else ROOT) / "desktop.ico"
WEBVIEW_PROFILE = ROOT / ".desktop-webview"
BROWSER_PROFILE = ROOT / ".desktop-browser"
APP_TITLE = "PDF 翻译工具"
STARTUP_TIMEOUT = 300  # 秒；首次启动需要下载模型时会慢一些
LOG_MAX_BYTES = 1_000_000

MUTEX_NAME = "Local\\PDFMathTranslateDesktop" + os.environ.get("PDF2ZH_DESKTOP_INSTANCE", "")
ERROR_ALREADY_EXISTS = 183
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40
CREATE_NO_WINDOW = 0x08000000

WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

_log_stream = None


# ---------------------------------------------------------------- 日志

def log(message):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _prepare_log():
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            LOG_FILE.unlink()
    except OSError:
        pass


def _redirect_streams():
    """pythonw 下没有控制台，把 print 与日志统一写进 desktop.log。"""
    global _log_stream
    try:
        _log_stream = LOG_FILE.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = _log_stream
    sys.stderr = _log_stream


def _log_tail(lines=15):
    try:
        return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# ---------------------------------------------------------------- Windows 辅助

def message_box(text, flags=MB_ICONERROR):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, flags | 0x40000)
    except Exception:
        pass


def acquire_single_instance():
    """已有实例在运行时返回 False。"""
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if handle and ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True


def focus_existing_window():
    """把已运行实例的窗口带到前台（最小化时先还原）；找到返回 True。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]

        handles = []
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buffer, 256)
            if buffer.value == APP_TITLE:
                handles.append(hwnd)
            return True

        user32.EnumWindows(callback(visit), 0)
        if not handles:
            return False
        hwnd = handles[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def webview2_available():
    """系统是否安装了 WebView2 运行时（原生窗口的依赖）。

    PDF2ZH_DESKTOP_NO_WEBVIEW2=1 可用于调试"未安装"时的降级路径。
    """
    if os.environ.get("PDF2ZH_DESKTOP_NO_WEBVIEW2") == "1":
        return False
    import winreg

    locations = (
        (winreg.HKEY_LOCAL_MACHINE,
         rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        (winreg.HKEY_LOCAL_MACHINE,
         rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
        (winreg.HKEY_CURRENT_USER,
         rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"),
    )
    for root, path in locations:
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
            if version and version != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def app_version():
    """桌面发行版本号（desktop_version.py），与 pdf2zh 引擎版本相互独立。"""
    try:
        from desktop_version import VERSION

        return VERSION
    except Exception:
        return "unknown"


def engine_version():
    try:
        from pdf2zh import __version__

        return __version__
    except Exception:
        return "unknown"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def preferred_window_size(offset=0):
    """按屏幕大小取一个合适的窗口尺寸（缩小屏幕或高 DPI 缩放时不会被截断）。

    offset 让同一进程里后开的窗口稍微错开，不会正好压住前一个窗口。
    """
    try:
        user32 = ctypes.windll.user32
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)
    except Exception:
        screen_width, screen_height = 1440, 900
    width = min(1440, max(1024, screen_width - 160))
    height = min(920, max(680, screen_height - 140))
    x = max(0, min((screen_width - width) // 2 + offset, screen_width - width))
    y = max(0, min((screen_height - height) // 2 + offset, screen_height - height))
    return width, height, x, y


# ---------------------------------------------------------------- 界面服务（进程内）

def _clean_proxy_env():
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"


def _export_release_env():
    """把发行信息传给界面：gui.py 读取这些变量显示"发行版本"一行，未设置则不显示。"""
    try:
        from desktop_version import PUBLISHER, PUBLISHER_URL, VERSION
    except Exception:
        return
    os.environ["PDF2ZH_RELEASE_VERSION"] = VERSION
    os.environ["PDF2ZH_RELEASE_PUBLISHER"] = PUBLISHER
    os.environ["PDF2ZH_RELEASE_URL"] = PUBLISHER_URL


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_ready(port):
    try:
        with _opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def start_gui_server(port):
    """在后台线程里启动 Gradio 界面服务，立即返回 holder 字典。

    holder["demo"] 是 Gradio 应用对象（用于退出时关闭），
    holder["error"] 保存启动异常，holder["thread"] 是后台线程。
    """
    _clean_proxy_env()
    _export_release_env()
    os.environ.setdefault("PDF2ZH_DATA_DIR", str(ROOT / "data"))
    import gradio as gr

    holder = {}
    original_launch = gr.Blocks.launch

    def launch_on_localhost(self, *args, **kwargs):
        kwargs.update(
            server_name="127.0.0.1", share=False, inbrowser=False, prevent_thread_lock=True,
        )
        if FROZEN:
            # 打包版不含 SSR 的 node 运行时，强制客户端渲染
            kwargs["ssr_mode"] = False
        holder["demo"] = self
        return original_launch(self, *args, **kwargs)

    def serve():
        gr.Blocks.launch = launch_on_localhost
        try:
            from pdf2zh.pdf2zh import main as pdf2zh_main

            pdf2zh_main(["-i", "--serverport", str(port)])
        except Exception:
            holder["error"] = traceback.format_exc()
            log("界面服务启动异常：\n" + holder["error"])
        finally:
            gr.Blocks.launch = original_launch

    worker = threading.Thread(target=serve, name="pdf2zh-gui", daemon=True)
    holder["thread"] = worker
    worker.start()
    return holder


def wait_ready(port, holder, timeout=STARTUP_TIMEOUT):
    """返回 None 表示就绪；否则返回失败原因字符串。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe_ready(port):
            return None
        if holder.get("error"):
            return "error"
        thread = holder.get("thread")
        if thread is not None and not thread.is_alive():
            return "no-server"
        time.sleep(0.5)
    return "timeout"


def close_gui_server(holder):
    demo = holder.get("demo")
    if demo is None:
        return
    try:
        demo.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 页面

SPLASH_HTML = """<!doctype html><meta charset="utf-8">
<style>
  html, body { height: 100%; margin: 0; }
  body { display: grid; place-items: center; background: #fcfdfa; color: #243b39;
         font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
  .box { text-align: center; }
  .ring { width: 42px; height: 42px; margin: 0 auto 22px; border-radius: 50%;
          border: 3px solid #dfe9e2; border-top-color: #217965; animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  h1 { font-size: 17px; font-weight: 600; margin: 0 0 8px; }
  p { font-size: 12px; color: #7a8580; margin: 0; line-height: 1.8; }
</style>
<div class="box">
  <div class="ring"></div>
  <h1>正在启动 PDF 翻译工具</h1>
  <p>首次启动需要加载翻译模型，请稍候片刻。</p>
</div>"""


def failure_html(status, holder=None):
    reason = {
        "timeout": f"等待 {STARTUP_TIMEOUT} 秒后界面服务仍未就绪。",
        "error": "界面服务启动时出现异常。",
        "no-server": "界面服务线程意外退出。",
    }.get(status, str(status))
    detail = _log_tail(15)
    error_text = (holder or {}).get("error")
    if error_text:
        detail = error_text.strip() + "\n\n" + detail
    return f"""<!doctype html><meta charset="utf-8">
<style>
  body {{ background: #fcfdfa; color: #243b39; font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
         padding: 48px 56px; }}
  h1 {{ font-size: 18px; color: #b3452f; }}
  p, li {{ font-size: 13px; color: #4a5a51; line-height: 1.9; }}
  pre {{ background: #f3f6f1; border: 1px solid #e4e8e2; border-radius: 8px; padding: 14px;
        font-size: 11px; color: #5a6a60; overflow: auto; max-height: 320px; white-space: pre-wrap; }}
</style>
<h1>启动失败</h1>
<p>{html.escape(reason)}</p>
<ul>
  <li>可先关闭本窗口，然后重新双击桌面图标再试一次。</li>
  <li>详细日志见：<code>{html.escape(str(LOG_FILE))}</code></li>
</ul>
<pre>{html.escape(detail)}</pre>"""


# ---------------------------------------------------------------- 原生窗口

class WindowBridge:
    """界面上的"新建窗口"按钮通过它再开一个原生窗口。

    pywebview 会把公开方法暴露成 window.pywebview.api.open_window，所以 WebView2
    窗口里也能用；浏览器版和 Edge 应用窗口模式没有这个桥，页面自己退回 window.open。
    多个窗口连的是同一个本地服务，但各自是独立会话，于是可以一边读已翻译的文献、
    一边翻译另一篇。
    """

    def __init__(self, port):
        self._port = port
        self._count = 0
        self._lock = threading.Lock()

    def open_window(self, path="/"):
        """path 只接受本站路径，避免把窗口导航到外部地址。"""
        target = self._target_url(path)
        if target is None:
            log(f"忽略了非法的新窗口地址：{path!r}")
            return False
        # pywebview 只允许从非主线程创建窗口（js 调用本来就跑在请求线程上，
        # 这里再开一个线程是为了不卡住页面上的这次调用）。
        threading.Thread(
            target=self._create, args=(target,), name="pdf2zh-window", daemon=True,
        ).start()
        return True

    def _target_url(self, path):
        if not path:
            path = "/"
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            return None
        return f"http://127.0.0.1:{self._port}{path}"

    def _create(self, url):
        import webview

        with self._lock:
            self._count += 1
            offset = 32 * ((self._count - 1) % 5 + 1)
        width, height, x, y = preferred_window_size(offset)
        try:
            webview.create_window(
                APP_TITLE,
                url=url,
                width=width,
                height=height,
                x=x,
                y=y,
                min_size=(1024, 680),
                background_color="#fcfdfa",
                text_select=True,
                js_api=WindowBridge(self._port),
            )
            log(f"已打开新窗口：{url}")
        except Exception:
            log("打开新窗口失败：\n" + traceback.format_exc())


def run_native_window(port, holder):
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    debug = os.environ.get("PDF2ZH_DESKTOP_DEBUG") == "1"
    width, height, x, y = preferred_window_size()
    window = webview.create_window(
        APP_TITLE,
        html=SPLASH_HTML,
        width=width,
        height=height,
        x=x,
        y=y,
        min_size=(1024, 680),
        background_color="#fcfdfa",
        text_select=True,
        js_api=WindowBridge(port),
    )

    def drive():
        status = wait_ready(port, holder)
        if status is None:
            log("服务就绪，加载界面")
            window.load_url(f"http://127.0.0.1:{port}/")
        else:
            log(f"启动失败：{status}")
            window.load_html(failure_html(status, holder))

    webview.start(
        drive,
        gui="edgechromium",
        private_mode=False,
        storage_path=str(WEBVIEW_PROFILE),
        debug=debug,
        icon=str(ICON_FILE) if ICON_FILE.is_file() else None,
    )
    log("窗口已关闭")


def run_native_window_with_fallback(port, holder):
    """优先原生窗口；系统缺少 WebView2 运行时则自动改用 Edge/Chrome 应用窗口。"""
    if webview2_available():
        run_native_window(port, holder)
        return

    log("未检测到 WebView2 运行时")
    fallback = find_browser()
    if fallback is None:
        log("也没有找到 Edge/Chrome，无法打开界面")
        message_box(
            "未检测到 WebView2 运行时，也没有找到 Microsoft Edge 或 Google Chrome。\n\n"
            "请安装微软官方的 WebView2 运行时后重试：\n" + WEBVIEW2_URL
        )
        return

    log(f"自动改用应用窗口模式：{fallback.name}")
    run_app_window(port, holder, fallback)


# ---------------------------------------------------------------- 浏览器应用窗口

def find_browser():
    candidates = [
        os.environ.get("PDF2ZH_BROWSER"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    for exe in ("msedge", "chrome"):
        try:
            found = subprocess.run(
                ["where", exe], capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW, check=False,
            )
            for line in found.stdout.splitlines():
                if line.strip() and Path(line.strip()).is_file():
                    return Path(line.strip())
        except Exception:
            pass
    return None


def _wait_with_splash(port, holder):
    """等待服务就绪；有 tkinter 时显示一个小提示窗。返回是否就绪。"""
    try:
        import tkinter as tk
    except Exception:
        return wait_ready(port, holder) is None

    root = tk.Tk()
    root.title(APP_TITLE)
    root.configure(bg="#fcfdfa")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    width, height = 400, 160
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")
    tk.Label(root, text="正在启动 PDF 翻译工具", bg="#fcfdfa", fg="#243b39",
             font=("Microsoft YaHei", 13, "bold")).pack(pady=(36, 6))
    tk.Label(root, text="首次启动需要加载翻译模型，请稍候片刻。", bg="#fcfdfa",
             fg="#7a8580", font=("Microsoft YaHei", 9)).pack()

    state = {"ready": False, "status": "timeout", "cancelled": False}
    deadline = time.monotonic() + STARTUP_TIMEOUT

    def on_close():
        state["cancelled"] = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def poll():
        if state["cancelled"]:
            return
        if probe_ready(port):
            state["ready"] = True
        elif holder.get("error"):
            state["status"] = "error"
        elif time.monotonic() > deadline:
            state["status"] = "timeout"
        else:
            root.after(400, poll)
            return
        root.destroy()

    root.after(400, poll)
    root.mainloop()
    if state["cancelled"]:
        return "cancelled"
    return state["ready"]


def _browser_pids():
    """返回使用本应用专用配置目录的浏览器进程号列表；查询失败返回 None。"""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{BROWSER_PROFILE.name}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        found = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW, check=False,
        )
    except Exception:
        return None
    if found.returncode != 0:
        return None
    return [int(line) for line in found.stdout.split() if line.strip().isdigit()]


def _wait_for_browser_exit():
    """等待应用窗口关闭。

    Edge/Chrome 启动时可能把请求交给另一个进程、自己立刻退出，因此不能只等
    Popen 的句柄；这里改为轮询"使用本应用配置目录的浏览器进程"是否还在。
    """
    start = time.monotonic()
    empty_readings = 0
    while True:
        time.sleep(2)
        pids = _browser_pids()
        if pids is None:
            log("浏览器进程查询失败，无法确认窗口状态")
            return
        if pids:
            empty_readings = 0
        elif time.monotonic() - start > 4:
            empty_readings += 1
            if empty_readings >= 2:
                return
        if time.monotonic() - start > 12 * 3600:
            log("等待浏览器窗口关闭超时")
            return


def _close_leftover_browser():
    pids = _browser_pids() or []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           timeout=20, creationflags=CREATE_NO_WINDOW, check=False)
        except Exception:
            pass


def run_app_window(port, holder, browser):
    outcome = _wait_with_splash(port, holder)
    if outcome == "cancelled":
        log("用户在启动画面关闭了窗口，取消启动")
        return
    if not outcome:
        log("浏览器窗口模式启动失败：服务未就绪")
        message_box("界面服务启动失败，请查看日志后重试：\n" + str(LOG_FILE) +
                    "\n\n最近日志：\n" + _log_tail(8))
        return

    url = f"http://127.0.0.1:{port}/"
    width, height, x, y = preferred_window_size()
    log(f"使用 {browser.name} 打开应用窗口：{url}")
    subprocess.Popen(
        [
            str(browser),
            f"--app={url}",
            f"--user-data-dir={BROWSER_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={width},{height}",
            f"--window-position={x},{y}",
        ],
        creationflags=CREATE_NO_WINDOW,
    )
    _wait_for_browser_exit()
    _close_leftover_browser()
    log("浏览器窗口已关闭")


# ---------------------------------------------------------------- 入口

def parse_args(argv):
    parser = argparse.ArgumentParser(description="PDF 翻译工具桌面版启动器")
    parser.add_argument("--app-window", action="store_true", help="使用 Edge/Chrome 应用窗口（兼容模式）")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        os.chdir(ROOT)
    except OSError:
        pass
    # 极简环境（部分启动器/服务）里可能缺少 SystemDrive，个别系统组件会把它当字面量用
    os.environ.setdefault("SystemDrive", os.environ.get("SystemRoot", "C:\\Windows")[:2])
    _prepare_log()
    _redirect_streams()
    log(f"===== 启动 v{app_version()}（{'浏览器应用窗口' if args.app_window else '原生窗口'}；"
        f"引擎 pdf2zh {engine_version()}）=====")

    if not acquire_single_instance():
        if focus_existing_window():
            log("已有实例在运行，已把它的窗口带到前台")
        else:
            message_box("PDF 翻译工具已经在运行。", MB_ICONINFORMATION)
        return 0

    browser = None
    if args.app_window:
        browser = find_browser()
        if browser is None:
            log("未找到 Edge 或 Chrome")
            message_box("未找到 Microsoft Edge 或 Google Chrome，无法打开应用窗口。\n"
                        "请改用默认方式启动，或先安装 Edge。")
            return 1

    port = free_port()
    log(f"本地端口：{port}")
    holder = start_gui_server(port)
    try:
        if args.app_window:
            run_app_window(port, holder, browser)
        else:
            run_native_window_with_fallback(port, holder)
    finally:
        close_gui_server(holder)
        log("已退出，界面服务已关闭")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except SystemExit:
        raise
    except Exception:
        log("启动器异常：\n" + traceback.format_exc())
        message_box("PDF 翻译工具启动失败：\n\n" + traceback.format_exc(limit=3) +
                    "\n\n详细日志：" + str(LOG_FILE))
        exit_code = 1
    os._exit(exit_code)  # 不等待后台线程，确保关窗后进程立即结束
