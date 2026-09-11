# Q1 ASIC Contract Digital-Twin Experiment

## Scope

This experiment is model-only. It does not change `mewmix/asm2464pd-opal`, the normal Rev-A twin branch, imported upstream ASM behavior, or any physical device. The evidence authority is `asm2464pd-opal` commit `48651a400ec50bdfb89462e0a8bb2c9d6f9d424b`.

## Candidate contract tested

The isolated candidate uses one generic queue-base decoder:

```text
queue_base = B26x_literal << 12
```

which maps the four observed initializers to:

- Admin SQ: `0800 -> 00800000`
- Admin CQ: `0808 -> 00808000`
- I/O SQ: `0820 -> 00820000`
- I/O CQ: `0828 -> 00828000`

I/O depth remains separately sourced from the proven Create Queue commands: 32 entries.

The candidate interprets producer notification as a ring interval `[old_hardware_producer, next_index)`. Therefore the exact first stock READ, with `B251=1`, consumes staging slot 0 rather than slot 1. Context-0 staging uses `A000 + slot*64`.

## Exact-stock READ

The test uses the reconstructed first READ without substitution:

- opcode `02`
- CID `0`
- NSID `1`
- SLBA `0`
- one 512-byte block
- PRP1 `00200000`
- PRP2 `0`

The candidate does not reuse the historical synthetic `00820400 -> A400` mapping. Instead, `00200000` targets an abstract sparse host/PCIe memory space. Deterministic 512-byte media is explicitly model input only.

## Completion model

The candidate creates an internal 32-entry CQ, toggles phase on wrap, and projects a selected completion into four bytes compatible with the already-established firmware extraction equations:

- B80C/B80D: identifier bytes
- B80E bit 0: phase
- `SC = (B80E >> 1) | ((B80F & 1) << 7)`
- `SCT = (B80F >> 1) & 7`

This demonstrates internal consistency only. It does not establish how the physical ASIC populates B80C-B80F.

Consumer acknowledgement uses the corresponding ring interval `[old_consumer, next_consumer)` and supports batching.

## Differential against the legacy model

The existing probe remains intact and demonstrates:

1. exact stock `B254=03` does not reach the legacy Q1 consumer;
2. forcing the legacy helper with stock PRP1 `00200000` does not transfer data;
3. the legacy helper transfers only when supplied its historical synthetic PRP1 `00820400`.

Therefore the shortcut `03 -> old Q1 helper` is contradicted by the twin as a complete stock explanation.

## Results

GitHub Actions run `34613866006` completed successfully.

- 72 digital-twin tests passed
- 0 failures
- 0 errors
- 19 pinned upstream queue/model tests passed
- existing generic recovery/fault gates remained green within their documented blockers
- hardware touched: false

The candidate specifically passed:

- generic B26x decode across Admin and I/O bases
- deliberate B26x mutation changes geometry rather than being silently repaired
- producer `0 -> 1`
- producer `31 -> 0`
- multi-entry producer notification
- exact stock READ decode
- stock `00200000` PRP host-memory transfer
- no implicit A400 mapping
- no implicit `00820400` mapping
- downstream candidate address `00820000 + slot*64`
- completion projection
- phase wrap
- batched consumer notification
- repeated zero-distance consumer notification

## Classification

### SUPPORTED_BY_TWIN

A single internally coherent candidate can combine:

```text
B26x queue-base decode
+ 32-entry geometry
+ 03 next-producer semantics
+ A000 staging
+ 00200000 abstract PRP destination
+ internal CQ
+ B80x-compatible projection
+ batched next-consumer acknowledgement
```

without inheriting the legacy four-entry/synthetic-PRP assumptions.

### CONTRADICTED_BY_TWIN

The old shortcut in which the exact stock path is represented by the legacy Q1 helper and `00820400 -> A400` mapping is insufficient.

### STILL_AMBIGUOUS

The experiment does not resolve:

- whether B26x physically programs queue bases;
- the real A000/A800-to-downstream-SQ transport mechanism;
- physical translation/backing of PRP1 `00200000`;
- how downstream CQ state is projected through C8D5/B80C-F;
- what asserts C806.5, CEF3.3, or INT0;
- physical queue-generation/ownership token semantics.

Event/IRQ generation was intentionally not invented merely to close the loop.

## Boundary

`PHYSICAL_EVIDENCE_CLAIMED = false`

`AUTHORITATIVE_SEMANTICS_CHANGED = false`

The candidate is suitable for targeted static analysis or a tightly scoped physical trace, but it is not ready to be ported into the authoritative emulator as stock behavior.
