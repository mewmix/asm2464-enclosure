from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import cadquery as cq
from cadquery import exporters


@dataclass(frozen=True)
class Design:
    # Source-derived controller PCB geometry (Leaves232 B2 Gerber / PnP).
    pcb_w: float = 22.039834
    pcb_l: float = 33.680400
    pcb_t: float = 1.60
    usb_x_from_pcb_left: float = 11.0699804
    usb_y_from_pcb_bottom: float = 29.1872670
    m2_x_from_pcb_left: float = 11.0238794
    m2_y_from_pcb_bottom: float = 4.6976538
    asm_x_from_pcb_left: float = 13.6672574
    asm_y_from_pcb_bottom: float = 14.8704808

    # Standard M.2 2230 mechanical envelope.
    ssd_w: float = 22.00
    ssd_l: float = 30.00
    ssd_t: float = 0.80

    # Assembly assumptions. The M.2 socket is ~4.7 mm from the PCB edge;
    # a 5 mm overlap makes the SSD edge land essentially at the socket center.
    ssd_controller_overlap: float = 5.00
    controller_z: float = 2.50
    ssd_z: float = 0.80

    # Prototype shell parameters.
    xy_clearance: float = 0.35
    wall: float = 1.50
    floor: float = 1.20
    roof: float = 1.50
    internal_height: float = 8.00
    corner_r: float = 2.00
    lid_fit: float = 0.20
    lid_tongue_h: float = 2.30
    lid_tongue_wall: float = 1.00

    # USB-C service opening. Conservative fit-test dimensions, not connector spec.
    usb_open_w: float = 10.50
    usb_open_h: float = 4.50
    usb_open_bottom_z: float = 1.45

    # Thermal interface region over ASM2464PD.
    thermal_insert_w: float = 16.0
    thermal_insert_l: float = 18.0
    thermal_insert_t: float = 0.80
    thermal_pocket_clearance: float = 0.20

    @property
    def assembly_l(self) -> float:
        return self.ssd_l + self.pcb_l - self.ssd_controller_overlap

    @property
    def inner_w(self) -> float:
        return max(self.pcb_w, self.ssd_w) + 2 * self.xy_clearance

    @property
    def inner_l(self) -> float:
        return self.assembly_l + 2 * self.xy_clearance

    @property
    def case_w(self) -> float:
        return self.inner_w + 2 * self.wall

    @property
    def case_l(self) -> float:
        return self.inner_l + 2 * self.wall

    @property
    def base_h(self) -> float:
        return self.floor + self.internal_height

    @property
    def case_h(self) -> float:
        return self.base_h + self.roof

    @property
    def pcb_y0(self) -> float:
        return self.ssd_l - self.ssd_controller_overlap

    def local_x(self, pcb_x_from_left: float) -> float:
        return pcb_x_from_left - self.pcb_w / 2

    def assembly_y(self, pcb_y_from_bottom: float) -> float:
        return self.pcb_y0 + pcb_y_from_bottom - self.assembly_l / 2


def rounded_box(w: float, l: float, h: float, r: float) -> cq.Workplane:
    solid = cq.Workplane("XY").box(w, l, h, centered=(True, True, False))
    if r > 0:
        solid = solid.edges("|Z").fillet(r)
    return solid


def make_base(d: Design) -> cq.Workplane:
    base = rounded_box(d.case_w, d.case_l, d.base_h, d.corner_r)

    cavity = (
        cq.Workplane("XY")
        .box(d.inner_w, d.inner_l, d.base_h - d.floor + 0.05, centered=(True, True, False))
        .translate((0, 0, d.floor))
    )
    base = base.cut(cavity)

    usb_cut = (
        cq.Workplane("XY")
        .box(d.usb_open_w, d.wall * 4.0, d.usb_open_h, centered=(True, True, False))
        .translate((0, d.case_l / 2, d.usb_open_bottom_z))
    )
    base = base.cut(usb_cut)

    rail_w = 1.20
    rail_h = 0.65
    rail_l = d.inner_l - 2.0
    rail_x = d.inner_w / 2 - rail_w / 2
    for x in (-rail_x, rail_x):
        rail = (
            cq.Workplane("XY")
            .box(rail_w, rail_l, rail_h, centered=(True, True, False))
            .translate((x, 0, d.floor))
        )
        base = base.union(rail)

    return base


