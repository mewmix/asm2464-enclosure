# Shared board simulation and imported ASM emulation

The primary stock reconstruction path uses unchanged upstream CPU8051, Memory
and HardwareState at `84c990c91eb41948649ead0b28d6720c31c434c3`. Exact baseline
fw.bin executes its reset jump and bounded bank-zero seed helper with boot ROM
bypassed. Stock routing/queue tests use the canonical pinned pre-BAR fixture.
Generic SDCC mcs51 firmware remains a secondary UART/SPI/recovery diagnostic.
No CPU or controller RTL has been recreated.

`hardware/rev-a/board.json` couples the mechanical model to flash capacity/profile, boot offset and physical recovery-link states. The virtual programmer and CPU adapter use the same NOR instance. The model supports WEL/WIP, program/erase timing, NOR bit direction, status, ID, interruption and unsafe ownership detection. The firmware emits the expected boot banner; deliberate erasure prevents boot; external reprogramming and full readback restore it.

Board owns flash, UART, shunts, power/reset and external endpoint/link stimuli.
CpuBackend owns HardwareState and the upstream coupled endpoint response engine.
Board imports do not load ASM internals or mutate sys.path. A namespaced adapter
resolves unchanged upstream legacy imports explicitly.

Run `python scripts/validate_twin.py --source /path/to/opal --reference-bundle`
to replay the preserved diagnostic artifact alongside the stock tests. See
[validation instructions](../validation/README.md) for fresh builds, authorized
local full images and blocked gates. A missing stock image or RTL is never PASS.

The complete authoritative source tree contains no RTL files. Backend B and CPU/RTL differential comparison are **BLOCKED**, not skipped passes. The preserved two-backend requirements remain in [SIMULATION_WORK_INSTRUCTIONS.md](../SIMULATION_WORK_INSTRUCTIONS.md). Locate the existing RTL in the authoritative project before satisfying that gate.
