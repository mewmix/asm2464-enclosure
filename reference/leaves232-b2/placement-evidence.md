# Reference placement evidence

Source: https://github.com/Leaves232/2230-USB4-SSD-Enclosure-Design
Commit: `8b7a5b2e510b9e8b6a98e5ba4e491064da3b950c`.
Read-only inputs: Pick Place for ASM2464PD - B2 Sample.csv and B2 Sample.GKO.

U31 row: `ZD25WQ16CEIGR`, BottomLayer, `USON-8_L3.0-W2.0-P0.50-BL-EP`, center 1394.313 / 1157.313 mil, rotation 0.
Gerber lower-left datum: X57029 / Y35900 in 2.5-inch coordinates = 570.29 / 359 mil.
Subtract datum, then multiply mil by 0.0254 to obtain mm. This agrees with the existing ASM U2 center from its 1108.371 / 944.452-mil PnP coordinates.

U31 footprint label supplies a nominal 3-by-2-mm body envelope, but the model's XY orientation and 0.6-mm height remain assumptions pending footprint/package validation. Geometry does not establish SPI net direction or supply voltage.

No third-party manufacturing files or firmware images are redistributed here.
