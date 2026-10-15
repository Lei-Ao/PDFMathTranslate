"""Persist model settings independently of starting a translation."""
from urllib.parse import urlparse

from pdf2zh.config import ConfigManager


def is_secret(key):
    key = key.upper()
    return "SECRET" in key or key.endswith(("KEY", "PASSWORD"))


def load_service_settings(translator):
    saved = ConfigManager.get_translator_by_name(translator.name) or {}
    return {key: saved[key] if saved.get(key) is not None else default for key, default in translator.envs.items()}


def save_service_settings(service, translator, values):
    settings = {}
    for key, default in translator.envs.items():
        value = values.get(key, default)
        value = "" if value is None else str(value).strip()
        if key.upper().endswith("MODEL") and not value:
            raise ValueError("请填写模型名称")
        if is_secret(key) and default is None and not value:
            raise ValueError("请填写接口密钥")
        if value and key.upper().endswith(("BASE_URL", "ENDPOINT", "HOST", "API_URL")):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("接口地址必须是完整的 http 或 https 网址")
        if key.upper().endswith("STREAM") and value.lower() not in {"true", "false"}:
            raise ValueError("流式响应请填写 true 或 false")
        if key.upper().endswith("MAX_TOKENS"):
            try:
                number = int(value)
            except ValueError as exc:
                raise ValueError("最大输出长度请填写正整数，或 -1 表示不限") from exc
            if number != -1 and number <= 0:
                raise ValueError("最大输出长度请填写正整数，或 -1 表示不限")
        settings[key] = value
    ConfigManager.set_translator_by_name(translator.name, settings)
    ConfigManager.set("PDF2ZH_SERVICE", service)
    return settings
