# 安全态势感知工具

本文档记录第二批新增的三个安全态势感知工具：`auth`（登录认证审计）、
`firewall`（防火墙暴露面）、`privilege`（提权风险扫描）。三者均为**只读**工具，
`category="security"`，与既有 `category="perception"` 的系统感知工具并列；命令
统一经 `backend/mcp_tools/command_runner.py` 的 `run_optional_template()` 执行，
不直接调用 `subprocess`，也不拼接 shell 字符串。

三者均登记在 `backend/security/rules.py` 的 `LOW_RISK_TOOLS` 集合中，因此在
`AgentOrchestrator._run_loop` 的多步推理闭环里可被自动连续调用，无需人工二次确认；
详见 `docs/multi-step-reasoning.md`。

工具注册集中在 `backend/mcp_tools/builtin.py`，注册机制见
`docs/mcp-tool-registration.md`。

## `auth`：登录认证审计工具

- 工具模块：`backend/mcp_tools/auth_tool.py`
- 输入参数：`lines`（整数，1-200，默认 20，对应 `last`/`lastb` 的展示行数）
- 命令模板（`backend/mcp_tools/command_runner.py`）：
  - `auth.last`：`last -n {lines}`，近期成功登录记录
  - `auth.lastb`：`lastb -n {lines}`，近期失败登录记录（读取 `/var/log/btmp`）
  - `auth.who`：`who`，当前活跃会话

`run()` 调用 `run_optional_template()` 分别采集三类数据，再交给 `_analyze()` 生成
`analysis` 字段：

| 字段 | 说明 |
| --- | --- |
| `success_login_count` | `last` 输出的有效登录记录数（过滤空行和 `wtmp begins ...` 提示行） |
| `failed_login_count` | `lastb` 输出的有效失败登录记录数（过滤规则同上） |
| `active_sessions` | `who` 输出的当前会话数 |
| `root_remote_login` | 是否存在以 `root` 开头且包含 IPv4 地址的登录记录，用于识别 root 远程登录 |
| `top_source_ips` | 成功+失败登录记录中出现次数最多的来源 IP（取前 3 个，`{ip: 次数}`） |
| `failed_log_readable` | `lastb` 调用结果是否未携带 `error` 字段；用于标记当前身份能否读取失败登录日志 |

最小权限优雅降级：当 Agent 以低权限身份运行、无法读取 `/var/log/btmp`（`lastb`
通常需要更高权限）时，`auth.lastb` 返回 `error` 字段，`_analyze()` 不会因此抛出
异常——`failed_login_count` 按空列表计算，`failed_log_readable` 置为 `False`，
其余基于 `last`/`who` 的字段照常给出结果。即“能读多少就分析多少，读不到的部分
用标志位明确告知”，而不是让整个工具调用失败。

## `firewall`：防火墙暴露面工具

- 工具模块：`backend/mcp_tools/firewall_tool.py`
- 输入参数：无（`input_schema` 为空对象）
- 命令模板：
  - `firewall.state`：`firewall-cmd --state`，firewalld 运行状态
  - `firewall.list_all`：`firewall-cmd --list-all`，默认区域的开放端口与服务

`_analyze()` 从 `firewall.state` 的输出中判断 `running`（包含 `running` 且不包含
`not running`，大小写不敏感）；从 `firewall.list_all` 的输出中按 `ports:` /
`services:` 前缀解析开放端口与服务列表。

| 字段 | 说明 |
| --- | --- |
| `running` | firewalld 是否处于运行状态 |
| `open_port_count` | 解析到的开放端口数量 |
| `open_service_count` | 解析到的开放服务数量 |
| `open_ports` | 开放端口列表（如 `["22/tcp", "8000/tcp"]`） |
| `open_services` | 开放服务列表（如 `["ssh", "dhcpv6-client"]`） |
| `high_risk_exposed` | 命中内置高危集合的端口/服务，按字母序排列 |
| `readable` | `firewall.list_all` 调用结果是否未携带 `error` 字段 |

高危端口集合（`_HIGH_RISK_PORTS`）：`23`（telnet）、`21`（ftp）、`3389`（rdp）、
`445`/`135`/`139`（samba/SMB 相关）。高危服务集合（`_HIGH_RISK_SERVICES`）：
`telnet`、`rdp`、`ftp`、`samba`。端口判断时会先去掉协议后缀（如 `23/tcp` 取
`23` 再比对）。

最小权限优雅降级：若当前身份无权调用 `firewall-cmd --list-all`（或 firewalld
未安装/未运行导致命令失败），`firewall.list_all` 返回 `error` 字段，
`open_port_count`/`open_service_count` 按 0 计算，`readable` 置为 `False`，
不影响 `running` 字段（仍由独立的 `firewall.state` 调用给出）。

## `privilege`：提权风险扫描工具

- 工具模块：`backend/mcp_tools/privilege_tool.py`
- 输入参数：无（`input_schema` 为空对象）
- 命令模板：
  - `privilege.suid`：`find /usr/bin /usr/sbin /bin /sbin /usr/local/bin -xdev -perm -4000 -type f`
  - `privilege.sgid`：`find /usr/bin /usr/sbin /bin /sbin /usr/local/bin -xdev -perm -2000 -type f`
  - `privilege.uid0`：`awk -F: '($3 == 0) {print $1}' /etc/passwd`
  - `privilege.empty_password`：`awk -F: '($2 == "") {print $1}' /etc/shadow`

