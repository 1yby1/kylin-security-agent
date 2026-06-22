# 部署说明

## 本地开发

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

打开：

```text
http://localhost:8000
```

本地需要测试前端提权操作时，可先在启动后端前写入令牌环境变量：

```bash
export AGENT_DEFAULT_ROLE=viewer
export AGENT_VIEWER_TOKEN=dev-viewer-token
export AGENT_OPERATOR_TOKEN=dev-operator-token
export AGENT_ADMIN_TOKEN=dev-admin-token
```

页面上的“访问令牌”输入框填写其中一个令牌后，请求会通过
`Authorization: Bearer <token>` 发送到后端。

## Linux 服务

1. 将项目复制到 `/opt/software-cup-ops`。
2. 执行 `sudo bash deploy/install.sh`。
3. 首次 root 安装时，脚本会生成 `/etc/software-cup-ops/software-cup-ops.env`。
   该文件包含 `AGENT_VIEWER_TOKEN`、`AGENT_OPERATOR_TOKEN` 和
   `AGENT_ADMIN_TOKEN`，前端页面需要使用时可复制对应令牌到输入框。
4. 将 `deploy/systemd.service` 复制到
   `/etc/systemd/system/software-cup-ops.service`。
5. 执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now software-cup-ops
```

`deploy/systemd.service` 会通过 `EnvironmentFile` 加载
`/etc/software-cup-ops/software-cup-ops.env`。如果需要轮换令牌，修改该文件后
执行：

```bash
sudo systemctl restart software-cup-ops
```

## 麒麟高级服务器 V11 + LoongArch 注意事项

- 优先使用操作系统提供的 Python 3 包，或使用 LoongArch 兼容的 Python 构建。
- 如果部分 Python 包没有可用二进制 wheel，需要从源码安装。
- 后端命令白名单依赖 `ps`、`ss`、`systemctl` 等 Linux 命令。
- 第一版建议保持大模型 API 调用模式，避免引入本地模型在 LoongArch 上的部署压力。

## 最小权限

安装脚本会创建专用系统用户：

```bash
software-cup-agent:software-cup-agent
```

systemd 服务会以该用户运行，并且只允许写入：

- `/var/lib/software-cup-ops`
- `/var/log/software-cup-ops`
- `/opt/software-cup-ops/tmp`

服务启用 `NoNewPrivileges=true`、空能力集合和 systemd 文件系统保护。生产环境
不要用 root 直接运行后端服务。

## 审计持久化

审计采用 SQLite 权威存储（hash 链 + `audit_meta`，篡改/尾删可发现）。相关环境变量：

- `AGENT_AUDIT_DB_PATH`：审计数据库路径。`deploy/systemd.service` 已设为
  `/var/lib/software-cup-ops/audit.db`，落在 `ReadWritePaths` 内。由于
  `ProtectSystem=strict` 会把 `/opt/software-cup-ops` 设为只读，**审计 DB 必须放在
  可写目录**，不能用默认的 `backend/audit/logs/audit.db`。
- SQLite 启用 WAL 模式，会在同目录生成 `audit.db-wal`、`audit.db-shm` 旁文件，
  均需落在 `/var/lib/software-cup-ops` 等可写路径内。
- `AGENT_AUDIT_FAIL_CLOSED`：默认 `false`（best-effort，审计写入失败不中断请求）。
  生产可设 `true`，使前置审计写入失败时请求直接报错（未审计不执行）。

查询与校验接口：`GET /api/audit/recent`（支持 `trace_id`/`user_id`/`status` 过滤）、
`GET /api/audit/verify`（链完整性校验）、`GET /api/audit/export`（导出 NDJSON）。
