"""i18n：缺翻译要回退到中文，而不是把 i18n key 直接显示给用户。"""

import json
import os
import unittest

import i18n
from i18n import FALLBACK_LANG, load_language, t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES = os.path.join(ROOT, "locales")


def _locale(name):
    with open(os.path.join(LOCALES, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


class I18nFallbackTest(unittest.TestCase):
    def tearDown(self):
        load_language("zh")

    def test_translated_key_wins_over_fallback(self):
        load_language("neko")
        # neko 自己翻过的 key 必须用它自己的
        self.assertIn("喵", t("menu.mod.header"))

    def test_missing_key_falls_back_to_chinese(self):
        """neko 没翻的 key 要显示中文，绝不能是裸 key。"""
        load_language("neko")
        # neko 现在已经翻全了（见 test_neko_is_complete），所以手动挖掉一条，
        # 模拟「这个语言还没翻到这里」的情况。
        i18n._translations.pop("frp.add_title", None)
        value = t("frp.add_title")
        self.assertNotEqual(value, "frp.add_title")
        self.assertEqual(value, _locale(FALLBACK_LANG)["frp.add_title"])

    def test_fallback_value_still_gets_formatted(self):
        load_language("neko")
        text = t("frp.add_saved", name="我的隧道")
        self.assertIn("我的隧道", text)
        self.assertNotIn("{name}", text)

    def test_unknown_key_falls_back_to_the_key_itself(self):
        load_language("neko")
        self.assertEqual(t("no.such.key.exists"), "no.such.key.exists")

    def test_zh_is_unaffected(self):
        load_language("zh")
        self.assertEqual(t("frp.add_title"), _locale("zh")["frp.add_title"])

    def test_missing_locale_file_does_not_crash(self):
        load_language("does-not-exist")
        self.assertEqual(t("no.such.key"), "no.such.key")
        load_language("zh")

    def test_english_is_complete(self):
        """en 缺任何一个 zh 有的 key，都会让英文用户看到裸 key。"""
        zh, en = _locale("zh"), _locale("en")
        missing = sorted(k for k in zh if k not in en and k != "_fallback")
        self.assertEqual(missing, [])

    def test_russian_is_complete(self):
        zh, ru = _locale("zh"), _locale("ru")
        missing = sorted(k for k in zh if k not in ru and k != "_fallback")
        self.assertEqual(missing, [])

    def test_neko_is_complete(self):
        """neko 也一样：frp/设置那一段曾经整块没翻，中文界面混着英文提示。"""
        zh, neko = _locale("zh"), _locale("neko")
        missing = sorted(k for k in zh if k not in neko and k != "_fallback")
        self.assertEqual(missing, [])

    def test_fallback_marker_is_documented_behaviour(self):
        """zh.json 里那个 _fallback 标记就是「兜底语言」的意思，别再放着没人读。"""
        self.assertTrue(_locale(FALLBACK_LANG).get("_fallback"))
        self.assertEqual(FALLBACK_LANG, "zh")


class PlaceholderParityTest(unittest.TestCase):
    """同一 key 在各语言里的占位符必须一致，否则 format 会留下 {xxx}。"""

    def test_placeholders_match_across_locales(self):
        import re
        pattern = re.compile(r"\{(\w+)\}")
        locales = {n: _locale(n) for n in ("zh", "en", "ru", "neko")}
        problems = []
        for key, value in locales["zh"].items():
            if not isinstance(value, str):
                continue
            want = set(pattern.findall(value))
            for name, table in locales.items():
                other = table.get(key)
                if not isinstance(other, str):
                    continue
                got = set(pattern.findall(other))
                if got != want:
                    problems.append(f"{key} [{name}]: {sorted(want)} vs {sorted(got)}")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
