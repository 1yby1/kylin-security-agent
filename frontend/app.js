const { createApp } = Vue;

function metricsEmpty(metrics) {
  return !metrics || Object.keys(metrics).length === 0;
}

createApp({
  data() {
    return {
      page: "chat",
      navItems: [
        { key: "chat", label: "智能对话", icon: "chat" },
        { key: "dashboard", label: "系统看板", icon: "dashboard" },
        { key: "metrics", label: "指标看板", icon: "metrics" },
        { key: "monitor", label: "巡检告警", icon: "monitor" },
        { key: "tools", label: "MCP 工具", icon: "tools" },
        { key: "audit", label: "审计日志", icon: "audit" },
      ],
      query: "查看系统状态、进程和端口情况",
      approved: false,
      token: "",
      sessionId: localStorage.getItem("ops-agent-session-id") || "",
      chatLoading: false,
      dashboardLoading: false,
      toolsLoading: false,
      auditLoading: false,
      chatResult: {},
      dashboard: {},
      tools: [],
      auditRecords: [],
      auditTraceId: "",
      auditLimit: 80,
      runtime: null,
      metrics: {},
      metricsError: "",
      metricsLoading: false,
      alerts: [],
      alertsError: "",
      alertsLoading: false,
      monitorStatus: {},
      monitorLoading: false,
    };
  },
  computed: {
    currentTitle() {
      const current = this.navItems.find((item) => item.key === this.page);
      return current ? current.label : "安全智能运维 Agent";
    },
    runtimeLabel() {
      const identity = this.runtime?.runtime_identity;
      if (!identity) return "runtime · loading";
      const runs = identity.runs_as_user || "—";
      const target = identity.target_user || "—";
      return `${runs} → ${target}`;
    },
    runtimeDotClass() {
      const identity = this.runtime?.runtime_identity;
      if (!identity) return "is-warning";
      if (this.runtime?.error) return "is-error";
      if (identity.warning) return "is-warning";
      return identity.least_privilege_enforced ? "" : "is-warning";
    },
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
  },
  mounted() {
    this.loadRuntime();
    this.loadTools();
  },
  methods: {
    switchPage(page) {
      this.page = page;
      if (page === "dashboard" && !Object.keys(this.dashboard).length) this.loadDashboard();
      if (page === "tools" && !this.tools.length) this.loadTools();
      if (page === "audit" && !this.auditRecords.length) this.loadAudit();
      if (page === "metrics" && metricsEmpty(this.metrics)) this.loadMetrics();
      if (page === "monitor") {
        this.loadMonitorStatus();
        if (!this.alerts.length) this.loadAlerts();
      }
    },
    format(value) {
      return JSON.stringify(value ?? {}, null, 2);
    },
    stepArgs(args) {
      const hidden = new Set(["query", "user_id", "user_role", "approved"]);
      const result = {};
      for (const [key, value] of Object.entries(args || {})) {
        if (hidden.has(key)) continue;
        result[key] = typeof value === "object" ? JSON.stringify(value) : value;
      }
      return result;
    },
    stageStatusClass(status) {
      if (!status) return "unknown";
      const value = String(status).toLowerCase();
      if (["completed", "passed", "success", "received", "llm", "rules"].includes(value)) return "success";
      if (["approval_required", "warning", "skipped", "started"].includes(value)) return "warning";
      if (["blocked", "failed", "error"].includes(value)) return "error";
      return "unknown";
    },
    stepTitle(step) {
      if (step?.step !== undefined && step?.step !== null) return `第 ${step.step} 步`;
      return step?.id || "步骤";
    },
    stepTools(step) {
      if (Array.isArray(step?.tools)) return step.tools;
      return step?.tool ? [step.tool] : [];
    },
    stepSource(step) {
      return step?.source || step?.status || "";
    },
    stepStatus(step) {
      return this.stageStatusClass(this.stepSource(step));
    },
    stepSummary(step) {
      return step?.observation_summary || step?.message || step?.result?.error || "";
    },
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
    async submitChat() {
      this.chatLoading = true;
      this.chatResult = {};
      try {
        this.chatResult = await this.api("/api/agent/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: this.query,
            user_id: "web-user",
            session_id: this.sessionId || undefined,
            approved: this.approved,
            context: {},
          }),
        });
        if (this.chatResult.session_id) {
          this.sessionId = this.chatResult.session_id;
          localStorage.setItem("ops-agent-session-id", this.sessionId);
        }
        if (this.chatResult.trace_id) {
          this.auditTraceId = this.chatResult.trace_id;
        }
      } catch (error) {
        this.chatResult = { error: this.errText(error) };
      } finally {
        this.chatLoading = false;
        this.approved = false;
      }
    },
    async loadRuntime() {
      try {
        this.runtime = await this.api("/api/security/runtime");
      } catch (error) {
        this.runtime = { error: this.errText(error) };
      }
    },
    async loadDashboard() {
      this.dashboardLoading = true;
      try {
        const [system, process, network, service] = await Promise.all([
          this.runTool("system", {}),
          this.runTool("process", { limit: 8 }),
          this.runTool("network", { limit: 12, include_lsof: false }),
          this.runTool("service", { limit: 12 }),
        ]);
        this.dashboard = {
          system: system.result || system,
          process: process.result || process,
          network: network.result || network,
          service: service.result || service,
        };
      } catch (error) {
        this.dashboard = { error: this.errText(error) };
      } finally {
        this.dashboardLoading = false;
      }
    },
    async runTool(tool, argumentsObject) {
      return this.api(`/api/tools/${tool}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ arguments: argumentsObject }),
      });
    },
    async loadTools() {
      this.toolsLoading = true;
      try {
        const data = await this.api("/api/mcp/tools");
        this.tools = data.tools || [];
      } catch (error) {
        this.tools = [{ name: "error", title: "加载失败", description: this.errText(error), input_schema: {} }];
      } finally {
        this.toolsLoading = false;
      }
    },
    async loadAudit() {
      this.auditLoading = true;
      try {
        const params = new URLSearchParams({ limit: String(this.auditLimit || 80) });
        if (this.auditTraceId) params.set("trace_id", this.auditTraceId);
        const data = await this.api(`/api/audit/recent?${params.toString()}`);
        this.auditRecords = data.records || [];
      } catch (error) {
        this.auditRecords = [
          {
            timestamp: new Date().toISOString(),
            trace_id: "-",
            stage: "error",
            status: "failed",
            data: { error: this.errText(error) },
          },
        ];
      } finally {
        this.auditLoading = false;
      }
    },
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
  },
}).mount("#app");
