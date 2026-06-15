# Project Status

本文档记录当前 Agent 已实现能力和后续待扩展能力。之后每次新增工具、调整链路、修改安全策略或补充前端页面时，优先更新本文档，保证开发状态清晰。

## 当前已实现功能

### Agent 主链路

- 用户请求接收：`backend/main.py`
- 意图分析与工具选择：`backend/agent/planner.py`
- 大模型固定 JSON 规划：`backend/agent/llm_client.py`
- 本地关键词兜底规划：`backend/agent/planner.py`
- 工具参数生成与部分参数纠正：`backend/agent/planner.py`
- 安全意图校验：`backend/security/guard.py`
- MCP-like 工具调度执行：`backend/agent/executor.py`
- 工具执行结果分析：`backend/agent/orchestrator.py`
- 大模型结果总结：`backend/agent/llm_client.py`
- 本地结果总结兜底：`backend/agent/orchestrator.py`
- 全链路审计日志：`backend/audit/logger.py`

当前完整链路：

```text
用户请求
  -> 接收指令
  -> 意图分析
  -> 工具选择
  -> 参数生成/纠正
  -> 安全校验
  -> 工具执行
  -> 结果分析
  -> 最终回答
  -> 审计追踪
```

### 系统感知工具

- `system`：系统概览工具
- `process`：进程分析工具
- `process.top`：高 CPU / 高内存进程定位工具
- `process.detail`：按 PID 查询进程详情工具
- `network`：网络端口工具
- `network.port_lookup`：按端口查询 PID / 进程工具
- `log`：日志分析工具
- `log.search`：日志关键词检索工具
- `service`：服务状态查询工具
- `disk`：磁盘使用率工具

### 受控操作工具

- `service.restart`：重启指定白名单 systemd 服务
- `temp.clean`：清理指定安全临时目录
- `process.kill`：终止指定非系统进程

### 安全能力

- 工具白名单校验
- 工具参数 schema 校验
- 参数危险字符校验
- 危险路径校验
- 危险命令校验
- 中风险操作二次确认
- 用户角色权限校验
- 受保护服务和进程校验
- 最小权限运行用户部署方案
- 执行命令审计记录

### 审计能力

当前审计阶段包括：

- `received_instruction`：接收用户请求
- `llm_decision`：记录规划结果
- `security_validation`：记录安全校验结果
- `tool_call`：记录工具调用
- `environment_perception`：记录环境感知结果
- `execution_result`：记录执行结果
- `final_answer`：记录最终回答
- `trace_complete`：记录完整链路闭环

### 前端页面

- 智能运维对话页
- 系统状态看板
- MCP 工具列表页
- 审计日志页面

### 部署与测试

- FastAPI 后端入口
- 静态前端页面
- SQLite 起步支持
- systemd 部署文件
- Agent 专用低权限用户脚本
- 受控工具功能测试报告
- MCP 工具开发 skill
- 测试报告编写 skill

## 待扩展功能

### Agent 链路增强

- 失败后的二次规划：工具执行失败后自动选择补充工具继续诊断。
- 多轮上下文管理：保留同一会话中的历史问题、工具结果和用户确认状态。
- 规划置信度：在 Plan 中记录 `confidence`、`risk_hint`、`need_confirmation`。
- 结构化推理摘要：记录可审计的决策摘要，不记录完整思维链。
- 工具结果裁剪：大结果进入大模型前做摘要，避免 token 过大。

### 工具扩展

- `process.children`：查看指定 PID 子进程。
- `network.connections`：查看连接状态统计。
- `disk.large_files`：查找指定目录下大文件。
- `disk.inodes`：查看 inode 使用情况。
- `log.recent_errors`：查看最近错误和告警。
- `service.logs`：查看指定服务最近日志。
- `service.enable_check`：查看服务是否开机自启。

### 安全扩展

- 按工具配置独立权限策略。
- 按环境区分开发、测试、生产策略。
- 操作审批记录持久化。
- 命令模板参数类型强校验。
- 高风险操作永久禁止策略单独配置。
- 审计日志签名或防篡改存储。

### 部署扩展

- PostgreSQL 数据库存储。
- Nginx 反向代理配置。
- 离线部署包。
- 麒麟 V11 + LoongArch 实机验证脚本。
- systemd 健康检查和日志轮转。

### 测试扩展

- 麒麟虚拟机真实工具测试。
- 中风险操作端到端测试。
- LLM JSON 异常返回测试。
- 审计日志回溯测试。
- 并发请求性能测试。
- 长日志和大工具结果性能测试。

## 后续开发规则

每次继续开发前，先确认：

1. 本次要做的是已有功能修复，还是待扩展功能新增。
2. 是否需要新增 MCP 工具。
3. 是否需要更新安全规则。
4. 是否需要更新审计字段。
5. 是否需要补测试或测试报告。
6. 是否需要同步更新本文档。
