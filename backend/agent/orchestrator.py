from __future__ import annotations

import json

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from backend.agent.executor import ToolExecutor
from backend.agent.llm_client import LLMConclusion, LLMClient
from backend.agent.planner import Plan, Planner
from backend.audit.logger import AuditLogger
from backend.config import get_reasoning_settings
from backend.security.rules import LOW_RISK_TOOLS
from backend.security.sanitizer import sanitize_output, scan_injection


@dataclass(frozen=True)
class AgentRunResult:
    trace_id: str
    intent: str
    tools: list[str]
    approved_required: bool
    blocked: bool
    message: str
    result: dict[str, Any]
    security: dict[str, Any]
    executed_commands: list[dict[str, Any]]
    conclusion: dict[str, Any]
    plan: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = field(default_factory=list)


class AgentOrchestrator:
    def __init__(
        self,
        planner: Planner | None = None,
        executor: ToolExecutor | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._executor = executor or ToolExecutor()
        self._llm_client = llm_client or LLMClient()
        self._planner = planner or Planner(self._llm_client)
        self._audit = AuditLogger()

    @property
    def executor(self) -> ToolExecutor:
        return self._executor

    @property
    def planner(self) -> Planner:
        return self._planner

    def run(self, query: str, user_id: str, context: dict[str, Any], approved: bool = False, role: str | None = None) -> AgentRunResult:
        trace_id = uuid4().hex
        self._audit.event(
            trace_id=trace_id,
            stage="received_instruction",
            user_id=user_id,
            status="received",
            data={"query": query, "context": context, "approved": approved, "role": role},
        )
        plan = self._planner.plan(query, context, self._executor.tool_manifest())
        plan_data = self._plan_to_dict(plan)
        self._audit.event(
            trace_id=trace_id,
            stage="llm_decision",
            user_id=user_id,
            status=plan.source,
            data={"plan": plan_data},
        )
        if approved or not self._is_read_only(plan.tools):
            return self._run_single(trace_id, query, user_id, context, approved, role, plan, plan_data)
        return self._run_loop(trace_id, query, user_id, context, role, plan, plan_data)

    @staticmethod
    def _is_read_only(tools: list[str]) -> bool:
        return bool(tools) and all(tool in LOW_RISK_TOOLS for tool in tools)

    def _run_single(self, trace_id, query, user_id, context, approved, role, plan, plan_data) -> AgentRunResult:
        execution = self._executor.execute(
            plan=plan, user_id=user_id, raw_query=query,
            approved=approved, trace_id=trace_id, role=role,
        )
        self._audit.event(
            trace_id=trace_id, stage="environment_perception", user_id=user_id,
            status="completed" if not execution.blocked else "skipped",
            data={
                "tools": plan.tools,
                "executed_commands": execution.executed_commands,
                "result": execution.result,
                "steps": execution.steps,
            },
        )
        self._audit.event(
            trace_id=trace_id, stage="execution_result", user_id=user_id,
            status="blocked" if execution.blocked else "completed",
            data={
                "approved_required": execution.approved_required, "blocked": execution.blocked,
                "message": execution.message, "executed_commands": execution.executed_commands,
                "result": execution.result, "security": execution.security,
            },
        )
        conclusion = self._conclude(query, plan, execution.security, execution.result, execution.blocked)
        self._audit.event(
            trace_id=trace_id, stage="final_answer", user_id=user_id,
            status=conclusion.get("status", "unknown"), data={"conclusion": conclusion},
        )
        self._audit.event(
            trace_id=trace_id, stage="trace_complete", user_id=user_id,
            status="blocked" if execution.blocked else "completed",
            data={"query": query, "plan": plan_data, "security": execution.security,
                  "executed_commands": execution.executed_commands, "final_answer": conclusion},
        )
        return AgentRunResult(
            trace_id=trace_id,
            intent=plan.intent,
            tools=plan.tools,
            approved_required=execution.approved_required,
            blocked=execution.blocked,
            message=execution.message,
            result=execution.result,
            security=execution.security,
            executed_commands=execution.executed_commands,
            conclusion=conclusion,
            plan=plan_data,
            steps=execution.steps,
        )

    def _run_loop(self, trace_id, query, user_id, context, role, first_plan, first_plan_data) -> AgentRunResult:
        max_steps = get_reasoning_settings().max_steps
        executed: set[str] = set()
        combined: dict[str, Any] = {}
        commands: list[dict[str, Any]] = []
        steps: list[dict[str, Any]] = []
        suggested: list[dict[str, Any]] = []
        last_security: dict[str, Any] = {}
        pending_security: dict[str, Any] | None = None
        message = "执行完成。"
        blocked = False
        current = first_plan

        for index in range(1, max_steps + 1):
            execution = self._executor.execute(
                plan=current, user_id=user_id, raw_query=query,
                approved=False, trace_id=trace_id, role=role,
            )
            last_security = execution.security
            combined.update(execution.result)
            commands.extend(execution.executed_commands)
            executed.update(current.tools)
            hits = scan_injection(json.dumps(execution.result, ensure_ascii=False, default=str))
            if hits:
                self._audit.event(
                    trace_id=trace_id, stage="injection_scan", user_id=user_id,
                    status="injection_suspected",
                    data={"step": index, "patterns": hits, "tools": current.tools},
                )
            steps.append({
                "step": index, "tools": current.tools, "source": current.source,
                "observation_summary": self._summarize_observation(execution.result),
                "injection_suspected": bool(hits),
            })
            self._audit.event(
                trace_id=trace_id, stage="reasoning_step", user_id=user_id,
                status="blocked" if execution.blocked else "completed",
                data={"step": index, "plan": self._plan_to_dict(current), "result": execution.result},
            )
            if execution.blocked:
                blocked = True
                message = execution.message
                break
            if index == max_steps:
                break
            next_plan = self._planner.plan_next(query, context, combined, executed, self._executor.tool_manifest())
            if next_plan is None:
                break
            operation_tools = [tool for tool in next_plan.tools if tool not in LOW_RISK_TOOLS]
            if operation_tools:
                suggested.extend(self._suggested_actions_for_plan(next_plan, operation_tools))
                pending_security = self._pending_action_security(next_plan, query, user_id, role, suggested)
                message = "已完成只读诊断，建议操作需要确认后才能执行。"
                self._audit.event(
                    trace_id=trace_id, stage="suggested_action", user_id=user_id,
                    status="pending_confirmation", data={"suggested_actions": suggested, "security": pending_security},
                )
                break
            current = next_plan

        final_security = pending_security or last_security
        conclusion = self._conclude(query, first_plan, final_security, combined, blocked)
        self._audit.event(
            trace_id=trace_id, stage="final_answer", user_id=user_id,
            status=conclusion.get("status", "unknown"),
            data={"conclusion": conclusion, "steps": steps, "suggested_actions": suggested},
        )
        self._audit.event(
            trace_id=trace_id, stage="trace_complete", user_id=user_id,
            status="blocked" if blocked else "completed",
            data={"query": query, "plan": first_plan_data, "steps": steps,
                  "suggested_actions": suggested, "security": final_security, "final_answer": conclusion},
        )
        return AgentRunResult(
            trace_id=trace_id, intent=first_plan.intent, tools=sorted(executed),
            approved_required=bool(suggested), blocked=blocked, message=message,
            result=combined, security=final_security, executed_commands=commands,
            conclusion=conclusion, plan=first_plan_data, steps=steps, suggested_actions=suggested,
        )

    @staticmethod
    def _suggested_actions_for_plan(plan: Plan, operation_tools: list[str]) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if plan.steps:
            for step in plan.steps:
                if step.tool in operation_tools:
                    actions.append({"tool": step.tool, "arguments": step.arguments, "reason": plan.summary})
            return actions
        return [
            {"tool": tool, "arguments": plan.arguments, "reason": plan.summary}
            for tool in operation_tools
        ]

    def _pending_action_security(
        self,
        plan: Plan,
        query: str,
        user_id: str,
        role: str | None,
        suggested_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            evaluated = self._executor.evaluate_security(
                plan=plan,
                user_id=user_id,
                raw_query=query,
                approved=False,
                role=role,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback for custom executors
            evaluated = {
                "risk_level": "medium",
                "blocked": False,
                "confirmation_required": True,
                "audit_required": True,
                "reasons": [f"suggested action security evaluation failed: {exc}"],
                "checks": [],
            }
        reasons = [
            *evaluated.get("reasons", []),
            "suggested action requires explicit confirmation before execution",
        ]
        return {
            **evaluated,
            "blocked": False,
            "confirmation_required": True,
            "pending_confirmation": True,
            "reasons": list(dict.fromkeys(str(reason) for reason in reasons)),
            "suggested_actions": suggested_actions,
            "suggested_action_security": evaluated,
        }

    @staticmethod
    def _summarize_observation(result: dict[str, Any]) -> str:
        parts: list[str] = []
        for name, value in result.items():
            if isinstance(value, dict) and value.get("error"):
                parts.append(f"{name}: 错误 {value['error']}")
            elif isinstance(value, dict) and "analysis" in value:
                parts.append(f"{name}: {value['analysis']}")
            else:
                parts.append(f"{name}: 已采集")
        return sanitize_output("; ".join(parts), max_len=300)

    def evaluate_security(self, query: str, user_id: str, context: dict[str, Any], approved: bool = False, role: str | None = None) -> dict[str, Any]:
        plan = self._planner.plan(query, context, self._executor.tool_manifest())
        return {
            "intent": plan.intent,
            "tools": plan.tools,
            "plan": self._plan_to_dict(plan),
            "security": self._executor.evaluate_security(
                plan=plan,
                user_id=user_id,
                raw_query=query,
                approved=approved,
                role=role,
            ),
        }

    def llm_status(self) -> dict[str, Any]:
        return self._llm_client.status()

    def test_llm(self) -> dict[str, Any]:
        decision = self._llm_client.analyze(
            "查看系统状态",
            {},
            self._executor.tool_manifest(),
        )
        return {
            "status": self._llm_client.status(),
            "decision": None
            if decision is None
            else {
                "intent": decision.intent,
                "tools": decision.tools,
                "arguments": decision.arguments,
                "summary": decision.summary,
                "reasoning": decision.reasoning,
            },
        }

    def _conclude(
        self,
        query: str,
        plan: Plan,
        security: dict[str, Any],
        tool_result: dict[str, Any],
        blocked: bool,
    ) -> dict[str, Any]:
        if blocked:
            return self._blocked_conclusion(security)

        llm_conclusion = self._llm_client.conclude(
            query=query,
            plan=self._plan_to_dict(plan),
            security=security,
            tool_result=tool_result,
        )
        if llm_conclusion is not None:
            return asdict(llm_conclusion)
        return self._fallback_conclusion(tool_result)

    @staticmethod
    def _plan_to_dict(plan: Plan) -> dict[str, Any]:
        return {
            "intent": plan.intent,
            "tools": plan.tools,
            "arguments": plan.arguments,
            "summary": plan.summary,
            "source": plan.source,
            "reasoning": plan.reasoning,
            "steps": [
                {"id": step.id, "tool": step.tool, "arguments": step.arguments}
                for step in plan.execution_steps()
            ],
        }

    @staticmethod
    def _blocked_conclusion(security: dict[str, Any]) -> dict[str, Any]:
        reasons = security.get("reasons", [])
        return asdict(
            LLMConclusion(
                conclusion="请求未执行，已被安全校验器拦截。",
                status="warning",
                root_cause="安全策略阻断或需要额外确认。",
                evidence=[str(reason) for reason in reasons],
                recommendations=["检查请求是否涉及危险命令、危险路径或未授权操作。"],
                needs_more_info=False,
                follow_up_questions=[],
                source="fallback",
            )
        )

    @staticmethod
    def _fallback_conclusion(tool_result: dict[str, Any]) -> dict[str, Any]:
        evidence: list[str] = []
        recommendations: list[str] = []
        status = "normal"
        for tool_name, result in tool_result.items():
            if isinstance(result, dict) and result.get("error"):
                status = "warning"
                evidence.append(f"{tool_name} 工具执行失败：{result['error']}")
            elif isinstance(result, dict):
                analysis = result.get("analysis")
                if analysis:
                    evidence.append(f"{tool_name} 分析结果：{analysis}")
                else:
                    evidence.append(f"{tool_name} 工具已返回结果。")
        if not evidence:
            evidence.append("工具未返回可分析结果。")
            status = "unknown"
        if status == "normal":
            recommendations.append("当前未发现明确异常，可结合业务日志继续观察。")
        else:
            recommendations.append("请根据失败工具的错误信息补充权限或运行环境后重试。")
        return asdict(
            LLMConclusion(
                conclusion="工具执行完成，已生成基础分析结论。",
                status=status,
                root_cause="未接入或未成功调用大模型，使用本地规则总结。",
                evidence=evidence,
                recommendations=recommendations,
                needs_more_info=status != "normal",
                follow_up_questions=[],
                source="fallback",
            )
        )
