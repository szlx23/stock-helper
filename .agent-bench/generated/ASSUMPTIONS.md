# Assumptions

## Assumption: Use stdlib sqlite3 instead of an ORM

- Decision: Use Python's `sqlite3` module with small repository functions.
- Risk level: medium.
- Reason: The requested app is an MVP and the code should stay easy to understand.
- Impact if wrong: A future migration to SQLAlchemy may be useful as schema complexity grows.
- How to change later: Replace `stock_helper/db.py` behind the same function names.

## Assumption: BaoStock failures are shown as latest scan failures

- Decision: A failed BaoStock login or data fetch creates a failed scan record and redirects to the home page with the error.
- Risk level: medium.
- Reason: The default data path depends on network and third-party availability.
- Impact if wrong: The user may prefer partial results or retry behavior.
- How to change later: Add per-stock error collection and retry settings to `StockScanner`.

## Assumption: Recent 40-day rise is a hard filter and also supported as a scoring risk

- Decision: Treat `max_recent_rise` as a hard filter because it is listed in the hard filter parameter group, while keeping the scoring penalty function for future relaxed modes.
- Risk level: medium.
- Reason: The prompt explicitly classifies it as a hard filter parameter.
- Impact if wrong: Some high-rise stocks will be excluded rather than included with a lower score.
- How to change later: Add a boolean config to switch between hard filtering and penalty-only behavior.
