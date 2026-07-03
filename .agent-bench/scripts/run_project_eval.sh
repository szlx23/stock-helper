#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q stock_helper tests
pytest
