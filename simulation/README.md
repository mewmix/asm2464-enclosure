# Board-Level Simulation Plan

This tree is for hardware-side simulation around the existing ASM2464PD CPU/firmware emulator.

## Scope

Model what is open and controllable before Rev-A hardware:

- external SPI NOR behavior;
- UART transport;
- reset/boot state;
- external-programmer ownership of the flash bus;
- flash backup/program/verify/recovery sequences;
- power/reset fault injection at a behavioral level;
- optional SPICE models for the actual reset/isolation/power circuitry;
- optional PCIe/NVMe behavioral endpoint and LiteNVMe differential reference.

Do not claim analog USB4 PHY equivalence without vendor PHY models.

## Required recovery regression

The mandatory scenario is:

```text
known-good flash
  -> ASM boot
  -> UART boot evidence
  -> corrupted flash
  -> failed boot
  -> safe SPI ownership
  -> external read/erase/program/verify
  -> release/reset
  -> recovered ASM boot
  -> UART confirmation
```

## Suggested open stack

- Verilator / SystemVerilog
- cocotb
- behavioral SPI NOR model matched to the selected flash
- Python virtual programmer
- ngspice or Xyce for circuit-level reset/isolation checks
- LiteNVMe / LitePCIe only as an independent downstream NVMe reference where useful

Stock ASM traces remain the primary oracle for ASM-specific lifecycle behavior.
