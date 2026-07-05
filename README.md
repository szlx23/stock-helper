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

扫描使用“并行拉取 + 即时分析”流水线：默认同时拉取 4 只股票，每只股票数据就绪后立即进入分析线程，不等待全市场下载完毕。`fetch_workers` 可在页面调整为 1–16；遇到数据源限流时建议降到 2–4。

历史 K 线缓存只用于补足均线窗口，不作为行情资格依据。每轮扫描开始时会通过 AKShare 更新并缓存沪深交易日历，再批量拉取东方财富全 A 股实时快照；快照不可用时会用逐股历史源校验最新交易日。最新交易日不会在零点切换：工作日 09:30 前仍沿用上一交易日，周末及法定休市日按交易日历回退。交易日历更新失败时优先使用本地缓存，缓存也不存在才降级为普通工作日规则。未取得目标交易日有效行情的股票不会进入分析线程。股票列表可缓存 12 小时；需要强制更新列表时，将 `stock_list_ttl_minutes` 设为 `0`。

独立的前复权日 K 缓存可通过 `from stock_helper.data import get_daily_kline` 使用。调用 `get_daily_kline("600000", 80)` 会按东方财富、腾讯、新浪、网易、搜狐、本地缓存的顺序获取数据，其中前三个已通过 AKShare 完整接入，网易和搜狐保留独立适配器等待稳定接口。所有适配器统一输出 DataFrame；在线数据写入 SQLite `daily_kline` 表，首次预取至少 260 个自然日，后续从缓存倒数第 3 个交易日开始覆盖更新，最终按日期升序返回最近 80 个交易日。也可直接调用 `fetch_daily_kline(code, start_date, end_date, adjust="qfq")` 获取统一 DataFrame。

首页的“股票数据查看”面板与扫描任务相互独立，并共享本地 K 线缓存。市场数据接口为 `GET /api/market/stocks`、`GET /api/market/daily-kline?code=600000&lookback_days=80` 和 `POST /api/market/daily-kline/refresh`。GET 详情只读取本地已有数据，不要求满 80 日且不会触发网络；只有手动刷新才执行增量拉取并覆盖最近交易日。

选股策略采用“尾盘阴线回踩均线反弹”硬门槛：当前必须为非爆量缩量阴线，收盘贴近 MA10 且不低于 MA20，MA10 向上、MA20 至少走平，短中期均线不得形成空头压制；最近 20 个交易日必须出现相对前 5 日均量至少 1.8 倍、涨幅至少 5% 的放量阳线。MA10 与 MA20/MA30 距离过大、近 40 日涨幅过高的高位退潮结构会直接淘汰。

扫描任务会持久化开始时间、结束时间和总耗时，总耗时覆盖股票列表读取、行情拉取、指标计算和策略分析。首页及状态接口始终恢复最近一次扫描 ID 对应的候选结果，不再因进入下一个交易日而隐藏历史结果。

扫描缓存复用按交易阶段控制：09:30–11:30、11:30–13:00 和 13:00–15:05 均要求刷新，保证 10 点与 11 点重复扫描会取得不同的盘中行情；开盘前、周末、节假日，以及当日 15:05 后已有收盘确认缓存时，可直接分析目标交易日缓存。批量快照也按目标交易日而非自然日校验，因此周一开盘前可正确接受上周五行情。

第一版只提供 Web 端手动触发筛选和候选展示；买卖记录、次日卖出计划和复盘统计预留给后续迭代。
