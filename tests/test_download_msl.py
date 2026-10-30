"""MSL 镜像不可用时的官方源兜底。

`api.mslmc.cn` 是社区维护的第三方服务：它改接口、限速或者直接挂掉，「开服」
这条路的第一步就断了。这里钉住三件事：

* 有官方 API 的核心（paper / purpur / vanilla / fabric …）要真的能兜住；
* 没有官方直链的核心（spigot / forge / 各种 arclight 分支）要老实返回 None，
  不能假装有；
* 镜像正常时不许去问官方源（多一次跨境请求就是多几秒等待）。
"""

import unittest
from unittest import mock

import download_msl as dm


class OfficialPaperFallbackTest(unittest.TestCase):
    def test_picks_the_highest_build_with_a_download(self):
        payload = {
            "builds": [
                {"build": 3, "downloads": {"application": {"name": "a.jar", "sha256": "s3"}}},
                {"build": 9, "downloads": {"application": {"name": "b.jar", "sha256": "s9"}}},
                {"build": 12, "downloads": {}},      # 没得下，必须跳过
            ]
        }
        with mock.patch.object(dm, "fetch_json", return_value=payload):
            got = dm._official_download_url("paper", "1.21.8")
        self.assertEqual(got["sha256"], "s9")
        self.assertIn("/builds/9/downloads/b.jar", got["url"])

    def test_returns_none_when_nothing_is_downloadable(self):
        with mock.patch.object(dm, "fetch_json", return_value={"builds": []}):
            self.assertIsNone(dm._official_download_url("paper", "1.21.8"))

    def test_returns_none_when_the_api_is_down(self):
        with mock.patch.object(dm, "fetch_json", return_value=None):
            self.assertIsNone(dm._official_download_url("paper", "1.21.8"))


class UnsupportedCoreTest(unittest.TestCase):
    """没有官方直链的核心不许假装能兜。"""

    def test_cores_without_an_official_source_are_not_pretended(self):
        for core in ("spigot", "forge", "neoforge", "arclight-forge", "banner"):
            self.assertIsNone(dm._official_download_url(core, "1.20.1"), core)

    def test_versions_are_not_pretended_either(self):
        self.assertIsNone(dm._official_versions("spigot"))


class FallbackWiringTest(unittest.TestCase):
    def test_download_url_falls_back_when_the_mirror_fails(self):
        with mock.patch.object(dm, "_msl_request", return_value=None), \
                mock.patch.object(dm, "_official_download_url",
                                  return_value={"url": "https://x/y.jar", "sha256": ""}):
            got = dm.msl_get_download_url("paper", "1.21.8")
        self.assertEqual(got["url"], "https://x/y.jar")

    def test_mirror_answer_is_used_as_is(self):
        mirror = {"url": "https://msl/z.jar", "sha256": "abc"}
        with mock.patch.object(dm, "_msl_request", return_value=mirror), \
                mock.patch.object(dm, "_official_download_url",
                                  side_effect=AssertionError("镜像正常时不该兜底")):
            self.assertIs(dm.msl_get_download_url("paper", "1.21.8"), mirror)

    def test_server_types_fall_back_to_the_builtin_list(self):
        with mock.patch.object(dm, "_msl_request", return_value=None):
            got = dm.msl_get_server_types()
        self.assertIn("paper", got["pluginsCore"])
        self.assertIn("fabric", got["modsCore_Fabric"])
        self.assertIn("vanilla", got["vanillaCore"])

    def test_versions_fall_back_and_are_newest_first(self):
        """_interactive_pick_version 把 versions[0] 当最新版，顺序不能反。"""
        with mock.patch.object(dm, "_msl_request", return_value=None), \
                mock.patch.object(dm, "fetch_json",
                                  return_value={"versions": ["1.20.1", "1.21.8"]}):
            got = dm.msl_get_versions("paper")
        self.assertEqual(got["versions"][0], "1.21.8")


class SourceOrderTest(unittest.TestCase):
    """下载源有三种模式，每一档都是「首选 + 两级兜底」。"""

    def tearDown(self):
        dm.set_source_preference("msl")

    def test_official_mode_asks_official_first(self):
        dm.set_source_preference("official")
        with mock.patch.object(
                dm, "_official_download_url",
                return_value={"url": "https://official/x.jar", "sha256": ""}) as official, \
                mock.patch.object(
                    dm, "_msl_request",
                    side_effect=AssertionError("官方优先时不该先问镜像")):
            got = dm.msl_get_download_url("paper", "1.21.8")
        official.assert_called_once()
        self.assertEqual(got["url"], "https://official/x.jar")

    def test_official_mode_falls_back_to_bmcl(self):
        dm.set_source_preference("official")
        with mock.patch.object(dm, "_official_download_url", return_value=None), \
                mock.patch.object(
                    dm, "_bmcl_download_url",
                    return_value={"url": "https://bmcl/x.jar", "sha256": ""}):
            got = dm.msl_get_download_url("vanilla", "1.21.8")
        self.assertEqual(got["url"], "https://bmcl/x.jar")

    def test_official_mode_falls_back_to_msl_last(self):
        dm.set_source_preference("official")
        mirror = {"url": "https://msl/z.jar", "sha256": "abc"}
        with mock.patch.object(dm, "_official_download_url", return_value=None), \
                mock.patch.object(dm, "_bmcl_download_url", return_value=None), \
                mock.patch.object(dm, "_msl_request", return_value=mirror):
            self.assertIs(dm.msl_get_download_url("paper", "1.21.8"), mirror)

    def test_bmcl_mode_tries_bmcl_first(self):
        dm.set_source_preference("bmcl")
        with mock.patch.object(
                dm, "_bmcl_download_url",
                return_value={"url": "https://bmcl/x.jar", "sha256": ""}), \
                mock.patch.object(
                    dm, "_msl_request",
                    side_effect=AssertionError("BMCL 优先时不该先问镜像")):
            got = dm.msl_get_download_url("vanilla", "1.21.8")
        self.assertEqual(got["url"], "https://bmcl/x.jar")

    def test_versions_follow_the_same_order(self):
        dm.set_source_preference("official")
        with mock.patch.object(dm, "_official_versions",
                               return_value={"versions": ["1.21.8"]}) as official, \
                mock.patch.object(
                    dm, "_msl_request",
                    side_effect=AssertionError("官方优先时不该先问镜像")):
            got = dm.msl_get_versions("paper")
        official.assert_called_once()
        self.assertEqual(got["versions"][0], "1.21.8")

    def test_legacy_boolean_still_works(self):
        dm.set_source_preference(True)
        self.assertEqual(dm.source_preference(), "bmcl")
        dm.set_source_preference(False)
        self.assertEqual(dm.source_preference(), "msl")

    def test_unknown_mode_falls_back_to_default(self):
        dm.set_source_preference("nonsense")
        self.assertEqual(dm.source_preference(), "msl")

    def test_config_migration(self):
        self.assertEqual(dm.source_from_config({"prefer_bmcl": True}), "bmcl")
        self.assertEqual(dm.source_from_config({"prefer_bmcl": False}), "msl")
        self.assertEqual(dm.source_from_config({}), "msl")
        self.assertEqual(
            dm.source_from_config({"download_source": "official",
                                   "prefer_bmcl": True}), "official")
        self.assertEqual(dm.source_from_config({"download_source": "bogus"}), "msl")


if __name__ == "__main__":
    unittest.main()
