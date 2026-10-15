import asyncio
import cgi
import os
import queue
import shutil
import socket
import threading
import time
import traceback
import uuid
from asyncio import CancelledError
from html import escape as _html_escape
from pathlib import Path
import typing as T

import gradio as gr
import requests
import tqdm
from string import Template
import logging

from pdf2zh import __version__
from pdf2zh.high_level import translate
from pdf2zh.doclayout import ModelInstance
from pdf2zh.config import ConfigManager
from pdf2zh.comparison import reader_html, latest_pair, launch_kwargs
from pdf2zh.model_settings import is_secret, load_service_settings, save_service_settings
from pdf2zh.translator import (
    AnythingLLMTranslator,
    AzureOpenAITranslator,
    AzureTranslator,
    BaseTranslator,
    BingTranslator,
    DeepLTranslator,
    DeepLXTranslator,
    DifyTranslator,
    ArgosTranslator,
    GeminiTranslator,
    GoogleTranslator,
    MiniMaxTranslator,
    ModelScopeTranslator,
    OllamaTranslator,
    OpenAITranslator,
    SiliconTranslator,
    TencentTranslator,
    XinferenceTranslator,
    ZhipuTranslator,
    GrokTranslator,
    GroqTranslator,
    DeepseekTranslator,
    OpenAIlikedTranslator,
    QwenMtTranslator,
    X302AITranslator,
)
from babeldoc.docvision.doclayout import OnnxModel
from babeldoc import __version__ as babeldoc_version

logger = logging.getLogger(__name__)


class _LazyModel:
    """Defers model loading until first access so the GUI starts instantly."""

    def __init__(self):
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            self._model = OnnxModel.load_available()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self._ensure_loaded()
        return getattr(self._model, name)


BABELDOC_MODEL = _LazyModel()
# The following variables associate strings with translators
service_map: dict[str, BaseTranslator] = {
    "Google": GoogleTranslator,
    "Bing": BingTranslator,
    "DeepL": DeepLTranslator,
    "DeepLX": DeepLXTranslator,
    "Ollama": OllamaTranslator,
    "Xinference": XinferenceTranslator,
    "AzureOpenAI": AzureOpenAITranslator,
    "OpenAI": OpenAITranslator,
    "Zhipu": ZhipuTranslator,
    "ModelScope": ModelScopeTranslator,
    "Silicon": SiliconTranslator,
    "Gemini": GeminiTranslator,
    "Azure": AzureTranslator,
    "Tencent": TencentTranslator,
    "Dify": DifyTranslator,
    "AnythingLLM": AnythingLLMTranslator,
    "Argos Translate": ArgosTranslator,
    "Grok": GrokTranslator,
    "Groq": GroqTranslator,
    "DeepSeek": DeepseekTranslator,
    "MiniMax": MiniMaxTranslator,
    "OpenAI-liked": OpenAIlikedTranslator,
    "Ali Qwen-Translation": QwenMtTranslator,
    "302.AI": X302AITranslator,
}

# The following variables associate strings with specific languages
lang_map = {
    "Simplified Chinese": "zh",
    "Traditional Chinese": "zh-TW",
    "English": "en",
    "French": "fr",
    "German": "de",
    "Japanese": "ja",
    "Korean": "ko",
    "Russian": "ru",
    "Spanish": "es",
    "Italian": "it",
}
language_labels = {
    "Simplified Chinese": "简体中文", "Traditional Chinese": "繁体中文",
    "English": "英语", "French": "法语", "German": "德语", "Japanese": "日语",
    "Korean": "韩语", "Russian": "俄语", "Spanish": "西班牙语", "Italian": "意大利语",
}
page_labels = {
    "All": "全部页面", "First": "第一页", "First 5 pages": "前五页",
    "First 20 pages": "前二十页", "Others": "自定义页码",
}
service_labels = {
    "Google": "谷歌翻译", "Bing": "必应翻译", "Tencent": "腾讯翻译",
    "Azure": "微软云翻译", "AzureOpenAI": "微软云 OpenAI", "Zhipu": "智谱",
    "ModelScope": "魔搭", "Silicon": "硅基流动", "DeepSeek": "深度求索",
    "Ali Qwen-Translation": "阿里通义翻译", "OpenAI-liked": "自定义兼容接口",
}


def environment_label(key):
    descriptions = {
        "BASE_URL": "接口地址", "API_URL": "接口地址", "ENDPOINT": "接口地址",
        "API_KEY": "接口密钥", "AUTH_KEY": "认证密钥", "SECRET_KEY": "密钥",
        "SECRET_ID": "密钥标识", "MODEL": "模型名称", "HOST": "服务地址",
        "STREAM": "流式响应", "STOP_TOKENS": "停止标记", "MAX_TOKENS": "最大输出长度",
        "API_VERSION": "接口版本", "DOMAINS": "专业领域",
    }
    description = next((text for suffix, text in descriptions.items() if key.upper().endswith(suffix)), "服务配置")
    return f"{description}（{key}）"

# The following variable associate strings with page ranges
page_map = {
    "All": None,
    "First": [0],
    "First 5 pages": list(range(0, 5)),
    "Others": None,
}

# Check if this is a public demo, which has resource limits
flag_demo = False

