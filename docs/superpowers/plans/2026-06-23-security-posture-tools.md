# 安全态势感知工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `auth` / `firewall` / `privilege` 三个只读安全态势感知工具，强化项目"安全运维"主线，并可被多步推理闭环自动调用。

**Architecture:** 每个工具沿用现有模式：在 `command_runner.COMMAND_TEMPLATES["linux"]` 新增白名单命令模板，新建 `backend/mcp_tools/<tool>_tool.py`（`run()` + 纯函数 `_analyze`），在 `builtin.py` 注册，加入 `LOW_RISK_TOOLS`，并补 planner 关键词、规划 prompt 工具清单与 `llm_client.analyze` 白名单。Windows 上优雅降级，不调用真实命令。

**Tech Stack:** Python 3 / FastAPI / 标准库 `unittest`。

## Global Constraints

- 所有系统命令必须经 `command_runner` 模板（`run_template`/`run_optional_template`），禁止直接 `subprocess` 或拼接 shell。
- 三个工具全部**只读**（`read_only=True`，risk_level 默认 low），并加入 `backend/security/rules.LOW_RISK_TOOLS`。
- Windows（`os.name == "nt"`）上不调用真实命令，返回结构化降级提示 `"该安全工具面向麒麟/Linux，开发环境不可用。"`。
- 最小权限下读不到的来源（`lastb`/`/etc/shadow`）经 `run_optional_template` 返回 `{"error": ...}`，工具聚合不崩溃并在 analysis 中标注可读性布尔位。
- SUID/SGID 扫描限定特权目录 `/usr/bin /usr/sbin /bin /sbin /usr/local/bin`，不扫全盘。
- 工具命名：`auth` / `firewall` / `privilege`。
- 文档默认中文；代码标识符、命令、JSON 字段保持原文。
- 测试入口：`python -m unittest discover -v`（标准库 unittest）。
- 提交信息结尾附：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: `auth` 登录认证审计工具

**Files:**
- Modify: `backend/mcp_tools/command_runner.py`（`COMMAND_TEMPLATES["linux"]` 新增三条）
- Create: `backend/mcp_tools/auth_tool.py`
- Modify: `backend/mcp_tools/builtin.py`（导入 + 注册）
- Modify: `backend/security/rules.py:7`（`LOW_RISK_TOOLS` 加 `"auth"`）
- Modify: `backend/agent/planner.py`（关键词规则加 auth）
- Modify: `backend/agent/prompt.py`（工具清单加 auth）
- Modify: `backend/agent/llm_client.py:79`（白名单集合加 `"auth"`）
- Test: `tests/test_auth_tool.py`

**Interfaces:**
- Produces: `backend.mcp_tools.auth_tool.run(arguments: dict) -> dict` 和 `_analyze(last: dict, failed: dict, sessions: dict) -> dict`（analysis 键：`success_login_count`、`failed_login_count`、`active_sessions`、`root_remote_login`、`top_source_ips`、`failed_log_readable`）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_tool.py
import os
import unittest
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import auth_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.rules import LOW_RISK_TOOLS


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class AuthAnalyzeTest(unittest.TestCase):
    def test_counts_root_remote_and_top_ips(self):
        last = {"stdout": [
            "root     pts/0   192.168.1.5    Mon Jun 23 10:00   still logged in",
            "alice    pts/1   192.168.1.9    Mon Jun 23 09:00 - 09:30  (00:30)",
            "wtmp begins Mon Jun 1 00:00:00 2026",
        ]}
        failed = {"stdout": [
            "baduser  ssh:notty 10.0.0.9   Mon Jun 23 08:00",
            "baduser  ssh:notty 10.0.0.9   Mon Jun 23 08:01",
        ]}
        who = {"stdout": ["root pts/0 2026-06-23 10:00 (192.168.1.5)", ""]}
        analysis = auth_tool._analyze(last, failed, who)
        self.assertEqual(analysis["success_login_count"], 2)
        self.assertEqual(analysis["failed_login_count"], 2)
        self.assertEqual(analysis["active_sessions"], 1)
        self.assertTrue(analysis["root_remote_login"])
        self.assertEqual(analysis["top_source_ips"]["10.0.0.9"], 2)
        self.assertTrue(analysis["failed_log_readable"])

    def test_failed_log_unreadable_flagged(self):
        analysis = auth_tool._analyze({"stdout": []}, {"error": "permission denied"}, {"stdout": []})
        self.assertFalse(analysis["failed_log_readable"])
        self.assertEqual(analysis["failed_login_count"], 0)


