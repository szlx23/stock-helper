# Project Spec

## Goal

Provide a local-first mobile Web MVP that helps a user scan A-share stocks for the "缩量阴线回踩10日线" strategy, review candidates, and preserve scan parameters/results for later review.

## Target User

A single individual using a phone browser against an Ubuntu cloud server.

## Core Flows

1. Open `/`, review latest scan summary.
2. Adjust hard filter parameters and score weights.
3. Submit `POST /run-scan` with the operation password.
4. Backend validates `StrategyConfig`, scans market data with provider fallback, and stores the scan task and candidates.
5. Open `/candidates`, review mobile cards sorted by score descending.

## Hard Constraints

- Python, FastAPI, SQLite, Jinja2.
- No React, Vue, or broker integration.
- `max_price` is a hard filter, never a score item.
- Score weights must come from the submitted form.
- Scan task must save the parameter snapshot.
- Invalid mutation requests must not start a scan or delete data.

## Non-goals

- Automatic trading.
- Native Android app.
- Multi-user authentication and authorization.
- Full buy/sell records and review statistics in MVP.

## Evaluation

Run `pytest` plus compilation and shell syntax checks. Tests cover strategy filtering, custom scoring weights, scanner behavior, provider fallback, database persistence, web validation, task lifecycle, and home page rendering.
