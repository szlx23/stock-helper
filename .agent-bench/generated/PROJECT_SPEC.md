# Project Spec

## Goal

Provide a local-first mobile Web MVP that helps a user scan A-share stocks for the "缩量阴线回踩10日线" strategy, review candidates, and preserve scan parameters/results for later review.

## Target User

A single individual using a phone browser against an Ubuntu cloud server.

## Core Flows

1. Open `/`, review latest scan summary.
2. Adjust hard filter parameters and score weights.
3. Submit `POST /run-scan`.
4. Backend builds `StrategyConfig`, scans BaoStock data, stores scan task and candidates.
5. Open `/candidates`, review mobile cards sorted by score descending.

## Hard Constraints

- Python, FastAPI, SQLite, Jinja2.
- No React, Vue, or broker integration.
- `max_price` is a hard filter, never a score item.
- Score weights must come from the submitted form.
- Scan task must save the parameter snapshot.

## Non-goals

- Automatic trading.
- Native Android app.
- Authentication.
- Full buy/sell records and review statistics in MVP.

## Evaluation

Run `pytest`. The tests cover strategy filtering, custom scoring weights, scanner behavior, database persistence, and home page form rendering.
