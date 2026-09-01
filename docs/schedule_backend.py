"""
Schedule Generator Backend - Accepts dynamic configuration.

Public API (unchanged):
    generator = ScheduleGenerator(config)
    generator.generate_schedule()
    generator.export_to_csv_string() -> str
    generator.get_summary() -> dict

Invalid configurations raise ScheduleConfigError (a ValueError) from the
constructor with every problem listed, instead of silently producing a
schedule that cannot meet the requirements.
"""
import csv
import random
from dataclasses import dataclass
from io import StringIO

FREE = 'FREE'
BREAK = 'Break'

# Score weights used by the placement heuristics.
W_ADJACENT_SAME_SUBJECT = 100   # same subject in the neighbouring period
W_SAME_SUBJECT_IN_PERIOD = 50   # extending a block already in this period
W_EMPTY_PERIOD = 40             # prefer starting in a clean period
W_FRAGMENTATION = 250           # penalty per extra distinct subject in a period
W_PERIOD_PREFERENCE = 30        # period already "belongs" to this subject
W_TIME_OF_DAY = 120             # priority subject landing in its preferred half
W_TIME_OF_DAY_PENALTY = 60      # non-priority subject taking a morning slot


class ScheduleConfigError(ValueError):
    """Raised when a configuration cannot produce a valid schedule."""


@dataclass
class SubjectBlock:
    """Represents a block of time for a subject within a period."""
    subject: str
    minutes: int

    def __repr__(self):
        return f"{self.subject} ({self.minutes}min)"


