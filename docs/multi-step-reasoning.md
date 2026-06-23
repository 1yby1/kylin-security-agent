# 多步推理闭环

`AgentOrchestrator.run()` 在「单次执行」之外新增了一条多步推理闭环路径：当首次规划全部
是只读工具且用户未预先确认时，Agent 可以连续执行多步只读观测，每步结果都会反馈给规划器
决定是否需要再观测一步；一旦下一步建议包含操作类工具，闭环立即停手，只产出建议，不会自动
执行。本文档描述触发条件、步数上限、只读边界、规则升级链、停手策略、审计事件和响应字段。

## 1. 触发条件：分流逻辑

`backend/agent/orchestrator.py` 的 `run()` 在做完首次规划后立即分流：

```python
plan = self._planner.plan(query, context, self._executor.tool_manifest())
...
if approved or not self._is_read_only(plan.tools):
    return self._run_single(...)   # 原有单次执行路径
return self._run_loop(...)         # 多步推理闭环
```

`_is_read_only` 的判定：

```python
@staticmethod
def _is_read_only(tools: list[str]) -> bool:
    return bool(tools) and all(tool in LOW_RISK_TOOLS for tool in tools)
```

也就是说，进入闭环（`_run_loop`）必须同时满足两个条件：

1. **`approved=False`**：请求没有携带「用户已确认风险操作」标记；只要 `approved=True`，
   无论工具是否只读，都走原有的 `_run_single` 单次路径（不进入闭环，保持向后兼容的行为）。
2. **首次规划的全部工具都是只读工具**（属于 `LOW_RISK_TOOLS`）：只要规划里出现任何一个非
   只读工具（如 `service.restart`、`process.kill`、`temp.clean`），同样退回 `_run_single`，
   即「直接操作请求不经过闭环」（参见 `tests/test_reasoning_loop.py::test_direct_operation_request_no_loop_regression`）。

只读工具集合定义在 `backend/security/rules.py`：

```python
LOW_RISK_TOOLS = {"system", "process", "network", "log", "service", "disk"}
```

## 2. 步数上限

闭环最多执行 `AGENT_MAX_REASONING_STEPS` 步，由 `backend/config.py` 的
`get_reasoning_settings()` 读取：

```python
def get_reasoning_settings() -> ReasoningSettings:
    raw = os.getenv("AGENT_MAX_REASONING_STEPS", "3")
    try:
        steps = int(raw)
    except ValueError:
        steps = 3
    return ReasoningSettings(max_steps=max(1, min(steps, 10)))
```

- 默认值为 `3`。
- 非法值（无法转换为整数）回退为 `3`。
- 最终结果会被夹到 `[1, 10]` 区间，避免配置成 0 步或异常大的步数。

`_run_loop` 用 `for index in range(1, max_steps + 1)` 控制步数；到达 `max_steps` 时即使
规划器还想继续，也会在本步执行完后直接 `break`，不再调用 `plan_next`。

## 3. 闭环单步流程

每一步（`_run_loop` 循环体）依次做：

1. 用当前 `Plan`（只读工具）调用 `self._executor.execute(...)`——**仍然经过
   `ToolExecutor` 和安全校验链路**，不存在跳过 guard 的「快速通道」。
2. 把本步结果并入累计结果 `combined`，把命令记录并入 `commands`，把本步工具名并入
   `executed` 集合（用于后续规划器去重）。
3. 对本步工具结果做 `scan_injection(json.dumps(execution.result, ...))` 注入扫描，命中则
   写入审计事件 `injection_scan`（见下文「审计事件」）。
4. 把本步信息追加到 `steps` 列表（字段见下文「响应字段」），并写入审计事件
   `reasoning_step`。
5. 如果本步执行被安全校验拦截（`execution.blocked`），整段循环立即结束，`blocked=True`。
6. 如果已经是最后一步（`index == max_steps`），结束循环，不再规划下一步。
7. 否则调用 `self._planner.plan_next(query, context, combined, executed, tool_manifest)`
   获取下一步建议；返回 `None` 表示规划器认为无需继续观测，循环正常结束。
8. 如果下一步建议包含任何非只读工具，进入「遇操作类工具停手」分支（见第 5 节），循环结束。
9. 否则把下一步设为新的 `current`，进入下一轮循环。

## 4. 规则升级链（无 LLM 时的本地规则）

当 LLM 未启用（或调用失败）时，`Planner.plan_next` 回退到 `_rule_next`，目前实现的升级链
是「`service` 工具发现异常 → 自动追加 `log` 工具」：

```python
service_output = prior_results.get("service")
if not isinstance(service_output, dict) or "log" in executed_tools:
    return None
analysis = service_output.get("analysis", {})
if analysis.get("failed_count", 0) <= 0 and analysis.get("inactive_count", 0) <= 0:
    return None
...
return Plan(
    intent="diagnosis",
    tools=["log"],
    ...
    reasoning=["service 工具发现 failed/inactive 服务，升级到 log 工具。"],
)
```

也就是说：只要本轮 `service` 工具结果里 `analysis.failed_count > 0` 或
`analysis.inactive_count > 0`，且 `log` 工具尚未执行过，规则链就会自动建议追加 `log` 工具
拉取日志进一步诊断；否则（服务正常，或已经拉取过日志）返回 `None`，闭环正常结束。

