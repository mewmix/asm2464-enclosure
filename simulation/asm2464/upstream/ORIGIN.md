# Pinned ASM2464 source provenance

- Source repository: https://github.com/mewmix/asm2464pd-opal
- Source branch: `agent/full-firmware-lifecycle`
- Source commit: `da87259d6bc0e2b86ff8bd0deb22938bd9fa89d7`
- Previous source commit: `84c990c91eb41948649ead0b28d6720c31c434c3`
- Physical baseline: `47aed2cd0fba21011b4f69ad34781e2c924b7f48`
- Reviewed repin: 2026-09-08. The lifecycle delta from `84c990c...` to
  `da87259...` merges the stock image/bank-coordinate reconstruction. Among the
  existing 15 imported runtime/evidence paths, only `emulate/memory.py` changes.
  Exact old/new Git-blob and SHA-256 values are recorded in `import-lock.json`.
- The imported memory model now preserves the proven body/file Bank 1 base
  `0xFF6B` and enforces the present code extent through CPU `0xFF6A`; reads from
  the absent `0xFF6B..0xFFFF` tail return `0xFF` as EMULATOR_MODEL_ONLY policy.
- Upstream audit evidence at this pin proves binary-internal Controller Identify
  reachability from the pinned bank-switch target `0x89DB` to Identify at
  `0x8A07`. It does not prove reset/boot reaches that callsite, Namespace
  Identify reachability, boot-ROM behavior, or physical-silicon execution.
- Routing/controller/Admin/Q1 completion ancestry remains EMULATOR_MODEL_ONLY.
  It does not establish physical link generation, namespace/security ownership,
  complete firmware teardown, or qualification of the newer lifecycle image.
- Exact imported paths and content hashes: `import-lock.json` (15 files).
- `test/test_nvme_queue_causality.py` is retained unchanged to run its 19
  upstream model regressions. Its generic route is not the enclosure's canonical
  stock route fixture. Existing lifecycle test sources remain evidence references
  because their compiled-firmware dependencies are not imported.
- Local patches: none. Every imported file matches its upstream Git blob SHA.
- Runtime dependencies: Python standard library; the Pyrite dependency `tools/tcg_session.py` is included because upstream hardware imports it.
- Omitted: production firmware/images, USB transport/proxy tools, unrelated firmware builds, stock-bank audit tools/docs/tests outside the runtime allowlist, other test suites and their fixtures. The two lifecycle test sources are evidence references, not independently runnable suites in this subset.
- No root license was found in the source tree. These user-directed same-owner imports retain source provenance and do not assert an open-source license or grant downstream redistribution rights. No proprietary production firmware is included.
- RTL discovery: the complete non-truncated source tree contains no `.v`, `.sv`, or `.vhd` files, nor a Verilog/RTL directory. Backend B is unavailable at this pin. Do not fabricate a replacement.

Reproduce using a source checkout containing the commit:
`python scripts/import_asm2464_sim.py --source /path/to/asm2464pd-opal`
Verify without network: `python scripts/import_asm2464_sim.py`.
Updating requires resolving the live ref and reviewing a new allowlist/hash lock; the script deliberately does not silently follow a branch.