class ScheduleGenerator:
    def __init__(self, config: dict):
        """
        Initialize with dynamic configuration.

        config should contain:
        - periods: dict of period_name -> [start, end, duration]
        - days: list of day names
        - pre_filled: dict of day -> [[period, activity], ...]
        - subject_requirements: dict of subject -> {min_per_block, min_per_day, min_per_cycle}
        - max_per_day: maximum minutes per subject per day (0 or absent = unlimited)
        - morning_periods / afternoon_periods: lists of period names
        - morning_priority_subjects / afternoon_priority_subjects: lists of subjects
        - ignored_periods: period names that are never scheduled into
        - random_seed: optional; varies tie-breaking to produce a different schedule
        """
        self.config = config or {}
        self.periods = self.config.get('periods') or {}
        self.days = self.config.get('days') or []
        self.pre_filled = self.config.get('pre_filled') or {}
        self.subject_requirements = self.config.get('subject_requirements') or {}
        self.morning_periods = self.config.get('morning_periods') or []
        self.afternoon_periods = self.config.get('afternoon_periods') or []
        self.morning_priority_subjects = self.config.get('morning_priority_subjects') or []
        self.afternoon_priority_subjects = self.config.get('afternoon_priority_subjects') or []

        ignored = self.config.get('ignored_periods')
        if ignored is None:
            ignored = ['snack', 'lunch', 'recess']
        self.ignored_periods = list(ignored)

        # 0 / None / negative means "no cap".
        try:
            max_per_day = int(self.config.get('max_per_day') or 0)
        except (TypeError, ValueError):
            max_per_day = 0
        self.max_per_day = max_per_day if max_per_day > 0 else float('inf')

        self.warnings = []
        self._validate()

        # Ordered list of periods that can actually hold subjects.
        self.teaching_periods = [p for p in self.periods if p not in self.ignored_periods]
        self.min_block_overall = min(
            req['min_per_block'] for req in self.subject_requirements.values()
        )

        seed = self.config.get('random_seed')
        self._rng = random.Random(seed if seed not in (None, '') else 0)
        # Only jitter tie-breaks when the user actually asked for a variation.
        self._jitter = 6.0 if seed not in (None, '') else 0.0

        # Initialize schedule tracking
        self.schedule = {day: {} for day in self.days}
        self.subject_totals = {subject: 0 for subject in self.subject_requirements}
        self.daily_subject_totals = {
            day: {subject: 0 for subject in self.subject_requirements}
            for day in self.days
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(self):
        """Check the configuration up front and normalise numeric fields."""
        errors = []

        if not isinstance(self.periods, dict) or not self.periods:
            errors.append("At least one period must be defined.")
        if not isinstance(self.days, list) or not self.days:
            errors.append("At least one day must be defined.")
        if not isinstance(self.subject_requirements, dict) or not self.subject_requirements:
            errors.append("At least one subject must be defined.")
        if errors:
            raise ScheduleConfigError(self._format_errors(errors))

        # --- period shapes -------------------------------------------------
        normalised_periods = {}
        for name, spec in self.periods.items():
            if not isinstance(spec, (list, tuple)) or len(spec) != 3:
                errors.append(f"Period '{name}' must be [start, end, duration].")
                continue
            try:
                duration = int(spec[2])
            except (TypeError, ValueError):
                errors.append(f"Period '{name}' has a non-numeric duration.")
                continue
            if duration <= 0:
                errors.append(f"Period '{name}' must be longer than 0 minutes.")
                continue
            normalised_periods[name] = [spec[0], spec[1], duration]

        if len(set(self.days)) != len(self.days):
            errors.append("Day names must be unique.")

        # --- subject shapes ------------------------------------------------
        normalised_subjects = {}
        for subject, req in self.subject_requirements.items():
            if not isinstance(req, dict):
                errors.append(
                    f"Subject '{subject}' must define min_per_block, min_per_day and min_per_cycle."
                )
                continue
            values = {}
            bad_field = False
            for key in ('min_per_block', 'min_per_day', 'min_per_cycle'):
                try:
                    value = int(req.get(key, 0) or 0)
                except (TypeError, ValueError):
                    errors.append(f"Subject '{subject}' has a non-numeric {key}.")
                    bad_field = True
                    continue
                if value < 0:
                    errors.append(f"Subject '{subject}' has a negative {key}.")
                    bad_field = True
                    continue
                values[key] = value
            if bad_field:
                continue
            if values['min_per_block'] <= 0:
                errors.append(f"Subject '{subject}' needs a min_per_block greater than 0.")
                continue
            normalised_subjects[subject] = values

        if errors:
            raise ScheduleConfigError(self._format_errors(errors))

        self.periods = normalised_periods
        self.subject_requirements = normalised_subjects

        teaching = [p for p in self.periods if p not in self.ignored_periods]
        if not teaching:
            raise ScheduleConfigError(self._format_errors(
                ["Every period is marked as ignored, so nothing can be scheduled."]
            ))

        longest_period = max(self.periods[p][2] for p in teaching)
        num_days = len(self.days)

        # --- pre-filled activities ----------------------------------------
        cleaned_pre_filled = {}
        for day, entries in (self.pre_filled or {}).items():
            if day not in self.days:
                self.warnings.append(
                    f"Pre-filled activities for '{day}' were skipped - that day is not in the cycle."
                )
                continue
            seen_periods = set()
            kept = []
            for entry in entries or []:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    errors.append(f"Pre-filled entry on '{day}' must be [period, activity].")
                    continue
                period, activity = entry[0], entry[1]
                if period not in self.periods:
                    errors.append(
                        f"Pre-filled activity '{activity}' on {day} uses unknown period '{period}'."
                    )
                    continue
                if period in self.ignored_periods:
                    self.warnings.append(
                        f"'{activity}' on {day} was skipped - '{period}' is marked as an ignored break."
                    )
                    continue
                if period in seen_periods:
                    self.warnings.append(
                        f"{day} has more than one activity in '{period}'; only the last one is kept."
                    )
                    kept = [item for item in kept if item[0] != period]
                seen_periods.add(period)
                kept.append([period, activity])
            if kept:
                cleaned_pre_filled[day] = kept
        self.pre_filled = cleaned_pre_filled

        # --- per-subject feasibility --------------------------------------
        for subject, req in self.subject_requirements.items():
            if req['min_per_block'] > longest_period:
                errors.append(
                    f"'{subject}' needs blocks of at least {req['min_per_block']} min, but the "
                    f"longest teaching period is only {longest_period} min."
                )
            if self.max_per_day != float('inf'):
                if req['min_per_day'] > self.max_per_day:
                    errors.append(
                        f"'{subject}' requires {req['min_per_day']} min per day, which exceeds the "
                        f"{int(self.max_per_day)} min per-day cap."
                    )
                elif req['min_per_cycle'] > self.max_per_day * num_days:
                    errors.append(
                        f"'{subject}' requires {req['min_per_cycle']} min per cycle, but the "
                        f"{int(self.max_per_day)} min per-day cap allows at most "
                        f"{int(self.max_per_day) * num_days} min over {num_days} days."
                    )
            if req['min_per_day'] * num_days > req['min_per_cycle']:
                self.warnings.append(
                    f"'{subject}' will total {req['min_per_day'] * num_days} min "
                    f"({req['min_per_day']}/day x {num_days} days), above its "
                    f"{req['min_per_cycle']} min cycle minimum."
                )

        # --- overall capacity ---------------------------------------------
        # A pre-filled activity takes its whole period out of the pool. When the
        # activity is named after a subject, initialize_schedule() credits those
        # minutes to that subject, so they have to come off its demand as well -
        # otherwise the block is charged twice and timetables that would in fact
        # work get rejected.
        available = sum(self.periods[p][2] for p in teaching) * num_days
        cycle_credit = {subject: 0 for subject in self.subject_requirements}
        daily_credit = {day: {} for day in self.days}
        for day, entries in self.pre_filled.items():
            for period, activity in entries:
                duration = self.periods[period][2]
                available -= duration
                if activity in self.subject_requirements:
                    cycle_credit[activity] += duration
                    day_credit = daily_credit[day]
                    day_credit[activity] = day_credit.get(activity, 0) + duration

        demand = sum(
            max(
                0,
                max(req['min_per_cycle'], req['min_per_day'] * num_days)
                - cycle_credit[subject],
            )
            for subject, req in self.subject_requirements.items()
        )
        if demand > available:
            errors.append(
                f"The subject minimums need {demand} min but only {available} min of teaching "
                f"time is available. Reduce the minimums by {demand - available} min, add periods, "
                f"or remove a pre-filled activity."
            )

        # Each individual day must also have room for every daily minimum, less
        # whatever that day's pre-filled activities already cover.
        day_capacity = sum(self.periods[p][2] for p in teaching)
        for day in self.days:
            credited = daily_credit[day]
            capacity = day_capacity - sum(
                self.periods[period][2] for period, _a in self.pre_filled.get(day, [])
            )
            daily_demand = sum(
                max(0, req['min_per_day'] - credited.get(subject, 0))
                for subject, req in self.subject_requirements.items()
            )
            if daily_demand > capacity:
                errors.append(
                    f"{day} has only {capacity} min of teaching time but the daily minimums "
                    f"need {daily_demand} min."
                )

        if errors:
            raise ScheduleConfigError(self._format_errors(errors))

    @staticmethod
    def _format_errors(errors):
        if len(errors) == 1:
            return errors[0]
        return "This configuration cannot produce a schedule:\n- " + "\n- ".join(errors)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def initialize_schedule(self):
        """Initialize schedule with pre-filled and ignored periods."""
        for day in self.days:
            # Ignored periods are breaks and always win their slot.
            for period in self.ignored_periods:
                if period in self.periods:
                    self.schedule[day][period] = [SubjectBlock(BREAK, self.periods[period][2])]

            # Pre-filled activities occupy their whole period. When the activity
            # name matches a subject, it counts toward that subject's totals.
            for period, activity in self.pre_filled.get(day, []):
                duration = self.periods[period][2]
                self.schedule[day][period] = [SubjectBlock(activity, duration)]
                if activity in self.subject_requirements:
                    self.subject_totals[activity] += duration
                    self.daily_subject_totals[day][activity] += duration

    def get_period_remaining_time(self, day: str, period: str) -> int:
        """Get how many minutes are left in a period."""
        if period not in self.schedule[day]:
            return self.periods[period][2]

        used = sum(block.minutes for block in self.schedule[day][period])
        return self.periods[period][2] - used

    def add_block_to_period(self, day: str, period: str, subject: str, minutes: int):
        """Add a subject block to a period."""
        if period not in self.schedule[day]:
            self.schedule[day][period] = []

        self.schedule[day][period].append(SubjectBlock(subject, minutes))
        self.subject_totals[subject] += minutes
        self.daily_subject_totals[day][subject] += minutes

    def can_fit_block(self, day: str, period: str, subject: str, minutes: int) -> bool:
        """Check if a block can fit in a period without breaking the daily cap."""
        remaining = self.get_period_remaining_time(day, period)
        min_block = self.subject_requirements[subject]['min_per_block']
        return (
            remaining >= minutes
            and minutes >= min_block
            and minutes <= self._headroom(day, subject)
        )

    def _headroom(self, day: str, subject: str):
        """Minutes of this subject that may still be added to this day."""
        return self.max_per_day - self.daily_subject_totals[day][subject]

    def _is_locked(self, day: str, period: str) -> bool:
        """True when a period holds a pre-filled activity and must not move."""
        return any(period == p for p, _activity in self.pre_filled.get(day, []))

    def _real_subjects_in(self, day: str, period: str):
        """Subject names already occupying a period, excluding FREE/Break."""
        return {
            block.subject
            for block in self.schedule[day].get(period, [])
            if block.subject not in (FREE, BREAK)
        }

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def generate_schedule(self, max_attempts: int = 25):
        """Generate a schedule, retrying with different tie-breaks if needed.

        Fitting blocks into periods is a packing problem, so a single greedy
        pass can miss an arrangement that exists. When an attempt leaves a
        minimum unmet, the tie-breaking is re-rolled and the best result is
        kept. The sequence of attempts is deterministic, so the same config
        still produces the same schedule every time.
        """
        seed = self.config.get('random_seed')
        has_seed = seed not in (None, '')

        best_snapshot = None
        best_penalty = None

        for attempt in range(max(1, max_attempts)):
            self._reset_state()
            self._rng = random.Random(f"{seed if has_seed else 0}:{attempt}")
            # The first attempt stays jitter-free so an unseeded config keeps
            # its plain, reproducible result.
            self._jitter = 0.0 if (attempt == 0 and not has_seed) else 6.0

            self._run_phases()

            penalty = self._shortfall_penalty()
            if best_penalty is None or penalty < best_penalty:
                best_penalty = penalty
                best_snapshot = self._snapshot_state()
            if penalty == 0:
                return

        if best_snapshot is not None:
            self._restore_state(best_snapshot)

    def _run_phases(self):
        """Run the five scheduling phases once, in order."""
        self.initialize_schedule()

        # PHASE 1: Ensure daily minimums
        self._phase1_daily_minimums()

        # PHASE 2: Meet cycle minimums with consolidation
        self._phase2_cycle_minimums()

        # PHASE 3: Leave remaining time as FREE
        self._phase3_mark_remaining_free()

        # PHASE 4: Optimize small gaps
        self._phase4_optimize_gaps()

        # PHASE 5: Post-processing optimization
        self._phase5_optimize_placement()

    def _reset_state(self):
        """Clear the board so another attempt can start from scratch."""
        self.schedule = {day: {} for day in self.days}
        self.subject_totals = {subject: 0 for subject in self.subject_requirements}
        self.daily_subject_totals = {
            day: {subject: 0 for subject in self.subject_requirements}
            for day in self.days
        }

    def _shortfall_penalty(self) -> int:
        """Total minutes of unmet daily and cycle minimums (0 = fully met)."""
        penalty = 0
        for subject, req in self.subject_requirements.items():
            penalty += max(0, req['min_per_cycle'] - self.subject_totals[subject])
            for day in self.days:
                penalty += max(
                    0, req['min_per_day'] - self.daily_subject_totals[day][subject]
                )
        return penalty

    def _snapshot_state(self):
        """Capture the board and totals so a better attempt can be restored."""
        return (
            {
                day: {period: list(blocks) for period, blocks in periods.items()}
                for day, periods in self.schedule.items()
            },
            dict(self.subject_totals),
            {day: dict(totals) for day, totals in self.daily_subject_totals.items()},
        )

    def _restore_state(self, snapshot):
        schedule, subject_totals, daily_totals = snapshot
        self.schedule = schedule
        self.subject_totals = subject_totals
        self.daily_subject_totals = daily_totals

    def _phase1_daily_minimums(self):
        """Phase 1: Ensure daily minimums for every subject on every day.

        Daily minimums are honoured even when they add up to more than the
        cycle minimum - min_per_cycle is a floor, not a ceiling. The per-day
        cap (max_per_day) is respected here as a hard limit.
        """
        for day in self.days:
            for subject, req in self.subject_requirements.items():
                daily_need = req['min_per_day']
                if daily_need <= 0:
                    continue

                min_block = req['min_per_block']

                while True:
                    assigned_today = self.daily_subject_totals[day][subject]
                    if assigned_today >= daily_need:
                        break

                    headroom = self._headroom(day, subject)
                    if headroom < min_block:
                        break

                    still_needed = daily_need - assigned_today

                    # Pick the best-placed period rather than the first one that
                    # happens to have room, so small subjects spread out instead
                    # of piling into a single period.
                    best = None
                    best_score = float('-inf')

                    for period in self.teaching_periods:
                        remaining_in_period = self.get_period_remaining_time(day, period)
                        if remaining_in_period < min_block:
                            continue

                        to_assign = min(remaining_in_period, max(still_needed, min_block), headroom)
                        if to_assign < min_block:
                            continue

                        # Absorb a leftover too small for any subject to use.
                        leftover = remaining_in_period - to_assign
                        if 0 < leftover < self.min_block_overall and remaining_in_period <= headroom:
                            to_assign = remaining_in_period

                        score = self._placement_score(day, period, subject)
                        if self._jitter:
                            score += self._rng.uniform(0, self._jitter)

                        if score > best_score:
                            best_score = score
                            best = (period, to_assign)

                    if best is None:
                        break

                    period, to_assign = best
                    self.add_block_to_period(day, period, subject, to_assign)

    def _phase2_cycle_minimums(self):
        """Phase 2: Meet remaining cycle minimums, consolidating where possible."""
        period_preferences = {}
        for period in self.teaching_periods:
            subject_counts = {}
            for day in self.days:
                for block in self.schedule[day].get(period, []):
                    if block.subject in self.subject_requirements:
                        subject_counts[block.subject] = subject_counts.get(block.subject, 0) + 1
            if subject_counts:
                period_preferences[period] = max(subject_counts, key=lambda s: subject_counts[s])

        subjects_by_need = sorted(
            self.subject_requirements,
            key=lambda s: self.subject_requirements[s]['min_per_cycle'] - self.subject_totals[s],
            reverse=True,
        )

        for subject in subjects_by_need:
            req = self.subject_requirements[subject]
            min_block = req['min_per_block']

            while True:
                need = req['min_per_cycle'] - self.subject_totals[subject]
                if need <= 0:
                    break

                best = None
                best_score = float('-inf')

                for day in self.days:
                    headroom = self._headroom(day, subject)
                    if headroom < min_block:
                        continue

                    for period in self.teaching_periods:
                        remaining = self.get_period_remaining_time(day, period)
                        if remaining < min_block:
                            continue

                        to_assign = min(remaining, max(need, min_block), headroom)
                        if to_assign < min_block:
                            continue

                        # Absorb a leftover too small for any subject to use.
                        leftover = remaining - to_assign
                        if 0 < leftover < self.min_block_overall and remaining <= headroom:
                            to_assign = remaining

                        score = to_assign
                        score += self._placement_score(day, period, subject)
                        if period_preferences.get(period) == subject:
                            score += W_PERIOD_PREFERENCE
                        if self._jitter:
                            score += self._rng.uniform(0, self._jitter)

                        if score > best_score:
                            best_score = score
                            best = (day, period, to_assign)

                if best is None:
                    break

                day, period, to_assign = best
                self.add_block_to_period(day, period, subject, to_assign)

    def _placement_score(self, day: str, period: str, subject: str) -> int:
        """Score a candidate placement: consolidation, fragmentation, time of day."""
        score = 0

        occupants = self._real_subjects_in(day, period)
        if not occupants:
            score += W_EMPTY_PERIOD
        elif subject in occupants:
            score += W_SAME_SUBJECT_IN_PERIOD
        # Every extra subject sharing a period fragments the day.
        score -= W_FRAGMENTATION * len(occupants - {subject})

        # Same subject in an adjacent period keeps blocks contiguous.
        try:
            period_idx = self.teaching_periods.index(period)
        except ValueError:
            return score

        for offset in (-1, 1):
            adj_idx = period_idx + offset
            if 0 <= adj_idx < len(self.teaching_periods):
                adj_period = self.teaching_periods[adj_idx]
                if subject in self._real_subjects_in(day, adj_period):
                    score += W_ADJACENT_SAME_SUBJECT

        # Time-of-day preferences.
        if subject in self.morning_priority_subjects and period in self.morning_periods:
            score += W_TIME_OF_DAY
        if subject in self.afternoon_priority_subjects and period in self.afternoon_periods:
            score += W_TIME_OF_DAY
        if (
            self.morning_priority_subjects
            and subject not in self.morning_priority_subjects
            and period in self.morning_periods
        ):
            score -= W_TIME_OF_DAY_PENALTY

        return score

    def _phase3_mark_remaining_free(self):
        """Phase 3: Mark any remaining unassigned time as FREE."""
        for day in self.days:
            for period in self.teaching_periods:
                remaining = self.get_period_remaining_time(day, period)
                if remaining > 0:
                    if period not in self.schedule[day]:
                        self.schedule[day][period] = []
                    self.schedule[day][period].append(SubjectBlock(FREE, remaining))

    def _phase4_optimize_gaps(self):
        """Phase 4: Convert FREE gaps into subjects that are still short.

        Only takes the minutes actually needed (the rest stays FREE) and never
        pushes a subject past its per-day cap.
        """
        shortfall = {
            subject: req['min_per_cycle'] - self.subject_totals[subject]
            for subject, req in self.subject_requirements.items()
            if self.subject_totals[subject] < req['min_per_cycle']
        }
        if not shortfall:
            return

        for day in self.days:
            for period in self.teaching_periods:
                blocks = self.schedule[day].get(period)
                if not blocks:
                    continue

                rebuilt = []
                changed = False

                for block in blocks:
                    if block.subject != FREE or not shortfall:
                        rebuilt.append(block)
                        continue

                    placed = False
                    for subject in sorted(shortfall, key=lambda s: shortfall[s], reverse=True):
                        min_block = self.subject_requirements[subject]['min_per_block']
                        headroom = self._headroom(day, subject)
                        if headroom < min_block or block.minutes < min_block:
                            continue

                        take = min(block.minutes, max(shortfall[subject], min_block), headroom)
                        if take < min_block:
                            continue

                        leftover = block.minutes - take
                        if 0 < leftover < self.min_block_overall and block.minutes <= headroom:
                            take = block.minutes
                            leftover = 0

                        rebuilt.append(SubjectBlock(subject, take))
                        self.subject_totals[subject] += take
                        self.daily_subject_totals[day][subject] += take
                        shortfall[subject] -= take
                        if shortfall[subject] <= 0:
                            del shortfall[subject]
                        if leftover > 0:
                            rebuilt.append(SubjectBlock(FREE, leftover))

                        changed = True
                        placed = True
                        break

                    if not placed:
                        rebuilt.append(block)

                if changed:
                    self.schedule[day][period] = rebuilt

    def _phase5_optimize_placement(self):
        """Phase 5: Hill-climb swaps of period contents to improve placement."""
        max_iterations = 20

        for day in self.days:
            movable = [p for p in self.teaching_periods if not self._is_locked(day, p)]

            for _ in range(max_iterations):
                current_score = self._calculate_day_quality_score(day)
                best_swap = None
                best_improvement = 0

                for i, period1 in enumerate(movable):
                    for period2 in movable[i + 1:]:
                        if not self._can_swap_periods(day, period1, period2):
                            continue

                        snapshot = {
                            period1: list(self.schedule[day].get(period1, [])),
                            period2: list(self.schedule[day].get(period2, [])),
                        }
                        self._swap_periods(day, period1, period2)
                        improvement = self._calculate_day_quality_score(day) - current_score
                        for period, blocks in snapshot.items():
                            self.schedule[day][period] = blocks

                        if improvement > best_improvement:
                            best_improvement = improvement
                            best_swap = (period1, period2)

                if not best_swap:
                    break

                self._swap_periods(day, *best_swap)

    def _committed_minutes(self, day: str, period: str) -> int:
        """Minutes in a period that hold real content (FREE padding excluded)."""
        return sum(
            block.minutes
            for block in self.schedule[day].get(period, [])
            if block.subject != FREE
        )

    def _can_swap_periods(self, day: str, period1: str, period2: str) -> bool:
        """Check whether each period's real content fits in the other period.

        FREE padding is ignored, so periods of different lengths can still be
        swapped as long as the scheduled work fits.
        """
        committed1 = self._committed_minutes(day, period1)
        committed2 = self._committed_minutes(day, period2)
        if committed1 == 0 and committed2 == 0:
            return False

        return (
            committed1 <= self.periods[period2][2]
            and committed2 <= self.periods[period1][2]
        )

    def _swap_periods(self, day: str, period1: str, period2: str):
        """Swap the contents of two periods and re-pad each with FREE time."""
        self.schedule[day][period1], self.schedule[day][period2] = \
            self.schedule[day].get(period2, []), self.schedule[day].get(period1, [])
        self._renormalize_free(day, period1)
        self._renormalize_free(day, period2)

    def _renormalize_free(self, day: str, period: str):
        """Rebuild a period's FREE padding so its blocks match its duration."""
        blocks = [b for b in self.schedule[day].get(period, []) if b.subject != FREE]
        used = sum(block.minutes for block in blocks)
        spare = self.periods[period][2] - used
        if spare > 0:
            blocks.append(SubjectBlock(FREE, spare))
        self.schedule[day][period] = blocks

    def _calculate_day_quality_score(self, day: str) -> int:
        """Calculate overall quality score for a day's schedule."""
        score = 0

        for period_idx, period in enumerate(self.teaching_periods):
            if period not in self.schedule[day]:
                continue

            for block in self.schedule[day][period]:
                if block.subject not in self.subject_requirements:
                    continue

                if block.subject in self.morning_priority_subjects and period in self.morning_periods:
                    score += 500

                if block.subject in self.afternoon_priority_subjects and period in self.afternoon_periods:
                    score += 300

                if (
                    self.morning_priority_subjects
                    and block.subject not in self.morning_priority_subjects
                    and period in self.morning_periods
                ):
                    score -= 200

                for offset in (-1, 1):
                    adj_idx = period_idx + offset
                    if 0 <= adj_idx < len(self.teaching_periods):
                        adj_period = self.teaching_periods[adj_idx]
                        if block.subject in self._real_subjects_in(day, adj_period):
                            score += 100
                            break

        return score

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def export_to_csv_string(self) -> str:
        """Export schedule to CSV string."""
        output = StringIO()
        writer = csv.writer(output)

        header = ['Period']
        header.extend(self.days)
        writer.writerow(header)

        for period in self.periods:
            start_time, end_time, _duration = self.periods[period]
            period_name = f"{period.replace('_', ' ').title()}\n{start_time}-{end_time}"

            row = [period_name]

            for day in self.days:
                blocks = self.schedule[day].get(period)
                if not blocks:
                    row.append(BREAK if period in self.ignored_periods else FREE)
                elif len(blocks) == 1:
                    row.append(blocks[0].subject)
                else:
                    # Consolidate duplicate subjects within the same period/day cell.
                    # Example: Science(10min) + Science(10min) => Science(20min).
                    totals = {}
                    subject_order = []
                    for block in blocks:
                        if block.subject not in totals:
                            totals[block.subject] = 0
                            subject_order.append(block.subject)
                        totals[block.subject] += block.minutes

                    if len(subject_order) == 1:
                        row.append(subject_order[0])
                    else:
                        row.append(' / '.join(
                            f"{subject}({totals[subject]}min)" for subject in subject_order
                        ))

            writer.writerow(row)

        return output.getvalue()

    def get_summary(self) -> dict:
        """Get summary statistics, including anything left unmet."""
        total_available = sum(
            self.periods[p][2] for p in self.teaching_periods
        ) * len(self.days)
        for day in self.days:
            for period, _activity in self.pre_filled.get(day, []):
                total_available -= self.periods[period][2]

        total_required = sum(
            req['min_per_cycle'] for req in self.subject_requirements.values()
        )
        total_assigned = sum(self.subject_totals.values())

        total_free = 0
        for day in self.days:
            for _period, blocks in self.schedule[day].items():
                for block in blocks:
                    if block.subject == FREE:
                        total_free += block.minutes

        unmet_cycle = [
            {
                'subject': subject,
                'assigned': self.subject_totals[subject],
                'required': req['min_per_cycle'],
                'short_by': req['min_per_cycle'] - self.subject_totals[subject],
            }
            for subject, req in self.subject_requirements.items()
            if self.subject_totals[subject] < req['min_per_cycle']
        ]

        unmet_daily = [
            {
                'day': day,
                'subject': subject,
                'assigned': self.daily_subject_totals[day][subject],
                'required': req['min_per_day'],
                'short_by': req['min_per_day'] - self.daily_subject_totals[day][subject],
            }
            for day in self.days
            for subject, req in self.subject_requirements.items()
            if self.daily_subject_totals[day][subject] < req['min_per_day']
        ]

        over_cap = [
            {
                'day': day,
                'subject': subject,
                'assigned': self.daily_subject_totals[day][subject],
                'cap': int(self.max_per_day),
            }
            for day in self.days
            for subject in self.subject_requirements
            if self.max_per_day != float('inf')
            and self.daily_subject_totals[day][subject] > self.max_per_day
        ]

        return {
            'total_available': total_available,
            'total_required': total_required,
            'total_assigned': total_assigned,
            'total_free': total_free,
            'subject_totals': self.subject_totals,
            'daily_totals': self.daily_subject_totals,
            'requirements': self.subject_requirements,
            'warnings': list(self.warnings),
            'unmet_cycle': unmet_cycle,
            'unmet_daily': unmet_daily,
            'over_cap': over_cap,
        }
