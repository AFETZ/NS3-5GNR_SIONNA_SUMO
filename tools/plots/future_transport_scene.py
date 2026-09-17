#!/usr/bin/env python3
"""Render the frozen synthetic intersection geometry used by the study.

The diagram is intentionally derived from the SUMO network, route file, and
ray-tracing PLY meshes.  It is a plan view in SUMO's local Cartesian frame;
it is not a map or a surveyed reconstruction of an urban intersection.
"""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NET = ROOT / "src/automotive/examples/sumo_files_v2i_map/map.net.xml"
DEFAULT_ROUTES = ROOT / "experiments/intersection_radar_comm/sumo/cars_intersection_radar_link.rou.xml"
DEFAULT_SCENE = ROOT / "experiments/future_transport_2026/scene.xml"


def _points(value: str) -> list[tuple[float, float]]:
    return [tuple(map(float, pair.split(","))) for pair in value.split()]


def _ply_bounds(path: Path) -> tuple[float, float, float, float]:
    """Read ASCII PLY vertex bounds, sufficient for frozen scene meshes."""
    lines = path.read_text(encoding="ascii").splitlines()
    try:
        header_end = lines.index("end_header")
        count = int(next(line.split()[-1] for line in lines if line.startswith("element vertex ")))
    except (StopIteration, ValueError) as exc:
        raise ValueError(f"invalid ASCII PLY header: {path}") from exc
    vertices = [tuple(map(float, line.split()[:3])) for line in lines[header_end + 1:header_end + 1 + count]]
    if len(vertices) != count:
        raise ValueError(f"incomplete vertex data: {path}")
    xs, ys = zip(*((vertex[0], vertex[1]) for vertex in vertices))
    return min(xs), max(xs), min(ys), max(ys)


def _scene_meshes(scene: Path) -> dict[str, Path]:
    root = ET.parse(scene).getroot()
    result = {}
    for shape in root.findall("shape"):
        if shape.get("type") != "ply" or not shape.get("id"):
            continue
        filename = shape.find("string[@name='filename']")
        if filename is None or not filename.get("value"):
            raise ValueError(f"PLY shape without a filename: {shape.get('id')}")
        result[shape.get("id")] = (scene.parent / filename.get("value")).resolve()
    if not {"north_block", "south_block", "road_plane"}.issubset(result):
        raise ValueError("scene must provide north_block, south_block, and road_plane meshes")
    return result


def _network_lanes(net: Path) -> tuple[dict[str, list[tuple[float, float]]], tuple[float, float]]:
    root = ET.parse(net).getroot()
    lanes = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        lane = edge.find("lane")
        if edge_id and lane is not None and lane.get("shape"):
            lanes[edge_id] = _points(lane.get("shape"))
    junction = root.find("junction[@id='w']")
    if junction is None:
        raise ValueError("network has no priority junction 'w'")
    return lanes, (float(junction.get("x")), float(junction.get("y")))


def _vehicle_routes(routes: Path) -> dict[str, list[str]]:
    root = ET.parse(routes).getroot()
    definitions = {route.get("id"): route.get("edges", "").split() for route in root.findall("route")}
    result = {}
    for vehicle in root.findall("vehicle"):
        ident, route = vehicle.get("id"), vehicle.get("route")
        if ident in {"veh2", "veh3"} and route in definitions:
            result[ident] = definitions[route]
    if set(result) != {"veh2", "veh3"}:
        raise ValueError("route file must define veh2 and veh3 with named routes")
    return result


def _save(fig, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    svg = out / "figure_1_synthetic_intersection.svg"
    png = out / "figure_1_synthetic_intersection.png"
    fig.savefig(svg, bbox_inches="tight")
    fig.savefig(png, bbox_inches="tight", dpi=600)
    return svg, png


def generate(net: Path, routes: Path, scene: Path, out: Path) -> tuple[Path, Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyArrowPatch, Rectangle

    lanes, junction = _network_lanes(net)
    vehicle_routes = _vehicle_routes(routes)
    meshes = _scene_meshes(scene)
    bounds = {name: _ply_bounds(path) for name, path in meshes.items()}
    route_edges = {edge for route in vehicle_routes.values() for edge in route}
    if not route_edges.issubset(lanes):
        raise ValueError(f"route edges missing lanes: {sorted(route_edges - set(lanes))}")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(5.4, 7.0))
    road = bounds["road_plane"]
    ax.add_patch(Rectangle((road[0], road[2]), road[1] - road[0], road[3] - road[2],
                           facecolor="#F2F2F2", edgecolor="none", zorder=0))
    for name, label, color in (("north_block", "14 m", "#9E9E9E"),
                               ("south_block", "11 m", "#B0B0B0")):
        xmin, xmax, ymin, ymax = bounds[name]
        ax.add_patch(Rectangle((xmin, ymin), xmax - xmin, ymax - ymin,
                               facecolor=color, edgecolor="#555555", linewidth=.8, zorder=3))
        ax.text((xmin + xmax) / 2, (ymin + ymax) / 2, label, ha="center", va="center", fontsize=8)

    for edge in sorted(route_edges):
        points = lanes[edge]
        xs, ys = zip(*points)
        ax.plot(xs, ys, color="#FFFFFF", lw=6.8, solid_capstyle="round", zorder=1)
        ax.plot(xs, ys, color="#505050", lw=1.2, solid_capstyle="round", zorder=2)

    colors = {"veh2": "#0072B2", "veh3": "#D55E00"}
    for vehicle, edges in vehicle_routes.items():
        approach = lanes[edges[0]]
        start, end = approach[-2], approach[-1]
        # The final lane segment establishes the modelled approach direction.
        arrow = FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=14,
                                linewidth=2.3, color=colors[vehicle], zorder=5)
        ax.add_patch(arrow)

    ax.plot(*junction, marker="o", ms=3.5, color="#222222", zorder=6)
    ax.annotate("Priority junction w", xy=junction, xytext=(-180, 48), fontsize=8,
                arrowprops={"arrowstyle": "-", "color": "#333333", "lw": .8})
    ax.set(xlabel="Local x coordinate (m)", ylabel="Local y coordinate (m)", aspect="equal",
           xlim=(-180, -30), ylim=(-120, 120))
    ax.grid(color="#D5D5D5", linewidth=.5, zorder=0)
    handles = [Line2D([0], [0], color=colors["veh2"], lw=2.3, label="veh2: priority route direction"),
               Line2D([0], [0], color=colors["veh3"], lw=2.3, label="veh3: minor route direction"),
               Rectangle((0, 0), 1, 1, facecolor="#9E9E9E", edgecolor="#555555", label="Ray-tracing building mesh")]
    ax.legend(handles=handles, loc="upper right", frameon=True, fontsize=8)
    fig.text(.5, .025, "Plan view from frozen SUMO lanes and PLY mesh bounds.\nSynthetic geometry; not a surveyed site.",
             ha="center", fontsize=7.5)
    fig.tight_layout(rect=(0, .055, 1, 1))
    result = _save(fig, out)
    plt.close(fig)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    svg, png = generate(args.net.resolve(), args.routes.resolve(), args.scene.resolve(), args.out.resolve())
    print(svg)
    print(png)


if __name__ == "__main__":
    main()
