# APU Timetable Analyzer

APU Timetable Analyzer helps students compare how convenient or frustrating different APU timetables are. It turns timetable records into understandable measurements, then ranks intake and group schedules according to the problems that matter most to the user.

The project is intended to make timetable comparisons clearer than statements such as "my timetable is worse." It shows the measurements behind every result and allows different students to choose different priorities.

## What the dashboard can do

The dashboard currently allows users to:

- Select the timetable week to analyze.
- Filter by group, programme route, academic level, course, specialism, and delivery mode.
- Reorder five frustration criteria based on personal priorities.
- See the best, worst, and most average timetable in the selected comparison set.
- Search for an intake code without changing how its score was calculated.
- Sort and inspect the full ranking table.
- Open an intake-group timetable to see its daily classes, gaps, and warning flags.
- Compare two timetable variants side by side.
- Check when the data was collected and which dates it covers.

## How to use it

1. Choose a week. Rankings only compare timetables scheduled in that week.
2. Apply any filters needed to create a fair comparison set, such as the same course or academic level.
3. Move the frustration criteria up or down. The first criterion receives the most influence on the score.
4. Review the best, worst, and most average results, or browse the ranking table.
5. Search for your intake code to find its row. Search only narrows the visible table, so it does not give the searched intake an artificial rank.
6. Select a result to inspect its score breakdown and daily timetable. Use the comparison section to place two schedules side by side.

## What the measurements mean

| Measurement                  | Meaning                                                                                                                                                                           |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Gap burden**               | The total idle time between class blocks while the day is bounded by physical campus classes. Online classes before the first or after the last campus class do not create gaps. Online classes between campus classes remain occupied time. |
| **Late-only campus day**     | A day with exactly one campus class slot, where that slot starts at or after 3:00 PM.                                                                                             |
| **Early-only campus day**    | A day with exactly one campus class slot, where that slot starts at or before 9:30 AM.                                                                                            |
| **One-hour-only campus day** | A day that requires campus attendance but contains no more than 60 minutes of campus teaching.                                                                                    |
| **Overloaded day**           | A day with at least 360 minutes of teaching or at least four distinct class slots.                                                                                                |
| **Teaching time**            | The total occupied teaching time. Overlapping classes are counted once rather than double-counted.                                                                                |
| **Daily span**               | The time from the first class starting to the final class ending, including gaps.                                                                                                 |
| **Longest gap**              | The longest single campus-bound wait between class blocks.                                                                                                                        |
| **Active day**               | A day containing at least one scheduled class.                                                                                                                                    |

Back-to-back and overlapping classes are merged when occupied time and gaps are calculated. A day with fewer than two campus classes has no gap burden. Commute-related flags focus on campus attendance, so an online-only day is not marked as early-only, late-only, or one-hour-only. A day containing online classes and one campus class can still receive a commute-related flag for that campus trip.

## How the score works

The frustration score is relative, not an absolute grade for a timetable.

For each selected week and comparison set, the dashboard compares every timetable variant against its peers on the five frustration measurements. A worse raw value produces a higher frustration percentile. The selected priority order then applies descending weights of 5, 4, 3, 2, and 1.

- A lower overall score is better.
- A higher overall score is worse.
- **Best** means the lowest score in the current comparison set.
- **Worst** means the highest score in the current comparison set.
- **Most average** means the score closest to the comparison set's median score.
- Equal scores remain tied.

Structured filters change the peer group and therefore recalculate rankings. Intake search does not change the peer group. This distinction prevents a searched intake from being compared only with itself.

## Understanding intake groups

One result represents one intake, one timetable week, and one group. For example, G1 and G2 are treated as separate timetable variants because their scheduled classes can differ.

Classes marked as common to the whole intake are included in each applicable group. The analyzer also prevents a class with multiple lecturer records from being counted as multiple class slots.

## Important context

- Filters are important. Comparing a full-time undergraduate intake with a weekend or postgraduate intake may not answer a useful question.
- An intake missing from a week is not treated as having a perfect timetable. It is simply unavailable for that comparison.
- There is no single universally correct frustration order. The score is designed to make the user's priorities visible rather than hide them.

## License

This project is provided under the [MIT License](LICENSE).
