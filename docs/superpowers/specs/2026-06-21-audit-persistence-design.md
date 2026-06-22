# 审计持久化升级设计方案

- 日期：2026-06-21
- 主题：把审计从扁平 JSONL 升级为 SQLite 权威存储 + hash 链（篡改可发现 / tamper-evident）+ 可查询接口
- 状态：已通过设计评审；按评审反馈修订（并发共享 store、部署路径、尾删检测、fail-closed）

## 1. 背景与目标

赛题 A2 需求⑤要求完整记录"接收指令 → 感知环境 → 推理决策 → 安全校验 → 执行结果"的闭环日志并支持异常回溯。审计是本项目的得分核心。

现状评估（核对代码）：
- `backend/database/db.py` 的 `init_db()` 建了一张 `audit_index` 表，但**全仓库无任何代码读写它**——空壳，SQLite 实际未使用。
- 真实审计落地是 `backend/audit/logger.py` 写 JSONL（`audit/logs/audit.log`），追加写。
- 查询 `read_recent(limit, trace_id)` 把**整个文件读入内存**、按行倒序过滤——O(n) 全文件扫描。
- 事件模型 `backend/audit/models.py::AuditEvent(timestamp, trace_id, stage, user_id, status, data)`。

问题：随 agent 长期运行日志增大，查询变慢；无索引查询、无防篡改；那张为审计准备的 SQLite 表从未接上。

目标：审计升级为 **SQLite 权威存储**，提供按 trace_id/时间/user/status 的索引查询、hash 链篡改可发现与校验、按需 JSONL 导出；调用方与现有接口尽量零改动。

### 已确认的设计决策

1. 存储关系：**SQLite 为唯一权威源**；JSONL 仅通过按需导出接口产出。
2. 防篡改：**hash 链 + audit_meta（last_hash/event_count）+ 校验接口**，做到**篡改可发现（tamper-evident）**：覆盖内容篡改、中间行删除、**尾部截断**。明确威胁模型：对拥有 DB 完整写权限、能同时重建整链+meta 的攻击者，本方案不构成密码学级防篡改（那需要外部签名/锚定，属后续）。
3. 保留/轮转：**先不做**（YAGNI，记为后续；与 hash 链冲突，留待分段/checkpoint 方案）。
4. 旧日志：**全新开始**，不导入 `audit.log`，文件留在原地不删。
5. 并发：**按 DB 路径进程内共享单个 `AuditStore` 实例**（多个 `AuditLogger` 共用），写入用类级锁 + `BEGIN IMMEDIATE` + `busy_timeout`，保证 hash 链不分叉。
6. 写入失败策略：可配置 `AGENT_AUDIT_FAIL_CLOSED`，开发默认 best-effort，生产可设 fail-closed。

## 2. 实现路线选型

| 路线 | 说明 | 结论 |
|------|------|------|
| A. 新增 `AuditStore` + `AuditLogger` 变薄 façade | SQLite 逻辑独立成 store，logger 保持签名转调 | 采用。关注点分离、hash 链可独立测试、调用方零改动 |
| B. SQLite 逻辑直接塞进 `AuditLogger` | 不加新类 | 否决。序列化+存储+hash 链混在一起，难测、文件臃肿 |
| C. JSONL + SQLite 并存双写 | — | 否决（已选 SQLite 权威） |

## 3. 组件与边界

- **`backend/audit/store.py`（新）** — `AuditStore`：拥有 SQLite 连接、建表、带 hash 链的插入、查询、`verify_chain()`。单一职责=审计存储。
  - **进程内按 DB 路径共享**：提供 `get_audit_store(path)` 工厂，用模块级 `dict[path] -> AuditStore` + 一把模块级锁缓存实例。所有 `AuditLogger` 经它取同一个 store，**确保同一 DB 全进程只有一个写入临界区**（解决多 logger 各自建 store 导致链分叉）。
- **`backend/audit/logger.py`（改）** — `AuditLogger` 保持现有公开方法（`event()`、`read_recent()`、`write()`）签名不变，内部经 `get_audit_store(resolved_path)` 取共享 store 并委托。仍是调用方使用的稳定门面。
  - `event()` → 写入一条 `audit_events`（带 trace_id/stage）。
  - `write(user_id, query, plan, status, result)`（被 `backend/agent/executor.py:77,114` 调用，签名不含 trace_id）→ 映射为一条 `audit_events`：`stage="summary"`、`trace_id=""`、`data` 为 `AuditRecord` 的字段（query/intent/tools/result）。该行同样进入 hash 链；因 trace_id 为空，不会出现在按 trace_id 的查询里，但完整保留且可校验。executor 因此**零改动**。
