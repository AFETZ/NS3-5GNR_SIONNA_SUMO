#!/usr/bin/env python3
"""Aggregate the locked 14-power, 10-block emergency-warning cohort.

The replication unit is a completed power/block run.  Packet and receiver
records are retained for audit and descriptive Kaplan--Meier summaries, but
they never form the resampling unit for confidence intervals.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import emergency_campaign as emergency


POWERS = (-20, -15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15, 18, 20)
BLOCKS = tuple(range(1, 11))
HORIZON_S = 100.0
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20260917


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_dir(runs_root: Path, power: int, block: int) -> Path:
    return runs_root / "production" / f"power-{power:+03d}dBm" / f"block-{block:02d}"


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"invalid SHA-256 for {label}")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"invalid SHA-256 for {label}") from exc
    return value.lower()


def _record_path(run: Path, name: object) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError(f"invalid recorded artifact name in {run}")
    direct, artifact = run / name, run / "artifacts" / name
    if direct.is_file() == artifact.is_file():
        raise ValueError(f"recorded artifact is missing or ambiguous: {name} in {run}")
    return direct if direct.is_file() else artifact


def _manifest(run: Path, power: int, block: int) -> tuple[dict, dict[str, str]]:
    path = run / "manifest.json"
    if not path.is_file():
        raise ValueError(f"missing manifest: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "completed_pending_metric_audit":
        raise ValueError(f"run is not ready for metric audit: {path}")
    if data.get("pilot_excluded") is not False or data.get("campaign") != "emergency_warning_power":
        raise ValueError(f"run is not a qualifying emergency production run: {path}")
    if data.get("power_dbm") != power or data.get("block") != block:
        raise ValueError(f"manifest cell does not match its directory: {path}")
    if data.get("planned_horizon_seconds") != HORIZON_S or data.get("sionna_enabled") is not False:
        raise ValueError(f"unexpected locked emergency configuration: {path}")
    if data.get("simulator_exit_code") != 0:
        raise ValueError(f"nonzero simulator exit recorded: {path}")
    inputs = data.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError(f"missing input hashes: {path}")
    checked_inputs = {str(name): _validate_hash(value, f"input {name}") for name, value in inputs.items()}
    records = data.get("file_records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"missing artifact hash records: {path}")
    names = set()
    for record in records:
        if not isinstance(record, dict) or "name" not in record or "sha256" not in record or "bytes" not in record:
            raise ValueError(f"invalid artifact hash record: {path}")
        name = record["name"]
        if name in names:
            raise ValueError(f"duplicate artifact hash record {name!r}: {path}")
        names.add(name)
        artifact = _record_path(run, name)
        if artifact.stat().st_size != int(record["bytes"]):
            raise ValueError(f"artifact size mismatch: {artifact}")
        if _digest(artifact) != _validate_hash(record["sha256"], f"artifact {name}"):
            raise ValueError(f"artifact hash mismatch: {artifact}")
    required = {"simulator.log", "eva-netstate.xml", "eva-collision.xml", "eva-veh2-MSG.csv"}
    if not required.issubset(names):
        raise ValueError(f"manifest lacks required audited artifacts: {path}")
    return data, checked_inputs


def _finite_fraction(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"invalid {label}: {value!r}")
    return result


def _km_p90(receivers: list[dict], eligibility_relative: bool) -> dict:
    """Kaplan--Meier P90 of first CAM arrival, with documented time origin."""
    observations = []
    never_eligible = 0
    for row in receivers:
        eligible = row.get("first_eligible_tx_s")
        observed = row.get("first_rx_observed")
        if eligible is None:
            never_eligible += 1
            continue
        eligible = float(eligible)
        if not math.isfinite(eligible) or eligible < 0 or eligible > HORIZON_S:
            raise ValueError("invalid first-eligibility time")
        if observed is True:
            arrival = row.get("first_eligible_rx_s")
            event_time = arrival
            if event_time is None:
                raise ValueError("observed first reception has no event time")
            event_time = float(event_time)
            if not math.isfinite(event_time) or event_time < eligible or event_time > HORIZON_S:
                raise ValueError("invalid first-reception event time")
            if eligibility_relative:
                reported = row.get("first_rx_delay_from_eligibility_s")
                event_time -= eligible
                if reported is not None and abs(float(reported) - event_time) > 1e-7:
                    raise ValueError("inconsistent first-reception delay")
            observations.append((event_time, True))
        elif row.get("first_rx_censored") is True:
            censor_time = row.get("first_rx_censor_s")
            if censor_time is None:
                raise ValueError("censored first reception has no censor time")
            censor_time = float(censor_time)
            if not math.isfinite(censor_time) or censor_time < eligible or censor_time > HORIZON_S:
                raise ValueError("invalid first-reception censor time")
            if eligibility_relative:
                censor_time -= eligible
            observations.append((censor_time, False))
        else:
            raise ValueError("eligible receiver lacks first-reception outcome")
    at_risk, survival, p90 = len(observations), 1.0, None
    grouped: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for time_s, event in observations:
        grouped[time_s][0 if event else 1] += 1
    for time_s in sorted(grouped):
        events, censored = grouped[time_s]
        if events:
            survival *= 1 - events / at_risk
            if p90 is None and survival <= 0.10 + 1e-12:
                p90 = time_s
        at_risk -= events + censored
    events = sum(event for _, event in observations)
    return {"time_origin": "delay_from_first_eligible_tx_s" if eligibility_relative else "scenario_start_s",
            "quantile": 0.90, "p90_s": p90,
            "p90_estimable": p90 is not None, "eligible_receivers": len(observations),
            "events": events, "right_censored": len(observations) - events,
            "never_eligible": never_eligible,
            "method": "Kaplan-Meier; receiver records are descriptive and are not resampling units"}


def _bootstrap_km_p90(cells: dict[int, dict], seed_offset: int) -> dict:
    """Bootstrap the primary P90 by resampling complete run/seed blocks only."""
    rng = random.Random(BOOTSTRAP_SEED + seed_offset)
    estimates = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled_receivers = [receiver for _ in BLOCKS for receiver in cells[rng.choice(BLOCKS)]["receivers"]]
        p90 = _km_p90(sampled_receivers, eligibility_relative=True)["p90_s"]
        if p90 is not None:
            estimates.append(p90)
    fraction = len(estimates) / BOOTSTRAP_DRAWS
    result = {"resampling_unit": "complete seed block/run", "bootstrap_draws": BOOTSTRAP_DRAWS,
              "bootstrap_seed": BOOTSTRAP_SEED + seed_offset, "estimable_draw_fraction": fraction,
              "non_estimable_draws": BOOTSTRAP_DRAWS - len(estimates)}
    if fraction < .975:
        result.update(ci95_p90_s=None, ci95_estimable=False,
                      reason="P90 not estimable in more than 2.5% of whole-block bootstrap draws")
    else:
        estimates.sort()
        result.update(ci95_p90_s=[_percentile(estimates, .025), _percentile(estimates, .975)],
                      ci95_estimable=True)
    return result


def _percentile(sorted_values: list[float], probability: float) -> float:
    index = max(0, min(len(sorted_values) - 1, math.ceil(probability * len(sorted_values)) - 1))
    return sorted_values[index]


def _bootstrap_mean(values_by_block: dict[int, float], seed_offset: int = 0) -> dict:
    if set(values_by_block) != set(BLOCKS):
        raise ValueError("bootstrap requires the complete ten-block design")
    values = [values_by_block[block] for block in BLOCKS]
    rng = random.Random(BOOTSTRAP_SEED + seed_offset)
    draws = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(BOOTSTRAP_DRAWS))
    return {"estimate": sum(values) / len(values), "n_runs": len(values), "resampling_unit": "seed block/run",
            "bootstrap_draws": BOOTSTRAP_DRAWS, "bootstrap_seed": BOOTSTRAP_SEED + seed_offset,
            "ci95": [_percentile(draws, .025), _percentile(draws, .975)]}


def _paired_difference(left: dict[int, float], right: dict[int, float], seed_offset: int) -> dict:
    differences = {block: left[block] - right[block] for block in BLOCKS}
    result = _bootstrap_mean(differences, seed_offset)
    result["contrast"] = "higher_power_minus_lower_power"
    return result


def collect(runs_root: Path, net_xml: Path, route_xml: Path) -> list[dict]:
    rows, reference_inputs = [], None
    for power in POWERS:
        for block in BLOCKS:
            run = _run_dir(runs_root, power, block)
            if not run.is_dir():
                raise ValueError(f"missing planned production cell: {run}")
            _, inputs = _manifest(run, power, block)
            if reference_inputs is None:
                reference_inputs = inputs
            elif inputs != reference_inputs:
                raise ValueError(f"input hashes differ from the locked cohort: {run}")
            metric = emergency.analyze(run, net_xml, route_xml)
            prr = _finite_fraction(metric.get("prr"), f"PRR in {run}")
            if metric.get("horizon_s") != HORIZON_S or metric.get("receiver_count") != 19:
                raise ValueError(f"unexpected analyzer output for {run}")
            rows.append({"power_dbm": power, "block": block, "run_dir": str(run),
                         "eligible_tx": metric["eligible_tx"], "received_unique": metric["received_unique"],
                         "prr": prr, "receivers": metric["receivers"]})
    return rows


def summarize(rows: list[dict]) -> dict:
    if len(rows) != len(POWERS) * len(BLOCKS):
        raise ValueError("summary requires exactly 140 production runs")
    by_power: dict[int, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        power, block = row["power_dbm"], row["block"]
        if power not in POWERS or block not in BLOCKS or block in by_power[power]:
            raise ValueError("duplicate or unexpected power/block result")
        by_power[power][block] = row
    if set(by_power) != set(POWERS) or any(set(by_power[p]) != set(BLOCKS) for p in POWERS):
        raise ValueError("incomplete power/block matrix")
    power_rows = []
    for index, power in enumerate(POWERS):
        cells = by_power[power]
        prr = {block: float(cells[block]["prr"]) for block in BLOCKS}
        receiver_records = [receiver for block in BLOCKS for receiver in cells[block]["receivers"]]
        primary = _km_p90(receiver_records, True)
        primary["block_bootstrap_ci95"] = _bootstrap_km_p90(cells, 1_000 + index)
        item = {"power_dbm": power, "prr_run_level": _bootstrap_mean(prr, index),
                "eligible_tx_total": sum(cells[block]["eligible_tx"] for block in BLOCKS),
                "received_unique_total": sum(cells[block]["received_unique"] for block in BLOCKS),
                "first_eligible_receiver_reception": {
                    "primary_delay_from_first_eligibility": primary,
                    "secondary_absolute_from_scenario_start": _km_p90(receiver_records, False)}}
        power_rows.append(item)
    adjacent = []
    for index, (lower, higher) in enumerate(zip(POWERS, POWERS[1:])):
        adjacent.append({"lower_power_dbm": lower, "higher_power_dbm": higher,
                         "prr_run_level": _paired_difference(
                             {block: by_power[higher][block]["prr"] for block in BLOCKS},
                             {block: by_power[lower][block]["prr"] for block in BLOCKS},
                             100 + index)})
    return {"schema": 1, "campaign": "emergency_warning_power", "production_runs": len(rows),
            "pilot_runs_included": 0, "unit_of_inference": "power/block run (complete seed block)",
            "powers": power_rows, "adjacent_power_paired_differences": adjacent}


def write_outputs(rows: list[dict], summary: dict, out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing summary directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    public_rows = [{key: value for key, value in row.items() if key != "receivers"} for row in rows]
    with (out / "runs.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(public_rows[0]))
        writer.writeheader(); writer.writerows(public_rows)
    (out / "runs.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = collect(args.runs_root.resolve(), args.net.resolve(), args.route.resolve())
    write_outputs(rows, summarize(rows), args.out.resolve())


if __name__ == "__main__":
    main()
