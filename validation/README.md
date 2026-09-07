# Reproducible digital-twin validation

Install SDCC (tested 4.2.0) and Python dependencies from `simulation/requirements.txt`.
Run from the repository root:

```bash
python scripts/validate_twin.py
```

This verifies upstream hashes, builds the generic mcs51 firmware twice, runs flash/ownership tests and CPU boot/brick/recovery, executes all 18 fault rows, builds STEP/STL CAD, renders two reviews, checks the shared configuration hash, and writes `validation/twin-report.json` and compressed raw CPU traces.

`python scripts/validate_twin.py --require-rtl` returns nonzero while the mandatory second backend is unavailable. CPU repeatability is explicitly not CPU-versus-RTL differential equivalence.

Outputs:

- `mechanical/rev-a/build/assembly.step`, individual STEP/STL parts, assembled/exploded PNGs, dimensions and CAD validation.
- `simulation/asm2464/firmware/reference/build/`: IHX, raw binary, map, symbols, SHA-256, flash image, build manifest.
- `validation/twin-report.json`, `fault-matrix.json`, `cpu-recovery.jsonl.gz`.

The external NOR is a deterministic **command-level generic 16-Mbit profile**, with explicit assumed JEDEC and timings. Program/erase interruption deterministically affects a prefix proportional to elapsed model ticks. Illegal page-crossing and unaligned erases are rejected as a strict simulation policy. These are not claims of exact ZD25WQ16 silicon behavior. SPI events are normalized command transactions, not cycle-accurate pin waveforms. The controller MMIO ABI and flash-to-CODE loading convention come from the pinned upstream implementation; the silicon boot ROM is not reconstructed here.

The synthetic PCIe/NVMe endpoint remains the imported `HardwareState` implementation, owned by the board controller model. The minimal reference firmware exercises UART/SPI; PCIe/NVMe protocol boot is not validated by this milestone.

Physical component heights, connector selection, flash electrical parameters, reset behavior, full PCB ERC/DRC, USB4/PCIe signal integrity, thermal performance, assembly retention, and measured hardware recovery remain open gates. The renders use provisional fit envelopes. No fabrication readiness is claimed.
