# 前端可视化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Vue 3（CDN）前端上把闭环 steps、suggested_actions、脱敏徽标、`/api/metrics` 指标盘、`/api/alerts`+`/api/monitor/status` 巡检面板做成可视化界面。

**Architecture:** 改 `frontend/app.js`（取数逻辑 + helpers）、`frontend/index.html`（模板）、`frontend/styles.css`（样式），复用现有设计语言与 CSS 变量。无前端测试框架——验证靠控制方 agent-browser 截图（非 TDD）。零后端改动。

**Tech Stack:** Vue 3 (CDN, prod global)，原生 fetch，纯 CSS（无图表库）。

## Global Constraints

- 纯前端改动，不改后端 API 或安全模型；脱敏/角色门控由后端决定，前端只如实呈现。
- `suggested_actions` 绝不自动执行——仅预填输入框 + 勾选 approved，由人复核后发送。
- `api()` 抛结构化错误 `{status, statusText, detail}`；受限页用 `error.status === 403` 判权限不足、显示提示态（非错误红字）。
- 时间戳是 Unix 秒级 float（`last_run_at` 可为 null）：`formatTime` 用 `new Date(value*1000).toLocaleString()`，空 → "尚未运行"/"—"。
- `submitChat()` 在 `finally` 复位 `approved=false`（二次确认不黏住）。
- `hasRedaction` 递归检测 `detail_redacted===true`。
- 指标条形宽度 `maxP95>0 ? p95/maxP95*100 : 0`（避免 NaN%）；null 的 p50/p95 显示"—"。
- 沿用现有 CSS 变量（`--color-*`/`--font-*`/`--text-*`）与类（`.panel`/`.kv`/`.metric`/`.summary-grid`/`.pill`/`.step-chain`/`.empty-state`/`.muted`/`.eyebrow`）。
- 后端实际数据形状（已核对）：
  - 闭环 step：`{step:int, tools:list, source:"llm"|"rules", observation_summary:str, injection_suspected:bool}`（`_run_single` 路径 steps 为空）。
  - suggested_action：`{tool, arguments, reason}`。
  - metrics snapshot：`{requests:{ep:count}, blocked:int, rate_limited:int, concurrency_rejected:int, tools:{name:{count,p50_ms,p95_ms}}, llm:{success,failure,success_rate|null}}`。
  - alert：`{severity:"critical"|"warning", source, metric, value, threshold, message, timestamp:float}`。
  - monitor status：`{enabled, running, interval_seconds, last_run_at:float|null, last_alert_count, checks:list}`。
- 每个任务结束跑 `python -m unittest discover`（应仍 215 通过——零后端改动则无回归），并由控制方 agent-browser 截图核对该任务的页面（计划末尾"验证"节列了验收标准）。
- 提交信息结尾附：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: app.js 取数逻辑与 helpers

**Files:**
- Modify: `frontend/app.js`

**Interfaces:**
- Produces（供 Task 2-4 模板使用）：`data` 新增 `metrics`/`metricsError`/`metricsLoading`/`alerts`/`alertsError`/`alertsLoading`/`monitorStatus`/`monitorLoading`；方法 `errText`/`formatTime`/`hasRedaction`/`barWidth`/`loadMetrics`/`loadAlerts`/`loadMonitorStatus`/`applySuggestion`；computed `totalRequests`/`metricsMaxP95`；`navItems` 增 `metrics`/`monitor`。

- [ ] **Step 1: 改 `api()` 抛结构化错误**

把 `frontend/app.js` 的 `api` 方法替换为：

```javascript
    async api(path, options = {}) {
      const headers = { ...(options.headers || {}) };
      if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
      const response = await fetch(path, { ...options, headers });
      if (!response.ok) {
        let detail = "";
        try {
          detail = (await response.json())?.detail || "";
        } catch (err) {
          detail = "";
        }
        throw { status: response.status, statusText: response.statusText, detail };
      }
      return response.json();
    },
    errText(error) {
      if (error && typeof error === "object" && ("status" in error || "detail" in error)) {
        return error.detail || `${error.status || ""} ${error.statusText || ""}`.trim() || "请求失败";
      }
      return String(error);
    },
```

- [ ] **Step 2: 把现有 catch 的 `String(error)` 改为 `this.errText(error)`**

`api()` 现在抛对象，`String(error)` 会变成 `[object Object]`。在 `submitChat`、`loadRuntime`、`loadDashboard`、`loadTools`、`loadAudit` 五处的 `catch (error)` 里，把 `String(error)` 全部替换为 `this.errText(error)`（这些 catch 形如 `this.x = { error: String(error) }` 或 `error: String(error)`）。