def make_lid(d: Design) -> cq.Workplane:
    roof = rounded_box(d.case_w, d.case_l, d.roof, d.corner_r).translate(
        (0, 0, d.lid_tongue_h)
    )

    tongue_outer_w = d.inner_w - 2 * d.lid_fit
    tongue_outer_l = d.inner_l - 2 * d.lid_fit
    tongue_inner_w = tongue_outer_w - 2 * d.lid_tongue_wall
    tongue_inner_l = tongue_outer_l - 2 * d.lid_tongue_wall

    tongue_outer = cq.Workplane("XY").box(
        tongue_outer_w, tongue_outer_l, d.lid_tongue_h,
        centered=(True, True, False),
    )
    tongue_inner = cq.Workplane("XY").box(
        tongue_inner_w, tongue_inner_l, d.lid_tongue_h + 0.05,
        centered=(True, True, False),
    )
    tongue = tongue_outer.cut(tongue_inner)

    lid = roof.union(tongue)

    tongue_notch = (
        cq.Workplane("XY")
        .box(d.usb_open_w + 1.0, d.wall * 4.0, d.lid_tongue_h + 0.1,
             centered=(True, True, False))
        .translate((0, tongue_outer_l / 2, 0))
    )
    lid = lid.cut(tongue_notch)

    asm_x = d.local_x(d.asm_x_from_pcb_left)
    asm_y = d.assembly_y(d.asm_y_from_pcb_bottom)
    pocket = (
        cq.Workplane("XY")
        .box(
            d.thermal_insert_w + 2 * d.thermal_pocket_clearance,
            d.thermal_insert_l + 2 * d.thermal_pocket_clearance,
            d.thermal_insert_t,
            centered=(True, True, False),
        )
        .translate((asm_x, asm_y, d.lid_tongue_h))
    )
    lid = lid.cut(pocket)
    return lid


def make_pcb_proxy(d: Design) -> cq.Workplane:
    y = d.pcb_y0 + d.pcb_l / 2 - d.assembly_l / 2
    return (
        cq.Workplane("XY")
        .box(d.pcb_w, d.pcb_l, d.pcb_t, centered=(True, True, False))
        .translate((0, y, d.floor + d.controller_z))
    )


def make_ssd_proxy(d: Design) -> cq.Workplane:
    y = d.ssd_l / 2 - d.assembly_l / 2
    return (
        cq.Workplane("XY")
        .box(d.ssd_w, d.ssd_l, d.ssd_t, centered=(True, True, False))
        .translate((0, y, d.floor + d.ssd_z))
    )


def make_thermal_insert(d: Design) -> cq.Workplane:
    asm_x = d.local_x(d.asm_x_from_pcb_left)
    asm_y = d.assembly_y(d.asm_y_from_pcb_bottom)
    z = d.base_h + d.roof - d.thermal_insert_t
    return (
        cq.Workplane("XY")
        .box(d.thermal_insert_w, d.thermal_insert_l, d.thermal_insert_t,
             centered=(True, True, False))
        .translate((asm_x, asm_y, z))
    )


def export_all(out_dir: Path) -> None:
    d = Design()
    out_dir.mkdir(parents=True, exist_ok=True)

    base = make_base(d)
    lid = make_lid(d)
    pcb = make_pcb_proxy(d)
    ssd = make_ssd_proxy(d)
    lid_assembled = lid.translate((0, 0, d.base_h - d.lid_tongue_h))
    thermal = make_thermal_insert(d)

    parts = {
        "base": base,
        "lid": lid,
        "pcb_proxy": pcb,
        "ssd_2230_proxy": ssd,
        "thermal_insert": thermal,
    }

    for name, solid in parts.items():
        exporters.export(solid, str(out_dir / f"{name}.step"))
        exporters.export(
            solid,
            str(out_dir / f"{name}.stl"),
            tolerance=0.05,
            angularTolerance=0.1,
        )

    compound = cq.Compound.makeCompound([
        base.val(), lid_assembled.val(), pcb.val(), ssd.val(), thermal.val()
    ])
    exporters.export(compound, str(out_dir / "assembly.step"))

    dimensions = {
        "source_derived_mm": {
            "controller_pcb_width": d.pcb_w,
            "controller_pcb_length": d.pcb_l,
            "usb_c_center_from_pcb_left_bottom": [d.usb_x_from_pcb_left, d.usb_y_from_pcb_bottom],
            "m2_connector_center_from_pcb_left_bottom": [d.m2_x_from_pcb_left, d.m2_y_from_pcb_bottom],
            "asm2464pd_center_from_pcb_left_bottom": [d.asm_x_from_pcb_left, d.asm_y_from_pcb_bottom],
        },
        "assumed_mm": {
            "m2_2230_envelope": [d.ssd_w, d.ssd_l, d.ssd_t],
            "ssd_controller_overlap": d.ssd_controller_overlap,
            "xy_clearance_per_side": d.xy_clearance,
            "wall": d.wall,
            "floor": d.floor,
            "roof": d.roof,
            "internal_height_above_floor": d.internal_height,
        },
        "result_mm": {
            "assembled_electronics_length": d.assembly_l,
            "outer_width": d.case_w,
            "outer_length": d.case_l,
            "outer_height": d.case_h,
            "asm2464pd_case_xy": [
                d.local_x(d.asm_x_from_pcb_left),
                d.assembly_y(d.asm_y_from_pcb_bottom),
            ],
        },
    }
    (out_dir / "dimensions.json").write_text(
        json.dumps(dimensions, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    export_all(Path(__file__).resolve().parent / "build")
