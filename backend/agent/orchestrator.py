from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from backend.agent.executor import ToolExecutor
from backend.agent.llm_client import LLMConclusion, LLMClient
from backend.agent.planner import Plan, Planner
from backend.audit.logger import AuditLogger


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
        execution = self._executor.execute(
            plan=plan,
            user_id=user_id,
            raw_query=query,
            approved=approved,
            trace_id=trace_id,
            role=role,
        )
        self._audit.event(
            trace_id=trace_id,
            stage="environment_perception",
            user_id=user_id,
            status="completed" if not execution.blocked else "skipped",
            data={
                "tools": plan.tools,
                "executed_commands": execution.executed_commands,
                "result": execution.result,
            },
        )
        self._audit.event(
            trace_id=trace_id,
            stage="execution_result",
            user_id=user_id,
            status="blocked" if execution.blocked else "completed",
            data={
                "approved_required": execution.approved_required,
                "blocked": execution.blocked,
                "message": execution.message,
                "executed_commands": execution.executed_commands,
                "result": execution.result,
                "security": execution.security,
            },
        )
        conclusion = self._conclude(query, plan, execution.security, execution.result, execution.blocked)
        self._audit.event(
            trace_id=trace_id,
            stage="final_answer",
            user_id=user_id,
            status=conclusion.get("status", "unknown"),
            data={"conclusion": conclusion},
        )
        self._audit.event(
            trace_id=trace_id,
            stage="trace_complete",
            user_id=user_id,
            status="blocked" if execution.blocked else "completed",
            data={
                "query": query,
                "plan": plan_data,
                "security": execution.security,
                "executed_commands": execution.executed_commands,
                "final_answer": conclusion,
            },
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
        )

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
