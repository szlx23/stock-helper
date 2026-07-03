# Project Brief

Build a personal A-share short-term strategy helper.

The system must not connect to a broker, place orders, or automate trading. MVP scope is a mobile-friendly Web/PWA style FastAPI application using Python, SQLite, Jinja2 templates, simple CSS, and BaoStock as the first market data provider.

Core MVP:
- Run a manual stock scan from the home page.
- Let the user customize hard filter parameters and every score weight.
- Use `StrategyConfig` to carry scan parameters.
- Pull daily A-share history through BaoStock.
- Calculate moving averages and strategy metrics.
- Apply hard filters first, then scoring.
- Save scan task records with parameter snapshots.
- Save candidates to SQLite.
- Show latest summary and mobile card-based candidate list.

Maintenance request:
- Review the existing implementation for functional, reliability, security, and maintainability problems.
- Implement safe improvements and verify them without connecting to a brokerage or placing trades.
