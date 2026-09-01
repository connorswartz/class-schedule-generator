# Class Schedule Generator - Overview

A Flask web app that builds an optimized class timetable for a multi-day
rotating cycle. Set the periods, the fixed activities, and how much time each
subject needs, and it fills in the rest.

- **Run it:** `QUICK_START.md`
- **Put it online:** [DEPLOY.md](DEPLOY.md)

## What it does

Takes a timetable with fixed points in it - period times, breaks, PE on Day 1,
Music on Day 2 - and fills the remaining minutes with subjects so that:

- every subject hits its **minimum per day**, on every day
- every subject hits its **minimum per cycle**, across the whole cycle
- no subject is ever scheduled in a chunk smaller than its **minimum per block**
- no subject exceeds its **maximum minutes per day**
- morning-priority subjects land in the morning where possible
- subjects stay in whole blocks rather than being sliced up
- whatever is left over is marked **FREE**

If the settings make that impossible, it says so before generating, with the
numbers. If something is merely tight, it generates and reports what fell short.

A pre-filled activity takes its whole period out of the available pool. When it
is named after a subject those minutes are credited to that subject, so the
capacity check takes them off that subject's demand too - counting them on both
sides would reject timetables that in fact work.

## How it works

### Files

| File | Role |
|---|---|
| `app.py` | Routes, accounts, saved schedules (SQLite) |
| `schedule_backend.py` | The scheduling engine |
| `test_backend.py` | Test suite |
| `templates/index.html` | The generator UI |
| `templates/login.html`, `register.html` | Accounts |
| `templates/saved_schedules.html`, `saved_schedule_detail.html` | Saved schedules |
| `scheduler.db` | Accounts and saved schedules |

### The engine

`ScheduleGenerator(config)` validates the configuration and raises
`ScheduleConfigError` if it cannot work. `generate_schedule()` then runs five
phases:

1. **Daily minimums** - give every subject its per-day minimum on every day,
   choosing the best-placed period rather than the first with room.
2. **Cycle minimums** - fill the rest of each subject's cycle requirement,
   scored to keep blocks together and periods unfragmented.
3. **Mark free time** - everything still unassigned becomes FREE.
4. **Fill gaps** - convert FREE time back into any subject still short, taking
   only the minutes needed and never breaching the per-day cap.
5. **Improve placement** - swap period contents within a day to move priority
   subjects into their preferred half and lengthen runs of the same subject.

Packing blocks into periods is a packing problem, so one greedy pass can miss an
arrangement that exists. If a pass leaves a minimum unmet the engine re-rolls
its tie-breaking and keeps the best of up to 25 attempts. The sequence is
deterministic, so the same settings always give the same schedule.

Supplying a **variation number** (`random_seed`) shifts the tie-breaking to
produce a different valid schedule from the same requirements.

### Scoring

Placement is chosen by score. The weights are constants at the top of
`schedule_backend.py`:

| Constant | Value | Meaning |
|---|---|---|
| `W_FRAGMENTATION` | 250 | Penalty per extra subject sharing a period |
| `W_TIME_OF_DAY` | 120 | Priority subject in its preferred half |
| `W_ADJACENT_SAME_SUBJECT` | 100 | Same subject in the neighbouring period |
| `W_TIME_OF_DAY_PENALTY` | 60 | Non-priority subject taking a morning slot |
| `W_SAME_SUBJECT_IN_PERIOD` | 50 | Extending a block already in this period |
| `W_EMPTY_PERIOD` | 40 | Starting in a clean period |
| `W_PERIOD_PREFERENCE` | 30 | Period already tends to hold this subject |

### What the summary reports

`get_summary()` returns the totals plus everything that did not work out:

| Key | Meaning |
|---|---|
| `unmet_cycle` | Subjects short of their cycle minimum, and by how much |
| `unmet_daily` | Day/subject pairs short of the daily minimum |
| `over_cap` | Any breach of the per-day maximum (should always be empty) |
| `warnings` | Non-fatal issues, e.g. daily minimums totalling above the cycle minimum |

The UI shows these as red and yellow panels above the results.

## The school day

The teacher types their bell schedule in: the two outer bells, then a start and
an end time for every period and every break. Nothing is inferred. A period is
exactly as long as the times given for it, which is the only way to represent a
real timetable - bells are uneven, and any scheme that divides the day up
evenly gets them wrong.

Periods are numbered in clock order, so Period 1 is always the first of the day
however the rows happen to be arranged in the form. Breaks become ignored
periods, keyed by a slug of their name (`Lunch` -> `lunch`), so the engine
reserves the slot and never schedules into it.

The page adds up the totals as they are edited and shows the whole chain:
teaching minutes across the cycle, the minutes pre-filled activities book out
of that, and what is left for the subject minimums. That last figure is the one
the capacity check reports, so a rejection can be traced back to numbers
already on screen.

A day is rejected outright when it contradicts itself: a period or break
outside the outer bells, ending before it starts, or landing on top of another
one. Time that no period or break covers is different - it is reported, with
the gaps named, and otherwise left alone. That is often deliberate (passing
time, assembly), and guessing at it would mean changing a number the teacher
typed.

## The default configuration

The UI opens with the real timetable: a 6-day cycle, 8:41-15:10, seven teaching
periods (43/49/50/49/49/30/50 min) plus snack, lunch and recess - 320 teaching
minutes a day. Every one of those times is editable.

| Subject | Min/block | Min/day | Min/cycle |
|---|---|---|---|
| ELAL | 10 | 20 | 576 |
| Math | 20 | 20 | 288 |
| Science | 10 | 10 | 192 |
| Social | 10 | 10 | 192 |
| Religion | 5 | 20 | 192 |

Fixed activities: PE (Day 1 P2), Music (Day 2 P7), Music (Day 4 P4),
DPA (Day 5 P2), Learning Commons (Day 6 P7). ELAL and Math are morning
priorities; the cap is 120 min per subject per day.

That configuration schedules 1440 of 1673 available minutes, meets every
minimum, and leaves 233 minutes free.

## Testing

```bash
.venv\Scripts\python.exe test_backend.py
```

Covers the default timetable, pre-filled activities staying put, daily minimums,
the per-day cap, rejection of impossible configurations, period swapping,
fragmentation, reproducibility and seeding, validation messages, and a
randomised stress pass over ~120 generated configurations.

## Ideas for later

- Export to PDF or a printable calendar view
- Load a saved schedule's configuration back into the form to edit it
- Duplicate or rename saved schedules
- Per-subject colours carried into the CSV export
