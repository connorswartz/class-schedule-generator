"""
Test suite for the schedule generation engine.

Run it with:
    python test_backend.py

It checks the default configuration, the structural invariants every generated
schedule must hold, the configuration validation, and a randomised stress pass
over many generated configurations.
"""
import copy
import random
import sys
import traceback

from schedule_backend import FREE, ScheduleConfigError, ScheduleGenerator

# The configuration the web UI ships with (Miss K's real timetable).
DEFAULT_CONFIG = {
    "periods": {
        "period_1": ["8:41", "9:24", 43],
        "period_2": ["9:24", "10:13", 49],
        "snack": ["10:13", "10:18", 5],
        "period_3": ["10:18", "11:08", 50],
        "period_4": ["11:08", "11:57", 49],
        "lunch": ["11:57", "12:41", 44],
        "period_5": ["12:41", "13:30", 49],
        "recess": ["13:30", "13:50", 20],
        "period_6": ["13:50", "14:20", 30],
        "period_7": ["14:20", "15:10", 50],
    },
    "days": ["Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Day 6"],
    "pre_filled": {
        "Day 1": [["period_2", "PE"]],
        "Day 2": [["period_7", "Music"]],
        "Day 4": [["period_4", "Music"]],
        "Day 5": [["period_2", "DPA"]],
        "Day 6": [["period_7", "Learning Commons"]],
    },
    "subject_requirements": {
        "ELAL": {"min_per_block": 10, "min_per_day": 20, "min_per_cycle": 576},
        "Math": {"min_per_block": 20, "min_per_day": 20, "min_per_cycle": 288},
        "Science": {"min_per_block": 10, "min_per_day": 10, "min_per_cycle": 192},
        "Social": {"min_per_block": 10, "min_per_day": 10, "min_per_cycle": 192},
        "Religion": {"min_per_block": 5, "min_per_day": 20, "min_per_cycle": 192},
    },
    "max_per_day": 120,
    "morning_periods": ["period_1", "period_2", "period_3", "period_4"],
    "afternoon_periods": ["period_5", "period_6", "period_7"],
    "morning_priority_subjects": ["ELAL", "Math"],
    "afternoon_priority_subjects": [],
    "ignored_periods": ["snack", "lunch", "recess"],
}


# ----------------------------------------------------------------------
# Tiny test harness
# ----------------------------------------------------------------------
TESTS = []


def test(name):
    def decorator(fn):
        TESTS.append((name, fn))
        return fn
    return decorator


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def build(**overrides):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(copy.deepcopy(overrides))
    return config


def run(config):
    generator = ScheduleGenerator(config)
    generator.generate_schedule()
    return generator