class AuthWindowsDegradeTest(unittest.TestCase):
    def test_windows_returns_message_without_commands(self):
        with mock.patch("backend.mcp_tools.auth_tool.os.name", "nt"), \
             mock.patch("backend.mcp_tools.auth_tool.run_optional_template") as runner:
            result = auth_tool.run({})
            runner.assert_not_called()
        self.assertEqual(result["platform"], "windows")
        self.assertIn("麒麟", result["message"])


class AuthWiringTest(unittest.TestCase):
    def test_registered_and_low_risk(self):
        registry = build_registry()
        self.assertIn("auth", registry.names())
        self.assertIn("auth", LOW_RISK_TOOLS)

    def test_planner_keyword_selects_auth(self):
        plan = _disabled_planner().plan("最近有没有暴力破解登录", {}, None)
        self.assertIn("auth", plan.tools)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_auth_tool -v`
Expected: FAIL — `ModuleNotFoundError: backend.mcp_tools.auth_tool`.

- [ ] **Step 3a: Add command templates** — in `backend/mcp_tools/command_runner.py`, inside `COMMAND_TEMPLATES["linux"]` (after the `service.restart` line), add:

```python
        "auth.last": ["last", "-n", "{lines}"],
        "auth.lastb": ["lastb", "-n", "{lines}"],
        "auth.who": ["who"],
```

- [ ] **Step 3b: Create `backend/mcp_tools/auth_tool.py`**

```python
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template

WINDOWS_MESSAGE = "该安全工具面向麒麟/Linux，开发环境不可用。"
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {"platform": "windows", "message": WINDOWS_MESSAGE, "analysis": _analyze({}, {}, {})}
    lines = str(_clamp_lines(arguments.get("lines", 20)))
    last = run_optional_template("auth.last", {"lines": lines}, timeout=8)
    failed = run_optional_template("auth.lastb", {"lines": lines}, timeout=8)
    sessions = run_optional_template("auth.who", timeout=8)
    return {
        "source": "auth",
        "last": last,
        "lastb": failed,
        "who": sessions,
        "analysis": _analyze(last, failed, sessions),
    }


def _analyze(last: dict[str, Any], failed: dict[str, Any], sessions: dict[str, Any]) -> dict[str, Any]:
    last_lines = _entry_lines(last)
    failed_lines = _entry_lines(failed)
    who_lines = [line for line in _stdout(sessions) if line.strip()]
    return {
        "success_login_count": len(last_lines),
        "failed_login_count": len(failed_lines),
        "active_sessions": len(who_lines),
        "root_remote_login": _has_root_remote(last_lines),
        "top_source_ips": _top_source_ips(last_lines + failed_lines),
        "failed_log_readable": "error" not in failed,
    }


def _stdout(result: dict[str, Any]) -> list[str]:
    value = result.get("stdout") if isinstance(result, dict) else None
    return value if isinstance(value, list) else []


def _entry_lines(result: dict[str, Any]) -> list[str]:
    entries: list[str] = []
    for line in _stdout(result):
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("wtmp begins") or lowered.startswith("btmp begins"):
            continue
        entries.append(stripped)
    return entries


def _has_root_remote(lines: list[str]) -> bool:
    return any(line.startswith("root") and _IP_PATTERN.search(line) for line in lines)


