# Test Plan

Use deterministic providers returning current-day, previous-day, mixed, and failed responses. Seed valid cached histories to prove cache alone cannot pass. Assert stale stocks never reach `_analyze_one_full`, realtime skip counters are monotonic, and eligible current-day stocks retain the pipeline behavior. Run the complete suite after updating tests whose old cache-only acceptance conflicts with this new hard requirement.
