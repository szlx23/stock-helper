# Test Plan

Run the existing strategy, scanner, database, and template tests first. Add focused regression tests for each confirmed defect, including success, invalid-input, authorization, and persisted-state checks where relevant. Run `python -m compileall stock_helper tests` and `pytest` as hard gates.
