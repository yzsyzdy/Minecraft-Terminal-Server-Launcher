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
    """Locate the locales/ directory relative to the project root."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "locales")


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
    try:
        import locale
        lang, _ = locale.getdefaultlocale()
        if lang and lang.startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


def available_languages() -> list[tuple[str, str]]:
    """Return list of (lang_code, display_name) from available locale files."""
    lang_names: dict[str, str] = {
        "zh": "中文",
        "en": "English",
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
