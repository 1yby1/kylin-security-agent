# 会话上下文安全加固

记录对多轮会话上下文（`backend/agent/session_context.py`）的三项安全加固。会话用于把
上一轮的关键实体（`pid`/`service_name`/`path` 等）注入下一轮规划，支持"继续看它的日志"
这类追问。

## P1：会话按主体绑定 + 服务端签发随机 ID

**问题**：原 `resolve_session_id` 接受调用方自带的任意合法 `session_id`，`context()` 纯按
ID 取状态、无 owner 概念。一旦 ID 泄露或被共享，另一个调用者就能拿到上一轮的语境并影响
后续工具参数。`request.user_id` 是客户端可伪造的，不能作为身份依据。

**加固**：

- `backend/security/auth.py` 新增 `session_principal(token)`：无令牌 → `"anon"`；有令牌 →
  `"tok:" + sha256(token)[:16]`（稳定、不存明文）。这是项目"令牌→角色"模型下能拿到的
  最强主体锚点。
- `ConversationState` 增加 `owner` 字段；`update()` 记录 owner。
- `resolve_session_id(session_id, owner)`：**只认"已存在且 owner 匹配"的 ID**，否则一律
  签发新的随机 `uuid4`。调用方无法自选/猜测 ID，也无法冒用他人会话——必须复用服务端在
  上一轮响应里返回的 `session_id`。
- `context(session_id, owner)`：owner 不匹配返回空。
- `main.py` 的 `/api/agent/execute` 从 `Authorization` 头算出 `owner` 传入编排器。

**残余说明**：匿名 viewer（无令牌）共享 `anon` 主体，彼此之间靠"随机不可猜的服务端 ID"
隔离；已认证主体（operator/admin）之间靠 owner 严格隔离。会话里只保存低敏感的读类语境
（pid/service/path），且实体经 `sanitize_output` 清洗。

## P2：会话存储容量上限 + LRU 淘汰

**问题**：进程内 `dict` 存所有会话，只有访问时按 TTL 清理，无总量上限；不断换新
`session_id` 会在 30 分钟窗口内持续堆积内存。

**加固**：`ConversationSessionStore` 增加 `max_sessions`（默认 1000）；`update()` 写入后
`_evict_over_capacity()` 按 `updated_at` 淘汰最旧会话，直到不超过上限。

## P3：`/api/agent/plan` 与 execute 行为一致

**问题**：`execute` 注入会话上下文，但 plan-only 直接 `planner.plan(...)` 不注入。"继续看
它的日志"在 execute 能用上轮服务名、在 plan 退化成泛化查询。

**加固**：`/api/agent/plan` 也按 owner 读取会话上下文并注入规划（`agent.conversation_context`，
**只读不写**，不创建/不更新会话），与 execute 一致。

## 测试

`tests/test_session_security.py` 覆盖：`session_principal` 稳定/匿名/不含明文；跨 owner 读取
被拒；冒用/未签发 ID 被替换为新 ID；超容量按最旧淘汰；`/api/agent/plan` 注入会话上下文。
`tests/test_session_context.py` 的多轮注入用例同步改为"服务端签发 ID → 下一轮复用"的安全
流程。