LLM 启用时，`plan_next` 改为调用 `LLMClient.analyze()`，并对返回的工具列表按
`executed_tools` 去重；如果去重后没有新工具，也返回 `None`（参见
`docs/llm-agent-json-contract.md` 「闭环下一步」一节）。

## 5. 遇操作类工具停手：`suggested_actions`

**重要的契约修正**：`plan_next` **可以**返回包含操作类工具（如 `service.restart`、
`process.kill`、`temp.clean`）的 `Plan`——规划层不对工具类型做强制限制。真正的只读执行
边界在**编排器（orchestrator）**，不在规划器：

```python
next_plan = self._planner.plan_next(query, context, combined, executed, self._executor.tool_manifest())
if next_plan is None:
    break
operation_tools = [tool for tool in next_plan.tools if tool not in LOW_RISK_TOOLS]
if operation_tools:
    for tool in operation_tools:
        suggested.append({"tool": tool, "arguments": next_plan.arguments, "reason": next_plan.summary})
    self._audit.event(
        trace_id=trace_id, stage="suggested_action", user_id=user_id,
        status="pending_confirmation", data={"suggested_actions": suggested},
    )
    break
current = next_plan
```

行为总结：

- 只要下一步建议里有任意一个工具不属于 `LOW_RISK_TOOLS`，**该步骤整体不会被执行**——
  闭环立即 `break`，不会把其中的只读工具拆出来单独跑。
- 这些操作类工具连同其参数和理由被收集进 `suggested_actions` 列表，写入审计事件
  `suggested_action`（`status="pending_confirmation"`），随结果一起返回给前端/调用方。
- `AgentRunResult.approved_required` 在闭环路径下等价于 `bool(suggested_actions)`：只要
  产生了建议动作，调用方就需要走二次确认（带 `approved=True` 或更具体的二次请求）才能真正
  执行这些操作类工具；闭环本身**绝不会**自动执行它们。
- 不要把这条边界误读为「`plan_next` 只能返回只读工具」——实际实现允许规划器在任何一步建议
  操作类工具，只是编排器拒绝自动执行并改为产出建议。

## 6. 审计事件

闭环路径（`_run_loop`）相比单次路径新增/复用了以下审计阶段（写入
`backend/audit/logger.py` 管理的 SQLite 审计链）：

| Stage | 触发时机 | 关键字段 |
| --- | --- | --- |
| `reasoning_step` | 每一步工具执行完成后（无论是否被拦截） | `step`、`plan`（含 `intent`/`tools`/`arguments`/`source`/`reasoning`）、`result` |
| `injection_scan` | 某一步的工具结果触发 `scan_injection` 命中 | `step`、`patterns`（命中的规则名列表）、`tools` |
| `suggested_action` | 下一步建议包含操作类工具，闭环停手 | `suggested_actions`（工具、参数、理由列表），`status="pending_confirmation"` |

此外，闭环结束时仍会写入 `final_answer` 和 `trace_complete`，`data` 中额外带上
`steps` 和 `suggested_actions`，与原有单次路径的审计形状保持兼容（单次路径不产生这两个
字段，闭环路径才有）。

## 7. 响应字段：`steps` / `suggested_actions`

`AgentRunResult`（`backend/agent/orchestrator.py`）新增两个字段：

```python
steps: list[dict[str, Any]] = field(default_factory=list)
suggested_actions: list[dict[str, Any]] = field(default_factory=list)
```

`/api/agent/execute` 的响应模型 `AgentResponse`（`backend/main.py`）同步透传这两个字段：

```python
class AgentResponse(BaseModel):
    ...
    steps: list[dict[str, Any]] = Field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = Field(default_factory=list)
```

字段含义：

- **`steps`**：闭环每一步的摘要列表，元素形如
  `{"step": 1, "tools": [...], "source": "rules|llm", "observation_summary": "...", "injection_suspected": false}`。
  `observation_summary` 由 `_summarize_observation` 生成，是对工具结果的一句话摘要（截断
  到 300 字符），不是完整观测数据本身。
  - 走单次路径（`_run_single`）时 `steps` 始终为空列表。
- **`suggested_actions`**：闭环中被拦下、未执行的操作类工具建议列表，元素形如
  `{"tool": "service.restart", "arguments": {...}, "reason": "..."}`。
  - 没有产生建议（闭环正常结束或走单次路径）时为空列表。

## 8. 与已有不变量的关系

- 闭环每一步仍然调用 `ToolExecutor.execute`，因此仍然完整经过
  `docs/security-intent-validator.md` 描述的安全校验链路；闭环**不会**绕过 guard。
- 闭环只会自动执行只读工具（`LOW_RISK_TOOLS`）；任何操作类工具只能进入
  `suggested_actions`，需要走外层的二次确认/`approved=True` 才能真正触发——这与
  `CLAUDE.md` 「安全校验必须早于工具执行」「风险策略以后端为准」的不变量一致。
- 工具结果在喂给 `LLMClient.conclude()` 时仍然走 `observed_data` 隔离包装（见
  `docs/telemetry-injection-defense.md`），多步累计的 `combined` 结果同样不可信。
