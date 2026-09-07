# Proposed Rev-A recovery architecture

`board.json` is the shared digital/mechanical contract. The current design selects **four removable shunts**, one each in CS#, CLK, MOSI, and MISO. The programmer connects to the flash side. Reset is held during shunt removal and replacement. All four links must be open before programmer attachment. VREF is sense-only; the programmer must not power the target through it.

The CAD includes separate service-connector and isolation-shunt envelopes and top service openings. These are **design envelopes**, not verified manufacturer models. The isolated flash needs a defined idle CS# level in the eventual schematic; resistor value, rail voltage, connector part and signal integrity remain electrical gates. No completed Rev-A schematic/routed PCB is claimed by this fit-model milestone.

Simulation ownership is derived from each physical link state. Reset alone never grants ownership. A deliberately shorted link produces a contention error. A controller that refuses to tri-state cannot drive through removed shunts.

Procedure: hold reset → remove all four shunts → attach programmer to flash-side connector → read JEDEC → save complete backup → erase/program → full readback and SHA-256 verification → detach programmer → replace all four shunts → release reset → capture UART boot.

Controller SPI signal names in the previous reference notes contain conflicting DI/DO direction descriptions. This model therefore uses functional MOSI/MISO names and does **not** assign new ASM ball-to-flash wiring. Resolve the actual net directions from the reference schematic before authoring the board. The CAD and simulator share ownership architecture, not an unverified netlist.