SUID/SGID 扫描限定在 `/usr/bin`、`/usr/sbin`、`/bin`、`/sbin`、
`/usr/local/bin` 这五个特权目录内，并加 `-xdev` 阻止跨文件系统挂载点扫描，避免
对整个根文件系统做开销巨大且范围失控的全盘搜索。

| 字段 | 说明 |
| --- | --- |
| `suid_count` | 扫描到的 SUID 可执行文件数量 |
| `sgid_count` | 扫描到的 SGID 可执行文件数量 |
| `suid_files` | SUID 文件路径列表，最多返回前 50 条（`_MAX_FILES`） |
| `extra_uid0_accounts` | `/etc/passwd` 中 UID 为 0 但账户名不是 `root` 的额外账户（潜在后门账户） |
| `empty_password_accounts` | `/etc/shadow` 中密码字段为空的账户列表 |
| `shadow_readable` | `privilege.empty_password` 调用结果是否未携带 `error` 字段 |

最小权限优雅降级：`/etc/shadow` 默认仅 root 可读，Agent 以低权限身份运行时
`privilege.empty_password` 通常会返回 `error`。`_analyze()` 此时把
`empty_password_accounts` 按空列表处理，`shadow_readable` 置为 `False`；
`suid_count`/`sgid_count`/`extra_uid0_accounts` 等不依赖 `/etc/shadow` 的字段
不受影响，仍基于各自命令的真实输出给出结果。

## Windows 开发环境降级

三个工具在 `os.name == "nt"` 时均直接返回如下结构，不调用
`run_optional_template()`、不执行任何真实命令：

```json
{
  "platform": "windows",
  "message": "该安全工具面向麒麟/Linux，开发环境不可用。",
  "analysis": { ... }
}
```

`analysis` 字段在 Windows 降级路径下由 `_analyze()` 以全空输入计算得出（计数类
字段为 0 或空列表/空字典，`*_readable` 类布尔字段为各自的默认判定结果），保持
返回结构与 Linux 路径一致，调用方无需区分平台即可读取 `analysis` 的键。

## 接入规划与安全闭环

- Planner 关键词（`backend/agent/planner.py`）：
  - `auth`：`登录`、`认证`、`爆破`、`暴力破解`、`失败登录`、`login`、`auth`、`brute`
  - `firewall`：`防火墙`、`firewall`、`暴露`、`开放端口`、`iptables`、`exposure`
  - `privilege`：`提权`、`suid`、`sgid`、`特权`、`权限提升`、`privilege`、
    `escalation`、`空密码`
- LLM 规划 JSON 合约：`backend/agent/llm_client.py` 的 `ALLOWED_TOOLS` 已包含
  `auth`/`firewall`/`privilege`；`backend/agent/prompt.py` 中的 `tools` 取值
  枚举与工具说明列表同步加入这三者，供规划提示词使用。
- 安全分流：`auth`/`firewall`/`privilege` 均为只读工具且在 `LOW_RISK_TOOLS`
  内，因此首次规划只命中这三类工具时会进入 `AgentOrchestrator._run_loop` 多步
  推理闭环，每一步可被自动执行，不需要 `approved=True`；一旦下一步规划中混入
  操作类工具（如 `service.restart`），该步骤整体会被拦下并转入
  `suggested_actions`，等待人工二次确认。完整规则见
  `docs/multi-step-reasoning.md`。

## 角色分级输出脱敏（RBAC redaction）

`auth`/`firewall`/`privilege` 返回的明细是侦察级敏感信息（失败登录来源 IP、开放端口
清单、SUID 文件清单、UID0/空密码账户名）。由于三者在 `LOW_RISK_TOOLS` 内、无令牌
viewer 即可调用，`backend/security/redaction.py` 的 `redact_security_tool_output`
在 `ToolExecutor` 返回结果前按角色脱敏：

- **operator / admin**：返回全量结果。
- **viewer（默认、无令牌）**：只返回计数与风险标志，剥离明细，并加 `detail_redacted: true`：
  - `auth`：保留各类登录计数、`root_remote_login`、`top_source_ip_count`；剥离原始
    `last`/`lastb`/`who` 与 `top_source_ips`（IP 明细）。
  - `firewall`：保留 `running`、端口/服务数量、`high_risk_exposed` 告警；剥离原始输出与
    `open_ports`/`open_services` 明细清单。
  - `privilege`：保留 SUID/SGID 数量、`extra_uid0_count`、`empty_password_count`、
    `shadow_readable`；剥离原始输出、`suid_files` 清单与 UID0/空密码账户名。

脱敏只作用于返回给调用方与 LLM 的结果；**审计（`_audit.write`）与步骤引用（`outputs`）
仍保留全量**，便于取证与多步编排。脱敏在 `executor` 层集中实现，工具本身保持角色无关，
`/api/agent/execute`（闭环）与 `/api/tools/{name}`（直调）两条路径都被覆盖。

## API 调用示例

直接调用单个工具：

```bash
curl -X POST http://127.0.0.1:8000/api/tools/auth \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"lines":50}}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/tools/firewall \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{}}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/tools/privilege \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{}}'
```

通过自然语言规划触发（命中闭环时会自动连续调用只读工具）：

```bash
curl -X POST http://127.0.0.1:8000/api/agent/execute \
  -H 'Content-Type: application/json' \
  -d '{"query":"检查一下有没有暴力破解登录、防火墙暴露面和提权风险"}'
```