# Limit resources
if ConfigManager.get("PDF2ZH_DEMO"):
    flag_demo = True
    service_map = {
        "Google": GoogleTranslator,
    }
    page_map = {
        "First": [0],
        "First 20 pages": list(range(0, 20)),
    }
    client_key = ConfigManager.get("PDF2ZH_CLIENT_KEY")
    server_key = ConfigManager.get("PDF2ZH_SERVER_KEY")


# Limit Enabled Services
enabled_services: T.Optional[T.List[str]] = ConfigManager.get("ENABLED_SERVICES")
if isinstance(enabled_services, list):
    default_services = ["Google", "Bing"]
    enabled_services_names = [str(_).lower().strip() for _ in enabled_services]
    enabled_services = [
        k
        for k in service_map.keys()
        if str(k).lower().strip() in enabled_services_names
    ]
    if len(enabled_services) == 0:
        raise RuntimeError("No services available.")
    enabled_services = default_services + enabled_services
else:
    enabled_services = list(service_map.keys())


# Configure about Gradio show keys
hidden_gradio_details: bool = bool(ConfigManager.get("HIDDEN_GRADIO_DETAILS"))


# Public demo control
def verify_recaptcha(response):
    """
    This function verifies the reCAPTCHA response.
    """
    recaptcha_url = "https://www.google.com/recaptcha/api/siteverify"
    data = {"secret": server_key, "response": response}
    result = requests.post(recaptcha_url, data=data).json()
    return result.get("success")


def download_with_limit(url: str, save_path: str, size_limit: int) -> str:
    """
    This function downloads a file from a URL and saves it to a specified path.

    Inputs:
        - url: The URL to download the file from
        - save_path: The path to save the file to
        - size_limit: The maximum size of the file to download

    Returns:
        - The path of the downloaded file
    """
    chunk_size = 1024
    total_size = 0
    with requests.get(url, stream=True, timeout=10) as response:
        response.raise_for_status()
        content = response.headers.get("Content-Disposition")
        try:  # filename from header
            _, params = cgi.parse_header(content)
            filename = params["filename"]
        except Exception:  # filename from url
            filename = os.path.basename(url)
        filename = os.path.splitext(os.path.basename(filename))[0] + ".pdf"
        with open(save_path / filename, "wb") as file:
            for chunk in response.iter_content(chunk_size=chunk_size):
                total_size += len(chunk)
                if size_limit and total_size > size_limit:
                    raise gr.Error("文件超过允许的大小限制")
                file.write(chunk)
    return save_path / filename


def stop_translate_file(state: dict) -> None:
    """
    This function stops the translation process.

    Inputs:
        - state: The state of the translation process

    Returns:- None
    """
    session_id = state["session_id"]
    if session_id is None:
        return
    if session_id in cancellation_event_map:
        logger.info(f"Stopping translation for session {session_id}")
        cancellation_event_map[session_id].set()


