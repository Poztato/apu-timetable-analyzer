# APU Timetable Analyzer

APU Timetable Analyzer lets students enter an intake code, choose when they prefer physical classes, and see how their timetable compares with the rest of the university. It provides two views: a guided timetable checker for a personal result and a dashboard for filtering, ranking, inspecting, and comparing timetable variants.

The analyzer now uses one explainable convenience model instead of asking users to order five frustration criteria. Every timetable receives an absolute weekly score, while its rank shows where that score sits inside the comparison group selected by the user.

## What the website can do

- Find an intake code and select its week, group, and elective route.
- Choose a balanced midday, morning, or afternoon physical-class preference.
- Optionally place extra emphasis on avoiding short campus trips or heavy teaching days.
- View an absolute weekly score with a component-by-component explanation.
- Choose a fair comparison group and see the timetable's rank within it.
- Filter the full dashboard by programme, academic level, course, specialism, group, study mode, and delivery mode.
- Inspect a vertical weekly timetable with physical classes, online classes, and campus waiting periods.
- Compare two timetable variants using the same scoring settings.
- Check when the source data was collected and which dates it covers.

## How to use it

### Guided timetable checker

1. Enter an intake code and choose the intended intake.
2. Select an available week, group, and elective route.
3. Choose when physical classes should ideally occur. Add either personal emphasis checkbox if it reflects your priorities.
4. Choose which scheduled timetables should form the comparison group.
5. Review the score, rank, timetable, and score breakdown.

### Dashboard

1. Select a week and apply filters that create a meaningful comparison group.
2. Choose the preferred physical-class time and any optional emphasis.
3. Browse the ranked list, inspect one timetable, or compare two variants.

Filters affect rank because they change the comparison group. They do not change a timetable's absolute score. Search narrows the visible results only, so searching for an intake cannot give it an artificial rank.

## How the score works

Lower scores are better. Each calendar day is scored from its actual timetable measurements, then all seven days are averaged to produce the weekly score. Including all seven days means an empty day genuinely improves the week instead of disappearing from the calculation.

There are three daily score ranges:

| Day type | Daily score | Meaning |
| --- | ---: | --- |
| Empty | 0 | No scheduled teaching. |
| Online only | 5 to 19 | Better than making a campus trip, but still recognises the time commitment. |
| Physical campus day | 20 to 100 | Starts with the cost of travelling to campus, then adds timetable inconvenience. |

This ordering is deliberate. A free day is always rewarded more than an online-only day, and an online-only day always scores better than a day that requires travelling to campus.

### Physical-day components and caps

| Component | Daily cap | Measurement and smooth range |
| --- | ---: | --- |
| Campus trip | 20 | A fixed cost whenever at least one physical class requires attendance. |
| Time placement | 30 | Duration-weighted distance of physical teaching from the selected preferred band. The cost grows smoothly from 0 to 240 minutes of average deviation. |
| Campus span | 20 | Time from the first physical class starting to the last physical class ending. The cost grows smoothly from 180 to 540 minutes. |
| Campus waiting | 10 | Unoccupied time inside the campus-bound window. The cost grows smoothly from 0 to 180 minutes. |
| Short campus day | 10 | Physical teaching of 60 minutes or less receives the full cost, which falls smoothly to zero at 120 minutes. |
| Heavy teaching day | 10 | Total teaching has no cost up to 240 minutes, then grows smoothly to the full cost at 360 minutes. |

The smooth ranges use a capped smoothstep curve. A measurement below the good end receives no additional cost, a measurement above the bad end receives the full component cost, and values between them change gradually. Caps prevent one unusual measurement from overwhelming the rest of the day.

Time placement is measured across every occupied minute of physical teaching. Minutes inside the preferred band add no distance, while minutes outside it add their distance from the closest edge of that band. Early and late classes therefore cannot cancel one another out. A schedule with classes split across both edges of the day receives placement, span, and waiting costs, while a compact middle-of-day schedule avoids most of them.

The available preferred bands are:

- Balanced midday: 11:00 to 13:30
- Morning: 09:00 to 11:30
- Afternoon: 13:30 to 16:00

### Optional personal emphasis

The two emphasis controls are independent checkboxes, not alternatives to the time preference:

- Avoid short campus trips adds 5 raw weight points to the short-day curve.
- Avoid heavy teaching days adds 5 raw weight points to the long-day curve.

After an emphasis is added, the variable physical-day components are rescaled back into their shared 80-point allowance. The fixed 20-point campus-trip cost and the 100-point daily maximum do not change. Emphasis therefore changes what matters within the model without allowing the total score to exceed its stated range.

For online-only days, the model uses a fixed 5-point commitment plus up to 7 points for span and 7 points for teaching load. Heavy-day emphasis strengthens the online teaching-load share while preserving the 19-point online-only maximum.

## Timetable measurements

- Physical teaching time counts occupied physical-class minutes, with overlaps merged.
- Total teaching time counts all occupied teaching minutes, with overlaps merged.
- Campus span runs from the first physical class start to the last physical class end.
- Campus waiting counts unoccupied time inside that campus span.
- An online class before the first physical class or after the last physical class does not extend the campus span.
- An online class inside the campus span is occupied time and reduces the waiting calculation.
- Unknown delivery modes are treated as physical as a conservative fallback.
- Classes with multiple lecturer records are kept as one teaching slot.

## Rank and ties

The weekly score is absolute for a chosen preference setup. Adding or removing other timetables from the comparison group does not change that score. Rank is relative to the selected group:

- Best is the lowest score in the current group.
- Worst is the highest score in the current group.
- Most average is the score closest to the group's median.
- Equal scores share a rank and are shown as a tied position range.

One result represents one intake, timetable week, group, and elective configuration. Timetables that are not scheduled in the chosen week are unavailable, not perfect zero-score timetables.

## Scoring configuration and data pipeline

[`config/scoring.json`](config/scoring.json) is the single scoring contract used by the Python data pipeline and exported to the browser. It defines the preferred bands, component caps, smooth ranges, online-only range, and optional emphasis amounts. The previous separate ranking configuration is no longer used.

After timetable variants have been built, regenerate scoring data with:

```powershell
python scripts/calculate_daily_metrics.py
python scripts/calculate_weekly_metrics.py
python scripts/rank_timetables.py
python scripts/build_dashboard_data.py
```

Pass `--snapshot-id <id>` to the first three commands to process one indexed snapshot, or `--all` to process every indexed snapshot. Without either option, each command uses the latest snapshot. The dashboard export validates that the generated stages use the same scoring profile before writing browser data.

## Local development

```powershell
Set-Location web
npm install
npm run dev
```

Useful checks:

```powershell
python -m unittest discover -s tests -v
Set-Location web
npm test
npm run build
```

## License

This project is provided under the [MIT License](LICENSE).
