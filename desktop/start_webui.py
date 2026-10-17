"""Start the checked-out PDFMathTranslate GUI on IPv4 localhost."""
import os
from pathlib import Path

os.environ.setdefault("PDF2ZH_DATA_DIR", str(Path(__file__).resolve().parent / "data"))

for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(name, None)
os.environ["NO_PROXY"] = "*"
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

try:  # 发行信息，供界面"关于翻译程序"显示
    from desktop_version import PUBLISHER, PUBLISHER_URL, VERSION

    os.environ["PDF2ZH_RELEASE_VERSION"] = VERSION
    os.environ["PDF2ZH_RELEASE_PUBLISHER"] = PUBLISHER
    os.environ["PDF2ZH_RELEASE_URL"] = PUBLISHER_URL
except ImportError:
    pass

import gradio as gr

original_launch = gr.Blocks.launch


def launch_on_localhost(self, *args, **kwargs):
    kwargs.update(server_name="127.0.0.1", share=False, inbrowser=False)
    return original_launch(self, *args, **kwargs)


gr.Blocks.launch = launch_on_localhost

from pdf2zh.pdf2zh import main

if __name__ == "__main__":
    main(["-i", "--serverport", "7860"])