- [ ] **Step 3: `submitChat()` 在 `finally` 复位 approved**

在 `submitChat` 的 `finally` 块（现有 `this.chatLoading = false;` 所在处）追加一行：

```javascript
      } finally {
        this.chatLoading = false;
        this.approved = false;
      }
```

- [ ] **Step 4: 新增 data 字段**

在 `data()` 的 `return {...}` 里追加：

```javascript
      metrics: {},
      metricsError: "",
      metricsLoading: false,
      alerts: [],
      alertsError: "",
      alertsLoading: false,
      monitorStatus: {},
      monitorLoading: false,
```

- [ ] **Step 5: navItems 增两项**

把 `navItems` 数组改为（在 dashboard 后插入 metrics/monitor）：

```javascript
      navItems: [
        { key: "chat", label: "智能对话", icon: "chat" },
        { key: "dashboard", label: "系统看板", icon: "dashboard" },
        { key: "metrics", label: "指标看板", icon: "metrics" },
        { key: "monitor", label: "巡检告警", icon: "monitor" },
        { key: "tools", label: "MCP 工具", icon: "tools" },
        { key: "audit", label: "审计日志", icon: "audit" },
      ],
```

- [ ] **Step 6: switchPage 分发新页**

在 `switchPage(page)` 方法体里追加：

```javascript
      if (page === "metrics" && !this.metricsError && metricsEmpty(this.metrics)) this.loadMetrics();
      if (page === "monitor") {
        this.loadMonitorStatus();
        if (!this.alertsError && !this.alerts.length) this.loadAlerts();
      }
```

并在 `methods` 之外、文件顶部（`const { createApp } = Vue;` 之后）加一个模块级 helper：

```javascript
function metricsEmpty(metrics) {
  return !metrics || Object.keys(metrics).length === 0;
}
```

- [ ] **Step 7: 新增方法 formatTime / hasRedaction / barWidth / loadMetrics / loadAlerts / loadMonitorStatus / applySuggestion**

在 `methods` 里追加：

```javascript
    formatTime(value, fallback = "尚未运行") {
      if (value === null || value === undefined || value === "") return fallback;
      const n = Number(value);
      if (!Number.isFinite(n)) return fallback;
      return new Date(n * 1000).toLocaleString();
    },
    hasRedaction(obj) {
      if (Array.isArray(obj)) return obj.some((item) => this.hasRedaction(item));
      if (obj && typeof obj === "object") {
        if (obj.detail_redacted === true) return true;
        return Object.values(obj).some((item) => this.hasRedaction(item));
      }
      return false;
    },
    barWidth(p95, maxP95) {
      if (!maxP95 || maxP95 <= 0) return 0;
      const value = Number(p95);
      if (!Number.isFinite(value) || value <= 0) return 0;
      return Math.round((value / maxP95) * 100);
    },
    async loadMetrics() {
      this.metricsLoading = true;
      this.metricsError = "";
      try {
        this.metrics = await this.api("/api/metrics");
      } catch (error) {
        this.metrics = {};
        this.metricsError = error?.status === 403 ? "需 operator/admin 令牌查看指标快照。" : this.errText(error);
      } finally {
        this.metricsLoading = false;
      }
    },
    async loadAlerts() {
      this.alertsLoading = true;
      this.alertsError = "";
      try {
        const data = await this.api("/api/alerts?limit=100");
        this.alerts = data.alerts || [];
      } catch (error) {
        this.alerts = [];
        this.alertsError = error?.status === 403 ? "需 operator/admin 令牌查看告警。" : this.errText(error);
      } finally {
        this.alertsLoading = false;
      }
    },
    async loadMonitorStatus() {
      this.monitorLoading = true;
      try {
        this.monitorStatus = await this.api("/api/monitor/status");
      } catch (error) {
        this.monitorStatus = { error: this.errText(error) };
      } finally {
        this.monitorLoading = false;
      }
    },
    applySuggestion(action) {
      const args = action.arguments || {};
      let instruction;
      if (action.tool === "service.restart") instruction = `重启 ${args.service_name || ""} 服务`.trim();
      else if (action.tool === "process.kill") instruction = `终止 ${args.pid ?? ""} 号进程`.trim();
      else if (action.tool === "temp.clean") instruction = `清理临时目录 ${args.path || ""}`.trim();
      else instruction = `执行 ${action.tool}（参数：${JSON.stringify(args)}）`;
      this.query = instruction;
      this.approved = true;
      this.page = "chat";
      this.$nextTick(() => {
        const el = document.getElementById("query");
        if (el) {
          el.focus();
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    },
```

