#!/usr/bin/env python3
"""Assemble the research simulator without deleting or rewriting source checkouts.

Run inside the research Linux container. The Windows checkout is unsuitable for
ASN.1 files whose names differ only by case. Builds live on a Linux Docker volume.
"""
import argparse
import json
import shutil
import subprocess
import hashlib
import os
from pathlib import Path

NS3_SHA = "80b8e3109c0f654bea332d63676e21b03a271758"
NR_SHA = "321566011c1a49ed8e722c3edc9a659604fe1dda"
OVERLAY_SHA = "4d5f584ac865cc3ce7b4188c87a4e4a6653cb3c0"


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def checkout(url, path, revision):
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        run("git", "init", str(path))
        run("git", "remote", "add", "origin", url, cwd=path)
        run("git", "fetch", "--depth=1", "origin", revision, cwd=path)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=path)
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    if actual != revision:
        raise RuntimeError(f"Refusing to replace existing checkout {path}: {actual}")
    return actual


RESEARCH_FILES = (
    "src/automotive/model/utilities/sumo-sensor.cc",
    "src/automotive/model/utilities/sumo-sensor.h",
    "src/automotive/model/utilities/sensor-kinematics.h",
    "src/automotive/model/Applications/emergencyVehicleAlert.cc",
    "src/automotive/examples/v2v-emergencyVehicleAlert-nrv2x.cc",
    "experiments/intersection_radar_comm/tools/summarize_runs.py",
    "experiments/intersection_radar_comm/tools/analyze_outputs.py",
    "tools/analysis/research_metrics.py",
    "tests/research/test_sensor_kinematics.cc",
    "tests/research/test_collision_integrity.py",
    "tests/research/test_research_metrics.py",
    "experiments/future_transport_2026/PROTOCOL.md",
    "experiments/future_transport_2026/ANALYSIS_AMENDMENTS.md",
    "experiments/future_transport_2026/generate_scene.py",
    "experiments/future_transport_2026/scene.xml",
    "experiments/future_transport_2026/scene.manifest.json",
    "experiments/future_transport_2026/run_campaign.py",
    "experiments/future_transport_2026/run_urban_matrix.py",
)


