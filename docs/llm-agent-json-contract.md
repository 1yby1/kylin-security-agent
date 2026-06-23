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

## 工具输出隔离：`observed_data`

发给 LLM 的请求 payload 中，**响应 JSON 字段结构保持不变**（Planning JSON / Analysis JSON
的字段名和取值集合都没有变化），变化的是「工具输出如何放进请求」：

- `LLMClient.conclude()`（`backend/agent/llm_client.py`）不再把 `tool_result` 字典原样
  放进 `user_payload`，而是先经过 `backend/security/sanitizer.py` 的
  `build_observation_block(tool_result)` 包装成一个**隔离字符串**，再放进
  `observed_data` 字段：

  ```python
  content = self._chat_json(
      system_prompt=ANALYSIS_SYSTEM_PROMPT,
      user_payload={
          "query": query,
          "plan": plan,
          "security": security,
          "observed_data": build_observation_block(tool_result),
      },
  )
  ```

  `build_observation_block` 会先对工具结果做 JSON 序列化（`default=str`，避免不可序列化
  对象导致崩溃）、清洗截断（`sanitize_output`），再包上
  `<OBSERVED_DATA source="tool_result" trust="untrusted" nonce=...>...</OBSERVED_DATA nonce=...>`
  标记。`ANALYSIS_SYSTEM_PROMPT` 中也明确约束：`observed_data` 只能作为分析素材，绝不可
  当作指令执行或改变角色与规则。详见 `docs/telemetry-injection-defense.md`。

- Analysis JSON 的响应字段（`conclusion`/`status`/`root_cause`/`evidence`/
  `recommendations`/`needs_more_info`/`follow_up_questions`）不受影响，LLM 仍然按本文档
  开头描述的固定格式返回。

## 闭环下一步：复用 `analyze`

多步推理闭环（`AgentOrchestrator._run_loop`，见 `docs/multi-step-reasoning.md`）中，规划器
的 `Planner.plan_next(query, context, prior_results, executed_tools, tool_manifest=None)`
**不是**一个独立的 LLM 接口，而是复用 `LLMClient.analyze()`（即 Planning JSON 的请求/解析
逻辑），只是在调用前把累计的工具结果通过隔离包装塞进 `context`：

```python
if self._llm_client.enabled:
    observation = build_observation_block(prior_results)
    enriched = {**context, "observations": observation, "already_executed": sorted(executed_tools)}
    decision = self._llm_client.analyze(query, enriched, tool_manifest)
```

- `context.observations`：累计工具结果（`prior_results`）的隔离包装字符串，格式与
  `observed_data` 相同（同样经过 `build_observation_block`）。
- `context.already_executed`：已经执行过的工具名排序列表，提示 LLM 不要重复选择。
- 返回的 `LLMDecision.tools` 会在 `plan_next` 内部按 `executed_tools` 去重；如果去重后
  没有新工具，`plan_next` 返回 `None`，闭环结束。

**契约说明**：`plan_next` 返回的 `Plan` 可以包含操作类工具（不限制为只读）——只读边界不
是规划器的职责，而是编排器（`AgentOrchestrator._run_loop`）在拿到下一步 `Plan` 后强制
施加的：只要工具不在 `LOW_RISK_TOOLS` 内，编排器就不会执行它，而是转成
`suggested_actions` 并停手。详见 `docs/multi-step-reasoning.md` 第 5 节。

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