- [ ] **Step 8: 新增 computed totalRequests / metricsMaxP95**

在 `computed` 里追加：

```javascript
    totalRequests() {
      const requests = this.metrics?.requests || {};
      return Object.values(requests).reduce((sum, value) => sum + (Number(value) || 0), 0);
    },
    metricsMaxP95() {
      const tools = this.metrics?.tools || {};
      let max = 0;
      for (const tool of Object.values(tools)) {
        const value = Number(tool.p95_ms);
        if (Number.isFinite(value) && value > max) max = value;
      }
      return max;
    },
```

- [ ] **Step 9: 验证无回归 + 提交**

Run: `python -m unittest discover 2>&1 | tail -3`
Expected: `Ran 215 tests ... OK`（零后端改动，无回归）。
（前端 JS 无语法错误的把关：实现者通读改动，确认括号/逗号闭合；控制方在 Task 2 后用 agent-browser 加载验证。）

```bash
git add frontend/app.js
git commit -m "feat(frontend): app.js 取数逻辑与 helpers（结构化错误/指标/告警/建议预填）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 对话页增强（steps 闭环形状 + suggested_actions + 脱敏徽标）

**Files:**
- Modify: `frontend/index.html`（chat 页 + SVG 图标 defs）
- Modify: `frontend/styles.css`（新增组件样式）

**Interfaces:**
- Consumes: Task 1 的 `applySuggestion`/`hasRedaction`；后端 `chatResult.steps`/`suggested_actions`/`result`。

- [ ] **Step 1: 替换 chat 页"编排链路" article 为闭环 steps 渲染**

在 `frontend/index.html` 把现有 `<article class="panel" v-if="chatResult.steps?.length">...</article>`（"编排链路"那段，含 `step.id`/`step.tool`/`step.status`）整体替换为：

```html
          <article class="panel" v-if="chatResult.steps?.length">
            <header class="panel-header">
              <h2>多步推理闭环</h2>
              <span class="eyebrow">Reasoning loop · {{ chatResult.steps.length }} 步</span>
            </header>
            <ol class="step-chain">
              <li v-for="s in chatResult.steps" :key="s.step" class="step-item">
                <div class="step-head">
                  <span class="step-id">第 {{ s.step }} 步</span>
                  <span v-for="t in (s.tools || [])" :key="t" class="chip">{{ t }}</span>
                  <span class="pill">{{ s.source }}</span>
                  <span class="pill is-error" v-if="s.injection_suspected">⚠ 疑似注入（已隔离）</span>
                </div>
                <p class="step-obs muted" v-if="s.observation_summary">{{ s.observation_summary }}</p>
              </li>
            </ol>
          </article>
```

- [ ] **Step 2: 在 steps 之后插入 suggested_actions 面板**

紧接上面的 article 之后插入：

```html
          <article class="panel suggest-panel" v-if="chatResult.suggested_actions?.length">
            <header class="panel-header">
              <h2>建议的修复动作</h2>
              <span class="eyebrow">需二次确认 · {{ chatResult.suggested_actions.length }}</span>
            </header>
            <div v-for="(action, idx) in chatResult.suggested_actions" :key="idx" class="suggest-item">
              <div class="suggest-info">
                <span class="pill is-medium">{{ action.tool }}</span>
                <span class="muted">{{ action.reason }}</span>
                <code v-if="Object.keys(action.arguments || {}).length">{{ JSON.stringify(action.arguments) }}</code>
              </div>
              <button type="button" class="ghost" @click="applySuggestion(action)">确认执行</button>
            </div>
            <p class="muted suggest-note">点击后会把动作填入对话框并勾选二次确认，请复核后再发送——不会自动执行。</p>
          </article>
```

- [ ] **Step 3: 在"最终结论"面板里加脱敏徽标**

在结论 article（`<article class="panel" v-if="chatResult.trace_id || chatResult.error">`）的 `<header class="panel-header">...</header>` 之后插入：

```html
            <p class="redaction-badge" v-if="hasRedaction(chatResult.result)">🔒 明细已按角色脱敏 · 需 operator 令牌查看全量</p>
```

- [ ] **Step 4: 加 metrics/monitor 的 SVG 图标 symbol**

在 `<svg width="0" height="0" ...><defs>` 里追加两个 symbol（放在 `#icon-send` 之后）：

