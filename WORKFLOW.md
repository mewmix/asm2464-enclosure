# Agent-Driven Hardware Workflow

## What this reproduces from the public demo

The public i2cjak post says Astra-Medium produced a Bluetooth hardware design in roughly 30 minutes using **KiStack** and **T3CAD**, i2cjak's fork of T3Code. Public T3CAD documentation describes an agent-driven workspace combining coding-agent conversations with KiCad schematic, PCB, Gerber, BOM, footprint, symbol, 3D, and analysis views. KiStack is a human-written set of agent skills for KiCad schematic work, PCB review, parts/BOM decisions, exports, and Gerber review.

The original private agent transcript is not public. This document therefore defines our reproducible equivalent for the ASM2464PD enclosure.

## 0. Install the toolchain

### KiCad

Install a current KiCad release and ensure `kicad-cli` is on PATH. T3CAD requires `kicad-cli` for BOM, footprint, symbol, and 3D previews. Python 3 is required for Gerber previews.

Verify:

```bash
kicad-cli version
python3 --version
```

### KiStack

Install the public KiStack skills:

```bash
npx skills add American-Embedded/kistack
```

Source: https://github.com/American-Embedded/KiStack

### T3CAD

T3CAD currently runs from source. Install Vite+ first.

macOS/Linux:

```bash
curl -fsSL https://vite.plus | bash
```

Windows PowerShell:

```powershell
irm https://vite.plus/ps1 | iex
```

Then:

```bash
git clone https://github.com/i2cjak/T3CAD.git
cd T3CAD
vp i
vp run dev
```

Open the pairing URL printed by the development server. `vp run dev --share` can expose the isolated development environment over a Tailnet.

### Mechanical CAD

The tweet's stack is electronics-focused. This repository adds CadQuery for deterministic, version-controlled enclosure geometry:

```bash
pip install -r mechanical/rev-a/requirements.txt
```

## 1. Start the agent in repository mode

The agent must work against this repository, not a blank chat.

First read, in order:

1. `AGENTS.md`
2. `PROJECT_PLAN.md`
3. `DEBUG_ACCESS_AGENT_INSTRUCTIONS.md`
4. `reference/datasheets/ASM2464PD.md`
5. the Leaves232 reference repo and its schematic/PCB/fabrication files

The first response should summarize hard facts, assumptions, unresolved electrical questions, and the next smallest design pass. Do not immediately generate a finished PCB.

## 2. Ingest the reference design as evidence

Use the Leaves232 project as a read-only baseline:

- schematic and PCB source;
- Gerbers and drill files;
- BOM;
- pick-and-place;
- board outline;
- component placement;
- power tree;
- USB-C / ASM2464PD / M.2 topology;
- external flash connection;
- thermal constraints.

Create project-owned evidence artifacts rather than modifying the reference:

```text
reference/leaves232-b2/
  debug-net-map.md
  debug-net-map.json
  power-tree.md
  placement-datums.json
  open-questions.md
```

Every assertion should be classified as one of:

- `DATASHEET`
- `REFERENCE_SCHEMATIC`
- `REFERENCE_PCB`
- `FAB_OUTPUT`
- `MEASURED`
- `INFERRED`
- `ASSUMED`

## 3. Freeze the design contract before placement

Create/maintain a short design contract containing:

- exact board outline target;
- M.2 2230 mechanical datum;
- USB-C receptacle and shell datum;
- ASM2464PD footprint and thermal region;
- external SPI flash part/footprint;
- power requirements;
- UART/SPI recovery connector choice;
- enclosure/service-access constraints;
- manufacturing stackup and routing constraints.

Do not let the agent silently change these during routing.

## 4. Schematic pass

Use the agent + KiStack to reconstruct/author the KiCad schematic in small subsystems:

1. USB-C input and protection;
2. ASM2464PD core/power/clock/reset;
3. external SPI firmware flash;
4. UART + SPI recovery/service connector;
5. PCIe/M.2 interface;
6. power regulators and sequencing;
7. status LED and remaining support circuits.

After each subsystem:

```bash
kicad-cli sch erc <project>.kicad_sch
```

Review warnings deliberately. Do not suppress ERC failures merely to get a green build.

## 5. Footprint lock

Before serious PCB placement, lock footprints whose physical geometry affects the enclosure:

- USB-C receptacle;
- ASM2464PD;
- M.2 connector;
- SPI flash;
- large capacitors/inductors;
- service/debug connector;
- mounting/retention hardware.

Confirm courtyard, body height, pin-1 orientation, and 3D model where available.

This is a critical correction to a pure "one-shot" flow: an electrically valid connector that cannot be assembled or accessed is still a failed design.

## 6. PCB placement loop in T3CAD

Use T3CAD as the steering surface while the agent edits the KiCad project.

Recommended loop:

1. agent proposes one placement/routing pass;
2. inspect schematic/PCB/3D in T3CAD;
3. compare against the frozen mechanical datums;
4. run DRC;
5. inspect high-speed routes manually;
6. commit a small checkpoint;
7. continue.

Prioritize placement in this order:

1. board outline / USB-C / M.2 mechanical endpoints;
2. ASM2464PD;
3. clocks and high-speed support parts;
4. power conversion/decoupling;
5. SPI flash close to ASM;
6. recovery connector where it remains accessible in the final enclosure;
7. remaining low-speed support circuitry.

