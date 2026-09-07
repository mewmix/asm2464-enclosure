# USB4 2230 ASM2464PD Development Enclosure — Repo Plan

## Project goal

Build our own compact ASM2464PD + M.2 2230 USB4 enclosure that is also usable as a firmware-development unit.

The development scope is deliberately narrow:

1. **Write/read the ASM2464PD external firmware flash directly over SPI.**
2. **Debug/controller-console access over UART.**

Do not add JTAG, SWD, I2C, general GPIO, or unrelated service interfaces unless a later requirement explicitly needs them.

## Primary references

### ASM2464PD datasheet

See [`reference/datasheets/ASM2464PD.md`](reference/datasheets/ASM2464PD.md).

Relevant controller pins currently extracted from the datasheet:

- `UART_TX` — B21
- `UART_RX` — A21
- `SPI_CS#` — A2
- `SPI_DO` — A3
- `SPI_DI` — A4
- `SPI_CLK` — A5
- `RST#` — H1

### Reference PCB

Use Leaves232's `2230-USB4-SSD-Enclosure-Design` as the electrical/mechanical reference baseline. Keep imported/reference material read-only and place our changes under our own `hardware/` and `mechanical/` trees.

Known reference-board facts already extracted:

- controller PCB: 22.039834 x 33.680400 mm;
- controller: ASM2464PD;
- external serial flash: U31, `ZD25WQ16CEIGR`;
- M.2 2230 SSD extends beyond the controller PCB;
- USB-C receptacle is at the controller-board end.

## Repository structure target

```text
reference/
  datasheets/
    ASM2464PD.md
  leaves232-b2/
    README.md
    debug-net-map.md
hardware/
  rev-a/
    schematic/
    pcb/
    fab/
mechanical/
  rev-a/
    model.py
    build/
validation/
  electrical/
  mechanical/
docs/
  uart-debug.md
  flash-programming.md
README.md
PROJECT_PLAN.md
```

## Rev A debug connector

Prefer a compact keyed 2x5, 1.27 mm header or an equivalent pogo/Tag-Connect footprint if enclosure constraints make a permanent header undesirable.

Provisional signals:

| Pin | Signal | Direction/role |
|---:|---|---|
| 1 | GND | reference |
| 2 | VREF_SENSE | target logic/flash-rail sense only |
| 3 | ASM_UART_TX | target -> adapter |
| 4 | ASM_UART_RX | adapter -> target |
| 5 | ASM_RST_N | controller reset control |
| 6 | ASM_SPI_CS_N | flash programming |
| 7 | ASM_SPI_CLK | flash programming |
| 8 | ASM_SPI_DI | programmer MOSI -> flash path |
| 9 | ASM_SPI_DO | flash -> programmer MISO path |
| 10 | GND | reference |

Do **not** provide debugger/programmer power to the board through `VREF_SENSE`.

## Electrical architecture

### UART

Route the dedicated ASM2464PD UART pins directly to the development connector through optional small series resistors/0-ohm links for bring-up flexibility.

Requirements:

- determine and document the UART I/O voltage before attaching an adapter;
- label TX/RX from the **target's perspective**;
- keep the traces short and away from USB4 high-speed pairs;
- provide a solid ground next to the UART pins.

The repo must not hard-code a UART baud rate unless it is confirmed from firmware/runtime evidence. Datasheet pin identity and runtime serial configuration are separate facts.

### Direct SPI flash programming

U31 is the firmware flash. The direct-programming path must reach the **flash-side** SPI nets:

- CS#
- CLK
- DI/MOSI
- DO/MISO
- target ground
- target flash-rail reference/sense

The normal operating path remains ASM2464PD <-> U31.

The key design problem is **bus ownership**. An external programmer must never fight the ASM2464PD for the flash bus.

Implementation order:

1. Prove whether asserting `ASM_RST_N` causes the controller to release/tri-state all four SPI signals.
2. If yes, document `RST# asserted + board powered normally` as the preferred in-circuit programming state.
3. If no, add a deliberate hardware isolation mechanism in Rev A. Prefer a low-risk topology that does not compromise normal flash timing/signal integrity. Do not rely on software cooperation from potentially corrupted firmware.

Avoid casually inserting analog switches or long stubs into the production flash bus. If active isolation is needed, it requires signal-integrity review and validation at the actual flash clock rate.

## Bring-up workflow target

### UART firmware debug

1. Power the enclosure normally over USB-C.
2. Connect GND and `VREF_SENSE` first.
3. Confirm logic voltage.
4. Connect adapter RX to `ASM_UART_TX` and adapter TX to `ASM_UART_RX`.
5. Capture boot/debug output.
6. Determine baud/framing from verified firmware/runtime evidence and record it in `docs/uart-debug.md`.

### Direct flash write/recovery

1. Power the target using the documented target-power method.
2. Assert the verified controller-reset/isolation state.
3. Confirm the ASM is not driving CS#/CLK/DI/DO.
4. Connect programmer GND and sense the target flash voltage.
5. Read flash JEDEC ID.
6. Read and save a complete backup before any write/erase.
7. Program only a verified image with size/range checks.
8. Read back and verify the programmed contents.
9. Disconnect programmer.
10. Release reset/isolation and boot normally.

## Mechanical requirements

The enclosure model must include:

- the real debug-connector body;
- mating-plug/cable keepout;
- service aperture or removable cover;
- no collision with the USB-C cable;
- no interference with the M.2 2230 SSD;
- no loss of thermal contact to the ASM2464PD heat spreader/pad.

The header must be usable with the device fully assembled.

## Validation gates

Rev A is not complete until all of these are demonstrated:

- UART TX/RX continuity from ASM balls to connector;
- UART voltage confirmed;
- SPI CS#/CLK/DI/DO continuity from U31/ASM bus to connector;
- flash voltage confirmed;
- reset/isolation behavior proven with a scope or logic analyzer;
- external programmer can read JEDEC ID without contention;
- full flash can be backed up and verified;
- a test image can be written/read-back verified on sacrificial hardware;
- UART can capture controller boot/debug output;
- enclosure/cable/header clearances pass mechanically;
- ASM thermal-interface geometry remains intact.

## Deliverable sequence

1. `reference: add ASM2464PD datasheet facts and source pointer`
2. `reference: trace B2 UART and external-flash nets`
3. `hardware: add Rev A UART + direct-SPI service connector`
4. `hardware: add reset/isolation strategy for safe flash programming`
5. `mechanical: model debug connector and service aperture`
6. `docs: add UART debug and flash recovery procedures`
7. `validation: prove bus release, flash read/write, UART capture, and enclosure clearance`