def translate_file(
    file_type,
    file_input,
    link_input,
    service,
    lang_from,
    lang_to,
    page_range,
    page_input,
    prompt,
    threads,
    skip_subset_fonts,
    ignore_cache,
    vfont,
    mode_choice,
    recaptcha_response,
    state,
    *envs,
):
    """
    This function translates a PDF file from one language to another.

    Inputs:
        - file_type: The type of file to translate
        - file_input: The file to translate
        - link_input: The link to the file to translate
        - service: The translation service to use
        - lang_from: The language to translate from
        - lang_to: The language to translate to
        - page_range: The range of pages to translate
        - page_input: The input for the page range
        - prompt: The custom prompt for the llm
        - threads: The number of threads to use
        - recaptcha_response: The reCAPTCHA response
        - state: The state of the translation process
        - progress: The progress bar
        - envs: The environment variables

    Returns:
        - The translated file
        - The translated file
        - The translated file
        - The progress bar
        - The progress bar
        - The progress bar
    """
    session_id = uuid.uuid4()
    state["session_id"] = session_id
    cancellation_event_map[session_id] = asyncio.Event()
    # Translate PDF content using selected service.
    if flag_demo and not verify_recaptcha(recaptcha_response):
        raise gr.Error("人机验证未通过，请重试")

    output = Path("pdf2zh_files")
    output.mkdir(parents=True, exist_ok=True)

    if file_type == "File":
        if not file_input:
            raise gr.Error("请先上传文件")
        file_path = shutil.copy(file_input, output)
    else:
        if not link_input:
            raise gr.Error("请先填写文件链接")
        file_path = download_with_limit(
            link_input,
            output,
            5 * 1024 * 1024 if flag_demo else None,
        )

    filename = os.path.splitext(os.path.basename(file_path))[0]
    file_raw = output / f"{filename}.pdf"
    file_mono = output / f"{filename}-mono.pdf"
    file_dual = output / f"{filename}-dual.pdf"

    translator = service_map[service]
    if page_range != "Others":
        selected_page = page_map[page_range]
    else:
        selected_page = []
        for p in page_input.split(","):
            if "-" in p:
                start, end = p.split("-")
                selected_page.extend(range(int(start) - 1, int(end)))
            else:
                selected_page.append(int(p) - 1)
    lang_from = lang_map[lang_from]
    lang_to = lang_map[lang_to]

    _envs = {}
    for i, env in enumerate(translator.envs.items()):
        _envs[env[0]] = envs[i]
    for k, v in _envs.items():
        if str(k).upper().endswith("API_KEY") and str(v) == "***":
            # Load Real API_KEYs from local configure file
            real_keys: str = ConfigManager.get_env_by_translatername(
                translator, k, None
            )
            _envs[k] = real_keys

    print(f"Files before translation: {os.listdir(output)}")

    try:
        threads = int(threads)
    except ValueError:
        threads = 1

    _progress_begin("正在准备翻译…")

    # 翻译在后台线程里跑，本生成器负责把进度实时 yield 给界面。
    progress_queue: queue.Queue = queue.Queue()
    outcome: dict = {}

    def progress_bar(t: tqdm.tqdm):
        try:
            current = int(getattr(t, "n", 0) or 0)
            total = int(getattr(t, "total", 0) or 0)
        except (TypeError, ValueError):
            return
        _progress_update(current, total)
        progress_queue.put(None)

    def run_translation():
        try:
            from pdf2zh.kernel import KernelRegistry
            from pdf2zh.kernel.protocol import TranslateRequest

            KernelRegistry.switch(mode_choice)
            kernel = KernelRegistry.get()
            request = TranslateRequest(
                files=[str(file_raw)],
                output=str(output),
                pages=selected_page,
                lang_in=lang_from,
                lang_out=lang_to,
                service=f"{translator.name}",
                thread=int(threads),
                envs=_envs,
                prompt=str(prompt) if prompt else None,
                skip_subset_fonts=skip_subset_fonts,
                ignore_cache=ignore_cache,
                vfont=vfont,
            )
            kernel.translate(
                request,
                callback=progress_bar,
                cancellation_event=cancellation_event_map[session_id],
            )
            outcome["ok"] = True
        except BaseException as exc:  # 包含 asyncio.CancelledError
            outcome["error"] = exc
            outcome["traceback"] = traceback.format_exc()
            print("翻译过程出错：\n" + outcome["traceback"], flush=True)
        finally:
            progress_queue.put(None)

    worker = threading.Thread(
        target=run_translation, name="pdf2zh-translate", daemon=True,
    )
    worker.start()
    while worker.is_alive():
        try:
            progress_queue.get(timeout=0.4)
        except queue.Empty:
            pass
        snapshot = _progress_snapshot()
        yield _stream_outputs(progress_html(snapshot))
    worker.join()

    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, CancelledError):
            cancellation_event_map.pop(session_id, None)
            _progress_finish("cancelled", "翻译已取消")
            yield _stream_outputs(progress_html())
            raise gr.Error("翻译已取消")
        _progress_finish("error", "翻译失败，详情见运行日志")
        yield _stream_outputs(progress_html())
        raise gr.Error(f"翻译失败：{error}")

    _progress_stage("packing", "正在生成 PDF…")
    yield _stream_outputs(progress_html())
    print(f"Files after translation: {os.listdir(output)}")

    if not file_mono.exists() or not file_dual.exists():
        _progress_finish("error", "未生成翻译结果")
        yield _stream_outputs(progress_html())
        raise gr.Error("未生成翻译结果，请检查服务配置和日志")

    _progress_finish("done", "翻译完成")

    yield (
        str(file_mono),
        reader_html(file_raw, file_mono),
        str(file_dual),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        progress_html(),
    )


def babeldoc_translate_file(**kwargs):
    from babeldoc.high_level import init as babeldoc_init

    babeldoc_init()
    from babeldoc.high_level import async_translate as babeldoc_translate
    from babeldoc.translation_config import TranslationConfig as YadtConfig

    for translator in [
        GoogleTranslator,
        BingTranslator,
        DeepLTranslator,
        DeepLXTranslator,
        OllamaTranslator,
        XinferenceTranslator,
        AzureOpenAITranslator,
        OpenAITranslator,
        ZhipuTranslator,
        ModelScopeTranslator,
        SiliconTranslator,
        GeminiTranslator,
        AzureTranslator,
        TencentTranslator,
        DifyTranslator,
        AnythingLLMTranslator,
        ArgosTranslator,
        GrokTranslator,
        GroqTranslator,
        DeepseekTranslator,
        OpenAIlikedTranslator,
        QwenMtTranslator,
        X302AITranslator,
    ]:
        if kwargs["service"] == translator.name:
            translator = translator(
                kwargs["lang_in"],
                kwargs["lang_out"],
                "",
                envs=kwargs["envs"],
                prompt=kwargs["prompt"],
                ignore_cache=kwargs["ignore_cache"],
            )
            break
    else:
        raise ValueError("Unsupported translation service")
    import asyncio
    from babeldoc.main import create_progress_handler

    for file in kwargs["files"]:
        file = file.strip("\"'")
        yadt_config = YadtConfig(
            input_file=file,
            font=None,
            pages=",".join((str(x) for x in getattr(kwargs, "raw_pages", []))),
            output_dir=kwargs["output"],
            doc_layout_model=BABELDOC_MODEL,
            translator=translator,
            debug=False,
            lang_in=kwargs["lang_in"],
            lang_out=kwargs["lang_out"],
            no_dual=False,
            no_mono=False,
            qps=kwargs["thread"],
            use_rich_pbar=False,
            disable_rich_text_translate=not isinstance(translator, OpenAITranslator),
            skip_clean=kwargs["skip_subset_fonts"],
            report_interval=0.5,
        )

        async def yadt_translate_coro(yadt_config):
            progress_context, progress_handler = create_progress_handler(yadt_config)

            # 开始翻译
            with progress_context:
                async for event in babeldoc_translate(yadt_config):
                    progress_handler(event)
                    if yadt_config.debug:
                        logger.debug(event)
                    kwargs["callback"](progress_context)
                    if kwargs["cancellation_event"].is_set():
                        yadt_config.cancel_translation()
                        raise CancelledError
                    if event["type"] == "finish":
                        result = event["translate_result"]
                        logger.info("Translation Result:")
                        logger.info(f"  Original PDF: {result.original_pdf_path}")
                        logger.info(f"  Time Cost: {result.total_seconds:.2f}s")
                        logger.info(f"  Mono PDF: {result.mono_pdf_path or 'None'}")
                        logger.info(f"  Dual PDF: {result.dual_pdf_path or 'None'}")
                        file_mono = result.mono_pdf_path
                        file_dual = result.dual_pdf_path
                        break
            import gc

            gc.collect()
            return (
                str(file_mono),
                reader_html(kwargs["files"][0], file_mono),
                str(file_dual),
                gr.update(visible=True),
                gr.update(visible=True),
                gr.update(visible=True),
            )

        return asyncio.run(yadt_translate_coro(yadt_config))


