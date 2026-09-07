# ASM2464PD USB4 / M.2 2230 Development Enclosure

Open development project for a compact ASM2464PD + M.2 2230 USB4 enclosure with firmware-development access built into the board and enclosure.

The immediate goals are:

1. reproduce and then improve the public Leaves232 ASM2464PD 2230 reference design;
2. expose the ASM2464PD firmware UART cleanly;
3. provide safe direct SPI access to the external firmware flash for backup, programming, and recovery;
4. co-design the PCB and enclosure rather than treating the enclosure as an afterthought;
5. use an agent-driven KiCad workflow modeled on the public i2cjak Astra + KiStack + T3CAD demonstration;
6. validate as much of the digital/recovery path as possible in simulation before Rev-A hardware.

## Agent-driven workflow

The public i2cjak demonstration describes an Astra-Medium hardware-design loop using **KiStack** and **T3CAD**, a T3Code fork. The exact private prompt/transcript is not published, so this repository documents a reproducible workflow built from the public tools and their documented capabilities.

Start with [`WORKFLOW.md`](WORKFLOW.md). Agents working in this repository must also read [`AGENTS.md`](AGENTS.md) and [`PROJECT_PLAN.md`](PROJECT_PLAN.md).

## Reference design

Electrical/mechanical reference:

- https://github.com/Leaves232/2230-USB4-SSD-Enclosure-Design

Controller datasheet pointer and extracted debug pins:

- [`reference/datasheets/ASM2464PD.md`](reference/datasheets/ASM2464PD.md)

Known reference-board geometry already extracted from the public Gerber/PnP data:

- controller PCB: **22.039834 x 33.680400 mm**;
- ASM2464PD center: **(13.667257, 14.870481) mm** from the PCB lower-left datum;
- USB-C center: **(11.069980, 29.187267) mm**;
- M.2 connector center: **(11.023879, 4.697654) mm**.

## Firmware-development interface

The service interface is deliberately narrow:

- ASM UART TX/RX;
- ASM reset;
- direct external SPI flash CS#/CLK/DI/DO;
- GND and target-voltage sense.

See [`DEBUG_ACCESS_AGENT_INSTRUCTIONS.md`](DEBUG_ACCESS_AGENT_INSTRUCTIONS.md).

## Mechanical model

The current CadQuery fit model lives at:

```text
mechanical/rev-a/model.py
```

Build it with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r mechanical/rev-a/requirements.txt
python mechanical/rev-a/model.py
```

Generated STEP/STL files belong under `mechanical/rev-a/build/` and are intentionally ignored until geometry is mature enough to publish as release artifacts.

## Integrated CAD / simulation milestone

The shared board configuration now drives service access, physical SPI-isolation shunts, flash envelope clearance, and the virtual board. The imported upstream CPU executes project-owned reference firmware through UART/SPI MMIO. The CPU regression proves boot, deliberate flash bricking, programmer erase/program/full readback, and recovered boot.

```bash
python -m pip install -r simulation/requirements.txt
# Install SDCC using your platform package manager, then:
python scripts/validate_twin.py
```

See [validation outputs and limits](validation/README.md) and [the shared recovery design](hardware/rev-a/RECOVERY.md). STEP/STL and assembled/exploded PNGs are generated in `mechanical/rev-a/build/`. Geometry includes explicit provisional component envelopes, not a completed routed PCB.

**RTL remains blocked:** no Verilog/RTL sources exist at the inspected authoritative branch tip. `--require-rtl` makes that missing gate fail explicitly. No full dual-backend or fabrication-ready claim is made.

## Design gates before fabrication

Do not release Rev A for fabrication until:

- reference schematic/net tracing is complete;
- UART voltage is established;
- SPI flash voltage and exact part pinout are established;
- ASM SPI bus ownership under reset is proven or explicit isolation is designed;
- ERC/DRC pass with reviewed waivers only;
- USB4/PCIe routing rules are reviewed independently of generic DRC;
- debug connector and mating-cable keepouts are present in the mechanical model;
- ASM thermal path is modeled and does not depend on a printed-plastic lid for sustained operation;
- recovery simulation proves brick -> external flash recovery -> reboot -> UART confirmation.

## Source toolchain

- KiCad / `kicad-cli`
- KiStack: https://github.com/American-Embedded/KiStack
- T3CAD: https://github.com/i2cjak/T3CAD
- CadQuery
- Verilator + cocotb for digital board simulation
- ngspice/Xyce where circuit-level modeling is useful
- LiteNVMe as an optional open NVMe-host reference

## License / reference hygiene

Do not copy third-party source designs or datasheets into this repository unless their license permits redistribution. Keep source pointers, extracted facts, and our own derived design work separated and attributed.
