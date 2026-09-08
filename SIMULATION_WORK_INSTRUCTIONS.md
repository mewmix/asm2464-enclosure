# Mandatory ASM2464 Rev-A Co-Simulation Work Instructions

## Stock-grounding update — 2026-09-08

The primary realism target is stock/reconstructed ASM behavior. Generic reference
firmware is secondary diagnostic isolation, not evidence of stock boot, USB,
PCIe, NVMe, BOT or Pyrite. This priority supersedes the older first-milestone
ordering below. Consume the reviewed exact lifecycle pin 84c990c91eb41948649ead0b28d6720c31c434c3,
with physical authority at 47aed2cd0fba21011b4f69ad34781e2c924b7f48.
Never silently follow a moving ref. Keep the 512 KiB stock physical profile
separate from the assumed 2 MiB Rev-A design candidate.

Board owns external connections; CpuBackend owns HardwareState. Restricted full
SPI images are local inputs only and never become repository artifacts. Stock
code execution bypassing ROM and actual full-SPI startup have separate results.
See validation/STOCK_GROUNDING.md and validation/README.md for evidence limits,
upstream discrepancies, commands and current blocked gates. All work in this
pass is offline; no physical transport or installer is executed.

The Rev-A deliverable consists of FOUR coupled artifacts:

1. KiCad ASM2464PD development board.
2. CadQuery mechanical/thermal enclosure.
3. Imported/pinned ASM2464 CPU-emulator + Verilog simulation backend.
4. Board-level simulation proving firmware boot, UART debugging, external SPI programming, deliberate bricking, and recovery.

A change to one layer must be checked against the other layers. The existing ASM emulator/RTL is an engineering asset and MUST be reused. Do not replace it with a toy ASM behavioral stub. These requirements are mandatory parts of the Work assignment.

## 13. Import the existing ASM2464 emulation stack

Do NOT create a new ASM CPU emulator or controller RTL from scratch.

The authoritative source is `mewmix/asm2464pd-opal`, branch `agent/full-firmware-lifecycle`. At the beginning of implementation inspect the LIVE branch tip and discover the actual paths for the existing CPU emulator, Verilog/RTL models, peripheral models, test harnesses, memory maps, firmware-loading code, and relevant lifecycle tests. Do not guess paths from this document.

The last supplied baseline was `7d7fb9fdf8ecfbbd8cd4eb54a17f1cf8e416f90c`. During this instruction update, the live GitHub branch ref resolved to `acc935d351d49c66ab8d1026ee8cb4617609f782` on 2026-09-07. This observation is NOT an imported or validated snapshot. VERIFY the live tip again before importing, then pin the exact resolved commit for the whole import.

The source branch remains authoritative for ASM firmware/CPU reconstruction. The enclosure consumes a pinned copy of the portions needed for hardware co-simulation; do NOT move firmware-reconstruction ownership here.

## 14. Import / provenance policy

Vendor a reproducible snapshot under this layout:

```text
simulation/
  asm2464/
    upstream/
      ORIGIN.md
      <pinned imported emulator/RTL sources>
    adapter/
      spi/
      uart/
      reset/
      pcie_nvme/
    firmware/
      reference/
  flash/
  programmer/
  board/
  tests/
  traces/
```

Keep `upstream/` as close to the source as possible. Do not casually edit imported code; project-specific behavior belongs in adapters.

Create `simulation/asm2464/upstream/ORIGIN.md` recording source repository, source branch, source commit SHA, import date, exact imported paths, omitted dependencies, unavoidable local patches, and license/provenance notes.

Create a deterministic `scripts/import_asm2464_sim.py` or `.sh` that reproduces the snapshot from the recorded commit. Record dependency versions and any patch application needed for reproduction. Prefer a pinned vendored snapshot over a moving branch at runtime. Inspect redistribution rights before importing dependencies or firmware.

## 15. Two ASM execution backends

Where the existing project permits it, expose:

- Backend A: the existing software CPU emulator.
- Backend B: the existing Verilog/RTL model.

Both connect through a common board-facing pin/MMIO adapter to ONE board model covering external flash, UART, reset, power state, and PCIe/NVMe. Both consume the SAME flash contents, reset sequence, UART expectations, external-programmer behavior, fault injections, and test vectors. Normalize externally visible behavior for comparison. Do not let backend-specific board models diverge.

