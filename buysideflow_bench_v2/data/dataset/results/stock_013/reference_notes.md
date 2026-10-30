# stock_013 Reference

The revised question screens historically eligible main-board and ChiNext
companies at 2025-05-06 using positive parent profit and operating cash flow
in each of 2022, 2023 and 2024. It no longer uses dividends or market value.

Annual accounting rows are bounded by publication before 2025-05-07, then
selected per company and year using the existing background MARK priority.
Missing or nonpositive annual values do not fall back to older rows or periods.
Growth spans two annual intervals: `(NP2024 / NP2022) ** 0.5 - 1`.
The strict 10% filter and ranking use unrounded values; code breaks ties.
Only the first 20 are output, with money in 100 million yuan and growth in percent.

The 2026-09-30 extraction covered 4,239 historically eligible companies;
4,235 had complete financial panels and 795 passed the financial screen.
Four missing 2024 periods remain excluded, not replaced or zero-filled.
The earlier dividend-history blocker concerns the superseded task only.

Evidence and provenance:
`outputs/evaluation/stock011_stock013_revised_20260930/`.
This is a recomputed reference, not a model rerun or a certification of a
physically immutable database snapshot. Historic model grades are unchanged.
