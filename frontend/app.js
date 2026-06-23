const { createApp } = Vue;

createApp({
  data() {
    return {
      page: "chat",
      navItems: [
        { key: "chat", label: "智能对话", icon: "chat" },
        { key: "dashboard", label: "系统看板", icon: "dashboard" },
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
    async api(path, options = {}) {
      const headers = { ...(options.headers || {}) };
      if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
      const response = await fetch(path, { ...options, headers });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
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
        this.chatResult = { error: String(error) };
      } finally {
        this.chatLoading = false;
      }
    },
    async loadRuntime() {
      try {
        this.runtime = await this.api("/api/security/runtime");
      } catch (error) {
        this.runtime = { error: String(error) };
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
        this.dashboard = { error: String(error) };
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
        this.tools = [{ name: "error", title: "加载失败", description: String(error), input_schema: {} }];
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
            data: { error: String(error) },
          },
        ];
      } finally {
        this.auditLoading = false;
      }
    },
  },
}).mount("#app");
