# Pinned ASM2464 source provenance

- Source repository: https://github.com/mewmix/asm2464pd-opal
- Source branch: `agent/full-firmware-lifecycle`
- Source commit: `84c990c91eb41948649ead0b28d6720c31c434c3`
- Previous source commit: `acc935d351d49c66ab8d1026ee8cb4617609f782`
- Physical baseline: `47aed2cd0fba21011b4f69ad34781e2c924b7f48`
- Reviewed repin: 2026-09-08. The exact one-commit delta changes hardware.py,
  two queue/lifecycle test sources and an inheritance audit. Only hardware.py
  changes in the existing 14-file allowlist. Old/new blob and SHA-256 values
  and the behavioral reason are recorded in import-lock.json.
- Routing/controller/Admin/Q1 completion ancestry is EMULATOR_MODEL_ONLY.
  It does not establish physical link generation, namespace/security ownership,
  complete firmware teardown, or qualification of the newer lifecycle image.
- Exact imported paths and content hashes: `import-lock.json` (14 files).
- Local patches: none. Every imported file matches its upstream Git blob SHA.
- Runtime dependencies: Python standard library; the Pyrite dependency `tools/tcg_session.py` is included because upstream hardware imports it.
- Omitted: production firmware/images, USB transport/proxy tools, unrelated firmware builds, other test suites and their fixtures. The two lifecycle test sources are evidence references, not independently runnable suites in this subset.
- No root license was found in the source tree. These user-directed same-owner imports retain source provenance and do not assert an open-source license or grant downstream redistribution rights. No proprietary production firmware is included.
- RTL discovery: the complete non-truncated source tree contains no `.v`, `.sv`, or `.vhd` files, nor a Verilog/RTL directory. Backend B is unavailable at this pin. Do not fabricate a replacement.

Reproduce using a source checkout containing the commit:
`python scripts/import_asm2464_sim.py --source /path/to/asm2464pd-opal`
Verify without network: `python scripts/import_asm2464_sim.py`.
Updating requires resolving the live ref and reviewing a new allowlist/hash lock; the script deliberately does not silently follow a branch.
