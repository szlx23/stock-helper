# Test Plan

Use fake providers with controlled delays and no external network. Verify multiple fetches overlap, analysis begins before the final fetch finishes, first hit arrives early, progress counters remain monotonic, cancellation does not wait for all queued work, and final candidate ordering remains deterministic. Run the complete regression suite and static gates.
