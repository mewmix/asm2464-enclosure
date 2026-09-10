#!/usr/bin/env python3
"""Decode source-native Altium B2 routing geometry from altium2kicad dumps.

The parser intentionally follows byte offsets used by the pinned altium2kicad
converter. Native Tracks6/Arcs6/Vias6 remain the source geometry; the converted
KiCad report is used only as an independent cross-check of straight segments and
via counts.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

MIL_TO_MM = 0.0254
BMIL_TO_MM = MIL_TO_MM / 10000.0
LAYER_NAMES = {1: "TOP", 32: "BOTTOM"}
HS_RE = re.compile(
    r"^(?:PET\d+U?_[PN]|PER\d+_[PN]|UTX\d+(?:_CON)?_[PN]|URX\d+(?:_CON)?_[PN]|UD_[PN]|RefCLK_[PN])$",
    re.IGNORECASE,
)
LINE_RE = re.compile(r"\|LINENO=(\d+)\|([0-9A-Fa-f]+)")


def _s16(data: bytes, off: int) -> int:
    return struct.unpack_from("<h", data, off)[0]


def _i32(data: bytes, off: int) -> int:
    return struct.unpack_from("<i", data, off)[0]


def _f64(data: bytes, off: int) -> float:
    return struct.unpack_from("<d", data, off)[0]


def _bmil(data: bytes, off: int) -> float:
    return _i32(data, off) * BMIL_TO_MM


def _pipe_fields(line: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in line.split("|"):
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def load_nets(path: Path) -> dict[int, dict]:
    nets: dict[int, dict] = {}
    for line in path.read_text(encoding="latin-1", errors="replace").splitlines():
        m = re.search(r"\|LINENO=(\d+)\|", line)
        if not m:
            continue
        fields = _pipe_fields(line)
        name = fields.get("NAME")
        if not name:
            continue
        item = {"name": name}
        for source_key, dest_key in (("TOPLAYER_MRWIDTH", "mrwidth_mm"), ("TARGETLENGTH", "target_length_mm")):
            value = fields.get(source_key)
            if value and value.endswith("mil"):
                item[dest_key] = float(value[:-3]) * MIL_TO_MM
        nets[int(m.group(1))] = item
    return nets


def load_hex_records(path: Path) -> list[tuple[int, bytes]]:
    records: list[tuple[int, bytes]] = []
    for line in path.read_text(encoding="latin-1", errors="replace").splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        records.append((int(m.group(1)), bytes.fromhex(m.group(2))))
    return records


def _entry():
    return {
        "track_count": 0,
        "straight_length_mm": 0.0,
        "arc_count": 0,
        "arc_length_mm": 0.0,
        "via_count": 0,
        "layers": Counter(),
        "widths_mm": set(),
    }


def decode_geometry(nets: dict[int, dict], tracks: Path, arcs: Path, vias: Path) -> dict[str, dict]:
    per_raw_net = defaultdict(_entry)

    for _lineno, data in load_hex_records(tracks):
        if len(data) < 33:
            raise ValueError("short Tracks6 record")
        net = _s16(data, 3)
        e = per_raw_net[net]
        x1, y1, x2, y2 = (_bmil(data, off) for off in (13, 17, 21, 25))
        e["track_count"] += 1
        e["straight_length_mm"] += math.hypot(x2 - x1, y2 - y1)
        e["layers"][data[0]] += 1
        e["widths_mm"].add(round(_bmil(data, 29), 6))

    for _lineno, data in load_hex_records(arcs):
        if len(data) < 45:
            raise ValueError("short Arcs6 record")
        net = _s16(data, 3)
        e = per_raw_net[net]
        radius_mm = _bmil(data, 21)
        start_deg = _f64(data, 25)
        end_deg = _f64(data, 33)
        sweep_deg = end_deg - start_deg
        if sweep_deg < 0:
            sweep_deg += 360.0
        e["arc_count"] += 1
        e["arc_length_mm"] += abs(radius_mm) * math.radians(sweep_deg)
        e["layers"][data[0]] += 1
        e["widths_mm"].add(round(_bmil(data, 41), 6))

    for _lineno, data in load_hex_records(vias):
        if len(data) < 29:
            raise ValueError("short Vias6 record")
        per_raw_net[_s16(data, 3)]["via_count"] += 1

    by_name: dict[str, dict] = {}
    for raw_net, e in per_raw_net.items():
        info = nets.get(raw_net, {"name": f"__RAW_NET_{raw_net}"})
        name = info["name"]
        total = e["straight_length_mm"] + e["arc_length_mm"]
        by_name[name] = {
            "raw_net": raw_net,
            "name": name,
            "candidate_high_speed": bool(HS_RE.match(name)),
            "track_count": e["track_count"],
            "straight_length_mm": round(e["straight_length_mm"], 6),
            "arc_count": e["arc_count"],
            "arc_length_mm": round(e["arc_length_mm"], 6),
            "total_copper_centerline_mm": round(total, 6),
            "via_count": e["via_count"],
            "layers": [LAYER_NAMES.get(x, f"ALTIUM_LAYER_{x}") for x in sorted(e["layers"])],
            "layer_primitive_counts": {LAYER_NAMES.get(x, f"ALTIUM_LAYER_{x}"): n for x, n in sorted(e["layers"].items())},
            "widths_mm": sorted(e["widths_mm"]),
            "mrwidth_mm": round(info["mrwidth_mm"], 6) if "mrwidth_mm" in info else None,
            "target_length_mm": round(info["target_length_mm"], 6) if "target_length_mm" in info else None,
        }
    return by_name


def make_path(name: str, p_nets: list[str], n_nets: list[str], by_name: dict[str, dict]) -> dict:
    missing = [n for n in p_nets + n_nets if n not in by_name]
    if missing:
        return {"name": name, "status": "MISSING_NET", "missing": missing}

    def side(nets: list[str]) -> dict:
        rows = [by_name[n] for n in nets]
        return {
            "nets": nets,
            "copper_centerline_mm": round(sum(r["total_copper_centerline_mm"] for r in rows), 6),
            "straight_mm": round(sum(r["straight_length_mm"] for r in rows), 6),
            "arc_mm": round(sum(r["arc_length_mm"] for r in rows), 6),
            "vias": sum(r["via_count"] for r in rows),
            "layers": sorted({layer for r in rows for layer in r["layers"]}),
            "widths_mm": sorted({w for r in rows for w in r["widths_mm"]}),
        }

    p = side(p_nets)
    n = side(n_nets)
    return {
        "name": name,
        "status": "SOURCE_NATIVE_GEOMETRY",
        "positive": p,
        "negative": n,
        "copper_centerline_delta_mm": round(abs(p["copper_centerline_mm"] - n["copper_centerline_mm"]), 6),
        "via_count_delta": abs(p["vias"] - n["vias"]),
        "interpretation": "Geometric copper-centerline delta only; excludes package/pad/discontinuity electrical delay and is not claimed timing skew.",
    }


def build_paths(by_name: dict[str, dict]) -> list[dict]:
    paths: list[dict] = []
    for lane in range(4):
        paths.append(make_path(
            f"PCIE_TX{lane}",
            [f"PET{lane}_P", f"PET{lane}U_P"],
            [f"PET{lane}_N", f"PET{lane}U_N"],
            by_name,
        ))
    for lane in range(4):
        paths.append(make_path(f"PCIE_RX{lane}", [f"PER{lane}_P"], [f"PER{lane}_N"], by_name))
    paths.append(make_path("PCIE_REFCLK", ["RefCLK_P"], ["RefCLK_N"], by_name))
    for family in ("UTX", "URX"):
        for lane in range(2):
            paths.append(make_path(
                f"USB4_{family}{lane}",
                [f"{family}{lane}_P", f"{family}{lane}_CON_P"],
                [f"{family}{lane}_N", f"{family}{lane}_CON_N"],
                by_name,
            ))
    paths.append(make_path("USB2_UD", ["UD_P"], ["UD_N"], by_name))
    return paths


def converted_crosscheck(by_name: dict[str, dict], converted_path: Path | None) -> dict:
    if converted_path is None:
        return {"status": "NOT_REQUESTED"}
    converted = json.loads(converted_path.read_text())
    rows = {r["name"]: r for r in converted["all_routed_nets"]}
    checked = []
    for name, native in by_name.items():
        if not native["candidate_high_speed"] or name not in rows:
            continue
        conv = rows[name]
        checked.append({
            "name": name,
            "native_straight_mm": native["straight_length_mm"],
            "converted_straight_mm": conv["segment_length_mm"],
            "abs_length_delta_mm": round(abs(native["straight_length_mm"] - conv["segment_length_mm"]), 6),
            "native_vias": native["via_count"],
            "converted_vias": conv["via_count"],
            "via_count_delta": abs(native["via_count"] - conv["via_count"]),
        })
    return {
        "status": "CORROBORATED" if checked else "NO_MATCHES",
        "checked_high_speed_nets": len(checked),
        "max_abs_straight_length_delta_mm": max((x["abs_length_delta_mm"] for x in checked), default=None),
        "max_via_count_delta": max((x["via_count_delta"] for x in checked), default=None),
        "rows": checked,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--native-nets", type=Path, required=True)
    ap.add_argument("--native-tracks", type=Path, required=True)
    ap.add_argument("--native-arcs", type=Path, required=True)
    ap.add_argument("--native-vias", type=Path, required=True)
    ap.add_argument("--converted-report", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    nets = load_nets(args.native_nets)
    by_name = decode_geometry(nets, args.native_tracks, args.native_arcs, args.native_vias)
    candidates = [x for x in by_name.values() if x["candidate_high_speed"]]
    report = {
        "status": "SOURCE_NATIVE_ALTIUM_GEOMETRY_DIAGNOSTIC",
        "evidence": {
            "tracks": "ALTIUM_TRACKS6_DECODED_USING_PINNED_CONVERTER_OFFSETS",
            "arcs": "ALTIUM_ARCS6_DECODED_USING_PINNED_CONVERTER_OFFSETS",
            "vias": "ALTIUM_VIAS6_NET_ASSOCIATION_USING_PINNED_CONVERTER_OFFSETS",
            "note": "Via count is source-native. Via layer transition is not promoted because the pinned converter hardcodes F.Cu/B.Cu for vias.",
        },
        "declared_nets": len(nets),
        "routed_nets": len(by_name),
        "high_speed_net_count": len(candidates),
        "converted_crosscheck": converted_crosscheck(by_name, args.converted_report),
        "end_to_end_paths": build_paths(by_name),
        "high_speed_nets": sorted(candidates, key=lambda x: x["name"].upper()),
        "interpretation": {
            "total_copper_centerline_mm": "Tracks6 straight centerline plus Arcs6 centerline arc length from source-native records.",
            "target_length_mm": "Altium net field retained as design metadata only; not substituted for measured source-native copper length.",
            "copper_centerline_delta_mm": "Physical copper-centerline difference across the reconstructed conductor path; not electrical timing skew.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "routed_nets": report["routed_nets"],
        "high_speed_net_count": report["high_speed_net_count"],
        "converted_crosscheck": report["converted_crosscheck"],
        "paths": [
            {"name": x["name"], "status": x["status"], "delta_mm": x.get("copper_centerline_delta_mm")}
            for x in report["end_to_end_paths"]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
