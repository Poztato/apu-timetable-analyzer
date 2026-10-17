# Dashboard UI fixture

`dashboard-ui.json` is a fixed subset of the real dashboard export at Git revision
`323fc2149975d4aefb3b7aa8441d34c6bf9c27db^`, originally collected on 2026-08-30.
It contains eight intakes and twelve variants for the week beginning 2026-08-24,
with their original daily metrics, timetable blocks, and scoring definition.

This is historical **test data**, not a replacement for the current export.
Original export rank fields and source-row statistics still describe the full
historical dataset. UI expectations use recalculated ranks for this subset.

The scenarios deliberately include:
- a tied pair of Digital Forensics intakes;
- single-configuration intakes with similar names for typo/keyboard navigation;
- multiple elective choices;
- Computer Science, Data Analytics, and Foundation filter options.

Only one week is retained, so both the current-week and fallback-week selection
resolve to the same week regardless of the real date. The small peer set avoids
assuming that a fuzzy match lands on the first page of a growing live dataset.

Do not regenerate this file on a timetable refresh. Change it only when a UI
scenario or the dashboard schema intentionally changes. App's exported-ranking
parity test and `filterAudit.test.ts` still validate `public/data/latest.json`.

Live audits are separate cases per week, filter dimension, comparison scope,
and scoring preference. Keep the default Vitest timeout: adding timetable weeks
must add cases, not grow one all-weeks test past a shared deadline. Do not replace
live integrity checks with this small UI fixture or require live feeds to contain
a particular tie, intake, or elective.

For an offline replay, set the test-only `APU_TEST_DASHBOARD` environment variable
to an absolute path to a schema-4 latest-snapshot export before running `npm test`.
This selects data for the live filter audit and Python/browser ranking parity
checks only; UI scenarios still use the fixed fixture. Compact historical exports
have a different `dataset_kind` and omit daily metrics and timetable blocks.
Replay historical snapshots by building complete latest-slot test copies from
their retained processed data with the normal dashboard builder. Never merely
change `dataset_kind`, weaken the production parser, or edit production exports.
