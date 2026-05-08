---
name: test-report-writing
description: Use when Codex needs to create, update, or review functional test reports or performance test reports for this safety operations Agent project. Covers test scope, test cases, expected results, actual results, API verification, frontend workflows, security blocking tests, audit trace checks, performance metrics, load test summaries, and report-ready wording for competition deliverables.
---

# Test Report Writing

Use this skill to produce competition-ready functional and performance test reports for this project.

## Report Types

Create one or both:

- Functional test report: verify user workflows and feature correctness.
- Performance test report: verify response time, concurrency, throughput, resource usage, and stability.

## Functional Test Report Workflow

1. Read project requirements and implemented modules.
2. List test environment: OS, Python, browser, model provider, database, deployment mode.
3. Define test scope by feature:
   - intelligent ops conversation
   - system status dashboard
   - MCP tool discovery
   - audit log query
   - LLM JSON planning
   - security intent validation
   - least privilege execution
   - controlled operation tools
4. Write test cases with this table shape:
   - Case ID
   - Feature
   - Preconditions
   - Steps
   - Expected Result
   - Actual Result
   - Status
5. Include positive, negative, and security-blocking cases.
6. Include trace ID or audit evidence for important cases.
7. Summarize pass rate, defects, unresolved risks, and conclusion.

## Performance Test Report Workflow

1. Define test goal and metric names before writing conclusions.
2. Record environment and test tool.
3. Measure at least:
   - average response time
   - P95 response time
   - requests per second
   - success rate
   - CPU and memory usage
   - concurrent users or request count
4. Separate API categories:
   - `/health`
   - `/api/mcp/tools`
   - `/api/agent/execute`
   - `/api/tools/system`
   - `/api/audit/recent`
5. Note whether LLM is enabled, because LLM latency dominates Agent requests.
6. Summarize bottlenecks and optimization suggestions.

## Suggested Functional Cases

Use `references/functional-test-cases.md` when drafting the functional report.

## Suggested Performance Metrics

Use `references/performance-metrics.md` when drafting the performance report.

## Output Guidance

When the user asks for a report:

- Prefer `.md` unless the user asks for `.docx`.
- Use clear Chinese section headings.
- Keep tables complete and consistent.
- Do not invent measured performance numbers. If measurements were not run, mark values as `待测试` and provide commands or a test plan.
- If test commands were run, cite the exact command and summarize key output.

