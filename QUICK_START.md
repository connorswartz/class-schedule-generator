# Quick Start

A web app that builds an optimized class timetable for a multi-day rotating cycle.

## Run it on Windows

**Easiest way:** double-click **`run.bat`**.

It creates the virtual environment the first time, installs Flask, starts the
server, and opens your browser. Leave the window open while you use the app;
close it (or press Ctrl+C) to stop.

**From a terminal instead:**

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python.exe app.py
```

Then open <http://localhost:5001>.

> The `venv/` folder in this project was built on a Mac and does **not** work on
> Windows. Use `.venv/` (created above) instead. You can delete `venv/` if you
> no longer use the Mac.

## Run it on macOS / Linux

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python app.py
```

## First time in

The app has user accounts, so your schedules are saved to your own login.

1. Go to <http://localhost:5001> - you will land on the sign-in page.
2. Click **Create an account**. Passwords need 8+ characters with an uppercase
   letter, a lowercase letter, a number, and a symbol.
3. Sign in.

Accounts live in `scheduler.db` in this folder.

## Using the generator

### 1. Days and periods
Pick how many days are in the cycle (1-12). Periods come pre-loaded with the
real timetable: seven teaching periods plus snack, lunch and recess. Breaks are
marked **ignored** so nothing is ever scheduled into them.

### 2. Pre-filled activities
Lock a fixed activity into a slot - PE on Day 1 Period 2, Music on Day 2
Period 7, and so on. These are never moved.

If a pre-filled activity is named exactly like one of your subjects, its
minutes count toward that subject's totals.

### 3. Subject requirements
For each subject set:

| Field | Meaning |
|---|---|
| **Min per block** | Shortest usable chunk. A subject is never scheduled in less. |
| **Min per day** | Must be reached every single day. |
| **Min per cycle** | Must be reached across the whole cycle. |

**Max minutes per subject per day** is a hard ceiling, so one subject cannot
take over a day. Set it to 0 hours 0 minutes for no limit.

### 4. Priorities
Tick which periods count as morning and afternoon, then which subjects should
land there. Morning priority subjects are pulled into morning periods and
other subjects are pushed out of them.

### 5. Variation number (optional)
Leave it empty for the standard schedule - the same settings always produce the
same result. Type a number, or press **Try a different layout**, to get a
different arrangement that still meets every requirement.

### 6. Generate
You get summary tiles, the full timetable, and a **Download CSV** button. Name
the schedule and press **Save Schedule** to keep it under **Saved Schedules**.

## If something cannot be scheduled

- **A red box before you get a schedule** means the settings are impossible -
  for example the minimums need more time than the timetable has. The message
  says exactly how many minutes over you are.
- **A red panel above the results** means the schedule was built but a minimum
  could not be reached, and names each shortfall.
- **A yellow panel** flags things worth a look, such as a daily minimum that
  adds up to more than the cycle minimum.

## Checking everything still works

```bash
.venv\Scripts\python.exe test_backend.py
```

Runs the full test suite: the default timetable, the structural rules every
schedule must obey, configuration validation, and a randomised stress pass.

## Troubleshooting

**Port 5001 already in use** - run with a different port:

```bash
set PORT=5002 && .venv\Scripts\python.exe app.py
```

**"Python was not found"** - install Python 3.10+ from
[python.org](https://www.python.org/downloads/) and tick *Add python.exe to PATH*.

**Signed out after restarting** - normal only if the app cannot write its
`.flask_secret` file; otherwise sessions persist.

See [DEPLOY.md](DEPLOY.md) to put it online.
