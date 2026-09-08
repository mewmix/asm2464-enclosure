# Shared board simulation and imported ASM emulation

The working CPU/CAD milestone uses the **unchanged imported upstream CPU8051**, Memory, and HardwareState implementations at `acc935d351d49c66ab8d1026ee8cb4617609f782`. Generic SDCC-built mcs51 firmware runs from the upstream CODE-loading convention and drives actual UART/SPI MMIO. No CPU or controller RTL has been recreated.

`hardware/rev-a/board.json` couples the mechanical model to flash capacity/profile, boot offset and physical recovery-link states. The virtual programmer and CPU adapter use the same NOR instance. The model supports WEL/WIP, program/erase timing, NOR bit direction, status, ID, interruption and unsafe ownership detection. The firmware emits the expected boot banner; deliberate erasure prevents boot; external reprogramming and full readback restore it.

Run `python scripts/validate_twin.py` after installing SDCC and `simulation/requirements.txt`. See [validation instructions](../validation/README.md) for outputs, exact modeling limits, and the stricter `--require-rtl` gate.

The complete authoritative source tree contains no RTL files. Backend B and CPU/RTL differential comparison are **BLOCKED**, not skipped passes. The preserved two-backend requirements remain in [SIMULATION_WORK_INSTRUCTIONS.md](../SIMULATION_WORK_INSTRUCTIONS.md). Locate the existing RTL in the authoritative project before satisfying that gate.
