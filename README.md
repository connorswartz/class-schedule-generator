# Class Schedule Generator

A web app that builds an optimized class timetable for a multi-day rotating
cycle. Set the periods, lock in the fixed activities, say how much time each
subject needs, and it fills in the rest.

Built for a 6-day elementary cycle with fixed PE, Music, DPA and Learning
Commons slots, but everything is configurable through the interface - no code
editing.

## Use it online

**<https://connorswartz.github.io/class-schedule-generator/>**

No sign-up, nothing to install. The scheduling engine runs in your browser
(Python via [Pyodide](https://pyodide.org)), and saved schedules are kept in
that browser's storage - so they stay on your device, and clearing your browser
data removes them. Download the CSV to keep a copy.

The first visit downloads the Python runtime, which takes a few seconds; after
that it is cached.

## Two versions, one engine

| | Browser version (`docs/`) | Flask app (`app.py`) |
|---|---|---|
| Accounts | None needed | Username + password |
| Saved schedules | This browser only | Shared database, any device |
| Hosting | GitHub Pages, free | Needs a Python host |

Both run the exact same `schedule_backend.py`. `build_static.py` generates the
browser version from `templates/index.html`, so the UI and the engine are never
duplicated - and CI fails if `docs/` drifts out of date.

```bash
python build_static.py
```

## Run it locally

On Windows, double-click **`run.bat`**. Otherwise:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python app.py
```

Then open <http://localhost:5001> and create an account.

Full instructions: **[QUICK_START.md](QUICK_START.md)**
Hosting it online: **[DEPLOY.md](DEPLOY.md)**
How the algorithm works: **[OVERVIEW.md](OVERVIEW.md)**

## What it guarantees

Given a workable configuration, every generated schedule satisfies:

- each subject's **minimum per day**, on every day
- each subject's **minimum per cycle**, across the cycle
- no block shorter than a subject's **minimum per block**
- no subject over its **maximum minutes per day**
- pre-filled activities stay exactly where you put them
- breaks are never scheduled into

Morning-priority subjects are pulled toward the morning, subjects are kept in
whole blocks rather than sliced up, and leftover time is marked FREE.

If the settings are impossible it says so before generating, with the numbers.
If something merely falls short it generates anyway and reports exactly what.

## Testing

```bash
.venv/bin/python test_backend.py
```

Ten checks covering the default timetable, the structural rules every schedule
must obey, configuration validation, reproducibility, and a randomised stress
pass over ~120 generated configurations.

## Configuration reference

| Setting | Meaning |
|---|---|
| **Min per block** | Shortest usable chunk of a subject |
| **Min per day** | Must be reached every day |
| **Min per cycle** | Must be reached across the whole cycle |
| **Max per day** | Hard ceiling per subject per day (0 = no limit) |
| **Morning/afternoon periods** | Which slots count as which half |
| **Priority subjects** | Which subjects get pulled to which half |
| **Variation number** | Same number = same schedule; change it for a different valid layout |

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `FLASK_SECRET_KEY` | random, saved to `.flask_secret` | **Set this when hosting.** Signs session cookies. |
| `DATA_DIR` | the project folder | Where `scheduler.db` and the session key live. |
| `DATABASE_PATH` | `$DATA_DIR/scheduler.db` | Override the database location directly. |
| `HOST` | `127.0.0.1` | Set to `0.0.0.0` when hosting. |
| `PORT` | `5001` | Most platforms set this for you. |
| `FLASK_DEBUG` | off | **Leave off.** The debugger can run arbitrary code. |

## Project structure

```
├── app.py                      # Routes, accounts, saved schedules
├── schedule_backend.py         # Scheduling engine
├── test_backend.py             # Test suite
├── run.bat                     # Windows launcher
├── requirements.txt
├── Procfile / render.yaml      # Hosting config
├── scheduler.db                # SQLite: accounts + saved schedules (gitignored)
├── .flask_secret               # Generated session key (gitignored)
└── templates/
    ├── index.html              # Generator UI
    ├── login.html / register.html
    └── saved_schedules.html / saved_schedule_detail.html
```

## Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Generator UI (sign-in required) |
| `/register`, `/login`, `/logout` | GET/POST | Accounts |
| `/generate` | POST | Config JSON in, schedule CSV + summary out |
| `/save-schedule` | POST | Save the current schedule |
| `/schedule-default-name` | GET | Next auto name ("Schedule 3") |
| `/saved-schedules` | GET | List your saved schedules |
| `/saved-schedules/<id>` | GET | View one |
| `/saved-schedules/<id>/download` | GET | Download one as CSV |
| `/download-csv` | POST | Download the schedule just generated |

## Tuning the algorithm

Scoring weights are constants at the top of `schedule_backend.py`:

| Constant | Effect |
|---|---|
| `W_FRAGMENTATION` | Higher = fewer subjects sharing one period |
| `W_ADJACENT_SAME_SUBJECT` | Higher = longer runs of the same subject |
| `W_TIME_OF_DAY` | Higher = stronger morning/afternoon preference |
| `W_TIME_OF_DAY_PENALTY` | Higher = harder push of non-priority subjects out of mornings |
| `W_EMPTY_PERIOD` | Higher = prefers starting in an empty period |

Run the tests after any change.

## License

Free to use and modify for educational purposes.