If source capabilities block either backend, record the precise unsupported behavior and evidence. Unsupported execution is a blocker, not a passing substitute or permission to invent a replacement ASM.

## 16. Generic reference firmware

Create small project-owned deterministic reference firmware specifically for simulation. Do NOT put proprietary ASMedia production firmware in this public repository without explicit redistribution rights.

Use the actual architecture, memory map, build format, reset vector, linker requirements, and image-loading convention established by the authoritative emulator work. Do not invent these before source inspection.

The first firmware initializes minimum runtime state and UART, emits a deterministic boot banner, exercises external-flash access where the architecture/model permits, reports status, and enters idle/heartbeat. Keep exact strings machine-testable, for example:

```text
ASM2464 REF FW
BUILD=<build-id>
FLASH_ID=<jedec-id>
FLASH_STATUS=<status>
BOOT=OK
```

Produce an ELF where applicable, raw binary, flash image, map file, symbol file, SHA-256, and build manifest. Suggested output directory: `simulation/asm2464/firmware/reference/build/`, including `asm2464_ref.elf`, `asm2464_ref.bin`, `asm2464_ref.map`, `asm2464_ref.sym`, `flash.bin`, `SHA256SUMS`, and `manifest.json`.

The manifest records source commit, compiler/toolchain, exact build command, firmware and flash-image SHA-256, load address/layout, and expected UART banner. Distinguish the enclosure firmware source commit from the imported emulator commit. Make builds deterministic where practical, including a stable build ID. Document inapplicable output formats rather than fabricating them.

## 17. Common board model

Build the virtual board around the imported ASM implementation rather than mocking away the ASM. Required connections:

| ASM / board interface | Connected model |
|---|---|
| ASM SPI pins | Behavioral ZD25WQ16-style external firmware flash |
| ASM UART | Virtual UART terminal/assertion harness |
| ASM reset | Board reset and recovery-state model |
| External SPI recovery connector | Virtual programmer accessing the SAME physical flash model |
| ASM PCIe/NVMe side | Synthetic downstream endpoint |
| Optional differential reference | LiteNVMe |

The board model must be independent of the selected execution backend.

## 18. Flash must act like hardware

Do not expose firmware storage as unrestricted RAM. Model JEDEC identification, READ, WREN, WRDI, WEL, WIP, status registers, page program, sector erase, block/chip erase where applicable, page and erase boundaries, program/erase timing, power-up state, reset behavior, and illegal operations.

Enforce NOR semantics: program changes only physically permitted bits, erase restores erased state, and programming cannot silently perform arbitrary RAM writes. Support deliberate interruption during page program, sector erase, flash read, and reset transitions. Derive part-specific commands, geometry, and timing from evidence; label unsupported/assumed interruption outcomes.

## 19. CPU emulator vs Verilog differential test

Run the SAME firmware and SAME board corpus against both backends whenever technically possible. Capture normalized events:

```text
RESET_ASSERT RESET_RELEASE
SPI_CS_ASSERT SPI_TX SPI_RX SPI_CS_RELEASE
FLASH_READ FLASH_WREN FLASH_PROGRAM FLASH_ERASE FLASH_STATUS
UART_TX
MMIO_READ MMIO_WRITE
PCIE_CFG_READ PCIE_CFG_WRITE
NVME_MMIO_READ NVME_MMIO_WRITE
```

Classify comparisons as exact match, semantically equivalent, timing-only difference, CPU-emulator discrepancy, RTL discrepancy, or unknown/unmodeled behavior. Preserve raw and normalized traces and document normalization rules. Do not assign fault to a backend without supporting evidence; otherwise classify it as unknown.

Functional equivalence is the first target. Do not require cycle-exact equality unless source evidence supports it.

## 20. Basic reference-firmware regression

Build generic firmware, construct the flash image, load identical images into the common flash model for each run, reset the CPU-emulator backend, require expected UART banner/status, repeat with Verilog, and compare normalized traces.

PASS requires both backends to execute the image, reach expected boot state, emit semantically matching UART output, make valid external-flash accesses, and avoid unexpected bus contention. Record every difference. A skipped/unsupported backend cannot satisfy this two-backend gate.

## 21. Full brick / recovery regression

Run the following on the CPU emulator first, then the same regression on Verilog where supported. Ultimately both must pass:

