"""Validate the Rev-A KiCad constraint scaffold without inventing production geometry."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAFFOLD_PATH = ROOT / "hardware/rev-a/kicad-constraint-scaffold.json"
REFERENCE_ONLY_WIDTHS_MM = {0.08128, 0.110744}
EXPECTED_PAIRS = {
    "USB4_UTX0", "USB4_UTX1", "USB4_URX0", "USB4_URX1",
    "PCIE_TX0", "PCIE_TX1", "PCIE_TX2", "PCIE_TX3",
    "PCIE_RX0", "PCIE_RX1", "PCIE_RX2", "PCIE_RX3",
    "PCIE_REFCLK", "USB2_UD",
}
EXPECTED_CLASSES = {"USB4_DIFF", "PCIE_DIFF", "USB2_DIFF", "DEBUG_LOW_SPEED"}


def load(path: Path | None = None) -> dict:
    return json.loads((path or SCAFFOLD_PATH).read_text())


def _is_locked(stackup: dict) -> bool:
    return (
        stackup.get("status") == "LOCKED"
        and bool(stackup.get("fabricator"))
        and bool(stackup.get("stackup_code"))
        and isinstance(stackup.get("layer_count"), int)
        and stackup.get("layer_count", 0) > 0
    )


def validate(scaffold: dict | None = None) -> dict:
    s = scaffold or load()
    errors: list[str] = []
    warnings: list[str] = []

    if s.get("status") != "SCAFFOLD_ONLY_NOT_FABRICATION_READY":
        errors.append("KiCad scaffold must remain explicitly non-fabrication-ready")

    classes = set(s.get("net_classes", {}))
    if classes != EXPECTED_CLASSES:
        errors.append(f"KiCad scaffold net classes drifted: {sorted(classes)}")

    pairs = s.get("planned_pairs", [])
    pair_names = {p.get("name") for p in pairs}
    if pair_names != EXPECTED_PAIRS:
        errors.append("KiCad scaffold planned differential-pair set is incomplete or has unexpected entries")

    for pair in pairs:
        if pair.get("class") not in EXPECTED_CLASSES:
            errors.append(f"{pair.get('name')} references unknown net class {pair.get('class')}")
        if pair.get("positive_net") is not None or pair.get("negative_net") is not None:
            if pair.get("rev_a_connectivity") != "SCHEMATIC_LOCKED":
                errors.append(f"{pair.get('name')} has Rev-A net names before schematic connectivity is locked")

    guard = s.get("reference_geometry_guard", {})
    if guard.get("status") != "REFERENCE_ONLY_NOT_REV_A_TARGETS":
        errors.append("Leaves232 geometry guard must remain reference-only")
    if set(guard.get("forbidden_as_implicit_production_widths_mm", [])) != REFERENCE_ONLY_WIDTHS_MM:
        errors.append("Leaves232 reference-width guard drifted")

    stackup = s.get("fabrication_stackup", {})
    production = s.get("production_geometry", {})
    stackup_locked = _is_locked(stackup)

    for domain in ("usb4", "pcie", "usb2"):
        geom = production.get(domain, {})
        if not geom:
            errors.append(f"missing production geometry block for {domain}")
            continue
        numeric_keys = (
            "target_diff_ohms",
            "trace_width_mm",
            "pair_gap_mm",
            "max_intra_pair_skew_mm",
            "max_vias_per_conductor",
        )
        populated = {key: geom.get(key) for key in numeric_keys if geom.get(key) is not None}
        if populated and not stackup_locked:
            errors.append(f"{domain} has production routing values before fabricator stackup is locked")
        width = geom.get("trace_width_mm")
        if width in REFERENCE_ONLY_WIDTHS_MM and not stackup_locked:
            errors.append(f"{domain} copied a Leaves232 reference width into unlocked Rev-A production geometry")
        if width is not None and width <= 0:
            errors.append(f"{domain} trace width must be positive")
        gap = geom.get("pair_gap_mm")
        if gap is not None and gap <= 0:
            errors.append(f"{domain} pair gap must be positive")

    emit = s.get("kicad_emit", {})
    if not stackup_locked and emit.get("status") != "BLOCKED":
        errors.append("KiCad output generation must remain blocked until the fabrication stackup is locked")
    required_blockers = {
        "REV_A_SCHEMATIC_NET_NAMES_NOT_LOCKED",
        "FABRICATION_STACKUP_NOT_SELECTED",
        "FIELD_SOLVED_WIDTH_GAP_NOT_AVAILABLE",
        "AUTHORITATIVE_ELECTRICAL_LAYOUT_LIMITS_NOT_LOCKED",
    }
    if emit.get("status") == "BLOCKED" and not required_blockers.issubset(set(emit.get("blocked_by", []))):
        warnings.append("KiCad emit blocker list does not enumerate all current design gates")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "stackup_locked": stackup_locked,
        "planned_pair_count": len(pairs),
        "kicad_emit_status": emit.get("status"),
    }