- **`backend/config.py`（改）** — 新增 `AGENT_AUDIT_DB_PATH`（开发默认 `backend/audit/logs/audit.db`）与 `AGENT_AUDIT_FAIL_CLOSED`（默认 `false`）。注意：生产经 systemd 覆盖为 `/var/lib/software-cup-ops/audit.db`（见第 9 节部署）。
- **`backend/database/db.py`（改）** — 移除未使用的 `audit_index` 空壳表；审计表 schema 由 `AuditStore` 负责 `CREATE TABLE IF NOT EXISTS`，`init_db()` 启动时确保一次（构造一个 `AuditStore` 即建表）。

**不改**：orchestrator / main 的 Agent 链路 / mcp_server / executor —— 因为 `AuditLogger` 公开签名不变。

## 4. 数据模型（schema）

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,      -- ISO UTC（沿用 AuditEvent.timestamp）
    trace_id  TEXT NOT NULL DEFAULT '',  -- event() 填真实 trace_id；write() 摘要行为空串
    stage     TEXT NOT NULL,
    user_id   TEXT,
    status    TEXT,
    data_json TEXT NOT NULL,      -- json.dumps(data, sort_keys=True, ensure_ascii=False)
    prev_hash TEXT,               -- 上一行 hash（创世为 ""）
    hash      TEXT NOT NULL       -- 见下方定义
);
CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts    ON audit_events(timestamp);

-- 单行元数据，用于检测尾部截断（删除最后若干行）
CREATE TABLE IF NOT EXISTS audit_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),  -- 恒为 1，单行
    last_hash   TEXT NOT NULL DEFAULT '',
    event_count INTEGER NOT NULL DEFAULT 0
);
```

每次 `append()` 在**同一事务**内：插入 `audit_events` 行后，`UPDATE audit_meta SET last_hash=?, event_count=event_count+1 WHERE id=1`（首次 `INSERT OR IGNORE` 一行 id=1）。

hash 定义（确定性）：

```
hash = sha256(
    prev_hash + "|" + timestamp + "|" + trace_id + "|" + stage + "|" +
    (user_id or "") + "|" + (status or "") + "|" + data_json
).hexdigest()
```

- `data_json` 用 `json.dumps(data, sort_keys=True, ensure_ascii=False)`，保证同一 data 序列化稳定。
- 创世行 `prev_hash = ""`。

## 5. 数据流与并发

- `AuditLogger.event(...)` / `write(...)` → 经 `get_audit_store(path)` 取**进程内共享**的 `AuditStore` → `append(event_dict)`。
- **并发正确性（评审 P1）**：当前 `main.py:29`、`orchestrator.py:38`、`executor.py:26`、`mcp_server/server.py:46` 各自 `AuditLogger()`。若每个 logger 各建 store，`threading.Lock` 只在实例内，多个 store 可能读到同一 `prev_hash` → 链分叉/DB 锁。故：
  1. 同一 DB 路径**全进程共享一个 `AuditStore`**（工厂 + 模块级缓存）；
  2. `append()` 持 store 的**类级/实例锁**串行，并用 `BEGIN IMMEDIATE` 事务包住「取尾 hash + 插入 event + 更新 meta」，连接设 `busy_timeout`（如 5000ms）避免多进程/多连接偶发 `database is locked` 直接失败。
- 连接：`sqlite3.connect(path, check_same_thread=False)` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`。应用是 async + 线程池（executor 在 `anyio.to_thread` 内调用），故需 `check_same_thread=False`。
- `read_recent(limit, trace_id)` → `SELECT ... WHERE (trace_id=? or 全部) ORDER BY id DESC LIMIT ?`，再倒回时间正序返回。**返回字段与现状一致**：`timestamp/trace_id/stage/user_id/status/data`，额外附 `hash`（增量，向后兼容）。`/api/audit/recent` 行为不变。

## 6. 新增查询能力与接口