```html
        <symbol id="icon-metrics" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 3v18h18" />
          <rect x="7" y="12" width="3" height="6" rx="0.5" />
          <rect x="12" y="8" width="3" height="10" rx="0.5" />
          <rect x="17" y="5" width="3" height="13" rx="0.5" />
        </symbol>
        <symbol id="icon-monitor" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </symbol>
```

- [ ] **Step 5: 加样式到 `frontend/styles.css`**

在文件末尾追加（沿用现有变量）：

```css
.chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}
.step-obs {
  margin: 6px 0 0;
  font-size: var(--text-sm);
}
.suggest-panel {
  border-left: 3px solid var(--color-warning);
}
.suggest-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
}
.suggest-item:last-of-type {
  border-bottom: none;
}
.suggest-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.suggest-info code {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}
.suggest-note {
  margin-top: 10px;
  font-size: var(--text-xs);
}
.redaction-badge {
  display: inline-block;
  margin: 0 0 12px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--color-warning-soft);
  color: var(--color-warning);
  font-size: var(--text-xs);
}
```

> 注：若 `frontend/styles.css` 已存在 `.ghost` 按钮样式则复用；若没有，沿用 `.actions button.ghost` 现有外观（实现前确认 `.ghost` 是否已定义，避免按钮无样式）。

- [ ] **Step 6: 验证 + 提交**

Run: `python -m unittest discover 2>&1 | tail -3`（应仍 215 OK）。
控制方 agent-browser 截图核对：发一条诊断 query，看闭环 steps 时间线、（命中操作类时）suggested_actions、（viewer 调安全工具时）脱敏徽标；点"确认执行"只预填不自动发请求。

```bash
git add frontend/index.html frontend/styles.css
git commit -m "feat(frontend): 对话页闭环步骤/修复建议/脱敏徽标可视化

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 指标看板页

**Files:**
- Modify: `frontend/index.html`（新增 metrics 页 section）
- Modify: `frontend/styles.css`（条形样式）

**Interfaces:**
- Consumes: Task 1 的 `loadMetrics`/`metrics`/`metricsError`/`metricsLoading`/`totalRequests`/`metricsMaxP95`/`barWidth`。

- [ ] **Step 1: 在 dashboard section 之后插入 metrics 页**

在 `frontend/index.html` 的 `<!-- Dashboard -->` section 闭合 `</section>` 之后插入：

```html
        <!-- Metrics -->
        <section v-if="page === 'metrics'" class="page">
          <div class="actions right">
            <button class="ghost" @click="loadMetrics" :disabled="metricsLoading">
              <svg class="icon" aria-hidden="true" style="width:14px;height:14px"><use href="#icon-refresh" /></svg>
              {{ metricsLoading ? "刷新中" : "刷新指标" }}
            </button>
          </div>
          <article class="panel" v-if="metricsError">
            <p class="empty-state">{{ metricsError }}</p>
          </article>
          <template v-else-if="metrics.tools !== undefined">
            <section class="summary-grid">
              <article class="metric"><span class="metric-label">总请求数</span><span class="big-number">{{ totalRequests }}</span></article>
              <article class="metric"><span class="metric-label">安全拦截</span><span class="big-number">{{ metrics.blocked ?? 0 }}</span></article>
              <article class="metric"><span class="metric-label">限流</span><span class="big-number">{{ metrics.rate_limited ?? 0 }}</span></article>
              <article class="metric"><span class="metric-label">并发拒绝</span><span class="big-number">{{ metrics.concurrency_rejected ?? 0 }}</span></article>
            </section>
            <article class="panel">
              <header class="panel-header"><h2>LLM 调用</h2><span class="eyebrow">LLM</span></header>
              <dl class="kv">
                <dt>成功</dt><dd>{{ metrics.llm?.success ?? 0 }}</dd>
                <dt>失败</dt><dd>{{ metrics.llm?.failure ?? 0 }}</dd>
                <dt>成功率</dt><dd>{{ metrics.llm?.success_rate == null ? "—" : (metrics.llm.success_rate * 100).toFixed(1) + "%" }}</dd>
              </dl>
            </article>
            <article class="panel">
              <header class="panel-header"><h2>工具耗时</h2><span class="eyebrow">P50 / P95 (ms)</span></header>
              <div class="bar-row" v-for="(tool, name) in metrics.tools" :key="name">
                <span class="bar-label">{{ name }}</span>
                <div class="bar-track"><div class="bar-fill" :style="{ width: barWidth(tool.p95_ms, metricsMaxP95) + '%' }"></div></div>
                <span class="bar-value">{{ tool.count }}× · p50 {{ tool.p50_ms ?? "—" }} · p95 {{ tool.p95_ms ?? "—" }}</span>
              </div>
              <p v-if="!Object.keys(metrics.tools).length" class="empty-state">暂无工具调用记录</p>
            </article>
            <article class="panel">
              <header class="panel-header"><h2>端点请求</h2><span class="eyebrow">Requests</span></header>
              <dl class="kv">
                <template v-for="(count, ep) in metrics.requests" :key="ep"><dt>{{ ep }}</dt><dd>{{ count }}</dd></template>
              </dl>
              <p v-if="!Object.keys(metrics.requests || {}).length" class="empty-state">暂无请求</p>
            </article>
          </template>
          <article class="panel" v-else>
            <p class="empty-state">点击"刷新指标"加载（需 operator/admin 令牌）。</p>
          </article>
        </section>
