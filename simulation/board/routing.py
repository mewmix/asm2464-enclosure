"""Evidence-bound board routing/topology model.

This module intentionally models only facts and constraints supported by repository
evidence. Reference-board topology is checked separately from Rev-A copper geometry:
a recovered reference path does not make the new board routed or fabrication-ready.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTING_PATH = ROOT / "hardware/rev-a/routing.json"
REFERENCE_XREF_PATH = ROOT / "reference/leaves232-b2/high-speed-routing-xref.json"

EXPECTED_ASM_BALLS = {
    "ASM_UART_TX": "B21",
    "ASM_UART_RX": "A21",
    "ASM_SPI_CS_N": "A2",
    "ASM_SPI_DO": "A3",
    "ASM_SPI_DI": "A4",
    "ASM_SPI_CLK": "A5",
    "ASM_RST_N": "H1",
}
EXPECTED_SERVICE_PINS = {
    "ASM_UART_TX": 3,
    "ASM_UART_RX": 4,
    "ASM_RST_N": 5,
    "ASM_SPI_CS_N": 6,
    "ASM_SPI_CLK": 7,
    "ASM_SPI_DI": 8,
    "ASM_SPI_DO": 9,
}
SPI_NETS = {"ASM_SPI_CS_N", "ASM_SPI_CLK", "ASM_SPI_DI", "ASM_SPI_DO"}
HIGH_SPEED_DOMAINS = {"USB4", "PCIE"}
EXPECTED_REFERENCE_PAIRS = {
    "UD",
    "UTX0", "UTX0_CON", "UTX1", "UTX1_CON",
    "URX0", "URX0_CON", "URX1", "URX1_CON",
    "PET0", "PET0U", "PET1", "PET1U", "PET2", "PET2U", "PET3", "PET3U",
    "PER0", "PER1", "PER2", "PER3", "RefCLK",
}


def load(path: Path | None = None) -> dict:
    return json.loads((path or ROUTING_PATH).read_text())


def load_reference_xref(path: Path | None = None) -> dict:
    return json.loads((path or REFERENCE_XREF_PATH).read_text())


def sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or ROUTING_PATH).read_bytes()).hexdigest()


def _component_xy(contract: dict, name: str) -> tuple[float, float]:
    xy = contract["components"][name].get("center_mm")
    if not isinstance(xy, list) or len(xy) != 2:
        raise ValueError(f"component {name} has no fixed center_mm")
    return float(xy[0]), float(xy[1])


def chord_mm(contract: dict, a: str, b: str) -> float:
    """Straight-line placement distance only; never a routed trace length."""
    ax, ay = _component_xy(contract, a)
    bx, by = _component_xy(contract, b)
    return math.hypot(ax - bx, ay - by)


def _validate_reference_xref(xref: dict, errors: list[str], warnings: list[str]) -> None:
    if xref.get("pair_membership_evidence") != "ALTIUM_DIFFERENTIALPAIRS6":
        errors.append("reference differential-pair membership is not source-native Altium evidence")
    if xref.get("source", {}).get("pcbdoc_sha256") != "641ef37a898217b3b2591e653d212322ea6044bb661b0b2a267a3ef24dfb8d93":
        errors.append("reference PcbDoc hash drifted from the pinned B2 source")

    pairs = {p.get("name"): p for p in xref.get("pairs", [])}
    missing = EXPECTED_REFERENCE_PAIRS - set(pairs)
    extra = set(pairs) - EXPECTED_REFERENCE_PAIRS
    if missing:
        errors.append(f"reference xref missing differential pairs: {sorted(missing)}")
    if extra:
        warnings.append(f"reference xref has additional differential pairs: {sorted(extra)}")

    for name in EXPECTED_REFERENCE_PAIRS & set(pairs):
        p = pairs[name]
        if not p.get("positive_endpoints") or not p.get("negative_endpoints"):
            errors.append(f"reference pair {name} has unresolved converted endpoints")
        if p.get("native_user_routed") != "TRUE":
            warnings.append(f"reference pair {name} is not marked USERROUTED=TRUE in DifferentialPairs6")

    topo = {d.get("domain"): d for d in xref.get("reference_topology", [])}
    usb2 = topo.get("USB2", {})
    if usb2.get("logical_link") != "USBC1<->U2":
        errors.append("reference USB2 topology must resolve USBC1<->U2")

    usb4 = topo.get("USB4", {})
    if usb4.get("logical_link") != "USBC1<->U2":
        errors.append("reference USB4 topology must resolve USBC1<->U2")
    usb4_names = {p.get("logical_pair") for p in usb4.get("split_pairs", [])}
    if usb4_names != {"UTX0", "UTX1", "URX0", "URX1"}:
        errors.append("reference USB4 split-pair topology is incomplete")
    for p in usb4.get("split_pairs", []):
        if len(p.get("positive_shared_components", [])) != 1 or len(p.get("negative_shared_components", [])) != 1:
            errors.append(f"reference USB4 pair {p.get('logical_pair')} does not resolve one P/N coupling component per polarity")

    pcie = topo.get("PCIE", {})
    if pcie.get("logical_link") != "U2<->CN1":
        errors.append("reference PCIe topology must resolve U2<->CN1")
    tx_lanes = {p.get("lane") for p in pcie.get("tx_split_pairs", [])}
    rx_lanes = {p.get("lane") for p in pcie.get("rx_pairs", [])}
    if tx_lanes != {0, 1, 2, 3}:
        errors.append("reference PCIe TX topology does not contain lanes 0..3")
    if rx_lanes != {0, 1, 2, 3}:
        errors.append("reference PCIe RX topology does not contain lanes 0..3")
    if pcie.get("reference_clock", {}).get("pair") != "RefCLK":
        errors.append("reference PCIe RefCLK pair is unresolved")

    caveats = " ".join(xref.get("caveats", []))
    if "not electrical skew" not in caveats:
        errors.append("reference xref must explicitly reject interpreting converted segment delta as skew")


def validate(contract: dict | None = None, reference_xref: dict | None = None) -> dict:
    c = contract or load()
    xref = reference_xref or load_reference_xref()
    errors: list[str] = []
    warnings: list[str] = []

    if c.get("status") != "PROVISIONAL_CONSTRAINT_MODEL_NOT_FABRICATION_READY":
        errors.append("routing contract must remain explicitly provisional")

    outline = c.get("board_outline_mm", {})
    width = float(outline.get("width", 0))
    length = float(outline.get("length", 0))
    if width <= 0 or length <= 0:
        errors.append("board outline must have positive dimensions")

    components = c.get("components", {})
    for name in ("ASM2464PD", "SPI_FLASH", "USB_C", "M2_2230"):
        if name not in components:
            errors.append(f"missing placement anchor {name}")
            continue
        try:
            x, y = _component_xy(c, name)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if not (0 <= x <= width and 0 <= y <= length):
            errors.append(f"placement anchor {name} lies outside controller PCB outline")

    if components.get("SERVICE", {}).get("placement_status") != "UNPLACED_FOOTPRINT_NOT_LOCKED":
        warnings.append("service connector placement changed; mechanical coupling must be reviewed")

    routes = {r.get("name"): r for r in c.get("debug_routes", [])}
    for name, ball in EXPECTED_ASM_BALLS.items():
        route = routes.get(name)
        if route is None:
            errors.append(f"missing required debug route {name}")
            continue
        if route.get("asm_ball") != ball:
            errors.append(f"{name} uses {route.get('asm_ball')!r}; expected ASM ball {ball}")
        expected_pin = EXPECTED_SERVICE_PINS[name]
        if int(route.get("service_pin", -1)) != expected_pin:
            errors.append(f"{name} service pin is not {expected_pin}")

    for name in SPI_NETS:
        route = routes.get(name, {})
        if route.get("programmer_attachment") != "FLASH_SIDE_OF_ISOLATION":
            errors.append(f"{name} programmer attachment must remain on flash side of isolation")
        normal = route.get("normal_path", [])
        if not any(isinstance(item, str) and item.endswith("REMOVABLE_SHUNT") for item in normal):
            errors.append(f"{name} normal path is missing explicit removable isolation")
        if route.get("flash_pin_status") != "UNRESOLVED_AUTHORITATIVE_PINOUT":
            warnings.append(f"{name} flash pin mapping changed; require authoritative U31 evidence")

    service = c.get("service_connector", {}).get("pins", {})
    if service.get("1") != "GND" or service.get("10") != "GND":
        errors.append("service connector must retain ground at pins 1 and 10")
    if service.get("2") != "VREF_SENSE":
        errors.append("service connector pin 2 must remain VREF_SENSE")

    hs = c.get("high_speed_domains", {})
    for domain in HIGH_SPEED_DOMAINS:
        d = hs.get(domain)
        if not d:
            errors.append(f"missing high-speed domain {domain}")
            continue
        metrics = d.get("actual_metrics", {})
        if d.get("geometry_status") == "UNROUTED" and any(value is not None for value in metrics.values()):
            errors.append(f"{domain} has claimed route metrics while Rev-A geometry is UNROUTED")
        if d.get("geometry_status") != "UNROUTED" and str(d.get("topology_status", "")).startswith("BLOCKED_"):
            errors.append(f"{domain} cannot claim routed geometry while topology remains blocked")
        constraints = set(d.get("constraints", []))
        for required in (
            "STACKUP_REQUIRED_BEFORE_TRACE_WIDTH_OR_GAP",
            "TARGET_DIFFERENTIAL_IMPEDANCE_REQUIRED",
            "CONTINUOUS_REFERENCE_PLANE",
            "MINIMIZE_VIAS_AND_STUBS",
            "NO_DEBUG_STUBS",
        ):
            if required not in constraints:
                errors.append(f"{domain} missing routing constraint {required}")

    _validate_reference_xref(xref, errors, warnings)

    lower_bounds = {}
    for key, endpoints in {
        "usb_c_to_asm": ("USB_C", "ASM2464PD"),
        "asm_to_m2": ("ASM2464PD", "M2_2230"),
        "asm_to_flash": ("ASM2464PD", "SPI_FLASH"),
    }.items():
        try:
            lower_bounds[key] = round(chord_mm(c, *endpoints), 4)
        except (KeyError, TypeError, ValueError):
            lower_bounds[key] = None

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "routing_sha256": sha256() if contract is None else None,
        "reference_pair_count": len(xref.get("pairs", [])),
        "reference_topology_domains": [d.get("domain") for d in xref.get("reference_topology", [])],
        "placement_chord_lower_bounds_mm": lower_bounds,
        "note": "Chord values are geometric lower bounds, not routed trace lengths; reference converted segment deltas are not electrical skew.",
    }
