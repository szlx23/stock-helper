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
pytest
```

## 部署

Ubuntu 服务器部署见 [DEPLOY.md](DEPLOY.md)。推荐把代码推到 GitHub，服务器通过 `git clone` / `git pull` 更新，再运行：

```bash
sudo bash scripts/deploy_ubuntu.sh
```

## 数据说明

默认数据源是 BaoStock。运行筛选时服务器会登录 BaoStock，拉取股票列表和历史日线数据。数据库默认写入 `data/stock_helper.db`，可用环境变量 `STOCK_HELPER_DB` 修改。

第一版只提供 Web 端手动触发筛选和候选展示；买卖记录、次日卖出计划和复盘统计预留给后续迭代。
