#!/usr/bin/env bash
set -euo pipefail
PYTHON="${PYTHON:-.venv/bin/python}"
"${PYTHON}" -m compileall -q stock_helper tests
"${PYTHON}" -m pytest -q
node --check stock_helper/static/app.js
git diff --check
bash -n scripts/*.sh .agent-bench/scripts/*.sh
