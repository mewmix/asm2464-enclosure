# Stock-grounded offline simulation review

The physical authority remains `mewmix/asm2464pd-opal` branch
`agent/usb-storage-bringup` at `47aed2cd0fba21011b4f69ad34781e2c924b7f48`.
The imported lifecycle model is pinned to
`da87259d6bc0e2b86ff8bd0deb22938bd9fa89d7`. Neither authoritative source branch was changed by this enclosure repin.
All work here is offline; hardware_touched=false.

## Inheritance decisions

| Subsystem | Relationship to physical baseline | Scope here |
|---|---|---|
| SPI capacity | Inherited, PHYSICALLY_PROVEN geometry from two full-device dumps, hardware.py SPI_FLASH_SIZE | 512 KiB stock profile; no new part identification |
| Pre-BAR configuration | Inherited stock reconstruction, INSTRUCTION_PROVEN / CALLER_CALLEE_PROVEN with STANDARD_BACKED_INTERPRETATION of fields | Exact six-row pcie_pio.h table, ordered write projection, original 14-stage audit ledger and source hashes in stock-route.json |
| USB enumeration, BOT, EP0 recovery | Inherited evidence in USB_BRINGUP_RECONSTRUCTION.md, not reconstructed here | Generic success does not qualify these paths; upstream MSC-reset model regression retained |
| Queue ancestry | Newer upstream lifecycle implementation, EMULATOR_MODEL_ONLY | Exact unpatched import; same-instance routing/controller/Admin/Q1 tests |
| External link loss | New board-level model contract, EMULATOR_MODEL_ONLY / CONSERVATIVE_SAFETY_POLICY | Immediate loss notification revokes upstream routing before deferred delivery; link return cannot restore owners |
| Programmer ownership | Existing enclosure design assumption | Four open physical shunts, held reset; reset alone still does not imply tri-state |
| Firmware boot/banking | Bank coordinates/extent resolved upstream; complete startup still unresolved | Exact upstream memory model; no enclosure offset workaround; no boot-ROM claim |

The pre-BAR audit's physical verdict is still
`STOCK_PRE_BAR_CONFIGURATION_PATH_MISSING_FROM_HANDMADE; PHYSICAL_UR_CAUSE_NOT_YET_ISOLATED_TO_ONE_REGISTER`.
Baseline location does not make the pre-BAR candidate physically qualified.
Bus 1 is reconstructed; DevFn=0 and capability pointer=0x40 are synthetic fixture
inputs. The fixture is a write projection, not the complete stock enumeration
algorithm. Its wide window, commands, byte enables, quiesce/probe/assignment,
BAR companions, Device Control and window restore follow the pinned source.
No generic route values are substituted in enclosure stock causal tests.

## Image and boot evidence

| Item | Established fact | Classification / limitation |
|---|---|---|
| fw.bin | 98,006-byte code artifact, SHA-256 1a4734745f0c00235754a00bd7e3e995064e1e6ae6f88a6aff163683a22d0037 | STOCK_CODE_ARTIFACT; read from exact baseline Git object, not copied to this repository |
| Full physical flash | 0x80000 bytes | STOCK_FULL_SPI_IMAGE; restricted local input only, unavailable for this run |
| Package wrapper | 4-byte LE body length, body, A5, additive byte checksum, LE CRC32 | Exact checks in baseline tools/stock_guarded_firmware_write.py::parse_wrapper; does not prove ROM loading |
| Installer layout | Preserves first 0x100 configuration bytes; padded E3 regions 0xFF00 + 0x7FE0; final 32-byte guard retained | Historical path described by tools/install_usb4_demo_from_generic.py; applies to exact recorded acquisitions/candidates |
| Reset vector | Exact fw.bin LJMP at 0 to 0x436B | INSTRUCTION_PROVEN; one CPU instruction executed, not complete cold startup |
| Seed helper | Bank-zero 0xCF91..0xCFE3 (end excludes RET); 51 instructions, 16 ordered writes | INSTRUCTION_PROVEN bytes and EMULATOR_MODEL_ONLY execution; tested with initial zero companion bits |
| Bank mapping | Body/file Bank 1 base 0xFF6B; wrapped offset 0xFF6F; present Bank 1 maps CPU 0x8000..0xFF6A | STOCK_BYTES/INSTRUCTION_PROVEN upstream; absent CPU 0xFF6B..0xFFFF returns 0xFF only as EMULATOR_MODEL_ONLY fail-closed policy |
| Controller Identify path | Pinned bank-switch target 0x89DB / body 0x10946 falls through 25 instruction-aligned instructions to Controller Identify 0x8A07 / body 0x10972 | STOCK_INSTRUCTION_AND_DATAFLOW_PROVEN upstream; reset/boot reachability and physical execution remain unproven |
| SPI-to-CODE loading | Upstream emu.py accepts caller length and default offset 0x100 | EMULATOR_MODEL_ONLY caller convention, not boot-ROM proof |
| Generic image | Project manifest owns offset 0x100 and 501-byte code length | GENERIC_REFERENCE_FIRMWARE; diagnostic replay only |
| Handmade storage image | Separate usb4-demo firmware/package | HANDMADE_STORAGE_CANDIDATE; not built, packaged or executed here |

