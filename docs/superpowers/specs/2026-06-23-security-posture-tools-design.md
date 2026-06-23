# 安全态势感知工具 设计文档

- 日期：2026-06-23
- 分支：`feature/security-posture-tools`
- 范围：新增三个只读安全态势感知工具，强化项目"安全运维"主线，并可被多步推理闭环自动调用。

## 1. 背景与目标

项目定位是"面向麒麟操作系统的**安全**智能运维 Agent"，但现有感知工具（system/process/network/log/service/disk）偏通用运维，安全视角较弱。本设计新增三个只读工具，补齐登录审计、防火墙暴露面、提权风险三个安全态势维度，使"安全"主题在能力层面落地。

三个工具都是只读 perception 工具，纳入 `LOW_RISK_TOOLS`，因此可被多步推理闭环自动串联用于诊断（例如失败登录激增 → 自动看防火墙暴露面）。

### 已锁定的设计决策

| 决策点 | 取定 |
|---|---|
| 工具范围 | 登录/认证审计、防火墙与暴露面、提权风险扫描（不含安全补丁查询） |
| SUID 扫描范围 | 限定常见特权目录，不扫全盘 |
| Windows 行为 | Linux/麒麟优先；Windows 上优雅降级返回结构化提示，不造假数据 |
| 工具命名 | `auth` / `firewall` / `privilege`（与现有单词命名一致） |

## 2. 非目标（YAGNI）

- 不做安全补丁/漏洞更新查询（`dnf updateinfo`）。
- 不提供 Windows 等价安全命令（Windows 上仅返回降级提示）。
- 不新增任何操作类（状态变更）能力——三个工具全部只读。
- 不扫描全盘 SUID（仅限定特权目录）。
- 不改动现有工具与安全校验链路；只新增工具与其接入点。

## 3. 三个工具

### 3.1 `auth` — 登录/认证审计

| 子命令（Linux 模板） | 用途 |
|---|---|
| `last -n {lines}` | 近期成功登录 |
| `lastb -n {lines}` | 失败登录 / 暴力破解迹象（读 `/var/log/btmp`，需 root） |
| `who` | 当前登录会话 |

`_analyze` 输出：`failed_login_count`、`success_login_count`、`active_sessions`、`root_remote_login`（是否存在 root 远程登录）、`top_source_ips`（来源 IP 计数）。

### 3.2 `firewall` — 防火墙与暴露面

| 子命令（Linux 模板） | 用途 |
|---|---|
| `firewall-cmd --state` | 防火墙是否运行 |
| `firewall-cmd --list-all` | 默认区域：开放端口、服务、规则 |

`_analyze` 输出：`running`（bool）、`open_port_count`、`open_service_count`、`high_risk_exposed`（是否放行高危端口，如 23/3389 等）。

### 3.3 `privilege` — 提权风险扫描

| 子命令（Linux 模板） | 用途 |
|---|---|
| SUID/SGID 扫描 | `find /usr/bin /usr/sbin /bin /sbin /usr/local/bin -xdev -perm -4000 -type f`（SUID）；同样方式 `-perm -2000`（SGID） |
| UID 0 账户 | `awk -F: '($3 == 0) {print $1}' /etc/passwd` |
| 空密码账户 | `awk -F: '($2 == "") {print $1}' /etc/shadow`（需 root） |

`_analyze` 输出：`suid_count`、`sgid_count`、`suid_files`（清单，截断）、`extra_uid0_accounts`（除 root 外的 UID 0 账户）、`empty_password_accounts`。

> 命令模板均为固定 argv（仅 `{lines}` 是参数，经 `SAFE_PARAM` 校验）；`find` 不接收外部路径参数，目录写死在模板内，天然安全、保持"命令模板"不变量。

## 4. 最小权限下的优雅降级

生产环境服务以低权限用户 `software-cup-agent` 运行，**无法读取 `/var/log/btmp`（lastb）与 `/etc/shadow`**。这是正确的安全姿态，不应让工具崩溃：

- 每个子命令通过 `run_optional_template` 调用，失败时返回 `{"error": ...}` 而非抛异常。
- 工具聚合所有可读结果，对读不到的来源在输出中标注 `permission_denied: true` 或同义提示。
- `_analyze` 对缺失来源做空值兜底，不因某一子命令失败而整体失败。

这一行为本身呼应项目"最小权限"主线：工具自身也受最小权限约束。

## 5. Windows 降级

当 `os.name == "nt"`：三个工具的 `run()` 直接返回结构化提示，不调用任何命令：

```python
{"platform": "windows", "message": "该安全工具面向麒麟/Linux，开发环境不可用。", "analysis": {...空值...}}
```

参照现有 `log_tool` 在 Windows 上对 journal 的处理方式。

## 6. 接入点（每个工具都要同步）

1. `backend/mcp_tools/command_runner.py`：在 `COMMAND_TEMPLATES["linux"]` 新增模板（windows 不加）。
2. `backend/mcp_tools/auth_tool.py` / `firewall_tool.py` / `privilege_tool.py`：新建工具模块，各含 `run(arguments)` 与 `_analyze*`。
3. `backend/mcp_tools/builtin.py`：注册三个工具（`category="security"`、`read_only=True`、risk_level 默认 low、`input_schema`）。
4. `backend/security/rules.py`：`LOW_RISK_TOOLS` 加入 `"auth"`、`"firewall"`、`"privilege"`。
5. `backend/agent/planner.py`：关键词规则——登录/失败/爆破/认证 → `auth`；防火墙/暴露/开放端口 → `firewall`；提权/SUID/权限/特权 → `privilege`。
6. `backend/agent/prompt.py`：规划 prompt 工具清单新增三项及说明。
7. `backend/agent/llm_client.py`：`analyze()` 的工具白名单集合新增三项。

## 7. 测试计划（unittest，TDD 先行）

- `auth` / `firewall` / `privilege` 各自的 `_analyze` 纯函数：给定样本命令输出，断言统计与风险判定正确（含高危端口识别、额外 UID 0 账户识别、失败登录计数）。
- Windows 降级：`os.name` patch 为 `"nt"` 时返回降级提示且不调用真实命令。
- 权限降级：某子命令返回 `error` 时，工具聚合不崩溃并标注权限不足。
- 接入校验：三工具出现在 `tool_manifest`、在 `LOW_RISK_TOOLS`；planner 关键词能命中对应工具；`llm_client.analyze` 白名单接受三工具名。

## 8. 文档更新

- 新增 `docs/security-posture-tools.md`：三工具、命令模板、最小权限降级、分析逻辑、安全态势解读。
- 更新 `docs/system-perception-tools.md`：补充三个安全感知工具。
- 更新 `CLAUDE.md`：工具列表、API 工具数量、`LOW_RISK_TOOLS` 说明。

## 9. 风险与缓解

- **`lastb`/`/etc/shadow` 在生产读不到** → 设计即按优雅降级处理，标注权限不足而非失败（见 §4）。
- **`find` 扫描耗时** → 限定特权目录 + `-xdev`，通常 < 1s，沿用默认 8s 超时。
- **闭环误触发** → 三工具均只读，进 `LOW_RISK_TOOLS`，闭环自动执行是安全的；任何操作类后续动作仍需二次确认（既有不变量）。
- **跨平台** → Windows 优雅降级，单元测试覆盖降级路径，不依赖真实安全命令。
