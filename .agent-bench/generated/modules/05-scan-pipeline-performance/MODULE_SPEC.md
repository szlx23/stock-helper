# Module: Concurrent fetch and streaming analysis pipeline

Reduce daily and first-run scan latency with bounded concurrent market-data fetching. Submit each usable history to the analysis pool immediately after its fetch completes so candidates can be reported before the remaining universe finishes downloading. Preserve deterministic final results, cancellation, provider fallback, and SQLite consistency.
