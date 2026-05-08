# 受控运维工具功能测试报告

## 1. 测试概述

本报告验证安全智能运维 Agent 中三个受控类 MCP 工具：

- `service.restart`：白名单服务重启工具
- `temp.clean`：安全临时目录清理工具
- `process.kill`：非系统进程终止工具

测试重点覆盖工具注册、工具选择、安全意图校验、二次确认、权限控制、危险输入阻断、执行调度和审计追踪。

## 2. 测试环境

| 项目 | 内容 |
| --- | --- |
| 测试日期 | 2026-05-07 |
| 测试目录 | `E:\QQ下载及记录\jianli\软件杯` |
| 操作系统 | Windows 10 10.0.26200 SP0 |
| CPU 架构 | AMD64 |
| Python 版本 | 3.10.10 |
| 后端框架 | FastAPI |
| 测试框架 | Python `unittest` |
| LLM 状态 | 未启用，使用本地规则规划工具 |
| 数据库 | SQLite 起步配置 |
| 目标部署环境 | 麒麟高级服务器版 V11 + LoongArch |

说明：本次在 Windows 开发环境执行自动化测试。三个受控工具在真实执行阶段均限制为 Kylin/Linux，因此 Windows 下只验证安全链路和“不支持平台时不执行破坏性操作”的保护行为。麒麟实机部署后仍建议补充受控沙箱执行测试。

## 3. 测试命令

```powershell
python -m unittest tests.test_controlled_tools -v
python -m compileall backend tests
```

执行结果摘要：

```text
Ran 6 tests in 0.091s
OK
```

编译检查结果：`backend` 与 `tests` 均完成编译检查，无语法错误。

## 4. 测试范围

| 范围 | 是否覆盖 |
| --- | --- |
| MCP 工具注册与元数据 | 已覆盖 |
| 中风险等级标记 | 已覆盖 |
| 非只读工具标记 | 已覆盖 |
| 自然语言工具选择 | 已覆盖 |
| 参数必填校验 | 已覆盖 |
| 操作员/管理员权限控制 | 已覆盖 |
| viewer 用户阻断 | 已覆盖 |
| 二次确认 `approved=true` | 已覆盖 |
| 危险服务/路径/命令阻断 | 已覆盖 |
| Windows 平台保护性返回 | 已覆盖 |
| 审计链路记录 | 已覆盖 |

## 5. 测试用例