# Global setup
custom_teal = gr.themes.Color(
    c50="#f6f9f2", c100="#edf4ee", c200="#dbe8db",
    c300="#b4cfbc", c400="#7aab93", c500="#217965",
    c600="#196551", c700="#164e40", c800="#123f34",
    c900="#0b3028", c950="#061f19",
)

custom_css = (Path(__file__).parent / 'upload.css').read_text(encoding='utf-8')

demo_recaptcha = """
    <script src="https://www.google.com/recaptcha/api.js?render=explicit" async defer></script>
    <script type="text/javascript">
        var onVerify = function(token) {
            el=document.getElementById('verify').getElementsByTagName('textarea')[0];
            el.value=token;
            el.dispatchEvent(new Event('input'));
        };
    </script>
    """

# 发行信息由桌面启动器（desktop_app.py / start_webui.py）通过环境变量注入；
# 未设置时（例如直接跑 CLI 或上游原版），下面这行不显示。
_release_version = os.environ.get("PDF2ZH_RELEASE_VERSION", "").strip()
_release_publisher = os.environ.get("PDF2ZH_RELEASE_PUBLISHER", "").strip()
_release_url = os.environ.get("PDF2ZH_RELEASE_URL", "").strip()
if _release_version:
    if _release_url:
        _publisher_html = f'<a href="{_release_url}">{_release_publisher or _release_url}</a>'
    else:
        _publisher_html = _release_publisher
    release_line = (
        f"- 发行版本：v{_release_version}"
        + (f" by {_publisher_html}" if _publisher_html else "")
        + "<br>\n                    "
    )
else:
    release_line = ""

tech_details_string = f"""
                    <summary>项目信息</summary>
                    {release_line}- GitHub: <a href="https://github.com/Lei-Ao/PDFMathTranslate">Lei-Ao/PDFMathTranslate</a><br>
                    - BabelDOC: <a href="https://github.com/funstory-ai/BabelDOC">funstory-ai/BabelDOC</a><br>
                    - 界面作者：Ao-Lei<br>
                    - 引擎版本（pdf2zh）：{__version__} <br>
                    - BabelDOC 版本：{babeldoc_version}
                """
cancellation_event_map = {}

# --------------------------------------------------------------- 翻译进度面板
# translate_file 是生成器：真正的翻译跑在后台线程里，主线程每 0.4 秒把一次进度
# 快照 yield 给界面。这样翻译过程中面板会一直走动，不会让人误以为没有在翻译。
_TRANSLATE_PROGRESS = {
    "active": False,
    "phase": "idle",  # idle / preparing / translating / packing / done / cancelled / error
    "current": 0,
    "total": 0,
    "started": 0.0,
    "elapsed": 0.0,
    "note": "",
}
_translate_progress_lock = threading.Lock()


def _progress_reset():
    with _translate_progress_lock:
        _TRANSLATE_PROGRESS.update(
            active=False, phase="idle", current=0,
            total=0, started=0.0, elapsed=0.0, note="",
        )


def _progress_begin(note):
    with _translate_progress_lock:
        _TRANSLATE_PROGRESS.update(
            active=True, phase="preparing", current=0,
            total=0, started=time.time(), elapsed=0.0, note=note,
        )


def _progress_update(current, total):
    with _translate_progress_lock:
        _TRANSLATE_PROGRESS["current"] = current
        _TRANSLATE_PROGRESS["total"] = total
        if total:
            _TRANSLATE_PROGRESS["phase"] = "translating"
            _TRANSLATE_PROGRESS["note"] = "正在翻译文档…"


def _progress_stage(phase, note):
    with _translate_progress_lock:
        _TRANSLATE_PROGRESS["phase"] = phase
        _TRANSLATE_PROGRESS["note"] = note