```

- [ ] **Step 2: 加条形样式到 `frontend/styles.css`**

追加：

```css
.bar-row {
  display: grid;
  grid-template-columns: 160px 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 7px 0;
}
.bar-label {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-track {
  height: 8px;
  border-radius: 6px;
  background: var(--color-surface-2);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 6px;
  background: var(--color-primary);
  transition: width 0.3s ease;
}
.bar-value {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  white-space: nowrap;
}
```

- [ ] **Step 3: 验证 + 提交**

Run: `python -m unittest discover 2>&1 | tail -3`（应仍 215 OK）。
控制方 agent-browser 核对：填 operator 令牌点"指标看板"→看 KPI/工具耗时条/LLM 成功率；清空令牌刷新 → 看 403 提示态。

```bash
git add frontend/index.html frontend/styles.css
git commit -m "feat(frontend): 指标看板页（/api/metrics 可视化）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 巡检告警页

**Files:**
- Modify: `frontend/index.html`（新增 monitor 页 section）
- Modify: `frontend/styles.css`（告警条样式）

**Interfaces:**
- Consumes: Task 1 的 `loadMonitorStatus`/`loadAlerts`/`monitorStatus`/`alerts`/`alertsError`/`alertsLoading`/`monitorLoading`/`formatTime`。

- [ ] **Step 1: 在 metrics section 之后插入 monitor 页**

```html
        <!-- Monitor -->
        <section v-if="page === 'monitor'" class="page">
          <div class="actions right">
            <button class="ghost" @click="loadMonitorStatus(); loadAlerts();" :disabled="alertsLoading || monitorLoading">
              <svg class="icon" aria-hidden="true" style="width:14px;height:14px"><use href="#icon-refresh" /></svg>
              {{ (alertsLoading || monitorLoading) ? "刷新中" : "刷新" }}
            </button>
          </div>
          <article class="panel">
            <header class="panel-header"><h2>巡检状态</h2><span class="eyebrow">Monitor</span></header>
            <dl class="kv" v-if="monitorStatus.checks">
              <dt>启用</dt><dd>{{ monitorStatus.enabled ? "是" : "否" }}</dd>
              <dt>运行中</dt><dd>{{ monitorStatus.running ? "是" : "否" }}</dd>
              <dt>间隔 (秒)</dt><dd>{{ monitorStatus.interval_seconds }}</dd>
              <dt>上次巡检</dt><dd>{{ formatTime(monitorStatus.last_run_at) }}</dd>
              <dt>上轮告警数</dt><dd>{{ monitorStatus.last_alert_count }}</dd>
              <dt>检查项</dt><dd>{{ (monitorStatus.checks || []).join(" · ") }}</dd>
            </dl>
            <p v-else class="empty-state">加载中…</p>
          </article>
          <article class="panel">
            <header class="panel-header"><h2>告警</h2><span class="eyebrow">Alerts · {{ alerts.length }}</span></header>
            <p v-if="alertsError" class="empty-state">{{ alertsError }}</p>
            <section v-else-if="alerts.length" class="alert-list">
              <article v-for="(alert, idx) in alerts" :key="idx" class="alert-item" :class="`is-${alert.severity}`">
                <header class="alert-head">
                  <span class="pill" :class="alert.severity === 'critical' ? 'is-high' : 'is-medium'">{{ alert.severity }}</span>
                  <span class="alert-source">{{ alert.source }}</span>
                  <span class="muted">{{ formatTime(alert.timestamp, "—") }}</span>
                </header>
                <p class="alert-msg">{{ alert.message }}</p>
                <p class="muted">{{ alert.metric }} = {{ alert.value }}（阈值 {{ alert.threshold }}）</p>
              </article>
            </section>
            <p v-else class="empty-state">暂无告警（巡检未开启或一切正常）。</p>
          </article>
        </section>
```

- [ ] **Step 2: 加告警条样式到 `frontend/styles.css`**

追加：

```css
.alert-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.alert-item {
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-warning);
}
.alert-item.is-critical {
  border-left-color: var(--color-error);
  background: var(--color-error-soft);
}
.alert-item.is-warning {
  border-left-color: var(--color-warning);
  background: var(--color-warning-soft);
}
.alert-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.alert-source {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}
.alert-msg {
  margin: 0 0 4px;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}
```

- [ ] **Step 3: 验证 + 提交**

Run: `python -m unittest discover 2>&1 | tail -3`（应仍 215 OK）。
控制方 agent-browser 核对：点"巡检告警"→ 无需令牌看到状态卡；填 operator 令牌刷新看告警列表（可先 `AGENT_MONITOR_ENABLED=true` 跑出告警，或后端 seed 一条）。

```bash
git add frontend/index.html frontend/styles.css
git commit -m "feat(frontend): 巡检告警页（/api/monitor/status + /api/alerts 可视化）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 文档同步

**Files:**
- Modify: `CLAUDE.md`（本地运行/前端段落提一句新页面）
- Create: `docs/frontend-visualization.md`（可选，简述四页与数据来源）

- [ ] **Step 1: 写 `docs/frontend-visualization.md`**

简述前端六个页面（chat/dashboard/metrics/monitor/tools/audit）、各页数据来源 API、令牌门控（metrics/alerts 需 operator/admin）、suggested_actions 预填行为、脱敏徽标。

- [ ] **Step 2: 更新 `CLAUDE.md`**

在"本地运行"或前端相关段落补一句：前端新增"指标看板"（`/api/metrics`）与"巡检告警"（`/api/monitor/status`+`/api/alerts`）页，闭环 steps/suggested_actions/脱敏徽标已可视化；受限页需 operator/admin 令牌。只改相关句，不动其它。

- [ ] **Step 3: 验证 + 提交**

Run: `python -m unittest discover 2>&1 | tail -3`（应仍 215 OK）。

```bash
git add docs/frontend-visualization.md CLAUDE.md
git commit -m "docs: 同步前端可视化页面说明

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 验证（控制方 agent-browser，对照 spec 验收标准）

实现完成后，控制方起本地后端（`AGENT_OPERATOR_TOKEN=dev-operator-token`，可选
`AGENT_MONITOR_ENABLED=true AGENT_MONITOR_INTERVAL_SECONDS=30`），agent-browser 打开
`http://127.0.0.1:8000` 逐页截图，核对 spec §6 的 7 条验收标准：闭环 steps 形状正确、
suggested_actions 只预填不自动请求、脱敏徽标、metrics/alerts 的 403 提示态、monitor status 免令牌、
刷新/切换/空数据/403/网络失败稳定 UI、无文本溢出/布局错位/`NaN%`/`[object Object]`。

## Self-Review（计划自检）

- **Spec coverage:** api 结构化错误（Task 1 S1-2）、approved 复位（S3）、formatTime/hasRedaction/barWidth（S7）、loadMetrics/Alerts/MonitorStatus + applySuggestion（S7）、nav/switchPage（S5-6）、computed（S8）；对话增强（Task 2）；指标盘（Task 3）；巡检面板（Task 4）；文档（Task 5）；验收标准对照（验证节）——spec 各节均有对应任务。
- **Placeholder scan:** 无 TBD/占位；每步给出完整代码。
- **Type consistency:** `metrics`/`alerts`/`monitorStatus`/`metricsError`/`alertsError` 在 Task 1 定义、Task 3-4 模板消费一致；`barWidth(p95, maxP95)`/`metricsMaxP95`/`totalRequests`/`formatTime(value, fallback)`/`hasRedaction(obj)`/`applySuggestion(action)`/`errText(error)` 跨任务签名一致；后端字段（step/tools/source/observation_summary/injection_suspected、tool.count/p50_ms/p95_ms、alert.severity/source/metric/value/threshold/message/timestamp、status.* ）与已核对的真实形状一致。
- **不变量:** 零后端改动（全程 215 测试不变）；suggested_actions 只预填；受限页 403 提示态；纯前端、复用现有 CSS 变量。
