# ASM2464PD Datasheet Reference

Authoritative device reference for the controller used by this project.

## Public datasheet copy

- **ASM2464PD Data Sheet, R0.2**
- Public mirror: https://egpu.io/wp-content/uploads/wpforo/attachments/160145/15128-ASM2464PDData-SheetR02-2.pdf

Do not vendor or redistribute the PDF unless its redistribution rights are confirmed. Keep this repository entry as a source pointer plus project-specific extracted facts.

## Firmware-development signals used by this project

| Function | ASM2464PD ball/pin | Project net name | Purpose |
|---|---|---|---|
| UART TX | B21 | `ASM_UART_TX` | Controller debug/console output |
| UART RX | A21 | `ASM_UART_RX` | Controller debug/console input |
| SPI chip select | A2 | `ASM_SPI_CS_N` | External firmware-flash select |
| SPI data out | A3 | `ASM_SPI_DO` | ASM -> flash / programmer MISO-side observation |
| SPI data in | A4 | `ASM_SPI_DI` | Flash/programmer -> ASM MOSI-side input |
| SPI clock | A5 | `ASM_SPI_CLK` | External firmware-flash clock |
| Reset | H1 | `ASM_RST_N` | Hold/reset controller during recovery/programming if electrically valid |

The datasheet exposes a **4-wire SPI interface** for the external firmware flash. This repo should not describe the ASM2464PD side as QSPI unless later evidence proves additional controller flash-I/O pins exist.

## Design rule

These extracted pin assignments are permitted as schematic/PCB requirements, but voltage levels, reset-time pin states, and in-circuit programming behavior must still be verified electrically before manufacturing. In particular, `ASM_RST_N` must not be assumed to release/tri-state the SPI bus until that behavior is demonstrated.