def _progress_finish(phase, note):
    with _translate_progress_lock:
        started = _TRANSLATE_PROGRESS["started"] or time.time()
        _TRANSLATE_PROGRESS.update(
            active=False, phase=phase, note=note, elapsed=time.time() - started,
        )


def _progress_snapshot():
    with _translate_progress_lock:
        state = dict(_TRANSLATE_PROGRESS)
    if state["active"] and state["started"]:
        state["elapsed"] = time.time() - state["started"]
    return state


def _human_seconds(value):
    value = max(0, int(value))
    if value < 60:
        return f"{value} 秒"
    return f"{value // 60} 分 {value % 60:02d} 秒"


def progress_html(snapshot=None):
    """把进度快照渲染成覆盖在上传区上的浮层 HTML；空闲或已完成时返回空串。"""
    state = _progress_snapshot() if snapshot is None else snapshot
    # 翻译完成后界面会自动跳到对照阅读页，这里直接收起浮层，
    # 免得返回上传页时还盖着一块"翻译完成"挡住上传区。
    if state["phase"] in ("idle", "done"):
        return ""

    current = max(0, int(state["current"]))
    total = max(0, int(state["total"]))
    if state["phase"] == "packing":
        ratio = 1.0
    elif total:
        ratio = min(1.0, current / total)
    else:
        ratio = 0.0
    percent = int(round(ratio * 100))
    elapsed = max(0.0, float(state["elapsed"]))

    if state["phase"] == "translating" and total and 0 < current < total and elapsed > 0:
        eta = "预计剩余 " + _human_seconds(elapsed / current * (total - current))
    elif state["phase"] == "translating" and not total:
        eta = "正在分析版面…"
    else:
        eta = ""

    if state["phase"] == "cancelled":
        head, tone = "翻译已取消", "cancelled"
    elif state["phase"] == "error":
        head, tone = state["note"] or "翻译失败", "error"
    else:
        head, tone = state["note"] or "正在翻译…", "running"

    facts = []
    if total:
        facts.append(f"第 {min(current, total)} / {total} 页")
    facts.append(f"已用 {_human_seconds(elapsed)}")
    if eta:
        facts.append(eta)

    return (
        f'<div class="tr-progress tr-progress-{tone}">'
        f'<div class="tr-progress-ring">{percent}%</div>'
        f'<div class="tr-progress-head">{_html_escape(head)}</div>'
        f'<div class="tr-progress-track">'
        f'<div class="tr-progress-fill" style="width:{percent}%"></div></div>'
        f'<div class="tr-progress-meta">{_html_escape(" · ".join(facts))}</div>'
        f"</div>"
    )


def _stream_outputs(status_html):
    """生成器中间帧：只刷新进度面板，其余输出用 gr.skip() 保持不变。"""
    return (
        gr.skip(), gr.skip(), gr.skip(),
        gr.skip(), gr.skip(), gr.skip(),
        status_html,
    )


initial_original, initial_translated = (None, None) if flag_demo else latest_pair("pdf2zh_files")
initial_dual = (
    initial_translated.with_name(initial_translated.name.removesuffix("-mono.pdf") + "-dual.pdf")
    if initial_translated else None
)


def preview_uploaded_file(file):
    # A new upload must not retain the previous document's translation/downloads.
    _progress_reset()
    return (
        reader_html(file),
        gr.update(value=None, visible=False),
        gr.update(value=None, visible=False),
        gr.update(visible=False),
        progress_html(),
    )


# Navigate only after successful translation, so errors keep the upload form intact.
open_reader_js = """(html) => {
    const page = new DOMParser().parseFromString(html, 'text/html');
    const frame = page.querySelector('iframe');
    if (frame) window.location.assign(new URL(frame.getAttribute('src'), window.location.href).href);
}"""


def reopen_latest_translation():
    original, translated = latest_pair("pdf2zh_files")
    if not original or not translated:
        raise gr.Error("还没有已完成的翻译，请先上传并翻译文档。")
    return reader_html(original, translated)


def refresh_recent_document():
    original, translated = (None, None) if flag_demo else latest_pair("pdf2zh_files")
    return gr.update(
        value=f"▤ {original.name}" if original else "继续阅读最近译文",
        visible=bool(translated),
    )


def selected_model_service():
    saved = ConfigManager.get("PDF2ZH_SERVICE")
    if saved in enabled_services:
        return saved
    if "OpenAI" in enabled_services:
        previous = load_service_settings(service_map["OpenAI"])
        if previous.get("OPENAI_API_KEY"):
            return "OpenAI"
    return enabled_services[0]


def model_button_label(name):
    settings = load_service_settings(service_map[name])
    model = next((value for key, value in settings.items() if key.upper().endswith("MODEL") and value), "")
    label = f"模型设置 · {service_labels.get(name, name)}"
    return f"{label} / {model}" if model else label


initial_service = selected_model_service()