| Case ID | 功能 | 前置条件 | 步骤 | 预期结果 | 实际结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| CT-001 | 受控工具注册 | 后端模块可导入 | 读取 `ToolExecutor().available_tools()` 和工具元数据 | `service.restart`、`temp.clean`、`process.kill` 均存在，风险等级为 `medium`，`read_only=false` | 三个工具均注册成功，元数据符合预期 | 通过 |
| CT-002 | 服务重启二次确认 | `operator` 用户，`service_name=nginx` | 安全评估 `重启 nginx 服务`，`approved=false` | 阻断，原因包含二次确认 | 返回 `secondary confirmation required` | 通过 |
| CT-003 | 服务重启权限控制 | `viewer` 用户，`service_name=nginx` | 安全评估 `重启 nginx 服务`，`approved=true` | 阻断 viewer 用户 | 返回角色无权执行中风险操作 | 通过 |
| CT-004 | 服务重启参数校验 | `operator` 用户 | 安全评估 `重启服务`，不传 `service_name` | 阻断，提示缺少服务名 | 返回 `service.restart: service_name is required` | 通过 |
| CT-005 | 保护服务阻断 | `admin` 用户，`service_name=firewalld` | 安全评估 `重启 firewalld 服务`，`approved=true` | 高风险或默认阻断 | 风险等级为 `high`，请求被阻断 | 通过 |
| CT-006 | 白名单服务通过安全校验 | `operator` 用户，`service_name=nginx` | 安全评估 `重启 nginx 服务`，`approved=true` | 不阻断，风险等级 `medium` | 安全校验通过，风险等级 `medium` | 通过 |
| CT-007 | 临时目录清理安全路径 | `operator` 用户，`path=/tmp` | 安全评估 `清理 /tmp 临时文件`，`approved=true` | 不阻断 | 安全校验通过 | 通过 |
| CT-008 | 临时目录清理二次确认 | `operator` 用户，`path=/tmp` | 安全评估 `清理 /tmp 临时文件`，`approved=false` | 阻断，要求二次确认 | 返回 `secondary confirmation required` | 通过 |
| CT-009 | 临时目录清理权限控制 | `viewer` 用户，`path=/tmp` | 安全评估 `清理 /tmp 临时文件`，`approved=true` | 阻断 viewer 用户 | 返回角色无权执行中风险操作 | 通过 |
| CT-010 | 临时目录清理参数校验 | `operator` 用户 | 安全评估 `清理临时文件`，不传 `path` | 阻断，提示缺少路径 | 返回 `temp.clean: path is required` | 通过 |
| CT-011 | 路径穿越阻断 | `operator` 用户，`path=/tmp/../etc` | 安全评估 `清理 /tmp/../etc 临时文件`，`approved=true` | 阻断危险路径 | 请求被阻断 | 通过 |
| CT-012 | 核心目录阻断 | `operator` 用户，`path=/etc` | 安全评估 `清理 /etc 临时文件`，`approved=true` | 阻断核心目录 | 请求被阻断 | 通过 |
| CT-013 | 进程终止合法目标静态校验 | `operator` 用户，`pid=1234` | 安全评估 `杀死 pid 1234 进程`，`approved=true` | 静态安全校验通过 | 安全校验通过，进入执行前条件 | 通过 |
| CT-014 | 进程终止二次确认 | `operator` 用户，`pid=1234` | 安全评估 `杀死 pid 1234 进程`，`approved=false` | 阻断，要求二次确认 | 返回 `secondary confirmation required` | 通过 |
| CT-015 | 进程终止权限控制 | `viewer` 用户，`pid=1234` | 安全评估 `杀死 pid 1234 进程`，`approved=true` | 阻断 viewer 用户 | 返回角色无权执行中风险操作 | 通过 |
| CT-016 | 进程终止参数校验 | `operator` 用户 | 安全评估 `杀死进程`，不传 `pid` | 阻断，提示缺少 PID | 返回 `process.kill: pid is required` | 通过 |
| CT-017 | 系统 PID 阻断 | `operator` 用户，`pid=1` | 安全评估 `杀死 pid 1 进程`，`approved=true` | 阻断系统 PID | 请求被阻断 | 通过 |
| CT-018 | 危险 kill 命令阻断 | `operator` 用户，`pid=1234` | 安全评估 `kill -9 pid 1234 process`，`approved=true` | 阻断 `kill -9` | 请求命中危险命令规则并被阻断 | 通过 |
| CT-019 | 保护进程名阻断 | `operator` 用户，`pid=1234` | 安全评估 `杀死 sshd 进程 pid 1234`，`approved=true` | 阻断保护进程 | 请求命中 `sshd` 保护进程规则并被阻断 | 通过 |
| CT-020 | 审计链路 | 任一被阻断请求 | 执行未确认的 `重启 nginx 服务`，按 `trace_id` 读取审计记录 | 审计记录包含接收、决策、安全校验、执行结果、最终回答和完成事件 | 记录包含 `received_instruction`、`llm_decision`、`security_validation`、`execution_result`、`final_answer`、`trace_complete` | 通过 |
| CT-021 | 服务重启平台保护 | Windows 开发环境 | 已确认执行 `重启 nginx 服务` | 不阻断安全校验，但工具返回不支持平台，不执行系统命令 | 返回 `service.restart is only supported on Kylin/Linux with systemd` | 通过 |
| CT-022 | 临时目录清理平台保护 | Windows 开发环境 | 已确认执行 `清理 /tmp 临时文件`，`dry_run=true` | 不阻断安全校验，但工具返回不支持平台，不删除文件 | 返回 `temp.clean is only supported on Kylin/Linux` | 通过 |
| CT-023 | 进程终止平台保护 | Windows 开发环境 | 已确认执行 `杀死 pid 1234 进程`，`dry_run=true` | 不阻断安全校验，但工具返回不支持平台，不终止进程 | 返回 `process.kill is only supported on Kylin/Linux` | 通过 |

## 6. 审计验证

审计验证使用阻断请求 `重启 nginx 服务`，`approved=false`。系统生成 `trace_id` 后读取同一链路记录，已确认包含以下阶段：

- `received_instruction`
- `llm_decision`
- `security_validation`
- `execution_result`
- `final_answer`
- `trace_complete`

结论：受控操作即使被安全策略阻断，也会记录完整审计链路，满足“执行前判断”和“全过程可追踪”的要求。

## 7. 测试结论

本次自动化测试共执行 6 个 `unittest` 测试方法，覆盖 23 个功能与安全场景，结果全部通过。

三个受控类工具均满足当前阶段要求：

- 工具已注册到 MCP 工具列表；
- 风险等级均为中风险；
- 执行前必须经过安全意图校验；
- 中风险操作必须由 `operator` 或 `admin` 执行；
- 未二次确认时会阻断；
- 危险参数、危险路径、危险命令和保护对象会被阻断；
- 阻断和执行结果均进入审计链路。

## 8. 未解决风险与后续补充

- 当前自动化测试在 Windows 开发环境执行，未在麒麟高级服务器版 V11 + LoongArch 上进行真实系统命令执行。
- `service.restart` 的真实重启测试需要在比赛演示或测试环境中准备白名单服务，例如 `nginx` 或 `software-cup-ops`。
- `temp.clean` 的真实清理测试建议使用 `/opt/software-cup-ops/tmp` 下的专用测试文件，并优先使用 `dry_run=true`。
- `process.kill` 的真实终止测试建议启动一个普通用户拥有的临时测试进程，只对该进程发送 `TERM`，不得使用系统服务进程。