1. Build known-good generic firmware and program virtual flash.
2. Boot with the imported ASM implementation; require UART `BOOT=OK`.
3. Destroy/corrupt the boot image, reset, and require normal boot to fail.
4. Assert the hardware recovery condition; ASM relinquishes the flash bus OR modeled physical isolation disconnects it.
5. Attach the virtual external programmer.
6. Read JEDEC ID and existing flash contents.
7. Erase and program the known-good image.
8. Perform full readback verification and require SHA-256 match.
9. Disconnect the programmer and return flash-bus ownership to ASM.
10. Reset ASM and require reference firmware boot with UART `BOOT=OK`.

Ensure corrupt-image failure is observed from actual execution and that recovery uses programmer transactions through the shared flash model. Never fake UART success or repair flash by bypassing hardware semantics.

## 22. Hardware recovery architecture must match PCB

Model the actual recovery circuit selected for the Rev-A schematic. Do not create a magical simulation-only ownership signal. If Rev-A uses reset-held tri-state, 0-ohm isolation, series resistors, bus/analog switch, buffer, or jumper, represent that mechanism and its actual ownership limits.

The PCB schematic, simulation, and recovery procedure must describe the SAME architecture. Reset alone is not evidence of tri-state; series resistors alone do not establish disconnection. Keep unproven recovery configurations explicitly provisional. Direct recovery must not require healthy firmware. Simulation does not replace physical scope/logic-analyzer ownership validation.

## 23. Fault-injection matrix

Run both backends, where supported, against every case below:

- blank flash;
- corrupt reset/boot region;
- single-bit corruption;
- invalid firmware hash;
- bad JEDEC ID;
- flash stuck BUSY;
- WREN ignored;
- WEL unexpectedly cleared;
- program interrupted;
- erase interrupted;
- reset during flash access;
- reset during boot;
- brownout;
- rapid reset;
- programmer attached too early;
- programmer detached too late;
- ASM refuses to relinquish SPI;
- ASM/programmer contention.

Track CPU-emulator result, Verilog result, expected hardware behavior, and confidence/evidence source for each row. Retain failures and unsupported cases. Do not invent firmware hash enforcement if the established boot path lacks it; report that limitation explicitly.

## 24. Connection to the existing firmware project

Do not duplicate lifecycle reverse-engineering already completed in `mewmix/asm2464pd-opal`.

| Authoritative project | Responsibilities |
|---|---|
| `asm2464pd-opal` | ASM CPU behavior, reconstructed firmware behavior, controller MMIO, firmware lifecycle, PCIe/NVMe reconstruction, firmware-facing tests |
| `asm2464-enclosure` | Physical PCB, power/reset circuitry, external flash, UART connection, programmer ownership, mechanical enclosure, thermal path, board digital twin, electrical simulation, ASM integration with modeled hardware |

Document likely emulator/firmware-model defects exposed by enclosure simulation and fix them in the authoritative project where appropriate. Do not permanently fork that behavior inside enclosure adapters.

## 25. LiteNVMe role

LiteNVMe remains an optional SECONDARY differential oracle. Compare ASM behavior and LiteNVMe reference behavior through the synthetic downstream PCIe/NVMe target, connected to the board and imported CPU/RTL implementations grounded in ASM firmware reconstruction and stock evidence.

LiteNVMe must never redefine observed ASM behavior. Use it to identify likely protocol bugs, missing initialization, queue setup mistakes, invalid MMIO ordering, reset/recovery problems, and DMA mistakes.

Evidence priority:

1. Physical/stock ASM evidence.
2. ASM datasheet/reference evidence.
3. Protocol requirements.
4. LiteNVMe reference behavior.

## First milestone and validation statement

First prove generic firmware boot and brick/recovery through both imported execution backends on the same virtual Rev-A board, with UART, SPI, reset, ZD25WQ16-style flash, and programmer connections. Then introduce progressively more realistic firmware images without replacing the board model.

Report: “Rev-A simulation was validated against asm2464pd-opal emulator commit <full SHA>” only after the corresponding tests actually pass. Record enclosure commit, firmware/image hashes, backend/tool versions, commands, traces, and limitations with that result. Instruction changes alone do not establish imported backends, firmware execution, passing regression, or fabrication readiness.
