# Module: Same-scan current-day market data eligibility gate

Historical cache may supply the lookback window, but it must never make a stock eligible for analysis by itself. Every analyzed stock must complete a market-data request during the current scan, and that response must contain a bar dated today in Asia/Shanghai. Network failure, stale provider data, holidays, or a missing current-day bar make the stock ineligible and it must not enter the analysis pool.