# The following code creates the GUI
with gr.Blocks(
    title="PDF 对照翻译",
    theme=gr.themes.Default(
        primary_hue=custom_teal, spacing_size="md", radius_size="md",
        font=["Microsoft YaHei", "Segoe UI", "sans-serif"],
    ),
    css=custom_css,
    head=demo_recaptcha if flag_demo else "",
) as demo:
    gr.HTML(
        '<aside class="workspace-sidebar">'
        '<div class="workspace-brand"><span class="workspace-mark">Ⅱ</span>页间阅读</div>'
        '<div class="workspace-caption">你的 PDF 翻译工作台</div>'
        '<a href="/" class="workspace-new"><span>＋</span>新建翻译</a>'
        '<div class="workspace-library-label">最近译文</div>'
        + ('' if initial_translated else '<p class="workspace-empty">导入第一份文档，<br>开始跨越语言的阅读。</p>')
        + '<div class="workspace-bottom"><span class="local-dot"></span><div>本地工作空间'
        '<small>文件与译文保存在此设备</small></div></div></aside>'
        '<header class="workspace-topbar"><div class="workspace-crumb">工作空间<span>/</span>'
        '<b>新建翻译</b></div></header>',
        elem_id="workspace-shell",
    )
    model_settings_btn = gr.Button(model_button_label(initial_service), elem_id="model-settings-button")
    with gr.Column(visible=False, elem_id="model-settings-overlay") as model_settings_panel:
        with gr.Column(elem_id="model-settings-dialog"):
            gr.Markdown("## 模型与接口设置", elem_id="model-settings-title")
            gr.Markdown("选择翻译服务，填写接口与模型。保存一次，下次自动读取。", elem_id="model-settings-description")
            service = gr.Dropdown(
                label="翻译服务",
                choices=[(service_labels.get(name, name), name) for name in enabled_services],
                value=initial_service,
            )
            envs = []
            env_field_count = max(len(translator.envs) for translator in service_map.values())
            initial_keys = list(service_map[initial_service].envs)
            initial_settings = load_service_settings(service_map[initial_service])
            def create_api_field(i):
                key = initial_keys[i] if i < len(initial_keys) else None
                envs.append(gr.Textbox(
                    label=environment_label(key) if key else "服务配置",
                    value=str(initial_settings[key]) if key and initial_settings[key] is not None else "",
                    type="password" if key and is_secret(key) else "text",
                    visible=key is not None, interactive=True,
                ))
            for i in range(min(3, env_field_count)):
                create_api_field(i)
            with gr.Accordion("接口高级参数", open=False, visible=len(initial_keys) > 3) as api_extra:
                for i in range(3, env_field_count):
                    create_api_field(i)
            api_fields = list(envs)
            show_api_key = gr.Checkbox(label="显示 API Key", value=False, visible=any(is_secret(key) for key in initial_keys))
            with gr.Row():
                cancel_model_settings = gr.Button("取消", elem_id="cancel-model-settings")
                save_model_settings_btn = gr.Button("保存设置", variant="primary", elem_id="save-model-settings")
    gr.HTML(
        '<div class="welcome-eyebrow">让理解，不止于翻译</div>'
        '<h1 class="welcome-title">原文与新知，<br>在每一页相遇<span>。</span></h1>'
        '<p class="welcome-description">带上你的翻译模型，读懂每一份 PDF。<br>'
        '保留公式、图表与版面，让原文和译文并肩呈现。</p>',
        elem_id="welcome-hero",
    )
    resume_btn = gr.Button(
        f"▤ {initial_original.name}" if initial_original else "继续阅读最近译文",
        visible=bool(initial_translated), elem_id="resume-document",
    )

    with gr.Row(elem_id="upload-form"):
        with gr.Column(scale=1):
            gr.Markdown("## 上传文档（小于 5 MB）" if flag_demo else "## 上传文档")
            # 上传区与进度浮层放在同一个定位容器里：翻译时浮层直接盖在上传区上。
            with gr.Column(elem_id="upload-dropzone"):
                file_input = gr.File(
                    label="选择文档",
                    show_label=False,
                    file_count="single",
                    file_types=[".pdf", ".doc", ".docx"],
                    type="filepath",
                    elem_classes=["input-file"],
                )
                translate_status = gr.HTML(progress_html(), elem_id="translate-progress")
            link_input = gr.Textbox(
                label="文档链接",
                placeholder="请输入可下载的 PDF 文件网址",
                visible=False,
                interactive=True,
            )
            file_type = gr.Radio(
                choices=[("上传文件", "File"), ("网址链接", "Link")],
                label="文档来源", show_label=False, container=False,
                value="File",
            )
            gr.Markdown("## 翻译设置")
            with gr.Row():
                lang_from = gr.Dropdown(
                    label="原文语言",
                    choices=[(language_labels[name], name) for name in lang_map],
                    value=ConfigManager.get("PDF2ZH_LANG_FROM", "English"),
                )
                lang_to = gr.Dropdown(
                    label="译文语言",
                    choices=[(language_labels[name], name) for name in lang_map],
                    value=ConfigManager.get("PDF2ZH_LANG_TO", "Simplified Chinese"),
                )
            page_range = gr.Radio(
                choices=[(page_labels[name], name) for name in page_map],
                label="翻译范围",
                value=list(page_map.keys())[0],
            )

            page_input = gr.Textbox(
                label="自定义页码",
                placeholder="例如：1-3,5",
                visible=False,
                interactive=True,
            )

            with gr.Accordion("高级设置", open=False):
                gr.Markdown("#### 可选参数")
                threads = gr.Textbox(
                    label="并发翻译数", interactive=True, value="4"
                )
                skip_subset_fonts = gr.Checkbox(
                    label="保留完整字体（文件会更大）", interactive=True, value=False
                )
                ignore_cache = gr.Checkbox(
                    label="忽略缓存，重新翻译", interactive=True, value=False
                )
                vfont = gr.Textbox(
                    label="公式字体匹配规则（正则表达式）",
                    interactive=True,
                    value=ConfigManager.get("PDF2ZH_VFONT", ""),
                )
                prompt = gr.Textbox(
                    label="自定义翻译提示词", interactive=True, visible=False
                )
                mode_choice = gr.Dropdown(
                    label="翻译模式",
                    choices=[("快速模式", "fast"), ("精细模式", "precise")],
                    value="fast",
                    interactive=True,
                )
                envs.append(prompt)

            def on_select_service(service, evt: gr.EventData):
                translator = service_map[service]
                _envs = []
                for i in range(env_field_count + 1):
                    _envs.append(gr.update(visible=False, value=""))
                for i, env in enumerate(translator.envs.items()):
                    label = env[0]
                    value = load_service_settings(translator)[env[0]]
                    visible = True
                    if hidden_gradio_details:
                        if (
                            "MODEL" not in str(label).upper()
                            and value
                            and hidden_gradio_details
                        ):
                            visible = False
                        # Hidden Keys From Gradio
                        if "API_KEY" in label.upper():
                            value = "***"  # We use "***" Present Real API_KEY
                    _envs[i] = gr.update(
                        visible=visible,
                        label=environment_label(label),
                        value=value,
                        type="password" if is_secret(label) else "text",
                    )
                _envs[-1] = gr.update(visible=translator.CustomPrompt)
                return _envs + [gr.update(value=False, visible=any(is_secret(key) for key in translator.envs)),
                                gr.update(visible=len(translator.envs) > 3, open=False)]

            def on_select_filetype(file_type):
                return (
                    gr.update(visible=file_type == "File"),
                    gr.update(visible=file_type == "Link"),
                )

            def on_select_page(choice):
                if choice == "Others":
                    return gr.update(visible=True)
                else:
                    return gr.update(visible=False)

            def on_vfont_change(value):
                ConfigManager.set("PDF2ZH_VFONT", value)
                return value

            output_title = gr.Markdown("## 下载翻译结果", visible=False)
            output_file_mono = gr.File(
                label="译文 PDF", value=str(initial_translated) if initial_translated else None,
                visible=False, interactive=False,
            )
            output_file_dual = gr.File(
                label="双语 PDF", value=str(initial_dual) if initial_dual and initial_dual.exists() else None,
                visible=False, interactive=False,
            )
            recaptcha_response = gr.Textbox(
                label="人机验证结果", elem_id="verify", visible=False
            )
            recaptcha_box = gr.HTML('<div id="recaptcha-box"></div>')
            with gr.Row(elem_id="translation-actions"):
                translate_btn = gr.Button("开始翻译 →", variant="primary", elem_id="start-translation")
                cancellation_btn = gr.Button("取消翻译", variant="secondary", elem_id="cancel-translation")
            gr.HTML(
                '<div class="welcome-features"><div><i>01</i><b>保留版面</b><p>公式与图表保持原样</p></div>'
                '<div><i>02</i><b>自选模型</b><p>使用你熟悉的翻译接口</p></div>'
                '<div><i>03</i><b>并肩阅读</b><p>全窗口双栏与同步滚动</p></div></div>'
                '<p style="font-size:10px;color:#a1aa99;line-height:1.8">文件保存在本机；'
                '翻译时，待译文本将发送至你配置的服务。</p>'
            )
            with gr.Accordion("关于翻译程序", open=False):
                tech_details_tog = gr.Markdown(tech_details_string, elem_id="project-details")
            page_range.select(on_select_page, page_range, page_input)
            service.select(
                on_select_service,
                service,
                envs + [show_api_key, api_extra],
            )
            vfont.change(on_vfont_change, inputs=vfont, outputs=None)
            file_type.select(
                on_select_filetype,
                file_type,
                [file_input, link_input],
                js=(
                    f"""
                    (a,b)=>{{
                        try{{
                            grecaptcha.render('recaptcha-box',{{
                                'sitekey':'{client_key}',
                                'callback':'onVerify'
                            }});
                        }}catch(error){{}}
                        return [a];
                    }}
                    """
                    if flag_demo
                    else ""
                ),
            )

    preview = gr.HTML(reader_html(), elem_id="comparison-reader")
    resume_btn.click(reopen_latest_translation, outputs=preview).success(
        fn=None, inputs=[preview], outputs=None, js=open_reader_js,
    )
    demo.load(refresh_recent_document, outputs=resume_btn, queue=False)

    def restore_model_settings(open_panel=False):
        name = selected_model_service()
        return [gr.update(value=name), *on_select_service(name, None),
                gr.update(visible=open_panel), gr.update(value=model_button_label(name))]

    settings_outputs = [service, *envs, show_api_key, api_extra, model_settings_panel, model_settings_btn]
    def restore_model_settings_on_load(request: gr.Request):
        return restore_model_settings(request.query_params.get("settings") == "open")

    demo.load(restore_model_settings_on_load, outputs=settings_outputs)
    model_settings_btn.click(lambda: restore_model_settings(True), outputs=settings_outputs)
    cancel_model_settings.click(lambda: restore_model_settings(False), outputs=settings_outputs)

    def toggle_api_key(name, show):
        keys = list(service_map[name].envs)
        return [gr.update(type="text" if show or i >= len(keys) or not is_secret(keys[i]) else "password")
                for i in range(env_field_count)]

    show_api_key.change(toggle_api_key, inputs=[service, show_api_key], outputs=api_fields)

    def save_model_settings(name, *values):
        try:
            save_service_settings(name, service_map[name], dict(zip(service_map[name].envs, values)))
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        gr.Info("模型设置已保存，下次将自动读取")
        return gr.update(value=model_button_label(name)), gr.update(visible=False)

    save_model_settings_btn.click(save_model_settings, inputs=[service, *api_fields],
                                  outputs=[model_settings_btn, model_settings_panel]).success(
        on_select_service, inputs=[service], outputs=envs + [show_api_key, api_extra],
    )

    # Event handlers
    file_input.change(
        preview_uploaded_file,
        inputs=file_input,
        outputs=[preview, output_file_mono, output_file_dual, output_title, translate_status],
        js=(
            f"""
            (a,b)=>{{
                try{{
                    grecaptcha.render('recaptcha-box',{{
                        'sitekey':'{client_key}',
                        'callback':'onVerify'
                    }});
                }}catch(error){{}}
                return [a];
            }}
            """
            if flag_demo
            else ""
        ),
    )

    state = gr.State({"session_id": None})

    translate_btn.click(
        translate_file,
        inputs=[
            file_type,
            file_input,
            link_input,
            service,
            lang_from,
            lang_to,
            page_range,
            page_input,
            prompt,
            threads,
            skip_subset_fonts,
            ignore_cache,
            vfont,
            mode_choice,
            recaptcha_response,
            state,
            *envs,
        ],
        outputs=[
            output_file_mono,
            preview,
            output_file_dual,
            output_file_mono,
            output_file_dual,
            output_title,
            translate_status,
        ],
        # 进度由上传区上的自绘浮层显示，关掉 Gradio 自带的进度条，免得两者叠在一起。
        show_progress="hidden",
    ).success(fn=None, inputs=[preview], outputs=None, js=open_reader_js)

    cancellation_btn.click(
        stop_translate_file,
        inputs=[state],
    )


