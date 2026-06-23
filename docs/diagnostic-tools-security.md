# 诊断工具安全加固

记录对只读诊断工具（`disk.large_files`、`disk.top_dirs`、`package.repo`）的四项安全加固。
背景：这三个工具被放进 `LOW_RISK_TOOLS`，低权限 viewer 无需令牌即可调用，但它们原本
接受任意 `path`/`repo_dir` 并允许较深递归，存在信息泄露与 DoS 风险。

## P1：只读扫描路径白名单 + 遍历预算

- **路径白名单（后端权威）**：`backend/security/rules.py` 新增
  `READ_SCAN_TOOLS = {disk.large_files, disk.top_dirs, package.repo}` 与
  `SAFE_SCAN_DIRS = (/var/log, /tmp, /var/tmp, /opt/software-cup-ops, /etc/yum.repos.d)`。
- **动态评级**：`SecurityGuard._scan_path_risk` 校验工具目标路径——
  - 路径在白名单内 → 维持 `low`，viewer 可直接调用；
  - 路径在白名单外（或缺失）→ 升级为 `medium`，需 `operator`/`admin` 角色 + 二次确认；
  - `is_safe_scan_path` 同时拒绝 `..` 路径穿越。
  - 评级在 `guard` 层强制，工具自身不做路径决策，符合“风险策略以后端为准”。
- **遍历预算**：`large_file_tool` 与 `disk_top_dirs_tool` 设 `_MAX_SCAN_ENTRIES`（默认 20000）；
  超过即停止遍历并在结果中置 `budget_exceeded: true`，避免单次请求长时间遍历整机文件系统。

## P2：package.repo 凭据脱敏

- 默认 `repo_dir = /etc/yum.repos.d`（在白名单内，viewer 可读）；自定义到白名单外的目录
  按 P1 升级为 `medium`。
- `package_repo_tool._mask_url` 对 `baseurl`/`mirrorlist`/`metalink` 中内嵌的
  `://user:password@` 凭据脱敏为 `://***:***@`，避免把仓库账号密码返回给调用方。

## P3：planner `du` 命令词边界匹配

- 原先 `du` 作为普通子串放进大文件/目录扫描关键词，`module`、`schedule` 等英文词会误触发
  重扫描。改为 `Planner._has_word` 的 `\bdu\b` 词边界匹配，仅在 `du` 作为独立命令词时触发；
  中文磁盘语义词（大文件、磁盘满、谁占等）保持不变。

## P4：repolist 走命令模板

- `package.repo` 原先直接 `subprocess.run([manager, "repolist", ...])`，违反“所有系统命令必须
  通过命令模板执行”不变量。改为 `command_runner` 的白名单模板
  `package.repolist.dnf` / `package.repolist.yum`，经 `run_optional_template` 执行，
  自动套用最小权限子进程选项。

## 测试

`tests/test_diagnostic_hardening.py` 覆盖：白名单路径 low/viewer 放行、白名单外 medium 拦截
viewer、operator+确认放行、缺失路径默认 medium、`package.repo` 默认目录 low / 自定义目录
medium、路径穿越拒绝、两个扫描工具的预算触发、URL 凭据脱敏、`du` 词边界不误触发/正确触发。