def assemble(root, patch_source):
    overlay, build = root / "overlay", root / "ns-3-dev"
    revisions = {
        "overlay": checkout("https://github.com/AFETZ/NS3-5GNR_SIONNA_SUMO.git", overlay, OVERLAY_SHA),
        "ns3": checkout("https://gitlab.com/cttc-lena/ns-3-dev.git", build, NS3_SHA),
        "nr": checkout("https://gitlab.com/cttc-lena/nr.git", build / "src/nr", NR_SHA),
    }
    # Overlay only executable sources and scenario/tool assets. Historical data
    # and archival prose are deliberately absent from the assembled build.
    for name in ("src", "experiments", "tools", "scripts", "emulation-support"):
        # Preserve upstream links, including two dangling legacy strict-scene
        # mesh links. Dereferencing them would make installation fail. The
        # prospective generated scene contains no external mesh links.
        source_root, target_root = overlay / name, build / name
        def skip_existing_links(directory, entries):
            target_directory = target_root / Path(directory).relative_to(source_root)
            return [entry for entry in entries
                    if (Path(directory) / entry).is_symlink()
                    and ((target_directory / entry).exists() or (target_directory / entry).is_symlink())]
        shutil.copytree(source_root, target_root, dirs_exist_ok=True,
                        symlinks=True, ignore=skip_existing_links)
    revisions["research_files_sha256"] = {}
    if patch_source:
        for relative in RESEARCH_FILES:
            source = patch_source / relative
            destination = build / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Normalize line endings for Linux without touching the inspected
            # Windows checkout or colliding ASN.1 files.
            content = source.read_bytes().replace(b"\r\n", b"\n")
            destination.write_bytes(content)
            revisions["research_files_sha256"][relative] = hashlib.sha256(content).hexdigest()

    def cp(source, destination):
        shutil.copy2(build / source, build / destination)

    for name in ("cni-urbanmicrocell-propagation-loss-model.cc", "cni-urbanmicrocell-propagation-loss-model.h"):
        cp(f"src/automotive/propagation-extended/{name}", f"src/propagation/model/{name}")
    for module in ("propagation", "spectrum"):
        patches = build / "src/sionna/files" / module
        for source in patches.iterdir():
            if source.is_file():
                destination = build / "src" / module
                if source.name != "CMakeLists.txt":
                    destination /= "model"
                shutil.copy2(source, destination / source.name)
    cp("src/automotive/model/TxTracker/channel_files/modified/yans-wifi-phy.h", "src/wifi/model/yans-wifi-phy.h")
    signal = "src/automotive/model/SignalInfo"
    for module in ("wifi", "cv2x", "nr", "lte"):
        for stem in ("rssi", "timestamp", "rsrp", "sinr", "size"):
            for extension in ("cc", "h"):
                cp(f"{signal}/{stem}-tag.{extension}", f"src/{module}/model/{stem}-tag.{extension}")
    groups = {
        "WiFi": {"wifi-mac-queue-item.h": "wifi/model", "ocb-wifi-mac.cc": "wave/model", "frame-exchange-manager.cc": "wifi/model", "qos-frame-exchange-manager.cc": "wifi/model", "CMakeLists.txt": "wifi"},
        "CV2X": {name: "cv2x/model" for name in ("cv2x_lte-spectrum-phy.cc", "cv2x_lte-spectrum-phy.h", "cv2x_lte-ue-mac.h", "cv2x_lte-ue-mac.cc")},
        "NR": {name: "nr/model" for name in ("nr-spectrum-phy.cc", "nr-spectrum-phy.h", "nr-ue-phy.cc")},
        "LTE": {name: "lte/model" for name in ("lte-spectrum-phy.cc", "lte-ue-phy.cc", "lte-ue-phy.h")},
    }
    for group, files in groups.items():
        if group != "WiFi":
            files["CMakeLists.txt"] = group.lower()
        for name, destination in files.items():
            cp(f"{signal}/{group}/{name}", f"src/{destination}/{name}")
    cmake = build / "CMakeLists.txt"
    cmake.write_text(cmake.read_text().replace("project(NS3 CXX)", "project(NS3 C CXX)"))
    carla_cmake = build / "src/carla/CMakeLists.txt"
    carla_text = carla_cmake.read_text().replace("find_package(protobuf REQUIRED)", "find_package(Protobuf REQUIRED)")
    carla_text = carla_text.replace("find_package(gRPC CONFIG REQUIRED)", """find_package(gRPC CONFIG QUIET)
if(NOT TARGET gRPC::grpc++)
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(GRPCPP REQUIRED grpc++)
  add_library(gRPC::grpc INTERFACE IMPORTED)
  add_library(gRPC::grpc++ INTERFACE IMPORTED)
  target_link_libraries(gRPC::grpc INTERFACE ${GRPCPP_LIBRARIES})
  target_link_libraries(gRPC::grpc++ INTERFACE ${GRPCPP_LIBRARIES})
endif()""")
    carla_cmake.write_text(carla_text)
    for relative in ("src/network/utils/bit-deserializer.h", "src/network/utils/bit-serializer.h", "src/wifi/model/block-ack-type.h"):
        path = build / relative
        content = path.read_text()
        if "#include <cstdint>" not in content:
            path.write_text("#include <cstdint>\n" + content)
    # Generated CARLA bindings must match the installed protobuf toolchain.
    proto = build / "src/carla/proto"
    run("protoc", "--cpp_out=.", "--grpc_out=.", "--plugin=protoc-gen-grpc=/usr/bin/grpc_cpp_plugin", "carla.proto", cwd=proto)
    revisions["packages"] = subprocess.check_output(["dpkg-query", "-W"], text=True).splitlines()
    revisions["python"] = subprocess.check_output(["python3", "-m", "pip", "freeze"], text=True).splitlines()
    (root / "environment-manifest.json").write_text(json.dumps(revisions, indent=2) + "\n")
    print(f"Assembled simulator: {build}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/research"))
    parser.add_argument("--patch-source", type=Path)
    args = parser.parse_args()
    assemble(args.root.resolve(), args.patch_source)
