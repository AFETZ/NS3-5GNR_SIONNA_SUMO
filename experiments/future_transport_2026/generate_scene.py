#!/usr/bin/env python3
"""Construct a disclosed synthetic ray-tracing scene in SUMO XY coordinates.

This geometry is an experimental idealization, not a surveyed urban scene.
The road network supplies the crossing position and lane polylines. Building
boxes are fixed in the preregistered coordinate ranges below. A clearance
check fails if a box intersects a sampled route lane within 2 m.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

BUILDINGS = (
    ("north_block", -128.0, -90.0, 8.0, 35.0, 14.0),
    ("south_block", -128.0, -90.0, -35.0, -8.0, 11.0),
)
ROUTE_EDGES = {"s1_to_w", "w_to_n1", "c1_to_w", "w_to_s1"}


def segments(polyline):
    points = [tuple(map(float, pair.split(","))) for pair in polyline.split()]
    return list(zip(points, points[1:]))


def sample_segment(a, b):
    import math
    steps = max(1, math.ceil(math.dist(a, b) / 0.25))
    for i in range(steps + 1):
        t = i / steps
        yield a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1])


def verify_clearance(net):
    root = ET.parse(net).getroot()
    crossing = root.find("./junction[@id='w']")
    if crossing is None or (float(crossing.get("x")), float(crossing.get("y"))) != (-150.0, 0.0):
        raise ValueError("The scene requires the reviewed SUMO map with junction w at (-150, 0)")
    seen = set()
    for edge in root.findall("edge"):
        if edge.get("id") not in ROUTE_EDGES:
            continue
        seen.add(edge.get("id"))
        for lane in edge.findall("lane"):
            for a,b in segments(lane.attrib["shape"]):
                for x,y in sample_segment(a,b):
                    for name,xmin,xmax,ymin,ymax,_ in BUILDINGS:
                        if xmin-2 <= x <= xmax+2 and ymin-2 <= y <= ymax+2:
                            raise ValueError(f"Building {name} intersects a route lane at {x:.2f},{y:.2f}")
    if seen != ROUTE_EDGES:
        raise ValueError(f"Missing route edges: {ROUTE_EDGES-seen}")
    return root.find("location").attrib


def mesh_shape(name, material):
    return f'''  <shape type="ply" id="{name}">
    <string name="filename" value="meshes/{name}.ply"/>
    <ref id="{material}" name="bsdf"/>
  </shape>'''


def write_ply(path, vertices, faces):
    lines = ["ply", "format ascii 1.0", f"element vertex {len(vertices)}",
             "property float x", "property float y", "property float z",
             f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    lines.extend(" ".join(map(str, vertex)) for vertex in vertices)
    lines.extend("3 " + " ".join(map(str, face)) for face in faces)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def box_mesh(center, size):
    x,y,z = center
    sx,sy,sz = size
    x0,x1 = x-sx/2,x+sx/2
    y0,y1 = y-sy/2,y+sy/2
    z0,z1 = z-sz/2,z+sz/2
    vertices = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
                (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    faces = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),
             (0,1,5),(0,5,4),(1,2,6),(1,6,5),
             (2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return vertices, faces


def render():
    blocks = [mesh_shape(name, "mat-itu_concrete") for name,*_ in BUILDINGS]
    blocks += [mesh_shape(f"car_{vehicle}", "mat-itu_metal") for vehicle in (2,3)]
    return '''<scene version="2.1.0">
  <integrator type="path"/>
  <bsdf type="diffuse" id="mat-itu_concrete"><rgb name="reflectance" value="0.55 0.55 0.55"/></bsdf>
  <bsdf type="diffuse" id="mat-itu_metal"><rgb name="reflectance" value="0.15 0.15 0.15"/></bsdf>
  <bsdf type="diffuse" id="mat-itu_medium_dry_ground"><rgb name="reflectance" value="0.30 0.30 0.30"/></bsdf>
''' + mesh_shape("road_plane", "mat-itu_medium_dry_ground") + "\n" + "\n".join(blocks) + "\n</scene>\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    location = verify_clearance(args.net)
    meshes = args.out.parent / "meshes"
    meshes.mkdir(parents=True, exist_ok=True)
    write_ply(meshes / "road_plane.ply",
              [(-300,-150,-0.01),(100,-150,-0.01),(100,150,-0.01),(-300,150,-0.01)],
              [(0,1,2),(0,2,3)])
    for name,xmin,xmax,ymin,ymax,h in BUILDINGS:
        vertices,faces = box_mesh(((xmin+xmax)/2,(ymin+ymax)/2,h/2),
                                  (xmax-xmin,ymax-ymin,h))
        write_ply(meshes / f"{name}.ply", vertices, faces)
    # Dynamic vehicle meshes are centered at the origin and moved to SUMO XY.
    for vehicle in (2,3):
        vertices,faces = box_mesh((0,0,0), (2.4,1.8,1.3))
        write_ply(meshes / f"car_{vehicle}.ply", vertices, faces)
    content = render()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    manifest = {"scene_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "sumo_net_sha256": hashlib.sha256(args.net.read_bytes()).hexdigest(),
                "sumo_location": location, "buildings": BUILDINGS,
                "meshes_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(meshes.glob("*.ply"))},
                "description": "Synthetic two-block intersection scene in SUMO local XY; not a surveyed city"}
    args.out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
