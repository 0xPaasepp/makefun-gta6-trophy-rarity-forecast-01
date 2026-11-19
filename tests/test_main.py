"""Tests for main.py, run as a subprocess only (never imported).

Checks: output shape on the practice cases, aggregate diagnostics
against the brief's published first-refresh totals, trend behaviour on
a synthetic rising case, null-rate and single-read edges, and
determinism under the grader's own invocation.
"""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN = REPO_ROOT / "main.py"
KIT_INPUT = (
    REPO_ROOT.parent
    / "kit"
    / "gta6-trophy-rarity-forecast-01-kit"
    / "input"
)
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
DIAG_RE = re.compile(
    r"(\S+)\s+forecast_players=([0-9.]+)\s+implied_total_earners=([0-9.]+)"
)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL") + " " + name + (" — " + detail if detail else ""))


def run_program(input_dir, output_dir, cwd=None, grader=False):
    """Run main.py as a subprocess; returns (returncode, stderr, seconds)."""
    if grader:
        cmd = ["python3", "-P", "-s", str(MAIN)]
    else:
        cmd = [sys.executable, str(MAIN)]
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = str(input_dir)
    env["MAKEFUN_OUTPUT_DIR"] = str(output_dir)
    start = time.perf_counter()
    proc = subprocess.run(
        cmd, env=env, cwd=cwd, capture_output=True, text=True, timeout=30
    )
    return proc.returncode, proc.stderr, time.perf_counter() - start


def parse_diagnostics(stderr):
    found = {}
    for match in DIAG_RE.finditer(stderr):
        found[match.group(1)] = (
            float(match.group(2)),
            float(match.group(3)),
        )
    return found


def check_shape(out_path, expected_rows=55):
    """Validate one output file's shape; returns (ok, detail)."""
    raw = out_path.read_bytes()
    if len(raw) > 65536:
        return False, "over 65536 bytes: %d" % len(raw)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return False, "bad JSON: %s" % exc
    if set(data.keys()) != {"trophies"}:
        return False, "top-level keys %s" % sorted(data.keys())
    rows = data["trophies"]
    if len(rows) != expected_rows:
        return False, "expected %d rows, got %d" % (expected_rows, len(rows))
    seen = set()
    for row in rows:
        if set(row.keys()) != {"id", "q"}:
            return False, "row keys %s" % sorted(row.keys())
        if row["id"] in seen:
            return False, "duplicate id %s" % row["id"]
        seen.add(row["id"])
        q = row["q"]
        if len(q) != 5:
            return False, "id %s has %d quantiles" % (row["id"], len(q))
        for value in q:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False, "id %s non-numeric quantile" % row["id"]
            if not math.isfinite(value) or not 0.0 <= value <= 100.0:
                return False, "id %s quantile out of range: %r" % (row["id"], value)
        if any(b < a for a, b in zip(q, q[1:])):
            return False, "id %s quantiles decrease: %r" % (row["id"], q)
    return True, "%d rows, %d bytes" % (len(rows), len(raw))


def make_case(case_id, checkpoint, target, horizon, trophies, history):
    return {
        "schema": "makefun/gta6-trophies-case/v1",
        "id": case_id,
        "at": checkpoint,
        "horizon_minutes": horizon,
        "target": target,
        "levels": LEVELS,
        "game": {"title": "Grand Theft Auto VI"},
        "trophies": trophies,
        "history": history,
        "notes": "synthetic test case",
    }


def trophy(tid, tier, points):
    return {
        "id": tid,
        "name": "Trophy %d" % tid,
        "description": "Synthetic trophy",
        "tier": tier,
        "points": points,
        "hidden": False,
    }


def read_entry(history, at, players, earned, rates):
    return {
        "slot": len(history),
        "at": at,
        "players_tracked": players,
        "trophies_earned": earned,
        "rates": rates,
    }