- `AuditStore.query(limit, trace_id=None, user_id=None, status=None, since=None, until=None)` — 富查询（新）。
- `AuditStore.verify_chain()` → `{"ok": bool, "broken_at": id|None, "count": n, "tail_ok": bool}`（新）：
  1. 逐行重算 hash，遇首个不匹配返回其 `id`（检测内容篡改 / 中间行删除——删除会使后继行 `prev_hash` 对不上）。
  2. 比对 `audit_meta`：实际行数 == `event_count` 且实际末行 `hash` == `last_hash`；不符则 `tail_ok=false`（检测**尾部截断**，评审 P2）。
  3. `ok = 链逐行匹配 and tail_ok`。
- 新增接口（`backend/main.py`）：
  - `GET /api/audit/verify` — 返回链完整性结果。
  - `GET /api/audit/export?trace_id=&limit=` — 按需导出 JSONL（满足"原始流水/离线导出"）。
  - `GET /api/audit/recent` — 保持不变；可选新增 `user_id`/`status` 过滤参数（纯增量，默认行为不变）。

## 7. 错误处理

审计写入失败（DB 锁超时 / IO 异常）的处理由 `AGENT_AUDIT_FAIL_CLOSED` 控制（评审 P2，化解"完整闭环日志"与 best-effort 的冲突）：

- **`false`（开发默认）= best-effort**：捕获并记 stderr 警告，不把异常抛进运维请求链。
- **`true`（生产建议）= fail-closed**：审计写入失败时 `event()`/`write()` 抛异常，向上冒泡使该请求以错误返回。
  - **前置审计**（`received_instruction` / `security_validation`）失败 → 工具尚未执行，等价于"未审计则不执行"，真正 fail-closed。
  - **后置审计**（`execution_result` 等）失败 → 操作已执行无法回滚，只能让请求显式报错并提示"已执行但审计写入失败"。设计如实记录此局限；如需"中高风险绝不无审计执行"，前置审计的 fail-closed 已覆盖。

其它：
- `verify_chain()` 遇首个 hash 不匹配返回 `broken_at`，尾部不一致置 `tail_ok=false`，均不抛异常。
- 时间戳沿用现有 `AuditEvent`（UTC ISO）。

## 8. 测试（TDD）

- **`tests/test_audit_store.py`（新）**：
  1. `append()` 后 `read_recent`/`query` 能命中该事件。
  2. `query`/`read_recent` 按 trace_id 过滤、遵守 limit、返回时间正序。
  3. hash 链：连续 append 后每行 `hash` 依赖 `prev_hash`；clean 数据 `verify_chain().ok is True`。
  4. 篡改检测：直接 UPDATE 某行 `data_json` → `verify_chain()` 返回该行 `broken_at`、`ok=false`。
  5. **尾删检测（P2）**：删除最后一行（不动 `audit_meta`）→ `verify_chain()` 返回 `tail_ok=false`、`ok=false`。
  6. 富查询：按 `user_id`/`status` 过滤生效。
  7. `write()` 摘要：调用 `write()` 后产生一条 `stage="summary"`、`trace_id=""` 的行，可被 `query` 取到，且 `verify_chain` 仍 ok。
  8. **共享 store（P1）**：`get_audit_store(p)` 同一路径返回同一实例；两个 `AuditLogger` 指向同一路径交替 append 后，链连续（`verify_chain().ok`），`event_count` 等于总写入数。
  9. **fail-closed（P2）**：模拟 append 失败（如指向不可写路径/打桩抛错），`AGENT_AUDIT_FAIL_CLOSED=true` 时 `event()` 抛异常；`false` 时吞掉不抛。
- **`tests/test_controlled_tools.py`（改）**：`setUp` 改为把审计指向临时 SQLite（设 `AGENT_AUDIT_DB_PATH` 到临时路径）替代原 `AGENT_AUDIT_LOG_PATH`，使 `test_blocked_request_writes_audit_trace`、`test_executed_commands_are_written_to_audit_trace` 在 SQLite 后端下仍通过。
- 测试直接构造 `AuditStore`/`AuditLogger`，不依赖网络与 LLM。

## 9. 部署与文档同步

