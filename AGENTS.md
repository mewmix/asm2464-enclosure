# Agent Instructions

## Mission

Develop a compact ASM2464PD + M.2 2230 USB4 enclosure/PCB with integrated firmware-development access while preserving traceable evidence and manufacturability.

## Read first

1. `WORKFLOW.md`
2. `PROJECT_PLAN.md`
3. `DEBUG_ACCESS_AGENT_INSTRUCTIONS.md`
4. `reference/datasheets/ASM2464PD.md`
5. `SIMULATION_WORK_INSTRUCTIONS.md` — mandatory sections 13–25

Then inspect the current branch tip and the public Leaves232 reference design before changing schematic, PCB, or enclosure geometry.

## Coupled Rev-A deliverables

Rev-A includes the KiCad board, CadQuery mechanical/thermal enclosure, pinned existing ASM CPU-emulator and Verilog backends, and the common board simulation proving boot/UART/SPI programming/bricking/recovery. Check every layer change against the others. Reuse the authoritative ASM stack; do not create replacement CPU/RTL implementations. Follow `SIMULATION_WORK_INSTRUCTIONS.md` in full.

## Working rules

- Evidence before inference. Label assumptions explicitly.
- Never invent pinouts, voltages, package dimensions, reset behavior, or component heights.
- Keep third-party reference material read-only; put our work under our own project trees.
- Make small, reviewable commits.
- Run ERC/DRC/export checks after the relevant edits.
- A passing generic DRC is not sufficient validation for USB4/PCIe routing.
- Do not add debug buses we do not need. Current scope is UART firmware debug and safe direct SPI flash recovery.
- Direct SPI programming must not depend on healthy firmware relinquishing the bus.
- Do not assume `ASM_RST_N` tri-states the flash interface until proven.
- Keep mechanical and PCB datums synchronized.
- Do not freeze enclosure Z until actual component heights / thermal stack are verified.
- Do not rely on a polymer lid as the final thermal solution for sustained ASM2464PD load.
- Preserve generated manufacturing outputs as review artifacts only when they correspond to a reviewed design state.

## Agent loop

For each cycle:

1. state the specific design question;
2. collect evidence;
3. change the smallest relevant artifact;
4. run deterministic checks;
5. inspect the visual/3D result where applicable;
6. document assumptions and failures;
7. commit with a narrow message;
8. identify the next unresolved gate.

## Definition of fabrication-ready

Fabrication-ready means the gates in `WORKFLOW.md` and `PROJECT_PLAN.md` have passed. It does not mean "the agent finished routing."