def assert_invariants(generator, label=""):
    """Structural rules every generated schedule must satisfy."""
    prefix = f"[{label}] " if label else ""
    summary = generator.get_summary()

    for day in generator.days:
        for period in generator.teaching_periods:
            blocks = generator.schedule[day].get(period, [])
            total = sum(b.minutes for b in blocks)
            duration = generator.periods[period][2]
            check(
                total == duration,
                f"{prefix}{day}/{period} holds {total} min but the period is {duration} min",
            )
            for block in blocks:
                check(
                    block.minutes > 0,
                    f"{prefix}{day}/{period} has a non-positive block: {block}",
                )
                if block.subject in generator.subject_requirements:
                    min_block = generator.subject_requirements[block.subject]["min_per_block"]
                    check(
                        block.minutes >= min_block,
                        f"{prefix}{day}/{period} has {block} below its "
                        f"{min_block} min minimum block",
                    )

        # Ignored periods stay untouched breaks.
        for period in generator.ignored_periods:
            if period in generator.periods:
                blocks = generator.schedule[day].get(period, [])
                check(
                    len(blocks) == 1 and blocks[0].subject == "Break",
                    f"{prefix}{day}/{period} should be a single Break block, got {blocks}",
                )

    check(not summary["over_cap"], f"{prefix}per-day cap exceeded: {summary['over_cap']}")

    # Totals must agree with what is actually on the board.
    counted = {subject: 0 for subject in generator.subject_requirements}
    for day in generator.days:
        for blocks in generator.schedule[day].values():
            for block in blocks:
                if block.subject in counted:
                    counted[block.subject] += block.minutes
    check(
        counted == generator.subject_totals,
        f"{prefix}subject totals disagree with the schedule: "
        f"{counted} vs {generator.subject_totals}",
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@test("default config meets every requirement")
def test_default_meets_requirements():
    generator = run(build())
    summary = generator.get_summary()

    assert_invariants(generator, "default")
    check(not summary["unmet_cycle"], f"cycle minimums unmet: {summary['unmet_cycle']}")
    check(not summary["unmet_daily"], f"daily minimums unmet: {summary['unmet_daily']}")

    for subject, req in summary["requirements"].items():
        total = summary["subject_totals"][subject]
        check(
            total >= req["min_per_cycle"],
            f"{subject} got {total} of {req['min_per_cycle']} min",
        )
        print(f"    {subject:10} {total:4} / {req['min_per_cycle']:4} min")

    print(f"    available {summary['total_available']} | "
          f"assigned {summary['total_assigned']} | free {summary['total_free']}")


@test("pre-filled activities stay exactly where they were put")
def test_prefilled_respected():
    generator = run(build())
    for day, entries in DEFAULT_CONFIG["pre_filled"].items():
        for period, activity in entries:
            blocks = generator.schedule[day][period]
            check(
                len(blocks) == 1 and blocks[0].subject == activity,
                f"{day}/{period} should hold {activity}, got {blocks}",
            )


@test("pre-filled subject time counts against that subject's minimums (bug 6)")
def test_prefilled_counts_toward_demand():
    # Every pre-filled slot here is a real subject, so its minutes are both
    # removed from the available pool and credited to the subject. Charging
    # them twice used to reject this timetable even though it schedules fine.
    config = build(
        max_per_day=0,
        pre_filled={
            "Day 1": [["period_2", "Math"]],
            "Day 2": [["period_7", "Math"]],
            "Day 4": [["period_4", "Math"]],
            "Day 5": [["period_2", "Math"]],
            "Day 6": [["period_7", "Math"]],
        },
        subject_requirements={
            "ELAL": {"min_per_block": 10, "min_per_day": 20, "min_per_cycle": 700},
            "Math": {"min_per_block": 10, "min_per_day": 20, "min_per_cycle": 500},
            "Science": {"min_per_block": 10, "min_per_day": 10, "min_per_cycle": 250},
            "Social": {"min_per_block": 10, "min_per_day": 10, "min_per_cycle": 250},
        },
        morning_priority_subjects=["ELAL", "Math"],
    )
    generator = run(config)
    summary = generator.get_summary()

    assert_invariants(generator, "prefilled-demand")
    check(not summary["unmet_cycle"], f"cycle minimums unmet: {summary['unmet_cycle']}")
    print(f"    1700 min of minimums fitted into {summary['total_available']} min available")


@test("a pre-filled subject block covers that day's minimum for it (bug 6)")
def test_prefilled_covers_daily_minimum():
    # Day 1 period_2 is 49 min of Math, which already exceeds Math's 45 min
    # daily minimum - the day only has to find room for the other subjects.
    config = build(
        max_per_day=0,
        pre_filled={"Day 1": [["period_2", "Math"]]},
        subject_requirements={
            "ELAL": {"min_per_block": 10, "min_per_day": 120, "min_per_cycle": 720},
            "Math": {"min_per_block": 10, "min_per_day": 45, "min_per_cycle": 270},
            "Science": {"min_per_block": 10, "min_per_day": 100, "min_per_cycle": 600},
        },
        morning_priority_subjects=["ELAL", "Math"],
    )
    generator = run(config)
    summary = generator.get_summary()

    assert_invariants(generator, "prefilled-daily")
    check(not summary["unmet_daily"], f"daily minimums unmet: {summary['unmet_daily']}")
    got = summary["daily_totals"]["Day 1"]["Math"]
    check(got >= 45, f"Day 1 Math got {got} min, needs 45")


@test("daily minimums are honoured even above the cycle minimum (bug 2)")
def test_daily_minimum_not_capped_by_cycle():
    config = build()
    # 60/day x 6 days = 360, far above the 60 min cycle minimum.
    config["subject_requirements"]["Science"] = {
        "min_per_block": 10, "min_per_day": 60, "min_per_cycle": 60,
    }
    generator = run(config)
    summary = generator.get_summary()

    assert_invariants(generator, "daily-min")
    check(not summary["unmet_daily"], f"daily minimums unmet: {summary['unmet_daily']}")
    for day in generator.days:
        got = summary["daily_totals"][day]["Science"]
        check(got >= 60, f"{day} Science got {got} min, needs 60")
    check(
        any("above its" in w for w in summary["warnings"]),
        f"expected a warning about exceeding the cycle minimum, got {summary['warnings']}",
    )


@test("the per-day cap is never exceeded (bug 3)")
def test_max_per_day_respected():
    # A tight cap plus large cycle minimums forces the gap-filling phase to run.
    config = build(max_per_day=60)
    config["subject_requirements"] = {
        "ELAL": {"min_per_block": 10, "min_per_day": 20, "min_per_cycle": 340},
        "Math": {"min_per_block": 10, "min_per_day": 20, "min_per_cycle": 340},
        "Science": {"min_per_block": 10, "min_per_day": 10, "min_per_cycle": 300},
        "Social": {"min_per_block": 10, "min_per_day": 10, "min_per_cycle": 300},
    }
    generator = run(config)
    summary = generator.get_summary()

    assert_invariants(generator, "max-per-day")
    for day in generator.days:
        for subject, minutes in summary["daily_totals"][day].items():
            check(minutes <= 60, f"{day} {subject} got {minutes} min, over the 60 min cap")
    print(f"    cap held across {len(generator.days)} days, "
          f"{len(config['subject_requirements'])} subjects")


@test("impossible configurations raise instead of silently under-delivering (bug 4)")
def test_oversubscription_raises():
    config = build(max_per_day=0)  # remove the cap so capacity is the only limit
    config["subject_requirements"]["ELAL"]["min_per_cycle"] = 2000
    try:
        ScheduleGenerator(config)
    except ScheduleConfigError as error:
        check("only" in str(error), f"error should quote the available time: {error}")
        return
    raise AssertionError("expected ScheduleConfigError for an over-subscribed config")


@test("periods of different lengths can be swapped (bug 1)")
def test_cross_duration_swaps():
    generator = ScheduleGenerator(build())
    original = ScheduleGenerator._can_swap_periods
    allowed = set()

    def spy(self, day, period1, period2):
        result = original(self, day, period1, period2)
        if result:
            allowed.add((period1, period2))
        return result

    ScheduleGenerator._can_swap_periods = spy
    try:
        generator.generate_schedule()
    finally:
        ScheduleGenerator._can_swap_periods = original

    durations = {name: spec[2] for name, spec in generator.periods.items()}
    cross = [pair for pair in allowed if durations[pair[0]] != durations[pair[1]]]
    check(cross, "no swap between different-length periods was ever allowed")
    check(
        any("period_1" in pair or "period_6" in pair for pair in allowed),
        "the shortest periods still cannot be swapped",
    )
    print(f"    {len(allowed)} swappable pairs, {len(cross)} across different lengths")


@test("subjects are not fragmented across a period unnecessarily (bug 5)")
def test_low_fragmentation():
    generator = run(build())
    shared = sum(
        1
        for day in generator.days
        for period in generator.teaching_periods
        if len(generator._real_subjects_in(day, period)) > 1
    )
    cells = len(generator.days) * len(generator.teaching_periods)
    check(shared <= cells * 0.15, f"{shared} of {cells} cells hold 2+ subjects")
    print(f"    {shared} of {cells} cells hold more than one subject")


@test("output is reproducible, and a seed changes it")
def test_determinism_and_seed():
    first = run(build()).export_to_csv_string()
    second = run(build()).export_to_csv_string()
    check(first == second, "two runs of the same config produced different schedules")

    seeded = run(build(random_seed=7)).export_to_csv_string()
    check(seeded != first, "random_seed=7 produced the identical schedule")
    check(
        run(build(random_seed=7)).export_to_csv_string() == seeded,
        "the same seed produced two different schedules",
    )

    seeded_generator = run(build(random_seed=7))
    assert_invariants(seeded_generator, "seeded")
    summary = seeded_generator.get_summary()
    check(not summary["unmet_cycle"], f"seeded run left requirements unmet: {summary['unmet_cycle']}")


@test("bad configurations are rejected with a useful message")
def test_validation():
    cases = [
        ("no periods", build(periods={}), "period"),
        ("no subjects", build(subject_requirements={}), "subject"),
        ("zero-length period",
         build(periods={**DEFAULT_CONFIG["periods"], "period_1": ["8:41", "8:41", 0]}),
         "longer than 0"),
        ("unknown pre-filled period",
         build(pre_filled={"Day 1": [["period_99", "PE"]]}),
         "unknown period"),
        ("block longer than any period",
         build(subject_requirements={
             "ELAL": {"min_per_block": 999, "min_per_day": 0, "min_per_cycle": 10}}),
         "longest teaching period"),
        ("daily minimum above the cap",
         build(max_per_day=15), "exceeds"),
        ("everything ignored",
         build(ignored_periods=list(DEFAULT_CONFIG["periods"].keys())),
         "ignored"),
    ]
    for label, config, expected in cases:
        try:
            ScheduleGenerator(config)
        except ScheduleConfigError as error:
            check(
                expected in str(error),
                f"{label}: expected {expected!r} in the message, got {error}",
            )
            continue
        raise AssertionError(f"{label}: expected ScheduleConfigError, none raised")
    print(f"    {len(cases)} invalid configurations rejected")


@test("randomised stress pass holds every invariant")
def test_random_stress():
    rng = random.Random(1234)
    generated = 0
    fully_met = 0
    rejected = 0

    for _ in range(120):
        num_days = rng.randint(1, 8)
        num_periods = rng.randint(2, 7)

        periods = {}
        for index in range(num_periods):
            periods[f"period_{index + 1}"] = ["8:00", "9:00", rng.choice([20, 30, 40, 45, 50, 60])]
        if rng.random() < 0.6:
            periods["lunch"] = ["12:00", "12:40", 40]

        subjects = {}
        for index in range(rng.randint(1, 5)):
            min_block = rng.choice([5, 10, 15, 20])
            subjects[f"Subject{index + 1}"] = {
                "min_per_block": min_block,
                "min_per_day": rng.choice([0, min_block, min_block * 2]),
                "min_per_cycle": rng.choice([0, 30, 60, 120, 240]),
            }

        period_names = list(periods.keys())
        config = {
            "periods": periods,
            "days": [f"Day {i + 1}" for i in range(num_days)],
            "subject_requirements": subjects,
            "ignored_periods": ["lunch"] if "lunch" in periods else [],
            "max_per_day": rng.choice([0, 60, 120, 240]),
            "morning_periods": period_names[: max(1, len(period_names) // 2)],
            "afternoon_periods": period_names[max(1, len(period_names) // 2):],
            "morning_priority_subjects": (
                [next(iter(subjects))] if subjects and rng.random() < 0.5 else []
            ),
            "afternoon_priority_subjects": [],
            "pre_filled": {},
            "random_seed": rng.choice([None, rng.randint(1, 999)]),
        }

        if rng.random() < 0.4:
            teaching = [p for p in period_names if p != "lunch"]
            if teaching:
                config["pre_filled"] = {
                    "Day 1": [[rng.choice(teaching), rng.choice(["PE", "Music", "Assembly"])]]
                }

        try:
            generator = run(config)
        except ScheduleConfigError:
            rejected += 1
            continue

        generated += 1
        assert_invariants(generator, "stress")
        summary = generator.get_summary()

        # Daily minimums must always be met once a config has validated.
        check(
            not summary["unmet_daily"],
            f"stress config left daily minimums unmet: {summary['unmet_daily'][:3]}",
        )

        # Fitting blocks into periods is a packing problem, so a rare cycle
        # minimum may still fall short. What must never happen is a silent
        # shortfall: whatever is missing has to be reported.
        for subject, req in generator.subject_requirements.items():
            short = req["min_per_cycle"] - summary["subject_totals"][subject]
            reported = [u for u in summary["unmet_cycle"] if u["subject"] == subject]
            if short > 0:
                check(
                    reported and reported[0]["short_by"] == short,
                    f"{subject} is {short} min short but was not reported: "
                    f"{summary['unmet_cycle']}",
                )
            else:
                check(not reported, f"{subject} was reported short but is not")

        if not summary["unmet_cycle"]:
            fully_met += 1

    check(generated >= 40, f"only {generated} configurations were schedulable")
    rate = fully_met / generated
    check(rate >= 0.95, f"only {fully_met}/{generated} configs met every cycle minimum")
    print(f"    {generated} schedules generated, {rejected} rejected as impossible")
    print(f"    {fully_met}/{generated} met every minimum ({rate:.0%})")


# ----------------------------------------------------------------------
def main():
    print("Running schedule generator tests\n")
    passed = 0
    failed = []

    for name, fn in TESTS:
        try:
            fn()
        except Exception as error:  # noqa: BLE001 - report every failure
            failed.append(name)
            print(f"  FAIL  {name}")
            print(f"        {type(error).__name__}: {error}")
            if not isinstance(error, AssertionError):
                traceback.print_exc()
        else:
            passed += 1
            print(f"  PASS  {name}")

    print(f"\n{passed} passed, {len(failed)} failed")
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
