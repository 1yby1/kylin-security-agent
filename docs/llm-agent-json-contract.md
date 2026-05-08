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
