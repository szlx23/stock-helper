#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${STOCK_HELPER_DB:-data/stock_helper.db}"

if [ ! -f "$DB" ]; then
  echo "数据库文件不存在: $DB"
  exit 0
fi

echo "清空数据库: $DB"
.venv/bin/python -c "
from stock_helper import db
db.init_db()
import sqlite3
c = sqlite3.connect('$DB')
tables = ['stock_daily_bars', 'candidates', 'scan_tasks', 'stock_info_cache']
for t in tables:
    c.execute(f'DELETE FROM {t}')
c.commit()
for t in tables:
    r = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()
    print(f'  {t}: {r[0]} 条')
print('清空完成')
"
