#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8501}"
DB="${STOCK_HELPER_DB:-data/stock_helper.db}"

echo "=========================================="
echo "  stock-helper 启动中..."
echo "=========================================="
echo "  监听地址 : ${HOST}:${PORT}"
echo "  数据库   : ${DB}"
echo "  工作目录 : $(pwd)"
echo "=========================================="

export STOCK_HELPER_DB="${DB}"

echo "[1/3] 初始化数据库..."
.venv/bin/python -c "
from stock_helper import db
db.init_db()
print('  数据库初始化完成')
"

echo "[2/3] 检查数据源连通性..."
.venv/bin/python -c "
from stock_helper.data.multi_provider import make_multi_provider
import sys

logs = []
def log(msg):
    logs.append(msg)
try:
    p = make_multi_provider(log=log)
    for l in logs:
        print(f'  {l}')
    p.__enter__()
    p.__exit__(None, None, None)
except Exception as e:
    for l in logs:
        print(f'  {l}')
    print(f'  ✗ 所有数据源均不可用: {e}')
    sys.exit(1)
" || echo "  ⚠ 数据源不可用，扫描将失败"

echo "[3/3] 启动 Web 服务..."
echo "  访问 http://localhost:${PORT}"
echo "=========================================="

exec .venv/bin/uvicorn stock_helper.app:app --host "${HOST}" --port "${PORT}" --log-level info