Full-SPI startup remains `STOCK_FULL_SPI_BOOT_BLOCKED_ON_BOOT_ROM_MODEL` even
when a local image is supplied. No guessed stock offset/length copy is used.
Local mode validates size/hash, virtual program/full readback, deliberate
virtual corruption and repair. JEDEC validation is explicitly unavailable for
the stock profile, rather than returning a made-up ID. Program/erase timing,
page/erase sizes and interruption policy are model values with per-field labels.
Out-of-range accesses fail; upper 1.5 MiB access on the 2 MiB design profile is
only a model compatibility experiment, not controller support evidence.

## Upstream defects and remaining limits

1. **Bank mapping resolved; startup reachability remains open:** lifecycle
   `da87259...` establishes body/file Bank 1 base `0xFF6B`, wrapped offset
   `0xFF6F`, rejects the stale `0xFF67` interpretation, and bounds the present
   bank image through CPU `0xFF6A`. It also establishes binary-internal
   Controller Identify reachability from the pinned bank switch. It does not
   establish reset/boot reaching that callsite, Namespace Identify reachability,
   boot-ROM loading/banking behavior, or physical-silicon execution.
2. **Namespace/security ownership:** `da87259...` models queue ancestry, not the
   complete firmware namespace/Pyrite ownership hierarchy. No board-owned
   namespace or authorization flags are fabricated. BOT media readiness and
   stock Pyrite storage boot remain unsupported. No physical drive relocking
   is inferred. Upstream lifecycle R1–R5/R7 remain outside this enclosure pass.
3. **Coupled synthetic endpoint:** upstream protocol/DMA implementation remains
   coupled to HardwareState. Board owns external endpoint presence/link events;
   CpuBackend owns the controller and coupled response engine. A transport-level
   endpoint interface for a future real RTL backend requires upstream work.
4. **RTL:** no implementation was found in the authoritative source tree;
   CPU/RTL differential validation is BLOCKED, never a passing stub.
5. **Toolchain:** SDCC and CadQuery are unavailable in the original agent runtime.
   The exact committed diagnostic bundle can be verified/replayed without
   treating that as a fresh build or CAD regeneration. Existing renders remain
   historical unless those toolchain gates are rerun in an environment that has them.

## Data policy

No full stock binary is committed, copied into validation output, printed or
embedded in traces. Restricted boards suppress SPI payloads and refuse CPU
attachment until stock boot and trace handling are modeled. Manifests contain
only image size/hash/class/profile and simulator/upstream/baseline commits.
Synthetic temporary test inputs test that boundary and are TEST_ONLY_FAULT_INJECTION;
they never satisfy the real full-image availability gate. Existing compressed
SPI traces and tarball contain only the project-owned generic diagnostic image.

Inherited historical firmware, traces and device observations retain their
original evidence class. A handmade implementation agreeing with this emulator
does not create stock or physical evidence.
