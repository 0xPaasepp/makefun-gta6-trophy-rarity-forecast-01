"""Forecast how many GTA VI players earn each trophy in week one.

Reads every ``*.in`` case in the input directory and writes one ``*.out``
forecast per case to the output directory.

Method, in brief: model earners and players separately and divide.
Players tracked are extrapolated on the log scale against log time; each
trophy's earners are extrapolated on the logit scale, with the slope shrunk
toward its tier mean. When the history is flat (launch night, no refresh
seen yet) two priors taken from the totals the competition brief itself
publishes are used instead: the first refresh multiplies the players
tracked by about 6.06, and the new cohort earns at a rate damped by a
progress factor calibrated on the brief's published aggregate totals
(3,868 players tracked and 4,276 trophies earned at the first refresh).
No per-trophy answer from after any case's checkpoint is used anywhere.

Pure standard library, deterministic, no network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

# Hours are counted from the start of release day, 19 Nov 2026 00:00 UTC.
EPOCH = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
NORMAL_Z = [-1.645, -0.674, 0.0, 0.674, 1.645]
MAX_OUTPUT_BYTES = 65536
# Prior from the brief's published first refresh: players tracked jump.
FLAT_REFRESH_MULTIPLIER = 6.06
# Progress constant calibrated on the brief's published aggregate totals
# (3,868 players, 4,276 trophies earned at the first refresh): with it the
# implied new-cohort earners total about 3,329 against the published
# 3,333, and the trophy at 100% stays at essentially 100%.
DILUTION_SCALE = 13.5


def fail(message):
    """Fail loudly on a malformed case."""
    print("error: " + message, file=sys.stderr)
    raise SystemExit(1)


def parse_time(text):
    """Parse an ISO-8601 timestamp, accepting a trailing Z."""
    if not isinstance(text, str):
        fail("timestamp is not a string: %r" % (text,))
    cleaned = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        moment = datetime.fromisoformat(cleaned)
    except ValueError:
        fail("unparseable timestamp: %r" % (text,))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def hours_since_epoch(moment):
    """Hours elapsed since the start of release day."""
    return (moment - EPOCH).total_seconds() / 3600.0


def log_time(moment):
    """Time coordinate: natural log of one plus hours since release day."""
    hours = hours_since_epoch(moment)
    return math.log(max(1.0 + hours, 0.01))


def clamp(value, low, high):
    return low if value < low else high if value > high else value


def logit_of_percent(rate):
    """Logit of a percent, clamped into [0.01, 99.99] first."""
    clipped = clamp(rate, 0.01, 99.99)
    frac = clipped / 100.0
    return math.log(frac / (1.0 - frac))


def inv_logit_percent(logit_value):
    """Inverse logit, back to a percent in (0, 100)."""
    clipped = clamp(logit_value, -30.0, 30.0)
    if clipped >= 0.0:
        frac = 1.0 / (1.0 + math.exp(-clipped))
    else:
        exp_value = math.exp(clipped)
        frac = exp_value / (1.0 + exp_value)
    return 100.0 * frac


def least_squares(xs, ys):
    """Ordinary least squares line; returns (slope, intercept)."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0.0:
        return 0.0, mean_y
    numer = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = numer / denom
    return slope, mean_y - slope * mean_x


