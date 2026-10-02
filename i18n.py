"""
Internationalization (i18n) support

Provides translation lookup via JSON locale files in the locales/ directory.
Language is configured in config.json via the "language" field (default: "zh").
"""

import json
import os
import sys

_translations: dict[str, str] = {}
_current_lang: str = ""


def _locale_dir() -> str:
    """Locate the locales/ directory.

    locales 通过 --include-data-dir 嵌入 exe，解压后与 __file__ 同目录
    （onefile 模式在临时目录，standalone 在 .dist 目录，源码模式在项目根）。
    不要用 sys.executable：onefile 模式下它可能指向用户放置 exe 的原始路径，
    而资源实际解压在临时目录。
    """
    from paths import bundled_dir
    return os.path.join(bundled_dir(), "locales")


def load_language(lang: str) -> None:
    """Load translations for the given language code (e.g. 'zh', 'en')."""
    global _translations, _current_lang
    _current_lang = lang
    _translations.clear()
    path = os.path.join(_locale_dir(), f"{lang}.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _translations.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass


def detect_os_language() -> str:
    """Detect OS language. Returns 'zh' for Chinese locales, 'en' otherwise."""
    candidates: list[str] = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            candidates.append(value)

    try:
        import locale
    except ImportError:
        locale = None

    if locale is not None:
        # getdefaultlocale() 从 3.11 起被标记废弃、并已排定移除，所以只当
        # 兜底来源之一，getattr 拿不到时也不至于整个函数失效
        getter = getattr(locale, "getdefaultlocale", None)
        if getter is not None:
            try:
                candidates.append(getter()[0] or "")
            except Exception:
                pass
        try:
            candidates.append(locale.getlocale()[0] or "")
        except Exception:
            pass

    for value in candidates:
        low = value.lower()
        # Windows 上 getlocale() 返回的是 'Chinese (Simplified)_China'，
        # 并不以 zh 开头，所以要连 chinese 一起认
        if low.startswith("zh") or "chinese" in low:
            return "zh"
    return "en"


def available_languages() -> list[tuple[str, str]]:
    """Return list of (lang_code, display_name) from available locale files."""
    lang_names: dict[str, str] = {
        "zh": "中文",
        "en": "English",
        "ru": "Русский",
    }
    result: list[tuple[str, str]] = []
    d = _locale_dir()
    if not os.path.isdir(d):
        return [("zh", "中文")]
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            code = f[:-5]
            result.append((code, lang_names.get(code, code)))
    if not result:
        result.append(("zh", "中文"))
    return result


def t(key: str, *args, **kwargs) -> str:
    """Translate a key into the current language.

    Supports positional and keyword formatting via str.format().
    Falls back to the key itself if no translation is found.
    """
    text = _translations.get(key, key)
    if args:
        try:
            text = text.format(*args)
        except (KeyError, IndexError):
            pass
    elif kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError:
            pass
    return text
