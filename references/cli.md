# 命令与恢复

以下命令从仓库根目录执行，假设已按 README 安装依赖。输出放仓库外；Windows 使用对应的 `.venv/Scripts/python.exe`。

## 准备计划

只生成本地计划，不访问招聘网站：

```bash
.venv/bin/python job_search.py plan --input examples/custom-plan.json --out ~/job-search-runs/my-plan.json
.venv/bin/python job_search.py plan --keyword "采购专员" --city "苏州" --count 10 --out ~/job-search-runs/ten-each.json
.venv/bin/python job_search.py plan --keyword "采购专员" --city "苏州" --count 5 --platform-count boss=10 --out ~/job-search-runs/mixed-counts.json
```

计划格式见 [自定义示例](../examples/custom-plan.json) 和 [简历示例](../examples/resume-plan.json)。`jobs_per_platform` 控制统一数量，`platform_job_counts` 覆盖特定平台；一个平台的富余不能抵扣另一个平台的缺口。

新计划自动生成预算，显式上限及旧计划保持不变。需要扩容时只生成待确认计划，不自动搜索：

```bash
.venv/bin/python job_search.py plan --input ~/job-search-runs/my-plan.json --auto-budget --out ~/job-search-runs/larger-plan.json
.venv/bin/python job_search.py plan --input ~/job-search-runs/my-plan.json --pages 12 --details 60 --out ~/job-search-runs/custom-budget.json
```

输出变化给用户确认，后续仍累加旧用量。`--count` 不覆盖输入计划中已有的显式预算；`--auto-budget` 才重新估算。详见 [浏览预算](budget.md)。

## 确认后搜索

先展示完整计划并得到用户明确确认，再复制输出的 `confirmation_id`：

```bash
.venv/bin/python job_search.py scan --plan ~/job-search-runs/my-plan.json --confirm 已确认计划的ID --out ~/job-search-runs/first-scan
```

`--confirm` 只验证计划未变化，不代替用户同意。方向、条件或范围改变后必须重新确认。

只检查访问与登录状态：

```bash
.venv/bin/python job_search.py preflight --plan ~/job-search-runs/my-plan.json --out ~/job-search-runs/preflight
```

`scan` 完成后，Agent 必须读取 `next-actions.json`，继续处理 `needs_agent` 平台，向用户明确提示真正的登录 / 验证阻塞。详见 [SKILL.md](../SKILL.md) 与 [平台手册](platforms.md)。

## 单平台恢复

登录完成或平台冷却后，只恢复受影响平台，不重扫已成功的平台：

```bash
.venv/bin/python job_search.py scan --plan ~/job-search-runs/my-plan.json --confirm 已确认计划的ID --only boss --out ~/job-search-runs/resume-boss
```

使用新输出目录保留旧证据。不变的计划无需重复询问用户。`scan --only` 不会自动读取旧目录；如果已有查询记录，由 Agent 先合并旧岗位和累计用量，再从现有页面续接翻页/详情，不能通过重跑首页获得新预算。遇模型 429 时暂停整轮调用，恢复后先读已有计划、状态和原 request receipt，不从头重复搜索。

## 合并报告与检查进度

```bash
.venv/bin/python job_search.py report --plan ~/job-search-runs/my-plan.json \
  --input ~/job-search-runs/first-scan/raw-jobs.json ~/job-search-runs/guided-jobs.json \
  --statuses ~/job-search-runs/platform-status.json --out ~/job-search-runs/report

.venv/bin/python job_search.py worklist --plan ~/job-search-runs/my-plan.json \
  --input ~/job-search-runs/first-scan/raw-jobs.json ~/job-search-runs/guided-jobs.json \
  --statuses ~/job-search-runs/platform-status.json --out ~/job-search-runs/progress
```

输入格式见 [数据约定](data-contract.md)。未提供状态文件时，导入数据不代表已完成平台搜索。

- `can_deliver_final=false`：还有必须处理的事项，不能宣称完成。
- `can_deliver_final=true` 且 `complete=false`：可交付明确写出不足原因的部分结果。
- `complete=true`：所有计划平台都达到各自数量目标。

报告输出 `jobs.html`、`jobs.xlsx`、`jobs.json`、`jobs.jsonl`、`summary.json`、`plan.json` 和 `next-actions.json/md`。默认 CLI 回执保持精简；确需完整元数据时使用 `--verbose`。

退出码：`0` 表示命令成功且无待处理事项（离线 demo / plan 等除外）；`2` 表示参数、依赖或传输错误；`3` 表示登录等待、界面待办或平台覆盖缺口。退出码 3 也会保留已取得的结果。

## 地区与渠道路线（v0.3.0）

`plan` 自动在计划旁写出 `*.routes.json`。新计划默认六渠道（增加本地宝），已有显式平台清单不变。另可仅生成路线：

```bash
python job_search.py plan --input examples/local-plan.json --out /tmp/job-search-local/plan.json
python job_search.py routes --plan /tmp/job-search-local/plan.json --out /tmp/job-search-local/routes.json
```

这两条命令不联网、不启动浏览器。每个关键词×城市保留独立路线；城市入口、站内发现查询和待核对事项均在 JSON 中。`scan` 仍需用户确认，新增渠道由 Agent 根据实际页面完成 guided 流程。