## 7. High-speed routing rules

Do not treat generic autorouting or a clean DRC as proof of USB4/PCIe correctness.

The agent must explicitly track:

- stackup and target differential impedance;
- pair geometry and reference plane continuity;
- intra-pair and inter-pair skew budgets where applicable;
- via count and stubs;
- return-path discontinuities;
- connector escape geometry;
- separation from switch-node/power noise;
- avoidance of unnecessary debug/test stubs on high-speed nets.

Reference-design topology is evidence, not permission to copy blindly.

## 8. Firmware-development access

Implement only what we need:

- `ASM_UART_TX`
- `ASM_UART_RX`
- `ASM_RST_N`
- `ASM_SPI_CS_N`
- `ASM_SPI_CLK`
- `ASM_SPI_DI`
- `ASM_SPI_DO`
- GND
- target-voltage sense

Direct flash programming must be safe even when firmware is corrupt. Bus ownership is a hardware requirement. Prove that reset tri-states the ASM SPI pins, or provide explicit isolation.

See `DEBUG_ACCESS_AGENT_INSTRUCTIONS.md`.

## 9. Mechanical co-design

After the first stable placement pass, regenerate the CadQuery enclosure from actual PCB datums rather than hand-entered estimates.

Mechanical iteration must check:

- board outline;
- USB-C opening and plug insertion envelope;
- SSD envelope;
- component maximum-Z envelopes;
- debug connector body and mating cable;
- thermal pad/spreader compression;
- screw/clip locations;
- assembly sequence;
- service access without opening the enclosure.

Run PCB and enclosure development in parallel from this point onward.

## 10. Simulation before fabrication

The CPU/firmware emulator is not enough by itself. Add a board-level digital twin:

```text
ASM CPU/FW emulator
  |-- SPI controller -> behavioral flash -> virtual external programmer
  |-- UART -> virtual terminal/test harness
  |-- reset/boot -> board state model
  `-- PCIe/NVMe -> synthetic endpoint / optional LiteNVMe differential oracle
```

Required recovery simulation:

1. boot known-good flash image;
2. capture UART boot output;
3. corrupt or erase the boot image;
4. verify failed boot behavior;
5. assert the modeled safe SPI-ownership state;
6. externally identify/read/erase/program the flash;
7. verify readback/hash;
8. release programmer/reset;
9. boot recovered firmware;
10. require UART confirmation.

Fault-injection cases should include interrupted page program, interrupted erase, brownout, stale WIP/WEL state, bad JEDEC response, reset races, and simulated bus contention.

LiteNVMe is a **secondary reference**, not an ASM oracle. Stock ASM evidence remains authoritative when reproducing ASM-specific behavior.

## 11. Automated validation loop

Each meaningful design pass should run as much of this as applies:

```text
schematic ERC
PCB DRC
BOM generation/review
footprint review
Gerber export/review
pick-and-place export/review
3D inspection
mechanical model build
clearance/interference checks
simulation regression
```

The point of KiStack/T3CAD is not "AI drew a PCB." The useful loop is **agent edit -> deterministic validation -> visual inspection -> evidence update -> next agent edit**.

## 12. Pre-fabrication signoff

Before ordering Rev A, require all of the following:

- schematic reviewed subsystem-by-subsystem;
- DRC/ERC clean or every waiver documented;
- high-speed routing independently reviewed;
- pin-1/orientation review complete;
- BOM availability checked;
- flash/UART voltage assumptions resolved;
- safe external-flash ownership strategy resolved;
- fabrication outputs reviewed, not merely generated;
- enclosure interference check passes;
- thermal strategy defined;
- brick/recovery simulation passes;
- manufacturing notes and bring-up procedure committed.

## 13. Fabricate, assemble, and close the loop

On physical Rev A:

1. inspect bare/assembled board;
2. validate rails before fitting the ASM/SSD where practical;
3. measure reset and flash-bus behavior;
4. back up factory/reference flash before writing;
5. validate UART electrically;
6. perform controlled SPI recovery test on sacrificial/rework-safe hardware;
7. exercise USB4/NVMe and thermal behavior;
8. feed measured dimensions, temperatures, signal captures, and failures back into the repo as `MEASURED` evidence.

The physical board is the final oracle for the next revision.

## Canonical agent prompt

Use this as the starting instruction for an Astra/Work/Codex-style agent:

> Work directly in `mewmix/asm2464-enclosure`. Read `AGENTS.md`, `WORKFLOW.md`, `PROJECT_PLAN.md`, `DEBUG_ACCESS_AGENT_INSTRUCTIONS.md`, and the ASM2464PD datasheet notes before editing anything. Treat the Leaves232 ASM2464PD 2230 project as a read-only reference baseline. Reconstruct evidence first, then make the smallest reviewable design change. Use KiStack for KiCad-specific validation and T3CAD as the visual steering/inspection workspace. After every schematic or PCB pass run the available ERC/DRC/export checks and record unresolved assumptions. Preserve high-speed topology and mechanical datums deliberately. Our required service access is only UART debug plus safe direct SPI firmware-flash recovery. Do not claim a design is fabrication-ready until electrical, mechanical, thermal, recovery-simulation, and manufacturing-output gates in `WORKFLOW.md` pass. Commit small, auditable changes with evidence and validation notes.
