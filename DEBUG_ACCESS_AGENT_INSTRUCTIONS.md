# ASM2464PD Firmware Development Access — Agent Instructions

## Objective

Extend the USB4 2230 project only enough to support two development operations:

1. **direct read/write/recovery of the ASM2464PD external firmware flash over SPI**;
2. **ASM2464PD firmware debug/console over UART**.

Do not add JTAG, SWD, I2C, generic GPIO, or other debug buses unless later evidence shows they are required for one of those two operations.

Read [`PROJECT_PLAN.md`](PROJECT_PLAN.md) and [`reference/datasheets/ASM2464PD.md`](reference/datasheets/ASM2464PD.md) before making electrical or mechanical changes.

## Authoritative controller pins

From the ASM2464PD datasheet reference currently used by this repo:

| Function | Ball/pin | Rev A net |
|---|---|---|
| UART TX | B21 | `ASM_UART_TX` |
| UART RX | A21 | `ASM_UART_RX` |
| SPI CS# | A2 | `ASM_SPI_CS_N` |
| SPI DO | A3 | `ASM_SPI_DO` |
| SPI DI | A4 | `ASM_SPI_DI` |
| SPI CLK | A5 | `ASM_SPI_CLK` |
| Reset | H1 | `ASM_RST_N` |

Treat voltage levels and reset-time drive state as unresolved until verified. Do not infer them solely from signal names.

## Reference-board work

Trace the Leaves232 B2 schematic/PCB and create:

- `reference/leaves232-b2/debug-net-map.md`
- `reference/leaves232-b2/debug-net-map.json`

For UART and U31 only, record:

- ASM pin/ball;
- B2 net name;
- connected component pin/pad;
- board coordinate;
- voltage rail;
- evidence source;
- confidence.

U31 is currently identified as `ZD25WQ16CEIGR`. Confirm its exact pinout and supply voltage from an authoritative flash datasheet before finalizing the connector.

## UART implementation

Route `ASM_UART_TX`, `ASM_UART_RX`, GND, and `VREF_SENSE` to the service connector.

Rules:

- TX/RX labels are target-relative.
- `VREF_SENSE` is measurement/reference only; do not use it to power the target.
- Determine the UART logic voltage before attaching any adapter.
- Do not commit a baud rate until runtime/firmware evidence proves it.
- Keep UART routing short and clear of USB4 differential pairs.

## Direct SPI flash implementation

Expose the flash bus required by a standard SPI programmer:

- `ASM_SPI_CS_N`
- `ASM_SPI_CLK`
- `ASM_SPI_DI` / programmer MOSI direction
- `ASM_SPI_DO` / programmer MISO direction
- GND
- flash-rail `VREF_SENSE`

The programmer must connect to the flash side of any isolation element.

### Mandatory bus-ownership proof

Before declaring in-circuit programming safe:

1. assert `ASM_RST_N`;
2. observe CS#, CLK, DI, and DO with a scope/logic analyzer;
3. prove whether the ASM2464PD releases the bus;
4. document the result.

If reset reliably tri-states the ASM flash interface, use reset as the normal programming gate.

If reset does **not** reliably release the bus, revise the PCB with deliberate isolation. Do not allow simultaneous programmer/controller drive and do not require functional firmware to surrender the bus.

## Rev A connector

Default provisional footprint: keyed 2x5, 1.27 mm pitch.

| Pin | Signal |
|---:|---|
| 1 | GND |
| 2 | VREF_SENSE |
| 3 | ASM_UART_TX |
| 4 | ASM_UART_RX |
| 5 | ASM_RST_N |
| 6 | ASM_SPI_CS_N |
| 7 | ASM_SPI_CLK |
| 8 | ASM_SPI_DI |
| 9 | ASM_SPI_DO |
| 10 | GND |

If mechanical constraints favor Tag-Connect/pogo, preserve the same logical interface.

## Flash-programming validation

The recovery workflow must prove all of the following on sacrificial/rework-safe hardware:

1. target flash voltage measured and documented;
2. controller held in the verified non-driving state;
3. programmer reads correct JEDEC ID;
4. complete flash backup can be read twice with matching hashes;
5. erase/program operation is range/size checked;
6. programmed contents are read back and hash-verified;
7. controller boots after programmer removal and reset release.

Never make the first write before a complete verified backup exists.

## UART validation

Demonstrate:

- boot-time output capture;
- stable RX/TX electrical levels;
- confirmed baud/framing recorded in `docs/uart-debug.md`;
- bidirectional communication if the firmware console accepts input.

## Mechanical implementation

Extend the CadQuery enclosure model with:

- exact debug connector body;
- mating cable/plug keepout;
- service aperture/cover;
- adequate strain relief/finger clearance.

Keep the service connector accessible while the enclosure is fully assembled. It must not compromise the ASM2464PD thermal interface, SSD fit, or USB-C cable clearance.

## Definition of done

Rev A is firmware-development-ready when we can, without opening the enclosure:

- connect a level-compatible UART adapter and capture ASM2464PD firmware debug output;
- hold/isolate the ASM safely;
- connect an SPI programmer;
- back up, erase, program, read back, and verify U31;
- release reset/isolation and boot the ASM normally.
