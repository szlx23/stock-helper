# Module Evaluation Report

## Module

05-scan-pipeline-performance

## Summary

* Status: pass
* P0 pass rate: 6/6 (100%)
* Test suite: 48 passed

## Passed Checks

Bounded four-way fetching, immediate analysis submission, early hit delivery, deterministic result sorting, monotonic pipeline counters, bounded cancellation, stock-list caching, stale-list fallback, provider lifecycle isolation, integer worker validation, and all project quality gates. The former short-lived K-line reuse behavior was subsequently superseded by module 06's hard realtime eligibility requirement.

## Performance Evidence

With eight stocks and a deterministic 50ms fetch delay per stock, the former staged model could not emit its first hit before the 0.400s sequential fetch floor. The pipeline emitted its first hit at 0.074s and completed at 0.171s, with observed fetch concurrency of four. This synthetic benchmark isolates orchestration improvement; live API speed still depends on provider latency and rate limits.

## Failed Checks

None.

## Repairs Attempted

Implemented a bounded fetch/analysis future scheduler, thread-local provider pool, database cache-state reads, K-line TTL, stock-list TTL and fallback, pipeline-aware UI progress, and prompt cancellation of queued work.

## Assumptions Used

Four fetch workers and 12-hour stock-list freshness are conservative defaults and remain user-configurable. K-line cache never bypasses the module 06 realtime gate.

## Acceptance or Test Changes

Added performance, event-order, cancellation, cache, fallback, and validation regression tests. Existing tests were retained.

## Next Decision

continue_to_next_module
