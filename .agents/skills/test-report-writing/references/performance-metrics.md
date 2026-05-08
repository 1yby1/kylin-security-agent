# Performance Metrics

Use these metric definitions for the performance report.

## Metrics

| Metric | Meaning | Unit |
| --- | --- | --- |
| Average response time | Mean request latency | ms |
| P95 response time | 95th percentile latency | ms |
| Throughput | Completed requests per second | req/s |
| Success rate | Successful responses divided by total requests | % |
| Error rate | Failed responses divided by total requests | % |
| CPU usage | Backend process or host CPU usage during test | % |
| Memory usage | Backend process memory usage during test | MB |

## Suggested API Targets

| API | Test Goal |
| --- | --- |
| GET `/health` | Baseline FastAPI latency |
| GET `/api/mcp/tools` | Tool manifest serialization latency |
| POST `/api/tools/system` | Tool command execution latency |
| POST `/api/agent/execute` | Full Agent chain latency |
| GET `/api/audit/recent` | Audit query latency |

## Notes

- Run separate tests with LLM disabled and enabled.
- LLM-enabled Agent requests depend on external model latency and network quality.
- Do not compare local Windows results directly with Kylin/LoongArch production results.
- Record exact concurrency, request count, warm-up duration, and test command.

## Example Table

| Scenario | Concurrency | Requests | Avg ms | P95 ms | RPS | Success Rate | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Health check | 10 | 1000 | 待测试 | 待测试 | 待测试 | 待测试 | Local backend |
| MCP manifest | 10 | 500 | 待测试 | 待测试 | 待测试 | 待测试 | No LLM |
| Agent execute | 5 | 100 | 待测试 | 待测试 | 待测试 | 待测试 | LLM disabled/enabled separately |

