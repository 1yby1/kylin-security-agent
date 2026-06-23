# LLM Agent JSON Contract

The LLM is used for reasoning only. It never executes shell commands directly.
The backend parses fixed JSON, validates it, runs security checks, executes
registered tools, and then asks the LLM to analyze tool results.

## Responsibilities

1. Understand user intent.
2. Select suitable registered tools.
3. Generate tool parameters.
4. Analyze tool results.
5. Produce a concise conclusion for the user.

## Planning JSON

The planning step must return JSON only:

```json
{
  "intent": "inspection",
  "summary": "查看系统状态",
  "tools": ["system"],
  "arguments": {},
  "risk_hint": "low",
  "need_confirmation": false,
  "reasoning": ["系统状态查询需要系统概览工具"]
}
```

Allowed `intent` values:

- `inspection`
- `diagnosis`
- `risky_operation`

Allowed tools are the registered MCP-like tools:

- `system`
- `process`
- `process.kill`
- `network`
- `log`
- `service`
- `service.restart`
- `temp.clean`
- `disk`

## 工具编排（多步链路 / Tool Orchestration）

规划 JSON 可选携带 `steps` 数组，用于让多个工具按顺序协作，并把前一步的输出
作为后一步的输入。`steps` 缺省时，后端按 `tools` 顺序自动派生「每个工具一个步骤、
共享 `arguments`」，因此旧契约完全兼容。

```json
{
  "intent": "risky_operation",
  "summary": "终止 CPU 占用最高的非系统进程",
  "tools": ["process", "process.kill"],
  "arguments": {},
  "steps": [
    {"id": "s1", "tool": "process", "arguments": {"limit": 5}},
    {"id": "s2", "tool": "process.kill", "arguments": {"pid": "${s1.analysis.top_cpu[0].pid}"}}
  ],
  "risk_hint": "medium",
  "need_confirmation": true,
  "reasoning": ["先采集进程占用，再按结果终止目标进程"]
}
```

步骤约定：

- 每个 step 含唯一 `id`、注册工具 `tool` 和该步独立的 `arguments`。
- steps 按数组顺序**串行执行**。
- **快速失败**：任意一步被安全校验拦截或执行失败（工具返回顶层 `error` 或
  `analysis.succeeded == false`），整条链路立即中断，后续步骤不再执行。已执行步骤
  的输出仍保留在 `result` 中以供分析。
- **step id 必须唯一**：重复 id 会让引用产生歧义，规划解析阶段会拒绝整段
  orchestration（回退到扁平 `tools`），执行阶段也会直接阻断。
- **`result` 按工具名归集**，同一工具被多次调用时后续结果以 `tool#2`、`tool#3` …
  为键，避免覆盖；每一步的完整输出另见 `ExecutionResult.steps`（含 `step_id`）。

### 步骤间数据引用

参数值写成整串占位符 `"${stepId.path}"` 即可引用先前步骤的输出：

- `path` 从被引用步骤的工具结果根部逐层取值，支持点号与列表下标，
  例如 `${s1.analysis.top_cpu[0].pid}`。
- 占位符必须是**整个参数值**，不能与其他文字拼接，以保留原始类型（int 仍是 int）
  并避免字符串注入。
- 引用在该步**安全校验之前**解析；解析后的真实值才进入安全校验与工具执行，
  因此「安全校验早于每次执行」的不变量对每个步骤都成立。
- 当目标工具 schema 要求整数、而引用解析出的是纯数字字符串（如 `"4321"`，`process`
  工具的 pid 即为字符串），后端会按 schema **自动转换为整数**，使 `process -> process.kill`
  这类链路可以端到端工作；非数字字符串仍保持原值并在 schema 校验阶段被拦截。该转换在
  安全校验**之前**完成，guard 看到的是转换后的真实值。
- 引用的步骤不存在、尚未执行，或路径取不到值时，该步被拦截并中断链路。

### 安全与审计

- `SecurityGuard.check()` 对**每个步骤**单独执行（工具白名单、参数 schema、参数值、
  危险路径/命令、角色权限、二次确认）。
- `ExecutionResult.security` 是各步骤校验的聚合：`risk_level` 取各步最大值，
  `reasons`/`checks` 汇总，`blocked_step` 标记首个被拦截的步骤，`steps` 给出逐步摘要。
- 审计按同一 `trace_id` 为每个步骤写入 `security_validation` 与 `tool_call` 事件，
  事件数据带 `step_id`，可还原完整编排链路。
- `POST /api/security/evaluate` 不执行工具，无法解析引用：带 `${...}` 的步骤标记为
  `deferred`，不参与拦截判定，其真实校验发生在执行时。

## Analysis JSON

After tools run, the result analysis step must return JSON only:

```json
{
  "conclusion": "当前系统状态正常。",
  "status": "normal",
  "root_cause": "未发现明确故障。",
  "evidence": ["system 工具返回主机与资源信息"],
  "recommendations": ["继续观察业务日志"],
  "needs_more_info": false,
  "follow_up_questions": []
}
```

Allowed `status` values:

- `normal`
- `warning`
- `critical`
- `unknown`

## Backend Flow

1. `AgentOrchestrator` receives the user query.
2. `Planner` calls DeepSeek/Qwen and parses planning JSON.
3. If the LLM is disabled or invalid, local rules select tools.
4. `SecurityGuard` validates the selected tools and arguments.
5. `ToolExecutor` runs tools only after security validation.
6. `LLMClient.conclude()` sends tool results back to the LLM.
7. If the LLM is disabled or invalid, a local fallback conclusion is returned.

## Environment Variables

DeepSeek:

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=...
export LLM_MODEL=deepseek-chat
```

Qwen:

```bash
export LLM_PROVIDER=qwen
export QWEN_API_KEY=...
export LLM_MODEL=qwen-plus
```

Common overrides:

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=...
export LLM_TIMEOUT_SECONDS=20
```

## APIs

Plan only:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/plan \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统状态","context":{}}'
```

Full Agent run:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/execute \
  -H 'Content-Type: application/json' \
  -d '{"query":"查看系统状态","context":{}}'
```