def median_of(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def round_half_up_4dp(value):
    """Round to 4 decimals, half up; whole numbers come back as int."""
    rounded = Decimal(str(value)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def distinct_snapshots(history):
    """Collapse the reads to one snapshot per distinct page state."""
    kept = []
    for read in history:
        players = read.get("players_tracked")
        rates = read.get("rates")
        if kept:
            last = kept[-1]
            if players == last.get("players_tracked") and rates == last.get("rates"):
                continue
        kept.append(read)
    return kept


def forecast_players(snapshots, target, horizon_minutes):
    """Forecast players tracked at the target; also returns lag minutes."""
    if len(snapshots) >= 2:
        gaps = []
        for prev, cur in zip(snapshots, snapshots[1:]):
            gap = (
                parse_time(cur["at"]) - parse_time(prev["at"])
            ).total_seconds() / 60.0
            gaps.append(gap)
        lag = min(0.5 * median_of(gaps), 120.0) if gaps else 0.0
    else:
        lag = 0.0

    player_counts = [snap["players_tracked"] for snap in snapshots]
    last_count = player_counts[-1]
    if any(count <= 0 for count in player_counts):
        fail("players_tracked must be positive, got %r" % (player_counts,))
    if len(set(player_counts)) >= 3:
        xs = [log_time(parse_time(snap["at"])) for snap in snapshots]
        ys = [math.log(count) for count in player_counts]
        slope, _intercept = least_squares(xs, ys)
        slope = clamp(slope, 0.0, 2.0)
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        intercept = mean_y - slope * mean_x
        target_lag = target - timedelta(minutes=lag)
        forecast = math.exp(intercept + slope * log_time(target_lag))
        # Players tracked never falls: the fitted line can sit below the
        # last observed count when growth is faster than log-linear, so
        # never forecast fewer players than are already tracked.
        return max(forecast, float(last_count)), lag

    # Flat history: no usable trend. Prior from the brief's published
    # first refresh (see module docstring): one refresh is expected
    # before a horizon of at least an hour, multiplying players by 6.06.
    if horizon_minutes >= 60:
        return float(last_count) * FLAT_REFRESH_MULTIPLIER, lag
    return float(last_count), lag


def trophy_series(snapshots, index):
    """Carried-forward (time, rate) series for one trophy.

    Nulls are skipped, and a null after a known rate carries that rate
    forward. A trophy never observed gets an empty series.
    """
    series = []
    last_rate = None
    for snap in snapshots:
        rates = snap.get("rates") or []
        rate = rates[index] if index < len(rates) else None
        if rate is not None:
            last_rate = rate
        if last_rate is not None:
            series.append((log_time(parse_time(snap["at"])), last_rate))
    return series


def forecast_case(case):
    """Forecast one case; returns (rows, forecast_players, implied earners)."""
    levels = case.get("levels")
    if levels != LEVELS:
        fail("case %s has unexpected levels: %r" % (case.get("id"), levels))
    history = case.get("history")
    if not history:
        fail("case %s has no history" % (case.get("id"),))
    trophies = case.get("trophies") or []
    if not trophies:
        fail("case %s has no trophies" % (case.get("id"),))

    target = parse_time(case["target"])
    horizon = case.get("horizon_minutes")
    if horizon is None:
        checkpoint = parse_time(case["at"])
        horizon = (target - checkpoint).total_seconds() / 60.0
    horizon = float(horizon)

    snapshots = distinct_snapshots(history)
    forecast_n, lag = forecast_players(snapshots, target, horizon)
    if forecast_n <= 0:
        fail("case %s forecast non-positive players" % (case.get("id"),))

    target_lag = target - timedelta(minutes=lag)
    x_target = log_time(target_lag)
    last_players = snapshots[-1]["players_tracked"]

    # First pass: raw logit-trend fits, for the tier-mean slopes.
    raw_fits = []
    for idx in range(len(trophies)):
        series = trophy_series(snapshots, idx)
        distinct_rates = {rate for _x, rate in series}
        if len(distinct_rates) >= 3 and len(series) >= 3:
            xs = [x for x, _r in series]
            ys = [logit_of_percent(r) for _x, r in series]
            slope, intercept = least_squares(xs, ys)
            raw_fits.append((series, slope, intercept))
        else:
            raw_fits.append((series, None, None))

    tier_slopes = {}
    all_slopes = []
    for trophy, (_series, slope, _intercept) in zip(trophies, raw_fits):
        if slope is not None:
            tier_slopes.setdefault(trophy.get("tier"), []).append(slope)
            all_slopes.append(slope)
    tier_mean = {
        tier: sum(vals) / len(vals) for tier, vals in tier_slopes.items()
    }
    global_mean = sum(all_slopes) / len(all_slopes) if all_slopes else 0.0

    # Refresh part of the spread: observed refreshes use the lag; a flat
    # history with a refresh expected covers both outcomes with 0.9.
    if len(snapshots) >= 2:
        refresh_sd = 0.5 * math.sqrt(max(lag, 0.0) / 1440.0)
    elif horizon >= 60:
        refresh_sd = 0.9
    else:
        refresh_sd = 0.0
    walk_sd = 0.5 * math.sqrt(horizon / 1440.0)

    rows = []
    implied_total = 0.0
    for trophy, (series, slope, _intercept) in zip(trophies, raw_fits):
        if series:
            last_rate = series[-1][1]
        else:
            last_rate = 0.01
        last_earners = last_rate * float(last_players) / 100.0

        if slope is not None:
            # Shrink the slope toward the tier mean; more points means
            # more weight on the trophy's own fit.
            n_points = len(series)
            weight = n_points / (n_points + 3.0)
            tier = trophy.get("tier")
            mean_slope = tier_mean.get(tier, global_mean)
            shrunk_slope = weight * slope + (1.0 - weight) * mean_slope
            xs = [x for x, _r in series]
            ys = [logit_of_percent(r) for _x, r in series]
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys) / len(ys)
            shrunk_intercept = mean_y - shrunk_slope * mean_x
            median_logit = shrunk_intercept + shrunk_slope * x_target
            median = clamp(inv_logit_percent(median_logit), 0.05, 99.99)
        else:
            # Dilution prior (see module docstring): old earners stay,
            # the new cohort earns at the old rate damped by the
            # progress factor 1 - exp(-rate / 13.5).
            old_earners = last_rate * float(last_players) / 100.0
            new_players = max(0.0, forecast_n - float(last_players))
            progress = 1.0 - math.exp(-last_rate / DILUTION_SCALE)
            new_rate = last_rate * progress
            new_earners = new_players * new_rate / 100.0
            forecast_earners = old_earners + new_earners
            median = clamp(
                100.0 * forecast_earners / forecast_n, 0.05, 99.99
            )

        implied_total += median / 100.0 * forecast_n

        count_sd = min(0.5, 1.0 / math.sqrt(last_earners + 1.0))
        total_sd = math.sqrt(
            walk_sd ** 2 + refresh_sd ** 2 + count_sd ** 2
        )
        median_logit = logit_of_percent(median)
        quantiles = []
        running_max = None
        for z in NORMAL_Z:
            value = inv_logit_percent(median_logit + z * total_sd)
            value = round_half_up_4dp(value)
            value = clamp(value, 0.01, 99.99)
            if running_max is None or value > running_max:
                running_max = value
            else:
                value = running_max
            quantiles.append(value)
        rows.append({"id": trophy["id"], "q": quantiles})

    return rows, forecast_n, implied_total


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    try:
        names = sorted(
            name for name in os.listdir(input_dir) if name.endswith(".in")
        )
    except OSError as exc:
        fail("cannot list input directory %s: %s" % (input_dir, exc))
    if not names:
        fail("no case files in input directory %s" % (input_dir,))

    os.makedirs(output_dir, exist_ok=True)

    for name in names:
        case_id = name[: -len(".in")]
        in_path = os.path.join(input_dir, name)
        try:
            with open(in_path, "r", encoding="utf-8") as handle:
                case = json.load(handle)
        except (OSError, ValueError) as exc:
            fail("cannot read case %s: %s" % (in_path, exc))
        if not isinstance(case, dict):
            fail("case %s is not a JSON object" % (in_path,))

        rows, forecast_n, implied_total = forecast_case(case)
        payload = {"trophies": rows}
        text = json.dumps(payload, separators=(",", ":")) + "\n"
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            fail(
                "case %s output %d bytes exceeds %d"
                % (case_id, len(encoded), MAX_OUTPUT_BYTES)
            )
        out_path = os.path.join(output_dir, case_id + ".out")
        if os.path.islink(out_path):
            os.unlink(out_path)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(
            "%s forecast_players=%.2f implied_total_earners=%.2f"
            % (case.get("id", case_id), forecast_n, implied_total),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
