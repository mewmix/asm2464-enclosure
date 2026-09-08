# Reproducible digital-twin validation

Stock/reconstructed ASM behavior is the primary target. See
[the stock evidence review](STOCK_GROUNDING.md) for exact inherited sources,
boot limits and upstream defects. The offline entry point is:

```bash
python scripts/validate_twin.py --source /path/to/asm2464pd-opal --reference-bundle
```

`--source` reads only pinned Git objects from the authorized opal checkout.
`--reference-bundle` verifies and replays the preserved project-owned image;
it does not claim SDCC rebuilt it. Omit that flag for fresh deterministic builds.
The runner continues stock/model checks when SDCC or CadQuery is unavailable
and records those blocked gates. Exit 1 means a contract failed; exit 2 means
the full qualification gate remains blocked. Missing RTL always blocks that gate.

Run the two stock modes alone with:

```bash
python scripts/validate_stock.py --source /path/to/asm2464pd-opal
ASM2464_STOCK_SPI_IMAGE=/authorized/local/full-image.bin python scripts/validate_stock.py --source /path/to/asm2464pd-opal
```

The alternative `--stock-image` argument has the same meaning. Exact size
524288 and SHA-256 are checked before virtual execution. No input bytes or path
are written to reports. Missing input returns
`BLOCKED_STOCK_FULL_SPI_IMAGE_UNAVAILABLE`; a supplied full image still cannot
establish startup until the boot-ROM model exists. Do not put restricted images
in this repository. `stock-report.json` and `twin-report.json` distinguish the
bounded code execution, model causality, diagnostic replay and blocked modes.

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

The NOR has two explicit profiles: `stock_physical` (512 KiB physically proven
capacity; unknown part/JEDEC) and `rev_a_candidate` (2 MiB design assumption).
Page/erase geometry and timings carry separate model evidence labels. Program/
erase interruption affects a deterministic prefix; page-crossing, unaligned
erase and out-of-range access are rejected by conservative model policy.
SPI events are command transactions, not pin waveforms. The generic manifest's
load convention is never used as a stock boot oracle.

CpuBackend owns the imported HardwareState and its coupled synthetic protocol
engine. Board owns external endpoint/link stimuli, separate from routing and
controller generations. No namespace/security or BOT readiness is invented.
Generic PASS proves only this image's modeled CPU/UART/SPI/reset/programmer
path; it does not prove stock startup, USB, PCIe, NVMe, Pyrite, BOT or silicon.

Physical component heights, connector selection, flash electrical parameters, reset behavior, full PCB ERC/DRC, USB4/PCIe signal integrity, thermal performance, assembly retention, and measured hardware recovery remain open gates. The renders use provisional fit envelopes. No fabrication readiness is claimed.
