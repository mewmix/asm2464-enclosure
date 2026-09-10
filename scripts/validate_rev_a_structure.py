#!/usr/bin/env python3
"""Offline structural gate for the Rev-A netted KiCad study.

This gate intentionally does not certify fabrication readiness.  It prevents
structural CAD work from accidentally acquiring production routing claims
before stackup + field-solver lock.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PCB = ROOT / "hardware/rev-a/kicad/rev-a.kicad_pcb"
SCH = ROOT / "hardware/rev-a/kicad/rev-a.kicad_sch"
AUDIT = ROOT / "hardware/rev-a/design-audit.json"
INSPECTION = ROOT / "hardware/rev-a/kicad/rev-a-inspection.kicad_pcb"
BGA = ROOT / "hardware/rev-a/kicad/rev-a-footprints.pretty/ASM2464PD_BGA273_10x10_P0.46.kicad_mod"

EXPECTED_ANCHORS = {
    "U2": (13.6672574, 14.8704808),
    "J1": (11.0699804, 29.1872670),
    "J2": (11.0238794, 4.6976538),
    "U31": (20.9301842, 20.2771502),
}
EXPECTED_PAIR_NETS = {
    "USB4_UTX0_P_ASM", "USB4_UTX0_N_ASM", "USB4_UTX1_P_ASM", "USB4_UTX1_N_ASM",
    "USB4_URX0_P_ASM", "USB4_URX0_N_ASM", "USB4_URX1_P_ASM", "USB4_URX1_N_ASM",
    "PCIE_TX0_P_ASM", "PCIE_TX0_N_ASM", "PCIE_TX1_P_ASM", "PCIE_TX1_N_ASM",
    "PCIE_TX2_P_ASM", "PCIE_TX2_N_ASM", "PCIE_TX3_P_ASM", "PCIE_TX3_N_ASM",
    "PCIE_RX0_P_ASM", "PCIE_RX0_N_ASM", "PCIE_RX1_P_ASM", "PCIE_RX1_N_ASM",
    "PCIE_RX2_P_ASM", "PCIE_RX2_N_ASM", "PCIE_RX3_P_ASM", "PCIE_RX3_N_ASM",
    "PCIE_REFCLK_P", "PCIE_REFCLK_N", "USB2_DP", "USB2_DM",
}


def balanced_sexpr(text: str) -> bool:
    depth = 0
    quoted = False
    escaped = False
    for ch in text:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
            continue
        if ch == '"':
            quoted = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate(root: Path | None = None) -> dict:
    global ROOT, PCB, SCH, AUDIT, INSPECTION, BGA
    if root is not None:
        ROOT = Path(root)
        PCB = ROOT / "hardware/rev-a/kicad/rev-a.kicad_pcb"
        SCH = ROOT / "hardware/rev-a/kicad/rev-a.kicad_sch"
        AUDIT = ROOT / "hardware/rev-a/design-audit.json"
        INSPECTION = ROOT / "hardware/rev-a/kicad/rev-a-inspection.kicad_pcb"
        BGA = ROOT / "hardware/rev-a/kicad/rev-a-footprints.pretty/ASM2464PD_BGA273_10x10_P0.46.kicad_mod"

    for path in (PCB, SCH, AUDIT, INSPECTION, BGA):
        _assert(path.is_file(), f"missing required Rev-A artifact: {path.relative_to(ROOT)}")

    pcb = PCB.read_text(encoding="utf-8")
    sch = SCH.read_text(encoding="utf-8")
    bga = BGA.read_text(encoding="utf-8")
    inspection = INSPECTION.read_text(encoding="utf-8")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))

    _assert(balanced_sexpr(pcb), "rev-a.kicad_pcb has unbalanced S-expressions")
    _assert(balanced_sexpr(sch), "rev-a.kicad_sch has unbalanced S-expressions")
    _assert("REV-A INSPECTION ONLY" in inspection and "REV_A_PROXY:ASM2464PD" in inspection,
            "historical inspection board was replaced or lost its proxy identity")

    _assert(abs(audit["board"]["width_mm"] - 22.039834) < 1e-9, "board width datum drift")
    _assert(abs(audit["board"]["height_mm"] - 33.6804) < 1e-9, "board height datum drift")
    _assert(audit["board"]["stackup_status"] == "UNSELECTED", "stackup unexpectedly promoted")
    _assert(audit["board"]["field_solver_status"] == "NOT_RUN", "field solver unexpectedly promoted")
    _assert(audit["board"]["production_routing_geometry"] == "NULL_BY_POLICY",
            "production routing geometry must remain null before SI lock")
    _assert(audit["artifact_status"] == "STRUCTURAL_NETTED_NOT_FABRICATION_READY",
            "Rev-A structural artifact status changed unexpectedly")

    by_ref = {c["refdes"]: c for c in audit["components"]}
    for ref, (x, y) in EXPECTED_ANCHORS.items():
        _assert(ref in by_ref, f"missing audited major component {ref}")
        pos = by_ref[ref]["position_mm"]
        _assert(abs(pos["x"] - x) < 1e-7 and abs(pos["y"] - y) < 1e-7,
                f"major placement datum drift for {ref}: {pos}")

    for token in (
        'footprint "ASM2464PD_BGA273_10x10_P0.46"',
        'footprint "Molex_105450-0101"',
        'footprint "LOTES_APCI0113-P001A_MKey_RECONSTRUCTED"',
        'footprint "ZD25WQ16CEIGR_USON8_3x2_P0.5_RECONSTRUCTED"',
    ):
        _assert(token in pcb, f"missing major footprint: {token}")

    bga_pad_count = len(re.findall(r'^\s*\(pad "[A-Z]+\d+" ', bga, flags=re.M))
    _assert(bga_pad_count == 273, f"ASM2464PD footprint has {bga_pad_count} balls, expected 273")
    for ball in ("T1", "T2", "V2", "V1", "P1", "P2", "Y2", "Y1", "R20", "R21", "AA19", "Y19", "A2", "A3", "A4", "A5", "A21", "B21", "H1"):
        _assert(f'(pad "{ball}" ' in bga, f"missing authoritative ASM ball {ball}")

    for net in EXPECTED_PAIR_NETS:
        _assert(f'"{net}"' in pcb, f"missing required PCB net {net}")
        _assert(f'"{net}"' in sch, f"missing required schematic net {net}")

    # The first cycle is explicitly a ratsnest/placement artifact, not routed copper.
    _assert(not re.search(r'^\s*\(segment\s', pcb, flags=re.M), "routed segment emitted before stackup/solver lock")
    _assert(not re.search(r'^\s*\(via\s', pcb, flags=re.M), "routing via emitted before stackup/solver lock")
    _assert("0.08128" not in pcb and "0.110744" not in pcb,
            "Leaves source widths leaked into Rev-A PCB routing geometry")

    _assert(sch.count('(symbol (lib_id') >= 30, "schematic regressed to a graphics-only topology sheet")
    for lib in ("RevA:ASM2464PD_REQUIRED", "RevA:Molex_105450_0101", "RevA:M2_MKEY_PCIE_X4", "RevA:ZD25WQ16CEIGR", "RevA:SERVICE_2X5"):
        _assert(f'(lib_id "{lib}")' in sch, f"missing required schematic symbol instance {lib}")

    service = audit["service_contract"]
    _assert(service["programmer_attachment"] == "FLASH_SIDE_OF_RISO1_RISO4", "SPI programmer attachment moved controller-side")
    _assert(service["reset_grants_spi_bus_ownership"] is False, "reset incorrectly claims SPI ownership")
    _assert(service["asm_reset_tristate_behavior"] == "UNRESOLVED", "unresolved ASM tri-state behavior was guessed")
    for idx, net in ((6, "ASM_SPI_CS_N_FLASH"), (7, "ASM_SPI_CLK_FLASH"), (8, "ASM_SPI_DI_FLASH"), (9, "ASM_SPI_DO_FLASH")):
        _assert(service["pins"][str(idx)] == net, f"service SPI pin {idx} is not flash-side")

    for domain in ("USB4", "PCIe", "USB2"):
        hs = audit["high_speed_domains"][domain]
        _assert(hs["actual_solved_geometry"] is None, f"{domain} geometry promoted before solver")
        _assert(hs["p_length_mm"] is None and hs["n_length_mm"] is None, f"{domain} fabricated length metrics present")
        _assert(hs["routing_status"] == "LOGICAL_NETTED_UNROUTED", f"{domain} routing status is not first-cycle safe")

    cli = shutil.which("kicad-cli")
    kicad = {"status": "BLOCKED_KICAD_CLI_UNAVAILABLE"}
    if cli:
        # Parser/export smoke test only. DRC is intentionally not certified while
        # the board is unrouted and the power tree is incomplete.
        out = ROOT / "validation/rev-a-kicad-smoke.svg"
        out.parent.mkdir(parents=True, exist_ok=True)
        cp = subprocess.run([cli, "pcb", "export", "svg", "--layers", "F.Cu,F.SilkS,Edge.Cuts", "--output", str(out), str(PCB)],
                            cwd=ROOT, text=True, capture_output=True)
        _assert(cp.returncode == 0, f"kicad-cli PCB parse/export failed: {cp.stderr.strip()}")
        kicad = {"status": "PASS_PCB_PARSE_EXPORT", "artifact": str(out.relative_to(ROOT))}

    return {
        "passed": True,
        "fabrication_ready": False,
        "bga_ball_count": bga_pad_count,
        "major_anchors": EXPECTED_ANCHORS,
        "required_high_speed_nets": len(EXPECTED_PAIR_NETS),
        "routed_segments": 0,
        "routing_vias": 0,
        "kicad_cli": kicad,
    }


def main() -> int:
    result = validate()
    out = ROOT / "validation/rev-a-structure.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