def _top_source_ips(lines: list[str], top: int = 3) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for line in lines:
        for ip in _IP_PATTERN.findall(line):
            counter[ip] += 1
    return dict(counter.most_common(top))


def _clamp_lines(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return max(1, min(parsed, 200))
```

- [ ] **Step 3c: Register in `backend/mcp_tools/builtin.py`** — add `auth_tool` to the `from backend.mcp_tools import (...)` import block, and register inside `register_builtin_tools` (after the `disk` registration):

```python
    registry.register(
        ToolDefinition(
            name="auth",
            title="登录认证审计工具",
            description="采集近期成功登录、失败登录和当前会话，分析暴力破解与异常登录迹象。",
            category="security",
            handler=auth_tool.run,
            command_templates=["auth.last", "auth.lastb", "auth.who"],
            input_schema={
                "type": "object",
                "properties": {"lines": {"type": "integer", "minimum": 1, "maximum": 200}},
            },
        )
    )
```

- [ ] **Step 3d: Add to `LOW_RISK_TOOLS`** — in `backend/security/rules.py:7`:

```python
LOW_RISK_TOOLS = {"system", "process", "network", "log", "service", "disk", "auth", "firewall", "privilege"}
```

> 注：本步一次性加入三个名字，后续 Task 2/3 不再改这一行。

- [ ] **Step 3e: Add planner keyword** — in `backend/agent/planner.py`, after the `disk` rule (the `if self._contains_any(text, ["disk", ...])` block), add:

```python
        if self._contains_any(text, ["登录", "认证", "爆破", "暴力破解", "失败登录", "login", "auth", "brute"]):
            tools.append("auth")
```

- [ ] **Step 3f: Add to planning prompt** — in `backend/agent/prompt.py`, in `PLANNING_SYSTEM_PROMPT`: extend the `"tools"` enum line to include `auth|firewall|privilege`, and append to 工具说明:

```
- auth: 登录与认证审计，成功/失败登录、当前会话、暴力破解迹象
- firewall: 防火墙状态与开放端口/服务暴露面（只读）
- privilege: 提权风险扫描，SUID/SGID 文件、UID 0 账户、空密码账户
```

The tools enum line becomes:
```
  "tools": ["system|process|process.kill|network|log|service|service.restart|temp.clean|disk|auth|firewall|privilege"],
```

- [ ] **Step 3g: Add to LLM whitelist** — in `backend/agent/llm_client.py:79`, extend the set to include the three new names:

```python
            if tool in {"system", "process", "process.kill", "network", "log", "service", "service.restart", "temp.clean", "disk", "auth", "firewall", "privilege"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_auth_tool -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run full suite (no regression)**

Run: `python -m unittest discover -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_tools/command_runner.py backend/mcp_tools/auth_tool.py backend/mcp_tools/builtin.py backend/security/rules.py backend/agent/planner.py backend/agent/prompt.py backend/agent/llm_client.py tests/test_auth_tool.py
git commit -m "feat(tools): 新增 auth 登录认证审计安全感知工具

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `firewall` 防火墙与暴露面工具

**Files:**
- Modify: `backend/mcp_tools/command_runner.py`（`COMMAND_TEMPLATES["linux"]` 新增两条）
- Create: `backend/mcp_tools/firewall_tool.py`
- Modify: `backend/mcp_tools/builtin.py`（导入 + 注册）
- Modify: `backend/agent/planner.py`（关键词规则加 firewall）
- Test: `tests/test_firewall_tool.py`

**Interfaces:**
- Consumes: `LOW_RISK_TOOLS` 已含 `"firewall"`（Task 1 Step 3d 已加）；planning prompt 与 LLM 白名单已含 `firewall`（Task 1 已加）。
- Produces: `backend.mcp_tools.firewall_tool.run(arguments) -> dict` 与 `_analyze(state: dict, listing: dict) -> dict`（analysis 键：`running`、`open_port_count`、`open_service_count`、`open_ports`、`open_services`、`high_risk_exposed`、`readable`）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_firewall_tool.py
import unittest
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import firewall_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.rules import LOW_RISK_TOOLS


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class FirewallAnalyzeTest(unittest.TestCase):
    def test_parses_ports_services_and_high_risk(self):
        state = {"stdout": ["running"]}
        listing = {"stdout": [
            "public (active)",
            "  target: default",
            "  services: ssh dhcpv6-client",
            "  ports: 22/tcp 23/tcp 8000/tcp",
        ]}
        analysis = firewall_tool._analyze(state, listing)
        self.assertTrue(analysis["running"])
        self.assertEqual(analysis["open_port_count"], 3)
        self.assertEqual(analysis["open_service_count"], 2)
        self.assertIn("23", analysis["high_risk_exposed"])
        self.assertTrue(analysis["readable"])

    def test_not_running_and_unreadable(self):
        analysis = firewall_tool._analyze({"stdout": ["not running"]}, {"error": "permission denied"})
        self.assertFalse(analysis["running"])
        self.assertFalse(analysis["readable"])
        self.assertEqual(analysis["open_port_count"], 0)


class FirewallWindowsDegradeTest(unittest.TestCase):
    def test_windows_returns_message_without_commands(self):
        with mock.patch("backend.mcp_tools.firewall_tool.os.name", "nt"), \
             mock.patch("backend.mcp_tools.firewall_tool.run_optional_template") as runner:
            result = firewall_tool.run({})
            runner.assert_not_called()
        self.assertEqual(result["platform"], "windows")
        self.assertIn("麒麟", result["message"])


class FirewallWiringTest(unittest.TestCase):
    def test_registered_and_low_risk(self):
        registry = build_registry()
        self.assertIn("firewall", registry.names())
        self.assertIn("firewall", LOW_RISK_TOOLS)

    def test_planner_keyword_selects_firewall(self):
        plan = _disabled_planner().plan("防火墙有没有开放危险端口", {}, None)
        self.assertIn("firewall", plan.tools)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_firewall_tool -v`
Expected: FAIL — `ModuleNotFoundError: backend.mcp_tools.firewall_tool`.

- [ ] **Step 3a: Add command templates** — in `command_runner.py`, inside `COMMAND_TEMPLATES["linux"]` (after the `auth.who` line), add:

```python
        "firewall.state": ["firewall-cmd", "--state"],
        "firewall.list_all": ["firewall-cmd", "--list-all"],
```

- [ ] **Step 3b: Create `backend/mcp_tools/firewall_tool.py`**

```python
from __future__ import annotations

import os
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template

WINDOWS_MESSAGE = "该安全工具面向麒麟/Linux，开发环境不可用。"
_HIGH_RISK_PORTS = {"23", "21", "3389", "445", "135", "139"}
_HIGH_RISK_SERVICES = {"telnet", "rdp", "ftp", "samba"}


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {"platform": "windows", "message": WINDOWS_MESSAGE, "analysis": _analyze({}, {})}
    state = run_optional_template("firewall.state", timeout=8)
    listing = run_optional_template("firewall.list_all", timeout=8)
    return {"source": "firewall", "state": state, "list_all": listing, "analysis": _analyze(state, listing)}


def _analyze(state: dict[str, Any], listing: dict[str, Any]) -> dict[str, Any]:
    running = "running" in " ".join(_stdout(state)).lower()
    ports = _parse_field(listing, "ports:")
    services = _parse_field(listing, "services:")
    high_risk = sorted(
        {port.split("/")[0] for port in ports if port.split("/")[0] in _HIGH_RISK_PORTS}
        | {service for service in services if service in _HIGH_RISK_SERVICES}
    )
    return {
        "running": running,
        "open_port_count": len(ports),
        "open_service_count": len(services),
        "open_ports": ports,
        "open_services": services,
        "high_risk_exposed": high_risk,
        "readable": "error" not in listing,
    }


def _stdout(result: dict[str, Any]) -> list[str]:
    value = result.get("stdout") if isinstance(result, dict) else None
    return value if isinstance(value, list) else []


def _parse_field(listing: dict[str, Any], field: str) -> list[str]:
    for line in _stdout(listing):
        stripped = line.strip()
        if stripped.startswith(field):
            return stripped[len(field):].split()
    return []
```

- [ ] **Step 3c: Register in `builtin.py`** — add `firewall_tool` to the import block, and register (after the `auth` registration):

```python
    registry.register(
        ToolDefinition(
            name="firewall",
            title="防火墙暴露面工具",
            description="只读查看 firewalld 运行状态、默认区域开放端口与服务，识别高危暴露面。",
            category="security",
            handler=firewall_tool.run,
            command_templates=["firewall.state", "firewall.list_all"],
            input_schema={"type": "object", "properties": {}},
        )
    )
```

- [ ] **Step 3d: Add planner keyword** — in `backend/agent/planner.py`, after the `auth` rule added in Task 1, add:

```python
        if self._contains_any(text, ["防火墙", "firewall", "暴露", "开放端口", "iptables", "exposure"]):
            tools.append("firewall")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_firewall_tool -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run full suite**

Run: `python -m unittest discover -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_tools/command_runner.py backend/mcp_tools/firewall_tool.py backend/mcp_tools/builtin.py backend/agent/planner.py tests/test_firewall_tool.py
git commit -m "feat(tools): 新增 firewall 防火墙暴露面安全感知工具

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `privilege` 提权风险扫描工具

**Files:**
- Modify: `backend/mcp_tools/command_runner.py`（`COMMAND_TEMPLATES["linux"]` 新增四条）
- Create: `backend/mcp_tools/privilege_tool.py`
- Modify: `backend/mcp_tools/builtin.py`（导入 + 注册）
- Modify: `backend/agent/planner.py`（关键词规则加 privilege）
- Test: `tests/test_privilege_tool.py`

**Interfaces:**
- Consumes: `LOW_RISK_TOOLS` 已含 `"privilege"`；prompt 与 LLM 白名单已含 `privilege`（Task 1 已加）。
- Produces: `backend.mcp_tools.privilege_tool.run(arguments) -> dict` 与 `_analyze(suid, sgid, uid0, empty_pw) -> dict`（analysis 键：`suid_count`、`sgid_count`、`suid_files`、`extra_uid0_accounts`、`empty_password_accounts`、`shadow_readable`）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_privilege_tool.py
import unittest
from unittest import mock

from backend.agent.llm_client import LLMClient
from backend.agent.planner import Planner
from backend.config import LLMSettings
from backend.mcp_tools import privilege_tool
from backend.mcp_tools.builtin import build_registry
from backend.security.rules import LOW_RISK_TOOLS


def _disabled_planner() -> Planner:
    return Planner(LLMClient(LLMSettings(provider="disabled", api_key="", base_url="", model="")))


class PrivilegeAnalyzeTest(unittest.TestCase):
    def test_counts_suid_and_extra_uid0_and_shadow(self):
        suid = {"stdout": ["/usr/bin/passwd", "/usr/bin/sudo"]}
        sgid = {"stdout": ["/usr/bin/wall"]}
        uid0 = {"stdout": ["root", "backdoor"]}
        empty_pw = {"error": "permission denied"}
        analysis = privilege_tool._analyze(suid, sgid, uid0, empty_pw)
        self.assertEqual(analysis["suid_count"], 2)
        self.assertEqual(analysis["sgid_count"], 1)
        self.assertEqual(analysis["extra_uid0_accounts"], ["backdoor"])
        self.assertFalse(analysis["shadow_readable"])
        self.assertEqual(analysis["empty_password_accounts"], [])

    def test_clean_system_has_no_extra_uid0(self):
        analysis = privilege_tool._analyze({"stdout": []}, {"stdout": []}, {"stdout": ["root"]}, {"stdout": []})
        self.assertEqual(analysis["extra_uid0_accounts"], [])
        self.assertTrue(analysis["shadow_readable"])


class PrivilegeWindowsDegradeTest(unittest.TestCase):
    def test_windows_returns_message_without_commands(self):
        with mock.patch("backend.mcp_tools.privilege_tool.os.name", "nt"), \
             mock.patch("backend.mcp_tools.privilege_tool.run_optional_template") as runner:
            result = privilege_tool.run({})
            runner.assert_not_called()
        self.assertEqual(result["platform"], "windows")
        self.assertIn("麒麟", result["message"])


class PrivilegeWiringTest(unittest.TestCase):
    def test_registered_and_low_risk(self):
        registry = build_registry()
        self.assertIn("privilege", registry.names())
        self.assertIn("privilege", LOW_RISK_TOOLS)

    def test_planner_keyword_selects_privilege(self):
        plan = _disabled_planner().plan("扫一下有没有提权风险的 SUID 文件", {}, None)
        self.assertIn("privilege", plan.tools)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_privilege_tool -v`
Expected: FAIL — `ModuleNotFoundError: backend.mcp_tools.privilege_tool`.

- [ ] **Step 3a: Add command templates** — in `command_runner.py`, inside `COMMAND_TEMPLATES["linux"]` (after the `firewall.list_all` line), add:

```python
        "privilege.suid": ["find", "/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin", "-xdev", "-perm", "-4000", "-type", "f"],
        "privilege.sgid": ["find", "/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin", "-xdev", "-perm", "-2000", "-type", "f"],
        "privilege.uid0": ["awk", "-F:", "($3 == 0) {print $1}", "/etc/passwd"],
        "privilege.empty_password": ["awk", "-F:", "($2 == \"\") {print $1}", "/etc/shadow"],
```

> 这些模板均为固定 argv，无 `{param}` 占位符，目录与 awk 脚本写死，天然安全。

- [ ] **Step 3b: Create `backend/mcp_tools/privilege_tool.py`**

```python
from __future__ import annotations

import os
from typing import Any

from backend.mcp_tools.command_runner import run_optional_template

WINDOWS_MESSAGE = "该安全工具面向麒麟/Linux，开发环境不可用。"
_MAX_FILES = 50


def run(arguments: dict[str, Any]) -> dict[str, Any]:
    if os.name == "nt":
        return {"platform": "windows", "message": WINDOWS_MESSAGE, "analysis": _analyze({}, {}, {}, {})}
    suid = run_optional_template("privilege.suid", timeout=8)
    sgid = run_optional_template("privilege.sgid", timeout=8)
    uid0 = run_optional_template("privilege.uid0", timeout=8)
    empty_pw = run_optional_template("privilege.empty_password", timeout=8)
    return {
        "source": "privilege",
        "suid": suid,
        "sgid": sgid,
        "uid0": uid0,
        "empty_password": empty_pw,
        "analysis": _analyze(suid, sgid, uid0, empty_pw),
    }


def _analyze(
    suid: dict[str, Any],
    sgid: dict[str, Any],
    uid0: dict[str, Any],
    empty_pw: dict[str, Any],
) -> dict[str, Any]:
    suid_files = _lines(suid)
    sgid_files = _lines(sgid)
    uid0_accounts = _lines(uid0)
    return {
        "suid_count": len(suid_files),
        "sgid_count": len(sgid_files),
        "suid_files": suid_files[:_MAX_FILES],
        "extra_uid0_accounts": [name for name in uid0_accounts if name != "root"],
        "empty_password_accounts": _lines(empty_pw),
        "shadow_readable": "error" not in empty_pw,
    }


def _lines(result: dict[str, Any]) -> list[str]:
    value = result.get("stdout") if isinstance(result, dict) else None
    if not isinstance(value, list):
        return []
    return [line.strip() for line in value if line.strip()]
```

- [ ] **Step 3c: Register in `builtin.py`** — add `privilege_tool` to the import block, and register (after the `firewall` registration):

```python
    registry.register(
        ToolDefinition(
            name="privilege",
            title="提权风险扫描工具",
            description="扫描特权目录下的 SUID/SGID 文件、UID 0 账户和空密码账户，识别提权风险。",
            category="security",
            handler=privilege_tool.run,
            command_templates=[
                "privilege.suid",
                "privilege.sgid",
                "privilege.uid0",
                "privilege.empty_password",
            ],
            input_schema={"type": "object", "properties": {}},
        )
    )
```

- [ ] **Step 3d: Add planner keyword** — in `backend/agent/planner.py`, after the `firewall` rule added in Task 2, add:

```python
        if self._contains_any(text, ["提权", "suid", "sgid", "特权", "权限提升", "privilege", "escalation", "空密码"]):
            tools.append("privilege")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_privilege_tool -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run full suite**

Run: `python -m unittest discover -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_tools/command_runner.py backend/mcp_tools/privilege_tool.py backend/mcp_tools/builtin.py backend/agent/planner.py tests/test_privilege_tool.py
git commit -m "feat(tools): 新增 privilege 提权风险扫描安全感知工具

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 文档同步

**Files:**
- Create: `docs/security-posture-tools.md`
- Modify: `docs/system-perception-tools.md`
- Modify: `CLAUDE.md`

**Interfaces:** 无代码接口；文档需与 Task 1-3 的实际行为一致（读对应工具源码核对）。

- [ ] **Step 1: 写 `docs/security-posture-tools.md`**

覆盖：三个工具（`auth`/`firewall`/`privilege`）各自的命令模板、analysis 字段、最小权限下的优雅降级（`failed_log_readable`/`readable`/`shadow_readable` 标志位）、Windows 降级提示、SUID 扫描限定特权目录、三工具均只读且在 `LOW_RISK_TOOLS` 内可被闭环自动调用。

- [ ] **Step 2: 更新 `docs/system-perception-tools.md`**

在感知工具列表中补充 `auth`/`firewall`/`privilege` 三个安全感知工具的一句话说明，并指向 `docs/security-posture-tools.md`。

- [ ] **Step 3: 更新 `CLAUDE.md`**

- 项目概述/工具相关段落：工具数量由 9 增至 12，补充三个安全感知工具。
- `LOW_RISK_TOOLS` 相关说明同步加入 `auth`/`firewall`/`privilege`。
- 「API 表面」中 `GET /api/tools` 工具列表数量同步。

- [ ] **Step 4: 验证未改代码 + 提交**

Run: `python -m unittest discover -v`（应仍全绿，确认未误改代码）
然后：

```bash
git add docs/security-posture-tools.md docs/system-perception-tools.md CLAUDE.md
git commit -m "docs: 同步安全态势感知工具说明

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review（计划自检）

- **Spec coverage:** auth（Task 1）、firewall（Task 2）、privilege（Task 3）三工具及命令模板、Windows 降级、最小权限降级标志、SUID 限定目录、`LOW_RISK_TOOLS`、planner 关键词、prompt+LLM 白名单（Task 1 一次性加全部三名 + 各自关键词）、文档（Task 4）——spec 各节均有对应任务。
- **Placeholder scan:** 无 TBD / 「add error handling」等占位；每个代码步骤给出完整代码。
- **Type consistency:** `_analyze` 各工具签名与 analysis 键在任务内定义、在测试中断言一致；`run(arguments) -> dict`、`run_optional_template`、`LOW_RISK_TOOLS`、`build_registry` 跨任务一致。
- **共享文件编辑顺序:** `LOW_RISK_TOOLS` 三名在 Task 1 一次加全（Task 2/3 不再改该行）；prompt 与 LLM 白名单同样在 Task 1 一次加全；planner 关键词每个工具在各自任务加，互不冲突。
- **不变量:** 三工具只读、经命令模板执行、无新增 subprocess；Windows 降级不调真实命令。
