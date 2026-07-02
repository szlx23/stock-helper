#!/usr/bin/env bash
echo "=== 测试数据源 ==="

echo "[1] 腾讯源"
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy .venv/bin/python -c "import akshare as ak;df=ak.stock_zh_a_hist_tx(symbol='sh600519',start_date='20260701',end_date='20260703');print('OK:',len(df))" 2>&1

echo "[2] 新浪源"
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy .venv/bin/python -c "import akshare as ak;df=ak.stock_zh_a_daily(symbol='sh600519',start_date='20260701',end_date='20260703',adjust='qfq');print('OK:',len(df))" 2>&1

echo "[3] 股票列表"
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy .venv/bin/python -c "import akshare as ak;df=ak.stock_info_a_code_name();print('OK:',len(df))" 2>&1

echo "=== 完成 ==="
