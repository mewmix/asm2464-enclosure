"""Evidence-bound board routing/topology model.

This module intentionally models only facts and constraints that are supported by
repository evidence. It does not synthesize USB4/PCIe copper geometry, impedance,
via counts, or skew values when those inputs are unresolved.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTING_PATH = ROOT / "hardware/rev-a/routing.json"

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


def load(path: Path | None = None) -> dict:
    return json.loads((path or ROUTING_PATH).read_text())


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


def validate(contract: dict | None = None) -> dict:
    c = contract or load()
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
        blocked = str(d.get("topology_status", "")).startswith("BLOCKED_")
        metrics = d.get("actual_metrics", {})
        if blocked and any(value is not None for value in metrics.values()):
            errors.append(f"{domain} has fabricated route metrics while topology is unresolved")
        if blocked and d.get("geometry_status") != "UNROUTED":
            errors.append(f"{domain} cannot claim routed geometry before exact net-map extraction")
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
        "placement_chord_lower_bounds_mm": lower_bounds,
        "note": "Chord values are geometric lower bounds, not routed trace lengths.",
    }