def trending_case(case_id, checkpoint, target, horizon):
    trophies = [
        trophy(1, "platinum", 300),
        trophy(2, "gold", 90),
        trophy(3, "silver", 30),
        trophy(4, "bronze", 15),
    ]
    times = [
        "2026-11-19T00:00:00Z",
        "2026-11-19T06:00:00Z",
        "2026-11-19T12:00:00Z",
        "2026-11-19T18:00:00Z",
        "2026-11-20T00:00:00Z",
    ]
    players = [100, 200, 400, 800, 1600]
    rates = [
        [1.0, 0.5, 2.0, 5.0],
        [2.0, 1.0, 4.0, 8.0],
        [4.0, 2.0, 8.0, 12.0],
        [8.0, 4.0, 12.0, 18.0],
        [16.0, 8.0, 18.0, 25.0],
    ]
    history = []
    for i, moment in enumerate(times):
        earned = sum(r * players[i] / 100.0 for r in rates[i])
        history.append(read_entry(history, moment, players[i], earned, rates[i]))
    return make_case(case_id, checkpoint, target, horizon, trophies, history)


def main():
    if not KIT_INPUT.is_dir():
        print("FAIL kit input directory missing: %s" % KIT_INPUT)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out_dir = tmp / "out"
        rc, stderr, elapsed = run_program(KIT_INPUT, out_dir)
        check("practice run exits 0", rc == 0, "rc=%d stderr=%s" % (rc, stderr.strip()[:200]))
        check("practice run fast", elapsed < 8.0, "%.2fs" % elapsed)
        diag = parse_diagnostics(stderr)

        for case_id in ("p00", "p01"):
            out_path = out_dir / (case_id + ".out")
            ok = out_path.is_file()
            check("shape %s exists" % case_id, ok)
            if ok:
                ok, detail = check_shape(out_path, 55)
                check("shape %s" % case_id, ok, detail)
            if case_id in diag:
                players, earners = diag[case_id]
                check(
                    "aggregate %s players ~3868" % case_id,
                    3868 * 0.5 <= players <= 3868 * 1.5,
                    "forecast_players=%.1f" % players,
                )
                check(
                    "aggregate %s earners ~4276" % case_id,
                    4276 * 0.75 <= earners <= 4276 * 1.25,
                    "implied_total_earners=%.1f" % earners,
                )
            else:
                check("aggregate %s diagnostic present" % case_id, False, stderr[:200])

        # Trend: rising history should give medians above the last rates,
        # and a longer horizon should widen the q05-q95 spread.
        trend_dir = tmp / "trend_in"
        trend_dir.mkdir()
        short_case = trending_case(
            "t-short", "2026-11-20T00:00:00Z", "2026-11-20T06:00:00Z", 360
        )
        long_case = trending_case(
            "t-long", "2026-11-20T00:00:00Z", "2026-11-22T00:00:00Z", 2880
        )
        (trend_dir / "t-short.in").write_text(json.dumps(short_case))
        (trend_dir / "t-long.in").write_text(json.dumps(long_case))
        trend_out = tmp / "trend_out"
        rc, stderr, _elapsed = run_program(trend_dir, trend_out)
        check("trend run exits 0", rc == 0, stderr.strip()[:200])
        medians = {}
        spreads = {}
        for case_id in ("t-short", "t-long"):
            out_path = trend_out / (case_id + ".out")
            ok, detail = check_shape(out_path, 4)
            check("trend shape %s" % case_id, ok, detail)
            if ok:
                data = json.loads(out_path.read_text())
                for row in data["trophies"]:
                    medians[(case_id, row["id"])] = row["q"][2]
                    spreads[(case_id, row["id"])] = row["q"][4] - row["q"][0]
        last_rates = {1: 16.0, 2: 8.0, 3: 18.0, 4: 25.0}
        rising = all(
            medians.get(("t-long", tid), 0.0) > last_rates[tid]
            for tid in last_rates
        )
        check("trend medians rise above last rates", rising, str(medians))
        wider = all(
            spreads.get(("t-long", tid), 0.0) > spreads.get(("t-short", tid), 0.0)
            for tid in last_rates
        )
        check("trend spread widens with horizon", wider, str(spreads))
        trend_diag = parse_diagnostics(stderr)
        for case_id in ("t-short", "t-long"):
            if case_id in trend_diag:
                check(
                    "trend %s players never below last tracked" % case_id,
                    trend_diag[case_id][0] >= 1600.0,
                    "forecast_players=%.1f" % trend_diag[case_id][0],
                )
            else:
                check("trend %s diagnostic present" % case_id, False)

        # The 100% trophy stays at essentially 100% after dilution.
        p00_data = json.loads((out_dir / "p00.out").read_text())
        top_row = next(row for row in p00_data["trophies"] if row["id"] == 2)
        check("100% trophy median stays at top", top_row["q"][2] == 99.9493,
              "median=%r" % top_row["q"][2])

        # Nulls and edges: a null rate in one read, and a single read.
        edge_dir = tmp / "edge_in"
        edge_dir.mkdir()
        trophies = [trophy(1, "bronze", 15), trophy(2, "silver", 30)]
        history = [
            read_entry([], "2026-11-19T00:00:00Z", 500, 10, [2.0, 1.0]),
            read_entry([], "2026-11-19T06:00:00Z", 500, 10, [None, 1.0]),
            read_entry([], "2026-11-19T12:00:00Z", 500, 10, [2.0, 1.0]),
        ]
        null_case = make_case(
            "e-null", "2026-11-19T12:00:00Z", "2026-11-19T13:30:00Z", 90,
            trophies, history,
        )
        single_case = make_case(
            "e-single", "2026-11-19T00:00:00Z", "2026-11-19T01:30:00Z", 90,
            trophies,
            [read_entry([], "2026-11-19T00:00:00Z", 500, 10, [2.0, 1.0])],
        )
        (edge_dir / "e-null.in").write_text(json.dumps(null_case))
        (edge_dir / "e-single.in").write_text(json.dumps(single_case))
        edge_out = tmp / "edge_out"
        rc, stderr, _elapsed = run_program(edge_dir, edge_out)
        check("nulls/edges run exits 0", rc == 0, stderr.strip()[:200])
        for case_id in ("e-null", "e-single"):
            out_path = edge_out / (case_id + ".out")
            ok, detail = check_shape(out_path, 2)
            check("nulls/edges shape %s" % case_id, ok, detail)

        # Malformed cases must fail loudly, not silently emit a forecast.
        bad_dir = tmp / "bad_in"
        bad_dir.mkdir()
        bad_levels = trending_case(
            "b-levels", "2026-11-20T00:00:00Z", "2026-11-20T06:00:00Z", 360
        )
        bad_levels["levels"] = [0.1, 0.25, 0.5, 0.75, 0.95]
        (bad_dir / "b-levels.in").write_text(json.dumps(bad_levels))
        no_history = trending_case(
            "b-empty", "2026-11-20T00:00:00Z", "2026-11-20T06:00:00Z", 360
        )
        no_history["history"] = []
        (bad_dir / "b-empty.in").write_text(json.dumps(no_history))
        bad_out = tmp / "bad_out"
        rc, stderr, _elapsed = run_program(bad_dir, bad_out)
        check("malformed cases exit non-zero", rc != 0, "rc=%d" % rc)
        check("malformed cases write no output",
              not list(bad_out.glob("*.out")) if bad_out.is_dir() else True)

        # Determinism + grader reproduction, from a different cwd.
        other_cwd = tmp / "elsewhere"
        other_cwd.mkdir()
        out_a = tmp / "grade_a"
        out_b = tmp / "grade_b"
        rc_a, _err_a, time_a = run_program(
            KIT_INPUT, out_a, cwd=other_cwd, grader=True
        )
        rc_b, _err_b, time_b = run_program(
            KIT_INPUT, out_b, cwd=other_cwd, grader=True
        )
        check("grader run exits 0", rc_a == 0 and rc_b == 0,
              "rc_a=%d rc_b=%d" % (rc_a, rc_b))
        check("grader runs fast", time_a < 8.0 and time_b < 8.0,
              "%.2fs / %.2fs" % (time_a, time_b))
        identical = True
        for case_id in ("p00", "p01"):
            a = out_a / (case_id + ".out")
            b = out_b / (case_id + ".out")
            if not (a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()):
                identical = False
        check("grader runs byte-identical", identical)

    failed = [name for name, ok, _d in RESULTS if not ok]
    print("---")
    print("%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