**部署（评审 P1，必须做，否则生产写不了审计 DB）**：`deploy/systemd.service` 有 `ProtectSystem=strict`，`ReadWritePaths=/var/lib/software-cup-ops /var/log/software-cup-ops /opt/software-cup-ops/tmp`。默认 `backend/audit/logs/audit.db` 落在 `WorkingDirectory=/opt/software-cup-ops` 下，为只读 → 写入失败。

- `deploy/systemd.service`：新增 `Environment=AGENT_AUDIT_DB_PATH=/var/lib/software-cup-ops/audit.db`（与既有 `AGENT_DB_PATH=/var/lib/...` 同目录，已在 `ReadWritePaths`）。可选 `AGENT_AUDIT_FAIL_CLOSED=true`。
- `deploy/README.md`：同步说明新环境变量及 WAL 旁文件（`audit.db-wal`/`-shm`）也落在 `/var/lib/software-cup-ops`。

**文档**：`docs/audit-tracing.md`、`ARCHITECTURE.md` 更新审计存储说明（SQLite 权威、hash 链 + audit_meta、tamper-evident 威胁模型、verify/export 接口、`AGENT_AUDIT_DB_PATH`/`AGENT_AUDIT_FAIL_CLOSED`）。按各文件现状语言书写，代码标识符保留原文。

## 10. 改动清单

| 文件 | 改动 |
|------|------|
| `backend/audit/store.py` | 新增：`AuditStore`（audit_events + audit_meta schema、共享工厂 `get_audit_store`、`BEGIN IMMEDIATE` hash 链 append、query、verify_chain 含尾删检测） |
| `backend/audit/logger.py` | 改：经 `get_audit_store` 取共享 store 并委托，保持 `event`/`read_recent`/`write` 签名 |
| `backend/config.py` | 新增 `AGENT_AUDIT_DB_PATH`、`AGENT_AUDIT_FAIL_CLOSED` |
| `backend/database/db.py` | 移除 `audit_index` 空壳；`init_db` 经 `get_audit_store` 确保审计表 |
| `backend/main.py` | 新增 `/api/audit/verify`、`/api/audit/export`；`/api/audit/recent` 可选过滤 |
| `tests/test_audit_store.py` | 新增 |
| `tests/test_controlled_tools.py` | 改 setUp 指向临时审计 DB |
| `deploy/systemd.service` | 新增 `AGENT_AUDIT_DB_PATH=/var/lib/software-cup-ops/audit.db`（P1） |
| `deploy/README.md` | 同步新环境变量与 WAL 旁文件路径 |
| `docs/audit-tracing.md`、`ARCHITECTURE.md` | 文档同步 |

## 11. 验收标准

1. 审计事件写入 SQLite；`read_recent` 经 SQL 查询返回，`/api/audit/recent` 行为与字段向后兼容。
2. 可按 trace_id/时间/user/status 索引查询。
3. hash 链：clean 数据 `verify_chain` ok；篡改任一行 `data_json` 后能检出 `broken_at`、`ok=false`。
4. **尾删检测（评审 P2）**：删除最后若干行而不改 `audit_meta` 时，`verify_chain` 返回 `tail_ok=false`、`ok=false`。
5. **共享 store / 并发不分叉（评审 P1）**：`get_audit_store(path)` 对同一路径返回同一实例；多个 `AuditLogger`（main/orchestrator/executor/mcp_server）指向同一 DB 交替写入后，hash 链连续（`verify_chain().ok`），`audit_meta.event_count` 等于总写入条数。
6. **fail-closed（评审 P2）**：`AGENT_AUDIT_FAIL_CLOSED=true` 时前置审计写入失败使请求以错误返回（未审计不执行）；`false` 时写入失败为 best-effort 不中断请求链。
7. **部署可写（评审 P1）**：systemd 下 `AGENT_AUDIT_DB_PATH` 指向 `/var/lib/software-cup-ops/audit.db`（含 WAL 旁文件）落在 `ReadWritePaths` 内，`ProtectSystem=strict` 下可正常写入。
8. `GET /api/audit/verify` 返回完整性；`GET /api/audit/export` 产出 JSONL。
9. 新增 `tests/test_audit_store.py` 全通过；`tests/test_controlled_tools.py` 调整后全通过；其余既有测试不回归。
10. orchestrator/main 链路/mcp_server/executor 无需改动即继续工作。
