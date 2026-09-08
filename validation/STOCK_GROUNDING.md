# Stock-grounded offline simulation review

The physical authority remains `mewmix/asm2464pd-opal` branch
`agent/usb-storage-bringup` at `47aed2cd0fba21011b4f69ad34781e2c924b7f48`.
The imported lifecycle model is pinned to
`84c990c91eb41948649ead0b28d6720c31c434c3`. Neither source branch was changed.
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
| Firmware boot/banking | Contradiction requiring upstream investigation | Bounded bank-zero stock execution; no enclosure bank-address workaround |

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

1. **Bank-coordinate inconsistency:** pinned emulate/memory.py uses
   BANK1_FILE_BASE=0xFF6B while emu.py strips the wrapper. The newer
   tools/audit_stock_nvme_admin_init.py explicitly distinguishes body 0xFF67
   from raw 0xFF6B; tools/audit_stock_nvme_bank_reachability.py uses the same
   distinction. Some older USB audit comments also use 0xFF6B for fw.bin.
   Resolve these coordinate conventions upstream before full banked execution.
   Enclosure neither patches the memory model nor claims banked startup.
2. **Namespace/security ownership:** 84c990c models queue ancestry, not the
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
5. **Toolchain:** SDCC and CadQuery are unavailable in this runtime. The exact
   committed diagnostic bundle is verified/replayed; no fresh build or CAD
   regeneration is claimed. Geometry and recovery topology are checked against
   the original profile before replay. Existing renders remain historical.

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
