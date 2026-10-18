"""
Internationalization (i18n) support

Provides translation lookup via JSON locale files in the locales/ directory.
Language is configured in config.json via the "language" field (default: "zh").
"""

import json
import os
import sys

# 兜底语言。语境里缺某个 key 时用它，而不是把 key 本身显示给用户——
# zh.json 里那个 "_fallback": true 标记本来就是这个意思，但一直没人读它，
# 于是 neko 这种没翻全的语言会在界面上直接显示 "frp.add_title" 这样的裸 key。
FALLBACK_LANG = "zh"

_translations: dict[str, str] = {}
_fallback: dict[str, str] = {}
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


def _read_locale(lang: str) -> dict:
    """读一个语言文件；读不出来返回空字典（缺翻译不该让程序起不来）。"""
    path = os.path.join(_locale_dir(), f"{lang}.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_language(lang: str) -> None:
    """Load translations for the given language code (e.g. 'zh', 'en')."""
    global _current_lang
    _current_lang = lang
    _translations.clear()
    _translations.update(_read_locale(lang))

    # 兜底表：当前语言就是兜底语言时不用再读一遍
    _fallback.clear()
    if lang != FALLBACK_LANG:
        _fallback.update(_read_locale(FALLBACK_LANG))


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
    """Return list of (lang_code, display_name) from available locale files.

    显示名取 locale 文件自己的 `_lang`，而不是在这里维护一张表——加了第五种
    语言却忘了登记，菜单里就会露出 `neko` 这样的裸代码（这张表以前就漏过）。
    """
    lang_names: dict[str, str] = {
        "zh": "中文",
        "en": "English",
        "ru": "Русский",
        "neko": "萌猫语",
    }
    result: list[tuple[str, str]] = []
    d = _locale_dir()
    if not os.path.isdir(d):
        return [("zh", "中文")]
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            code = f[:-5]
            name = lang_names.get(code, code)
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    declared = json.load(fh).get("_lang")
            except (OSError, ValueError):
                declared = None
            if isinstance(declared, str) and declared.strip():
                name = declared.strip()
            result.append((code, name))
    if not result:
        result.append(("zh", "中文"))
    return result


def t(key: str, *args, **kwargs) -> str:
    """Translate a key into the current language.

    Supports positional and keyword formatting via str.format().

    查不到时依次退：当前语言 → 兜底语言（中文）→ key 本身。中间那一层是
    关键——少了它，任何没翻到的 key 都会把 "frp.add_title" 原样显示给用户。
    """
    text = _translations.get(key)
    if text is None:
        text = _fallback.get(key)
    if text is None:
        text = key
    if args:
        try:
            text = text.format(*args)
        except (KeyError, IndexError, AttributeError):
            pass
    elif kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, AttributeError):
            pass
    return text