def parse_user_passwd(file_path: str) -> tuple:
    """
    Parse the user name and password from the file.

    Inputs:
        - file_path: The file path to read.
    Outputs:
        - tuple_list: The list of tuples of user name and password.
        - content: The content of the file
    """
    tuple_list = []
    content = ""
    if not file_path:
        return tuple_list, content
    if len(file_path) == 2:
        try:
            with open(file_path[1], "r", encoding="utf-8") as file:
                content = file.read()
        except FileNotFoundError:
            print(f"Error: File '{file_path[1]}' not found.")
    try:
        with open(file_path[0], "r", encoding="utf-8") as file:
            tuple_list = [
                tuple(line.strip().split(",")) for line in file if line.strip()
            ]
    except FileNotFoundError:
        print(f"Error: File '{file_path[0]}' not found.")
    return tuple_list, content


def _has_ipv6() -> bool:
    """Check whether the system can bind an IPv6 socket."""
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.close()
        return True
    except OSError:
        return False


def setup_gui(
    share: bool = False, auth_file: list = ["", ""], server_port=7860
) -> None:
    """
    Setup the GUI with the given parameters.

    Inputs:
        - share: Whether to share the GUI.
        - auth_file: The file path to read the user name and password.

    Outputs:
        - None
    """
    user_list, html = parse_user_passwd(auth_file)

    auth_kwargs = {}
    if len(user_list) > 0:
        auth_kwargs = {"auth": user_list, "auth_message": html}

    if flag_demo:
        demo.launch(server_name="0.0.0.0", max_file_size="5mb", inbrowser=True, **launch_kwargs())
        return

    # Try binding addresses in order: "::" accepts both IPv4+IPv6 on most
    # dual-stack systems, "0.0.0.0" is IPv4-only, "127.0.0.1" is loopback,
    # and finally fall back to Gradio's share mode.
    bind_addresses = []
    if _has_ipv6():
        bind_addresses.append("[::]")
    bind_addresses.append("0.0.0.0")
    bind_addresses.append("127.0.0.1")

    for addr in bind_addresses:
        try:
            demo.launch(
                server_name=addr,
                debug=True,
                inbrowser=True,
                share=share,
                server_port=server_port,
                **launch_kwargs(),
                **auth_kwargs,
            )
            return
        except Exception:
            print(
                f"Error launching GUI using {addr}.\n"
                "This may be caused by global mode of proxy software."
            )

    # Last resort: let Gradio create a share link
    demo.launch(
        debug=True,
        inbrowser=True,
        share=True,
        server_port=server_port,
        **launch_kwargs(),
        **auth_kwargs,
    )


# For auto-reloading while developing
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    setup_gui()
