"""Create auditable figures for the locked Future Transportation campaigns.

The input directories are the public JSON outputs of ``emergency_summary.py``
and ``intersection_campaign.py``. Each cohort can be plotted independently,
so its archive contains only figures reproducible from that cohort's data.
Each figure is written as both vector SVG and 600-dpi PNG; the script never
derives a new inferential statistic.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


PALETTE = {"native_good": "#0072B2", "native_bad": "#D55E00",
           "sionna_good": "#009E73", "sionna_bad": "#CC79A7",
           "radar_only": "#4D4D4D"}
ARM_LABEL = {"native_good": "Native, base NF", "native_bad": "Native, +53 dB NF",
             "sionna_good": "Sionna RT, base NF", "sionna_bad": "Sionna RT, +53 dB NF",
             "radar_only": "Local sensor only"}
ARM_ORDER = ("radar_only", "native_good", "native_bad", "sionna_good", "sionna_bad")


def _load(directory: Path) -> tuple[list[dict], dict]:
    try:
        rows = json.loads((directory / "runs.json").read_text(encoding="utf-8"))
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read runs.json and summary.json in {directory}") from exc
    if not isinstance(rows, list) or not isinstance(summary, dict):
        raise ValueError(f"invalid JSON schema in {directory}")
    return rows, summary


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _save(fig, out: Path, stem: str) -> None:
    fig.tight_layout()
    fig.savefig(out / f"{stem}.svg", bbox_inches="tight")
    fig.savefig(out / f"{stem}.png", dpi=600, bbox_inches="tight")
    fig.clear()


def _style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#D9D9D9", "grid.linewidth": .6,
                         "axes.axisbelow": True, "svg.fonttype": "none"})
    return plt


def _emergency_maps(summary: dict) -> dict[int, dict]:
    powers = summary.get("powers")
    if not isinstance(powers, list):
        raise ValueError("emergency summary has no powers list")
    result = {}
    for item in powers:
        if not isinstance(item, dict) or not _finite(item.get("power_dbm")):
            raise ValueError("emergency power entry is invalid")
        result[int(item["power_dbm"])] = item
    return result


def figure_emergency_prr(plt, rows, summary, out):
    by_power = _emergency_maps(summary)
    grouped = defaultdict(list)
    for row in rows:
        if _finite(row.get("power_dbm")) and _finite(row.get("prr")):
            grouped[int(row["power_dbm"])].append(float(row["prr"]))
    powers = sorted(by_power)
    fig, ax = plt.subplots(figsize=(6.7, 3.5))
    for i, power in enumerate(powers):
        vals = grouped.get(power, [])
        if vals:
            ax.scatter([power] * len(vals), vals, s=17, color="#7A7A7A", alpha=.62,
                       zorder=2, label="Complete seed-block run" if i == 0 else None)
    estimates, lower, upper = [], [], []
    for power in powers:
        stat = by_power[power].get("prr_run_level", {})
        ci = stat.get("ci95", [None, None])
        if not (_finite(stat.get("estimate")) and len(ci) == 2 and all(_finite(x) for x in ci)):
            raise ValueError("emergency PRR bootstrap fields are invalid")
        estimates.append(stat["estimate"]); lower.append(stat["estimate"] - ci[0]); upper.append(ci[1] - stat["estimate"])
    ax.errorbar(powers, estimates, yerr=[lower, upper], fmt="o-", color="#0072B2", capsize=3,
                lw=1.5, ms=4, zorder=3, label="Mean; 95% block-bootstrap CI")
    ax.set(xlabel="Transmit power (dBm)", ylabel="Run-level CAM PRR", ylim=(0, 1.04))
    ax.legend(frameon=False, loc="best"); _save(fig, out, "figure_a_emergency_prr")


def figure_emergency_km(plt, summary, out):
    by_power = _emergency_maps(summary); powers = sorted(by_power)
    x, y, lo, hi = [], [], [], []
    for power in powers:
        primary = by_power[power].get("first_eligible_receiver_reception", {}).get("primary_delay_from_first_eligibility", {})
        boot = primary.get("block_bootstrap_ci95", {})
        if not (primary.get("p90_estimable") is True and _finite(primary.get("p90_s")) and
                boot.get("ci95_estimable") is True and isinstance(boot.get("ci95_p90_s"), list) and
                len(boot["ci95_p90_s"]) == 2 and all(_finite(v) for v in boot["ci95_p90_s"])):
            continue
        x.append(power); y.append(primary["p90_s"]); lo.append(primary["p90_s"] - boot["ci95_p90_s"][0]); hi.append(boot["ci95_p90_s"][1] - primary["p90_s"])
    fig, ax = plt.subplots(figsize=(6.7, 3.5))
    if x:
        # Non-estimable cells must remain visible as gaps in the power curve.
        ax.errorbar(x, y, yerr=[lo, hi], fmt="o", color="#0072B2", capsize=3, lw=1.5, ms=4)
    missing = sorted(set(powers) - set(x))
    for power in missing:
        ax.axvline(power, color="#999999", lw=.8, ls=":", zorder=0)
    if missing:
        ax.text(.01, .97, "No marker: P90 or its CI not estimable", transform=ax.transAxes, va="top", fontsize=8)
    ax.set(xlabel="Transmit power (dBm)", ylabel="P90 first CAM reception delay (s)")
    ax.set_title("Kaplan–Meier, time origin: first eligible transmission", fontsize=9)
    _save(fig, out, "figure_b_emergency_km_p90")


def _ordered(rows, kind):
    present = {r.get("arm") for r in rows if r.get("kind") == kind}
    return [arm for arm in ARM_ORDER if arm in present]


def figure_calibration(plt, rows, summary, out, labels=ARM_LABEL):
    arms = _ordered(rows, "calibration")
    if not arms:
        raise ValueError("intersection output has no calibration rows")
    by_block = defaultdict(dict)
    for row in rows:
        if row.get("kind") == "calibration" and _finite(row.get("cam_prr")):
            by_block[str(row.get("block"))][row["arm"]] = float(row["cam_prr"])
    fig, ax = plt.subplots(figsize=(7.0, 3.8)); positions = {arm: i for i, arm in enumerate(arms)}
    for block, values in sorted(by_block.items()):
        pts = [(positions[a], values[a]) for a in arms if a in values]
        if len(pts) > 1: ax.plot(*zip(*pts), color="#B8B8B8", lw=.7, zorder=1)
        ax.scatter(*zip(*pts), s=20, color="#777777", alpha=.72, zorder=2)
    for arm, pos in positions.items():
        vals = [v[arm] for v in by_block.values() if arm in v]
        if vals: ax.scatter(pos, sum(vals) / len(vals), marker="D", s=40, color=PALETTE[arm], zorder=4)
    ax.set(xticks=range(len(arms)), xticklabels=[labels.get(a, a) for a in arms], ylim=(0, 1.04),
           ylabel="CAM PRR in [2, 5) s")
    ax.tick_params(axis="x", rotation=18); ax.text(.01, .97, "Paired complete seed blocks; diamonds are arm means", transform=ax.transAxes, va="top", fontsize=8)
    _save(fig, out, "figure_c_calibration_prr")


def figure_collisions(plt, summary, out, labels=ARM_LABEL):
    collision = summary.get("collision", {})
    arms = [a for a in ARM_ORDER if a in collision]
    if not arms: raise ValueError("intersection summary has no behavioral collision statistics")
    rate, lo, hi, ns = [], [], [], []
    for arm in arms:
        stat = collision[arm]; ci = stat.get("wilson95", [None, None])
        if not (_finite(stat.get("rate")) and isinstance(ci, list) and len(ci) == 2 and all(_finite(v) for v in ci)):
            raise ValueError(f"invalid Wilson CI for {arm}")
        rate.append(stat["rate"]); lo.append(stat["rate"] - ci[0]); hi.append(ci[1] - stat["rate"]); ns.append(stat.get("runs"))
    fig, ax = plt.subplots(figsize=(7.0, 3.6)); xs = list(range(len(arms)))
    ax.bar(xs, rate, color=[PALETTE[a] for a in arms], width=.65, zorder=2)
    ax.errorbar(xs, rate, yerr=[lo, hi], fmt="none", color="#222222", capsize=3, zorder=3)
    for x, value, n in zip(xs, rate, ns): ax.text(x, min(1.02, value + .045), f"n={n}", ha="center", fontsize=8)
    ax.set(xticks=xs, xticklabels=[labels.get(a, a) for a in arms], ylim=(0, 1.08), ylabel="Collision proportion")
    ax.tick_params(axis="x", rotation=18); ax.text(.01, .97, "Bars: observed proportion; whiskers: 95% Wilson CI", transform=ax.transAxes, va="top", fontsize=8)
    _save(fig, out, "figure_d_behavioral_collisions")


def figure_sentinels(plt, rows, out):
    arms = ("sionna_good", "sionna_bad")
    kinds = [kind for kind in ("calibration", "behavior") if any(r.get("kind") == kind for r in rows)]
    fig, axes = plt.subplots(1, max(1, len(kinds)), figsize=(5.6 * max(1, len(kinds)), 3.5), squeeze=False)
    for ax, kind in zip(axes[0], kinds):
        values = [[r["sionna_no_path_fraction"] for r in rows if r.get("kind") == kind and r.get("arm") == arm
                   and _finite(r.get("sionna_no_path_fraction"))] for arm in arms]
        if any(values):
            box = ax.boxplot(values, tick_labels=[ARM_LABEL[a] for a in arms], patch_artist=True, showfliers=True)
            for patch, arm in zip(box["boxes"], arms): patch.set_facecolor(PALETTE[arm]); patch.set_alpha(.65)
            for i, vals in enumerate(values, 1): ax.scatter([i] * len(vals), vals, color="#333333", s=15, alpha=.65, zorder=3)
        ax.set(ylim=(0, 1.04), ylabel="Fraction of path-gain requests", title=kind.capitalize())
        ax.tick_params(axis="x", rotation=18)
    fig.suptitle("Sionna no-ray sentinel (computational diagnostic)", fontsize=10)
    fig.text(.5, .02, "This is not a physical packet-loss measure.", ha="center", fontsize=8)
    _save(fig, out, "figure_e_sionna_no_ray_sentinel")


def _empty_output(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to overwrite a non-empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)


def generate_emergency(emergency_dir: Path, out: Path) -> None:
    _empty_output(out)
    emergency_rows, emergency_summary = _load(emergency_dir)
    plt = _style()
    figure_emergency_prr(plt, emergency_rows, emergency_summary, out)
    figure_emergency_km(plt, emergency_summary, out)


def generate_intersection(intersection_dir: Path, out: Path, native_model: str = "unspecified") -> None:
    _empty_output(out)
    intersection_rows, intersection_summary = _load(intersection_dir)
    plt = _style()
    labels = dict(ARM_LABEL)
    if native_model in ("urban", "highway"):
        label = native_model.capitalize()
        labels["native_good"] = f"{label} native, base NF"
        labels["native_bad"] = f"{label} native, +53 dB NF"
    figure_calibration(plt, intersection_rows, intersection_summary, out, labels)
    figure_collisions(plt, intersection_summary, out, labels)
    figure_sentinels(plt, intersection_rows, out)


def generate(emergency_dir: Path, intersection_dir: Path, out: Path, native_model: str = "unspecified") -> None:
    """Backward-compatible combined output for local preview only."""
    _empty_output(out)
    emergency_rows, emergency_summary = _load(emergency_dir)
    intersection_rows, intersection_summary = _load(intersection_dir)
    plt = _style()
    labels = dict(ARM_LABEL)
    if native_model in ("urban", "highway"):
        label = native_model.capitalize()
        labels["native_good"] = f"{label} native, base NF"
        labels["native_bad"] = f"{label} native, +53 dB NF"
    figure_emergency_prr(plt, emergency_rows, emergency_summary, out)
    figure_emergency_km(plt, emergency_summary, out)
    figure_calibration(plt, intersection_rows, intersection_summary, out, labels)
    figure_collisions(plt, intersection_summary, out, labels)
    figure_sentinels(plt, intersection_rows, out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", choices=("all", "emergency", "intersection"), default="all")
    parser.add_argument("--emergency-dir", type=Path)
    parser.add_argument("--intersection-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--native-model", choices=("unspecified", "urban", "highway"), default="unspecified")
    args = parser.parse_args()
    if args.campaign in ("all", "emergency") and args.emergency_dir is None:
        parser.error("--emergency-dir is required for emergency figures")
    if args.campaign in ("all", "intersection") and args.intersection_dir is None:
        parser.error("--intersection-dir is required for intersection figures")
    if args.campaign == "emergency":
        generate_emergency(args.emergency_dir.resolve(), args.out.resolve())
    elif args.campaign == "intersection":
        generate_intersection(args.intersection_dir.resolve(), args.out.resolve(), args.native_model)
    else:
        generate(args.emergency_dir.resolve(), args.intersection_dir.resolve(), args.out.resolve(), args.native_model)


if __name__ == "__main__":
    main()
