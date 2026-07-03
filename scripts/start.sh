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

echo "[1/2] 初始化数据库..."
.venv/bin/python -c "
from stock_helper import db
db.init_db()
print('  数据库初始化完成')
"

echo "[2/2] 启动 Web 服务..."
echo "  访问 http://localhost:${PORT}"
echo "=========================================="

exec .venv/bin/uvicorn stock_helper.app:app --host "${HOST}" --port "${PORT}" --log-level info
