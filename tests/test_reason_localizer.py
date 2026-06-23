from __future__ import annotations

import unittest

from backend.agent.reason_localizer import localize_reason, localize_reasons


class ReasonLocalizerTests(unittest.TestCase):
    def test_known_reasons_are_translated(self) -> None:
        self.assertEqual(localize_reason("secondary confirmation required"), "需要二次确认")
        self.assertEqual(
            localize_reason("role viewer is not allowed for risk level medium"),
            "角色 viewer 无权执行中风险操作",
        )
        self.assertEqual(
            localize_reason("service.restart: service_name is required"),
            "service.restart：缺少必填参数 service_name",
        )
        self.assertEqual(localize_reason("pid must be integer"), "参数 pid 必须是整数")
        self.assertEqual(
            localize_reason("clean operation is only allowed under safe temp directories: /etc"),
            "清理操作只允许在安全临时目录下进行：/etc",
        )

    def test_unknown_or_chinese_reason_passes_through(self) -> None:
        # Already-Chinese orchestration messages are left untouched.
        self.assertEqual(localize_reason("编排步骤 id 重复：s1"), "编排步骤 id 重复：s1")
        self.assertEqual(localize_reason("some unmapped reason"), "some unmapped reason")

    def test_localize_reasons_dedupes(self) -> None:
        result = localize_reasons(
            [
                "secondary confirmation required",
                "secondary confirmation required",
                "role viewer is not allowed for risk level medium",
            ]
        )
        self.assertEqual(result, ["需要二次确认", "角色 viewer 无权执行中风险操作"])


if __name__ == "__main__":
    unittest.main()
