# A 股短线策略辅助系统

个人使用的 FastAPI + SQLite + Jinja2 手机端 Web/PWA MVP。系统只做选股辅助、候选展示和扫描记录，不连接券商、不自动交易、不下单。

## 启动

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
uvicorn stock_helper.app:app --host 0.0.0.0 --port 8501
```

浏览器打开 `http://服务器IP:8501`。

生产或局域网部署时请设置操作密码（未设置时为兼容旧部署仍使用 `001023`）：

```bash
export STOCK_HELPER_PASSWORD='替换为强密码'
```

## 运行测试

```bash
bash .agent-bench/scripts/run_project_eval.sh
```

该命令统一执行 Python 编译、完整测试、JavaScript 语法、Git diff 和 Shell 脚本检查；GitHub Actions 会在 Python 3.10 与 3.12 上运行同一套门禁。

## 部署

Ubuntu 服务器部署见 [DEPLOY.md](DEPLOY.md)。推荐把代码推到 GitHub，服务器通过 `git clone` / `git pull` 更新，再运行：

```bash
sudo bash scripts/deploy_ubuntu.sh
```

## 数据说明

默认优先使用 AKShare 的腾讯行情源，失败时切换到新浪源。数据库默认写入 `data/stock_helper.db`，可用环境变量 `STOCK_HELPER_DB` 修改。

扫描使用“并行拉取 + 即时分析”流水线：默认同时拉取 4 只股票，每只股票数据就绪后立即进入分析线程，不等待全市场下载完毕。`fetch_workers` 可在页面调整为 1–8；遇到数据源限流时建议降到 2。

历史 K 线缓存只用于补足均线窗口，不作为实时资格依据。每轮扫描、每只股票都必须重新请求行情源，并确认响应包含上海时区当天日线，否则不会进入分析线程。股票列表可缓存 12 小时；需要强制更新列表时，将 `stock_list_ttl_minutes` 设为 `0`。

第一版只提供 Web 端手动触发筛选和候选展示；买卖记录、次日卖出计划和复盘统计预留给后续迭代。
