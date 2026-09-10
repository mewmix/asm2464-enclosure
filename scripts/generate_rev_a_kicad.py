#!/usr/bin/env python3
"""Deterministically generate the Rev-A structural KiCad study.

This generator intentionally creates no routed copper for USB4/PCIe/USB2.
Production trace geometry remains blocked until a fabrication stackup and field
solver solution are locked. The generated PCB is a netted placement/ratsnest
artifact for electrical/mechanical review, not fabrication output.
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("REV_A_OUT", str(REPO_ROOT)))
KDIR = ROOT / "hardware/rev-a/kicad"
FPDIR = KDIR / "rev-a-footprints.pretty"
REVIEW = ROOT / "hardware/rev-a/review"

BOARD_W = 22.039834
BOARD_H = 33.6804
BOARD_T = 1.6

PLACEMENT = {
    "U2": (13.6672574, 14.8704808, 270.0, "F.Cu"),
    "J1": (11.0699804, 29.1872670, 0.0, "F.Cu"),
    "J2": (11.0238794, 4.6976538, 0.0, "B.Cu"),
    "U31": (20.9301842, 20.2771502, 0.0, "B.Cu"),
}

# PnP source-native -> current Rev-A datum is a translation.  Derived from U2
# and independently cross-checks J1/J2/U31 to <~2 um rounding.
PNP_DX = -14.485366
PNP_DY = -9.1186

def pnp(mil_x: float, mil_y: float):
    return (mil_x * 0.0254 + PNP_DX, mil_y * 0.0254 + PNP_DY)

# ASM2464PD authoritative ball groups from datasheet R02.
NC = "J2,H2,G2,F2,E2,D2,C2,B2,B3,B4,B5,B6,B7,B8,B9,B10,B11,B12,B13,B14,B15,B16,B17,B18,B19,B20,C20,D20,E20,F20,G20,H20,Y7,AA7,Y10,AA10,Y11,AA11,D21,E21,F21".split(",")
VDD = "J21,J20,K21,K20,J18,J17,J16,H16,F16,E16,D16,J14,J13,H14,H13,F14,E14,D14,L11,L14,N14,P14,T14,P13,T13,E11,F11,H11,T11,F9,H9,J9,J8,L8,N8,P8,T9,T8,U9,U8".split(",")
VCCL = "L21,L20,L18,L17,L16,N16,P16,T16,U16,U14,U13,U11,U6,T6,P6,N6,L6".split(",")
VCCH = "D13,E13,F13,D9,E9,D8,E8,D6,E6,F6,H6".split(",")
VCCA33 = ["H5"]
GNDA = "M21,M20,P21,P20,T21,T20,V21,V20,Y21,Y20,AA21,AA20,Y18,Y16,Y14,Y12,Y9,Y5,Y3,AA18,AA16,AA14,AA12,AA9,AA5,N18,N17,P18,P17,T18,T17,U18,U17,V18,V17,V16,V14,V13,V11,V9,V8,V6,L5,L4,N5,N4,P5,P4,T5,T4,U5,U4,V5,V4,AA1,AA2,W1,W2,U1,U2,R1,R2,N1,N2,L1,L2".split(",")
GND = "K2,D4,E4,F4,H4,J4,D5,E5,F5,J5,J6,F8,H8,L9,N9,P9,D11,J11,N11,P11,L13,N13,D17,E17,F17,H17,D18,E18,F18,H18".split(",")

SIG = {
    "M1": "USB2_DP", "M2": "USB2_DM",
    "T1": "USB4_UTX0_P_ASM", "T2": "USB4_UTX0_N_ASM",
    "V2": "USB4_UTX1_P_ASM", "V1": "USB4_UTX1_N_ASM",
    "P1": "USB4_URX0_P_ASM", "P2": "USB4_URX0_N_ASM",
    "Y2": "USB4_URX1_P_ASM", "Y1": "USB4_URX1_N_ASM",
    "G1": "VBUS_5V_SENSE", "AA6": "USB_CC1", "Y6": "USB_CC2",
    "K1": "USB_SBU1", "J1": "USB_SBU2",
    "Y8": "PCIE_REFCLK_N", "AA8": "PCIE_REFCLK_P",
    "W20": "PCIE_RX1_N_ASM", "W21": "PCIE_RX1_P_ASM",
    "N20": "PCIE_RX0_N_ASM", "N21": "PCIE_RX0_P_ASM",
    "U21": "PCIE_TX1_N_ASM", "U20": "PCIE_TX1_P_ASM",
    "R21": "PCIE_TX0_N_ASM", "R20": "PCIE_TX0_P_ASM",
    "Y13": "PCIE_RX3_N_ASM", "AA13": "PCIE_RX3_P_ASM",
    "AA19": "PCIE_RX2_N_ASM", "Y19": "PCIE_RX2_P_ASM",
    "AA15": "PCIE_TX3_N_ASM", "Y15": "PCIE_TX3_P_ASM",
    "AA17": "PCIE_TX2_N_ASM", "Y17": "PCIE_TX2_P_ASM",
    "G21": "PCIE_PERST_N", "H1": "ASM_RST_N", "H21": "ASM_TEST_EN",
    "F1": "ASM_I2C_DATA", "E1": "ASM_I2C_CLK",
    "D1": "ASM_GPIO0", "C1": "ASM_GPIO1", "B1": "ASM_GPIO2", "A1": "ASM_GPIO3",
    "A2": "ASM_SPI_CS_N_CTRL", "A3": "ASM_SPI_DO_CTRL", "A4": "ASM_SPI_DI_CTRL", "A5": "ASM_SPI_CLK_CTRL",
    "A19": "ASM_GPIO8_CLKREQ_N", "A20": "ASM_GPIO14",
    "B21": "ASM_UART_TX", "A21": "ASM_UART_RX", "C21": "ASM_HDDPC",
    "AA3": "ASM_REXT", "AA4": "ASM_XI", "Y4": "ASM_XO",
}
for i in range(6, 19):
    SIG[f"A{i}"] = f"ASM_GPIO{i + 9}"  # A6=GPIO15 .. A18=GPIO27

BALL_NET = {}
for b in NC: BALL_NET[b] = None
for b in VDD: BALL_NET[b] = "+1V05"
for b in VCCL: BALL_NET[b] = "+1V8"
for b in VCCH: BALL_NET[b] = "+3V3"
for b in VCCA33: BALL_NET[b] = "+3V3A"
for b in GNDA: BALL_NET[b] = "GND"
for b in GND: BALL_NET[b] = "GND"
BALL_NET.update(SIG)
assert len(BALL_NET) == 273, len(BALL_NET)

ROWS = ["A","B","C","D","E","F","G","H","J","K","L","M","N","P","R","T","U","V","W","Y","AA"]
ROW_INDEX = {r:i for i,r in enumerate(ROWS)}

def ball_xy(ball: str):
    m = re.fullmatch(r"([A-Z]+)(\d+)", ball)
    row, col = m.group(1), int(m.group(2))
    x = (col - 11) * 0.46
    y = (ROW_INDEX[row] - 10) * 0.46
    return x, y

# Nets.  No production routing widths are encoded here.
NETS = {
    "GND", "+3V3", "+3V3A", "+1V8", "+1V05", "VBUS_5V", "VBUS_5V_SENSE", "VREF_SENSE",
    "USB_CC1", "USB_CC2", "USB_SBU1", "USB_SBU2", "USB2_DP", "USB2_DM",
    "ASM_RST_N", "ASM_UART_TX", "ASM_UART_RX", "ASM_TEST_EN", "ASM_I2C_DATA", "ASM_I2C_CLK",
    "ASM_GPIO0", "ASM_GPIO1", "ASM_GPIO2", "ASM_GPIO3", "ASM_GPIO8_CLKREQ_N", "ASM_GPIO14", "ASM_HDDPC", "ASM_REXT", "ASM_XI", "ASM_XO",
    "ASM_SPI_CS_N_CTRL", "ASM_SPI_CLK_CTRL", "ASM_SPI_DI_CTRL", "ASM_SPI_DO_CTRL",
    "ASM_SPI_CS_N_FLASH", "ASM_SPI_CLK_FLASH", "ASM_SPI_DI_FLASH", "ASM_SPI_DO_FLASH",
    "FLASH_WP_N", "FLASH_HOLD_N",
    "PCIE_REFCLK_P", "PCIE_REFCLK_N", "PCIE_PERST_N",
}
for lane in range(4):
    for pol in ("P", "N"):
        NETS.add(f"PCIE_TX{lane}_{pol}_ASM")
        NETS.add(f"PCIE_TX{lane}_{pol}_M2")
        NETS.add(f"PCIE_RX{lane}_{pol}_ASM")
for lane in range(2):
    for d in ("UTX", "URX"):
        for pol in ("P", "N"):
            NETS.add(f"USB4_{d}{lane}_{pol}_ASM")
            NETS.add(f"USB4_{d}{lane}_{pol}_CONN")
for n in SIG.values(): NETS.add(n)
NET_ID = {name:i+1 for i,name in enumerate(sorted(NETS))}

def net_clause(name):
    if not name: return ""
    return f' (net {NET_ID[name]} "{name}")'

# ---------- footprint helpers ----------
def fp_header(name, layer="F.Cu", descr=""):
    return [f'(footprint "{name}"', '  (version 20240108)', '  (generator "pcbnew")', f'  (layer "{layer}")', f'  (descr "{descr}")', '  (attr smd)']

def fp_texts(ref="REF**", value="VALUE", hide_value=True):
    h = ' hide' if hide_value else ''
    return [
        f'  (fp_text reference "{ref}" (at 0 -6 0) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))',
        f'  (fp_text value "{value}" (at 0 6 0) (layer "F.Fab"){h} (effects (font (size 0.8 0.8) (thickness 0.12))))',
    ]

def line(x1,y1,x2,y2,layer="F.Fab",w=0.1):
    return f'  (fp_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) (stroke (width {w}) (type default)) (layer "{layer}"))'

def rect_lines(x1,y1,x2,y2,layer="F.Fab",w=0.1):
    return [line(x1,y1,x2,y1,layer,w),line(x2,y1,x2,y2,layer,w),line(x2,y2,x1,y2,layer,w),line(x1,y2,x1,y1,layer,w)]

def bga_footprint(instance=False, at=None):
    name="ASM2464PD_BGA273_10x10_P0.46"
    out = fp_header(name, "F.Cu", "ASM2464PD FC-CSP, 10x10mm, 273 balls, 0.46mm pitch; ball map from ASM2464PD datasheet R02. Land sizes remain structural, not fabrication locked.")
    if instance:
        out.insert(4, f'  (at {at[0]:.7f} {at[1]:.7f} {at[2]:.1f})')
        out += fp_texts("U2", "ASM2464PD")
    else: out += fp_texts()
    out += rect_lines(-5,-5,5,5,"F.Fab",0.1)
    out += rect_lines(-5.1,-5.1,5.1,5.1,"F.CrtYd",0.05)
    # pin-1/orientation marker
    out.append('  (fp_circle (center -4.35 -4.35) (end -4.05 -4.35) (stroke (width 0.15) (type default)) (fill none) (layer "F.SilkS"))')
    out.append('  (fp_text user "AA1 USB ESCAPE ORIENTATION CHECK" (at 0 5.55 0) (layer "F.Fab") (effects (font (size 0.55 0.55) (thickness 0.08))))')
    for ball in sorted(BALL_NET, key=lambda b:(ROW_INDEX[re.match(r"[A-Z]+",b).group()], int(re.search(r"\d+",b).group()))):
        x,y=ball_xy(ball); net=BALL_NET[ball]
        # Structural pad: nominal 0.23-mm ball-diameter representation.  The R02
        # datasheet gives an outer-row oblong recommendation, but no complete
        # fabrication land pattern.  Do not treat this as solver/fab locked.
        if instance:
            out.append(f'  (pad "{ball}" smd circle (at {x:.4f} {y:.4f}) (size 0.23 0.23) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause(net)})')
        else:
            out.append(f'  (pad "{ball}" smd circle (at {x:.4f} {y:.4f}) (size 0.23 0.23) (layers "F.Cu" "F.Paste" "F.Mask"))')
    out.append(')')
    return "\n".join(out)+"\n"

def usb_c_footprint(instance=False, at=None):
    name="Molex_105450-0101"
    out=fp_header(name,"F.Cu","Molex 105450-0101. Land/body geometry follows Molex SD-105450-001 A7 and the official KiCad USB_C_Receptacle_Molex_105450-0101 footprint; instance orientation is normalized so the documented PCB-edge datum faces the Rev-A board edge.")
    if instance:
        out.insert(4,f'  (at {at[0]:.7f} {at[1]:.7f} {at[2]:.1f})')
        out += fp_texts("J1","105450-0101")
    else: out += fp_texts()
    # Official KiCad F.Fab/Courtyard geometry, itself tied to Molex SD-105450-001.
    out += rect_lines(-4.6,-3.965,4.6,4.505,"F.Fab",0.1)
    out += rect_lines(-5.3,-4.46,5.3,5.0,"F.CrtYd",0.05)
    out.append(line(-4.0,4.325,4.0,4.325,"Dwgs.User",0.1))
    out.append('  (fp_text user "PCB EDGE DATUM" (at 0 3.875 0) (layer "Dwgs.User") (effects (font (size 0.5 0.5) (thickness 0.08))))')
    # Mating/insertion volume extends outboard of the documented PCB edge.
    out += rect_lines(-4.6,4.325,4.6,9.2,"Dwgs.User",0.1)
    out.append('  (fp_text user "USB INSERTION ENVELOPE" (at 0 6.8 0) (layer "Dwgs.User") (effects (font (size 0.55 0.55) (thickness 0.09))))')
    signal_map={
        "A1":"GND","A2":"USB4_UTX0_P_CONN","A3":"USB4_UTX0_N_CONN","A4":"VBUS_5V","A5":"USB_CC1","A6":"USB2_DP","A7":"USB2_DM","A8":"USB_SBU1","A9":"VBUS_5V","A10":"USB4_URX1_N_CONN","A11":"USB4_URX1_P_CONN","A12":"GND",
        "B12":"GND","B11":"USB4_URX0_P_CONN","B10":"USB4_URX0_N_CONN","B9":"VBUS_5V","B8":"USB_SBU2","B7":"USB2_DM","B6":"USB2_DP","B5":"USB_CC2","B4":"VBUS_5V","B3":"USB4_UTX1_N_CONN","B2":"USB4_UTX1_P_CONN","B1":"GND"}
    # Exact recommended signal-pad locations from official KiCad/Molex geometry.
    a_x={**{i:-3.0+(i-1)*0.5 for i in range(1,7)}, **{i:0.5+(i-7)*0.5 for i in range(7,13)}}
    b_x={1:3.1,12:-3.1, **{i:2.25-(i-2)*0.5 for i in range(2,12)}}
    for i in range(1,13):
        pad=f"A{i}"; net=signal_map[pad] if instance else None
        out.append(f'  (pad "{pad}" smd roundrect (at {a_x[i]:.3f} -3.215) (size 0.300 0.700) (layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25){net_clause(net)})')
    for i in range(1,13):
        pad=f"B{i}"; net=signal_map[pad] if instance else None; sx=1.0 if i in (1,12) else 0.3
        out.append(f'  (pad "{pad}" smd roundrect (at {b_x[i]:.3f} -1.915) (size {sx:.3f} 0.700) (layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25){net_clause(net)})')
    for x,y,sy,dr in [(-4.32,-2.805,2.1,1.6),(-4.32,2.555,2.6,2.1),(4.32,-2.805,2.1,1.6),(4.32,2.555,2.6,2.1)]:
        net="GND" if instance else None
        out.append(f'  (pad "SH" thru_hole oval (at {x:.2f} {y:.3f}) (size 1.1 {sy:.1f}) (drill oval 0.6 {dr:.1f}) (layers "*.Cu" "*.Mask"){net_clause(net)})')
    # Mechanical keepout/profile from official footprint.
    out += rect_lines(-3.5,-1.305,3.5,4.325,"Dwgs.User",0.08)
    out.append('  (model "${KICAD10_3DMODEL_DIR}/Connector_USB.3dshapes/USB_C_Receptacle_Molex_105450-0101.step" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
    out.append(')')
    return "\n".join(out)+"\n"

def m2_footprint(instance=False,at=None):
    name="LOTES_APCI0113-P001A_MKey_RECONSTRUCTED"
    layer="B.Cu" if instance else "F.Cu"
    out=fp_header(name,layer,"M.2 Socket 3 M-key 67-contact reconstruction for LOTES APCI0113-P001A. 0.5mm pitch/67P/right-angle/H4.75 grounded; exact LOTES solder-tail/post land pattern remains pending product drawing ingestion.")
    if instance:
        out.insert(4,f'  (at {at[0]:.7f} {at[1]:.7f} {at[2]:.1f})')
        # bottom footprint text layers need bottom layers
        out += [
          '  (fp_text reference "J2" (at 0 3.0 0) (layer "B.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12)) (justify mirror)))',
          '  (fp_text value "APCI0113-P001A" (at 0 -3.0 0) (layer "B.Fab") hide (effects (font (size 0.8 0.8) (thickness 0.12)) (justify mirror)))']
    else: out += fp_texts()
    layfab="B.Fab" if instance else "F.Fab"; layct="B.CrtYd" if instance else "F.CrtYd"
    out += rect_lines(-10.8,-2.2,10.8,2.2,layfab,0.1)
    out += rect_lines(-11.0,-2.5,11.0,2.5,layct,0.05)
    out += rect_lines(-11.0,-31.0,11.0,-1.7,"Dwgs.User",0.1)
    out.append('  (fp_text user "2230 SSD CARD ENVELOPE" (at 0 -14 0) (layer "Dwgs.User") (effects (font (size 0.6 0.6) (thickness 0.1))))')
    missing=set(range(59,67))
    m2_nets={
      1:"GND",2:"+3V3",3:"GND",4:"+3V3",5:"PCIE_RX3_N_ASM",7:"PCIE_RX3_P_ASM",9:"GND",11:"PCIE_TX3_N_M2",13:"PCIE_TX3_P_M2",15:"GND",
      17:"PCIE_RX2_N_ASM",19:"PCIE_RX2_P_ASM",21:"GND",23:"PCIE_TX2_N_M2",25:"PCIE_TX2_P_M2",27:"GND",
      29:"PCIE_RX1_N_ASM",31:"PCIE_RX1_P_ASM",33:"GND",35:"PCIE_TX1_N_M2",37:"PCIE_TX1_P_M2",39:"GND",
      41:"PCIE_RX0_N_ASM",43:"PCIE_RX0_P_ASM",45:"GND",47:"PCIE_TX0_N_M2",49:"PCIE_TX0_P_M2",50:"PCIE_PERST_N",51:"GND",53:"PCIE_REFCLK_N",55:"PCIE_REFCLK_P",57:"GND",
      67:"GND",69:"+3V3",71:"GND",73:"GND",75:"GND",
    }
    odd=[n for n in range(1,76,2) if n not in missing]; even=[n for n in range(2,75,2) if n not in missing]
    # positions follow M.2 contact field; exact solder-tail dimensions remain provisional.
    for idx,n in enumerate(range(1,76,2)):
        if n in missing: continue
        x=-9.25+idx*0.5; y=-0.65
        net=m2_nets.get(n) if instance else None
        layers='"B.Cu" "B.Paste" "B.Mask"' if instance else '"F.Cu" "F.Paste" "F.Mask"'
        out.append(f'  (pad "{n}" smd rect (at {x:.3f} {y:.3f}) (size 0.28 1.20) (layers {layers}){net_clause(net)})')
    for idx,n in enumerate(range(2,75,2)):
        if n in missing: continue
        x=-9.0+idx*0.5; y=0.65
        net=m2_nets.get(n) if instance else None
        layers='"B.Cu" "B.Paste" "B.Mask"' if instance else '"F.Cu" "F.Paste" "F.Mask"'
        out.append(f'  (pad "{n}" smd rect (at {x:.3f} {y:.3f}) (size 0.28 1.20) (layers {layers}){net_clause(net)})')
    out.append(')')
    return "\n".join(out)+"\n"

def uson8_footprint(instance=False,at=None):
    name="ZD25WQ16CEIGR_USON8_3x2_P0.5_RECONSTRUCTED"
    layer="B.Cu" if instance else "F.Cu"
    out=fp_header(name,layer,"ZD25WQ16CEIGR USON-8 3.0x2.0mm 0.50mm pitch. Exact suffix and pinout are resolved; pad land dimensions remain reconstructed pending manufacturer package drawing.")
    if instance:
        out.insert(4,f'  (at {at[0]:.7f} {at[1]:.7f} {at[2]:.1f})')
        out += ['  (fp_text reference "U31" (at 0 2.1 0) (layer "B.SilkS") (effects (font (size 0.7 0.7) (thickness 0.1)) (justify mirror)))',
                '  (fp_text value "ZD25WQ16CEIGR" (at 0 -2.1 0) (layer "B.Fab") hide (effects (font (size 0.7 0.7) (thickness 0.1)) (justify mirror)))']
    else: out += fp_texts()
    fab="B.Fab" if instance else "F.Fab"; crt="B.CrtYd" if instance else "F.CrtYd"
    out += rect_lines(-1,-1.5,1,1.5,fab,0.1); out += rect_lines(-1.25,-1.75,1.25,1.75,crt,0.05)
    pin_nets={1:"ASM_SPI_CS_N_FLASH",2:"ASM_SPI_DI_FLASH",3:"FLASH_WP_N",4:"GND",5:"ASM_SPI_DO_FLASH",6:"ASM_SPI_CLK_FLASH",7:"FLASH_HOLD_N",8:"+3V3"}
    # Four pads on each long side; exposed thermal/ground pad as EP.
    ys=[-0.75,-0.25,0.25,0.75]
    for i,y in enumerate(ys,1):
        n=i; net=pin_nets[n] if instance else None; layers='"B.Cu" "B.Paste" "B.Mask"' if instance else '"F.Cu" "F.Paste" "F.Mask"'
        out.append(f'  (pad "{n}" smd rect (at -0.85 {y:.2f}) (size 0.55 0.28) (layers {layers}){net_clause(net)})')
    for j,y in enumerate(reversed(ys),5):
        n=j; net=pin_nets[n] if instance else None; layers='"B.Cu" "B.Paste" "B.Mask"' if instance else '"F.Cu" "F.Paste" "F.Mask"'
        out.append(f'  (pad "{n}" smd rect (at 0.85 {y:.2f}) (size 0.55 0.28) (layers {layers}){net_clause(net)})')
    out.append('  (fp_circle (center -0.75 -1.18) (end -0.60 -1.18) (stroke (width 0.1) (type default)) (fill none) (layer "%s"))' % ("B.SilkS" if instance else "F.SilkS"))
    out.append(')')
    return "\n".join(out)+"\n"

def passive_fp(ref,value,x,y,rot,net1,net2,size="0201",layer="F.Cu"):
    if size=="0201": L,W,pad=(0.6,0.3,0.28)
    else: L,W,pad=(1.0,0.5,0.42)
    silk="B.SilkS" if layer=="B.Cu" else "F.SilkS"; fab="B.Fab" if layer=="B.Cu" else "F.Fab"; cu='"B.Cu" "B.Paste" "B.Mask"' if layer=="B.Cu" else '"F.Cu" "F.Paste" "F.Mask"'
    return f'''(footprint "RevA_{size}_2Pad"
  (version 20240108)
  (generator "pcbnew")
  (layer "{layer}")
  (at {x:.6f} {y:.6f} {rot:.1f})
  (property "Reference" "{ref}" (at 0 {-W-0.35:.2f} 0) (layer "{silk}") (effects (font (size 0.45 0.45) (thickness 0.08)){' (justify mirror)' if layer=='B.Cu' else ''}))
  (property "Value" "{value}" (at 0 {W+0.35:.2f} 0) (layer "{fab}") hide (effects (font (size 0.45 0.45) (thickness 0.08)){' (justify mirror)' if layer=='B.Cu' else ''}))
  (fp_rect (start {-L/2:.3f} {-W/2:.3f}) (end {L/2:.3f} {W/2:.3f}) (stroke (width 0.05) (type default)) (fill none) (layer "{fab}"))
  (pad "1" smd roundrect (at {-L/2:.3f} 0) (size {pad:.3f} {W:.3f}) (layers {cu}) (roundrect_rratio 0.25){net_clause(net1)})
  (pad "2" smd roundrect (at {L/2:.3f} 0) (size {pad:.3f} {W:.3f}) (layers {cu}) (roundrect_rratio 0.25){net_clause(net2)})
)'''

def simple_block_fp(ref,value,x,y,w,h,layer="F.Cu"):
    silk="B.SilkS" if layer=="B.Cu" else "F.SilkS"; fab="B.Fab" if layer=="B.Cu" else "F.Fab"; crt="B.CrtYd" if layer=="B.Cu" else "F.CrtYd"
    return f'''(footprint "RevA_{ref}_{value}"
  (version 20240108) (generator "pcbnew") (layer "{layer}") (at {x:.6f} {y:.6f})
  (property "Reference" "{ref}" (at 0 {-h/2-0.5:.2f} 0) (layer "{silk}") (effects (font (size 0.55 0.55) (thickness 0.09)){' (justify mirror)' if layer=='B.Cu' else ''}))
  (property "Value" "{value}" (at 0 {h/2+0.5:.2f} 0) (layer "{fab}") hide (effects (font (size 0.5 0.5) (thickness 0.08)){' (justify mirror)' if layer=='B.Cu' else ''}))
  (fp_rect (start {-w/2:.3f} {-h/2:.3f}) (end {w/2:.3f} {h/2:.3f}) (stroke (width 0.08) (type default)) (fill none) (layer "{fab}"))
  (fp_rect (start {-w/2-0.2:.3f} {-h/2-0.2:.3f}) (end {w/2+0.2:.3f} {h/2+0.2:.3f}) (stroke (width 0.05) (type default)) (fill none) (layer "{crt}"))
)'''

# Reference-derived AC coupling parts translated from Leaves source-native PnP.
AC_PARTS=[]
# USB: ref/value/layer/raw x/raw y, ASM-side net, connector-side net
usb_defs=[
 ("C64","220n","F.Cu",1066.646,1259.582,"USB4_UTX0_P_ASM","USB4_UTX0_P_CONN"),
 ("C65","220n","F.Cu",1036.646,1259.582,"USB4_UTX0_N_ASM","USB4_UTX0_N_CONN"),
 ("C75","220n","B.Cu",948.645,1259.582,"USB4_UTX1_P_ASM","USB4_UTX1_P_CONN"),
 ("C74","220n","B.Cu",978.645,1259.582,"USB4_UTX1_N_ASM","USB4_UTX1_N_CONN"),
 ("C62","330n","B.Cu",1154.064,1259.582,"USB4_URX0_P_ASM","USB4_URX0_P_CONN"),
 ("C63","330n","B.Cu",1124.064,1259.582,"USB4_URX0_N_ASM","USB4_URX0_N_CONN"),
 ("C73","330n","F.Cu",857.286,1259.582,"USB4_URX1_P_ASM","USB4_URX1_P_CONN"),
 ("C72","330n","F.Cu",887.286,1259.582,"USB4_URX1_N_ASM","USB4_URX1_N_CONN"),
]
for d in usb_defs:
    ref,val,layer,rx,ry,n1,n2=d; x,y=pnp(rx,ry); AC_PARTS.append((ref,val,x,y,270,n1,n2,"0201",layer,"REFERENCE_SOURCE_NATIVE"))
# PCIe U2 TX -> M.2 PER lanes. Leaves caps source-native.
pcie_defs=[
 ("C56","220n",1091.000,620.823,"PCIE_TX0_P_ASM","PCIE_TX0_P_M2"),
 ("C55","220n",1061.000,620.823,"PCIE_TX0_N_ASM","PCIE_TX0_N_M2"),
 ("C54","220n",999.631,587.909,"PCIE_TX1_P_ASM","PCIE_TX1_P_M2"),
 ("C53","220n",969.631,587.909,"PCIE_TX1_N_ASM","PCIE_TX1_N_M2"),
 ("C52","220n",813.654,617.422,"PCIE_TX2_P_ASM","PCIE_TX2_P_M2"),
 ("C50","220n",783.654,617.422,"PCIE_TX2_N_ASM","PCIE_TX2_N_M2"),
 ("C49","220n",763.500,528.771,"PCIE_TX3_P_ASM","PCIE_TX3_P_M2"),
 ("C48","220n",733.500,528.771,"PCIE_TX3_N_ASM","PCIE_TX3_N_M2"),
]
for d in pcie_defs:
    ref,val,rx,ry,n1,n2=d; x,y=pnp(rx,ry); AC_PARTS.append((ref,val,x,y,270,n1,n2,"0201","F.Cu","REFERENCE_SOURCE_NATIVE"))

# SPI isolation positions are a deliberate Rev-A engineering placement, physically explicit.
SPI_ISO=[
 ("RISO1","0R_LINK",4.02,11.7,0,"ASM_SPI_CS_N_CTRL","ASM_SPI_CS_N_FLASH"),
 ("RISO2","0R_LINK",4.02,13.8,0,"ASM_SPI_CLK_CTRL","ASM_SPI_CLK_FLASH"),
 ("RISO3","0R_LINK",4.02,15.9,0,"ASM_SPI_DI_CTRL","ASM_SPI_DI_FLASH"),
 ("RISO4","0R_LINK",4.02,18.0,0,"ASM_SPI_DO_CTRL","ASM_SPI_DO_FLASH"),
]

# Representative decoupling and crystal network for intelligibility; full population is not claimed.
SUPPORT_PASSIVES=[
 ("R4","12.1k",17.7,13.0,0,"ASM_REXT","GND","0402","F.Cu","AUTHORITATIVE_DATASHEET_VALUE"),
 ("CDEC1","100n",12.0,12.0,0,"+1V05","GND","0402","B.Cu","ENGINEERING_ASSUMPTION_REPRESENTATIVE"),
 ("CDEC2","2.2u",13.0,12.0,0,"+1V05","GND","0402","B.Cu","REFERENCE_SOURCE_NATIVE_REPRESENTATIVE"),
 ("CDEC3","100n",14.0,12.0,0,"+1V8","GND","0402","B.Cu","ENGINEERING_ASSUMPTION_REPRESENTATIVE"),
 ("CDEC4","100n",15.0,12.0,0,"+3V3","GND","0402","B.Cu","ENGINEERING_ASSUMPTION_REPRESENTATIVE"),
 ("CDEC5","100n",16.0,12.0,0,"+3V3A","GND","0402","B.Cu","ENGINEERING_ASSUMPTION_REPRESENTATIVE"),
 ("RFWP","10k",19.3,22.0,90,"FLASH_WP_N","+3V3","0402","B.Cu","ENGINEERING_ASSUMPTION"),
 ("RFHOLD","10k",20.0,22.0,90,"FLASH_HOLD_N","+3V3","0402","B.Cu","ENGINEERING_ASSUMPTION"),
 ("CF","100n",21.1,22.0,90,"+3V3","GND","0402","B.Cu","ENGINEERING_ASSUMPTION"),
]
# Reference crystal placement from Leaves source-native data.
x1x,x1y=pnp(798.164,1077.397)
c77x,c77y=pnp(853.091,1106.487); c78x,c78y=pnp(853.090,1047.755)
SUPPORT_PASSIVES += [
 ("C77","30p",c77x,c77y,90,"ASM_XI","GND","0402","F.Cu","REFERENCE_SOURCE_NATIVE"),
 ("C78","30p",c78x,c78y,270,"ASM_XO","GND","0402","F.Cu","REFERENCE_SOURCE_NATIVE"),
]


def esd2_fp(ref, value, x, y, net_a, net_b):
    # Generic electrical placeholder for a required 2-channel shunt ESD function.
    # It is intentionally not a selected component/land pattern.
    return f'''(footprint "RevA_ESD2_SELECTION_PENDING_{ref}"
  (version 20240108) (generator "pcbnew") (layer "F.Cu") (at {x:.6f} {y:.6f})
  (property "Reference" "{ref}" (at 0 -1.0 0) (layer "F.SilkS") (effects (font (size 0.45 0.45) (thickness 0.08))))
  (property "Value" "{value}" (at 0 1.0 0) (layer "F.Fab") hide (effects (font (size 0.45 0.45) (thickness 0.08))))
  (fp_rect (start -0.7 -0.45) (end 0.7 0.45) (stroke (width 0.05) (type default)) (fill none) (layer "F.Fab"))
  (pad "1" smd rect (at -0.55 -0.2) (size 0.35 0.25) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause(net_a)})
  (pad "2" smd rect (at 0.55 -0.2) (size 0.35 0.25) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause(net_a)})
  (pad "3" smd rect (at -0.55 0.2) (size 0.35 0.25) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause(net_b)})
  (pad "4" smd rect (at 0.55 0.2) (size 0.35 0.25) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause(net_b)})
  (pad "5" smd rect (at 0 0) (size 0.3 0.3) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause("GND")})
)'''

def crystal_fp(ref, x, y):
    return f'''(footprint "RevA_Crystal_4P_1.6x1.2_{ref}"
  (version 20240108) (generator "pcbnew") (layer "F.Cu") (at {x:.6f} {y:.6f} 90)
  (property "Reference" "{ref}" (at 0 -1.2 0) (layer "F.SilkS") (effects (font (size 0.45 0.45) (thickness 0.08))))
  (property "Value" "25MHz" (at 0 1.2 0) (layer "F.Fab") hide (effects (font (size 0.45 0.45) (thickness 0.08))))
  (fp_rect (start -0.8 -0.6) (end 0.8 0.6) (stroke (width 0.06) (type default)) (fill none) (layer "F.Fab"))
  (pad "1" smd rect (at -0.55 -0.35) (size 0.45 0.35) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause("ASM_XI")})
  (pad "2" smd rect (at 0.55 -0.35) (size 0.45 0.35) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause("GND")})
  (pad "3" smd rect (at 0.55 0.35) (size 0.45 0.35) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause("ASM_XO")})
  (pad "4" smd rect (at -0.55 0.35) (size 0.45 0.35) (layers "F.Cu" "F.Paste" "F.Mask"){net_clause("GND")})
)'''

# ---------- board ----------
def build_board():
    lines=['(kicad_pcb','  (version 20240108)','  (generator "pcbnew")','  (generator_version "8.0")',f'  (general (thickness {BOARD_T}) (legacy_teardrops no))',
    '  (paper "A4")',
    '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (36 "B.SilkS" user "b.silkscreen") (37 "F.SilkS" user "f.silkscreen") (44 "Edge.Cuts" user) (46 "B.CrtYd" user "b.courtyard") (47 "F.CrtYd" user "f.courtyard") (48 "B.Fab" user) (49 "F.Fab" user) (36 "B.SilkS" user) (37 "F.SilkS" user))']
    # use standard layer table instead, avoid duplicate IDs
    lines[-1]='''  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "b.silkscreen")
    (37 "F.SilkS" user "f.silkscreen")
    (44 "Edge.Cuts" user)
    (46 "B.CrtYd" user "b.courtyard")
    (47 "F.CrtYd" user "f.courtyard")
    (48 "B.Fab" user)
    (49 "F.Fab" user)
    (40 "Dwgs.User" user "user drawings")
  )'''
    lines += ['  (setup (pad_to_mask_clearance 0))']
    for name,nid in sorted(NET_ID.items(),key=lambda kv:kv[1]):
        lines.append(f'  (net {nid} "{name}")')
    # Major footprints
    for txt in [bga_footprint(True,PLACEMENT["U2"]),usb_c_footprint(True,PLACEMENT["J1"]),m2_footprint(True,PLACEMENT["J2"]),uson8_footprint(True,PLACEMENT["U31"])]:
        lines.append("\n".join("  "+ln if ln else ln for ln in txt.rstrip().splitlines()))
    # AC coupling and support passives
    for ref,val,x,y,rot,n1,n2,size,layer,_ev in AC_PARTS:
        lines.append("\n".join("  "+ln for ln in passive_fp(ref,val,x,y,rot,n1,n2,size,layer).splitlines()))
    for ref,val,x,y,rot,n1,n2 in SPI_ISO:
        lines.append("\n".join("  "+ln for ln in passive_fp(ref,val,x,y,rot,n1,n2,"0402","F.Cu").splitlines()))
    for ref,val,x,y,rot,n1,n2,size,layer,_ev in SUPPORT_PASSIVES:
        lines.append("\n".join("  "+ln for ln in passive_fp(ref,val,x,y,rot,n1,n2,size,layer).splitlines()))
    # Crystal and load network. X1 pin assignment is electrically meaningful; C77/C78 values remain reference-derived.
    lines.append("\n".join("  "+ln for ln in crystal_fp("X1",x1x,x1y).splitlines()))
    # Required shunt-protection functions. Exact low-capacitance parts remain deliberately unselected.
    esd_pairs=[
      ("DUSB41",6.0,24.2,"USB4_UTX0_P_CONN","USB4_UTX0_N_CONN"),
      ("DUSB42",6.0,25.0,"USB4_UTX1_P_CONN","USB4_UTX1_N_CONN"),
      ("DUSB43",6.0,25.8,"USB4_URX0_P_CONN","USB4_URX0_N_CONN"),
      ("DUSB44",6.0,26.6,"USB4_URX1_P_CONN","USB4_URX1_N_CONN"),
      ("DUSB2",8.0,27.0,"USB2_DP","USB2_DM"),
    ]
    for ref,x,y,na,nb in esd_pairs:
        lines.append("\n".join("  "+ln for ln in esd2_fp(ref,"ESD_2CH_SELECTION_PENDING",x,y,na,nb).splitlines()))
    # Power regulators/inductors are deliberately deferred until their datasheets are ingested and rails can be netted.
    # Service 2x5 provisional footprint at a deliberate accessible left-side location.
    sx,sy=4.019917,4.1402
    service_nets=["GND","VREF_SENSE","ASM_UART_TX","ASM_UART_RX","ASM_RST_N","ASM_SPI_CS_N_FLASH","ASM_SPI_CLK_FLASH","ASM_SPI_DI_FLASH","ASM_SPI_DO_FLASH","GND"]
    s=['(footprint "RevA_Service_2x5_1.27_PROVISIONAL"','  (version 20240108) (generator "pcbnew") (layer "F.Cu")',f'  (at {sx} {sy})',
       '  (property "Reference" "J3" (at 0 -4.0 0) (layer "F.SilkS") (effects (font (size 0.6 0.6) (thickness 0.1))))',
       '  (property "Value" "SERVICE_PROVISIONAL" (at 0 4.0 0) (layer "F.Fab") hide (effects (font (size 0.6 0.6) (thickness 0.1))))']
    for pin in range(1,11):
        col=0 if pin%2 else 1; row=(pin-1)//2; px=(col-0.5)*1.27; py=(row-2)*1.27; net=service_nets[pin-1]
        s.append(f'  (pad "{pin}" thru_hole circle (at {px:.3f} {py:.3f}) (size 0.9 0.9) (drill 0.45) (layers "*.Cu" "*.Mask"){net_clause(net)})')
    s.append('  (fp_rect (start -1.6 -3.2) (end 1.6 3.2) (stroke (width 0.08) (type default)) (fill none) (layer "F.CrtYd"))'); s.append(')')
    lines.append("\n".join("  "+ln for ln in s))
    # VREF_SENSE is exported on J3 pad 2 but intentionally not bound to a rail until the service-voltage source is established.
    # Board outline
    lines += [
      f'  (gr_line (start 0 0) (end {BOARD_W:.6f} 0) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))',
      f'  (gr_line (start {BOARD_W:.6f} 0) (end {BOARD_W:.6f} {BOARD_H:.6f}) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))',
      f'  (gr_line (start {BOARD_W:.6f} {BOARD_H:.6f}) (end 0 {BOARD_H:.6f}) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))',
      f'  (gr_line (start 0 {BOARD_H:.6f}) (end 0 0) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))',
      '  (gr_text "ASM2464 USB4 / PCIe" (at 11 31.9 0) (layer "F.SilkS") (effects (font (size 0.85 0.85) (thickness 0.13))))',
      '  (gr_text "REV A" (at 18.5 2.4 0) (layer "F.SilkS") (effects (font (size 0.9 0.9) (thickness 0.14))))',
      '  (gr_text "ENGINEERING SAMPLE" (at 11 8.0 0) (layer "F.SilkS") (effects (font (size 0.7 0.7) (thickness 0.11))))',
      '  (gr_text "NO FAB ROUTING - STACKUP/SOLVER PENDING" (at 11 10.1 0) (layer "Dwgs.User") (effects (font (size 0.55 0.55) (thickness 0.09))))',
      '  (gr_rect (start 16.4 29.6) (end 20.9 32.4) (stroke (width 0.1) (type default)) (fill none) (layer "Dwgs.User"))',
      '  (gr_text "LOGO / ARTWORK RESERVED" (at 18.65 31.0 90) (layer "Dwgs.User") (effects (font (size 0.42 0.42) (thickness 0.07))))',
      ')'
    ]
    return "\n".join(lines)+"\n"

# ---------- schematic ----------
def uid(seed): return str(uuid.uuid5(uuid.NAMESPACE_URL,"mewmix/asm2464-enclosure/rev-a/"+seed))

SHEET_UUID = uid("sheet")


def sch_text(text,x,y,size=1.0):
    return f'''  (text "{text}" (exclude_from_sim no) (at {x:.2f} {y:.2f} 0)
    (effects (font (size {size:.2f} {size:.2f})) (justify left bottom))
    (uuid {uid('text:'+text+str(x)+str(y))})
  )'''


def sch_label(name,x,y,rot=0,seed=""):
    return f'''  (label "{name}" (at {x:.2f} {y:.2f} {rot})
    (effects (font (size 0.75 0.75)) (justify left bottom))
    (uuid {uid('label:'+name+str(x)+str(y)+seed)})
  )'''


def _sch_prop(name, value, x, y, hide=False):
    h=' hide' if hide else ''
    return f'''    (property "{name}" "{value}" (at {x:.2f} {y:.2f} 0)
      (effects (font (size 1.0 1.0)){h})
    )'''


def lib_box_symbol(lib_id, ref_prefix, pins, width=20.0):
    n=max(1,len(pins)); per=(n+1)//2; pitch=2.54
    height=max(10.16,(per+1)*pitch)
    hx=width/2; hy=height/2
    short=lib_id.split(":")[-1]
    out=[f'    (symbol "{lib_id}"', '      (pin_names (offset 1.016))', '      (exclude_from_sim no)', '      (in_bom yes)', '      (on_board yes)',
         f'      (property "Reference" "{ref_prefix}" (at 0 {hy+2.54:.2f} 0) (effects (font (size 1.0 1.0))))',
         f'      (property "Value" "{short}" (at 0 {-hy-2.54:.2f} 0) (effects (font (size 1.0 1.0))))',
         '      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.0 1.0)) hide))',
         '      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.0 1.0)) hide))',
         f'      (symbol "{short}_0_1"',
         f'        (rectangle (start {-hx:.2f} {hy:.2f}) (end {hx:.2f} {-hy:.2f}) (stroke (width 0.254) (type default)) (fill (type background)))',
         '      )',
         f'      (symbol "{short}_1_1"']
    endpoints={}
    for i,(num,name,etype) in enumerate(pins):
        if i<per:
            y=hy-pitch*(i+1); x=-(hx+2.54); angle=0
        else:
            j=i-per; y=hy-pitch*(j+1); x=hx+2.54; angle=180
        endpoints[num]=(x,y)
        out += [f'        (pin {etype} line (at {x:.2f} {y:.2f} {angle}) (length 2.54)',
                f'          (name "{name}" (effects (font (size 0.75 0.75))))',
                f'          (number "{num}" (effects (font (size 0.75 0.75))))',
                '        )']
    out += ['      )','    )']
    return "\n".join(out), endpoints


def lib_series_symbol():
    return '''    (symbol "RevA:Series2"
      (pin_names (offset 0) hide)
      (exclude_from_sim no)
      (in_bom yes)
      (on_board yes)
      (property "Reference" "R" (at 0 2.00 0) (effects (font (size 1.0 1.0))))
      (property "Value" "Series2" (at 0 -2.00 0) (effects (font (size 1.0 1.0))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.0 1.0)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.0 1.0)) hide))
      (symbol "Series2_0_1"
        (rectangle (start -1.27 0.80) (end 1.27 -0.80) (stroke (width 0.254) (type default)) (fill (type none)))
      )
      (symbol "Series2_1_1"
        (pin passive line (at -3.81 0 0) (length 2.54) (name "1" (effects (font (size 0.75 0.75)))) (number "1" (effects (font (size 0.75 0.75)))))
        (pin passive line (at 3.81 0 180) (length 2.54) (name "2" (effects (font (size 0.75 0.75)))) (number "2" (effects (font (size 0.75 0.75)))))
      )
    )'''


def sch_instance(lib_id, ref, value, footprint, at, pin_numbers, project="rev-a"):
    x,y=at; suid=uid('sym:'+ref)
    out=[f'  (symbol (lib_id "{lib_id}") (at {x:.2f} {y:.2f} 0) (unit 1)',
         '    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
         f'    (uuid {suid})',
         _sch_prop('Reference',ref,x,y+2.0).replace('    ','',1),
         _sch_prop('Value',value,x,y-2.0).replace('    ','',1),
         _sch_prop('Footprint',footprint,x,y,True).replace('    ','',1),
         _sch_prop('Datasheet','~',x,y,True).replace('    ','',1)]
    for pn in pin_numbers:
        out.append(f'    (pin "{pn}" (uuid {uid("pin:"+ref+":"+str(pn))}))')
    out += ['    (instances',f'      (project "{project}"',f'        (path "/{SHEET_UUID}" (reference "{ref}") (unit 1))','      )','    )','  )']
    return "\n".join(out), suid


def build_schematic():
    j1_pins=[
      ("A1","GND","power_in"),("A2","USB4_UTX0_P_CONN","passive"),("A3","USB4_UTX0_N_CONN","passive"),("A4","VBUS_5V","power_in"),
      ("A5","USB_CC1","bidirectional"),("A6","USB2_DP","bidirectional"),("A7","USB2_DM","bidirectional"),("A8","USB_SBU1","bidirectional"),
      ("A9","VBUS_5V","power_in"),("A10","USB4_URX1_N_CONN","passive"),("A11","USB4_URX1_P_CONN","passive"),("A12","GND","power_in"),
      ("B12","GND","power_in"),("B11","USB4_URX0_P_CONN","passive"),("B10","USB4_URX0_N_CONN","passive"),("B9","VBUS_5V","power_in"),
      ("B8","USB_SBU2","bidirectional"),("B7","USB2_DM","bidirectional"),("B6","USB2_DP","bidirectional"),("B5","USB_CC2","bidirectional"),
      ("B4","VBUS_5V","power_in"),("B3","USB4_UTX1_N_CONN","passive"),("B2","USB4_UTX1_P_CONN","passive"),("B1","GND","power_in")]
    required={
      "USB4_UTX0_P_ASM","USB4_UTX0_N_ASM","USB4_UTX1_P_ASM","USB4_UTX1_N_ASM","USB4_URX0_P_ASM","USB4_URX0_N_ASM","USB4_URX1_P_ASM","USB4_URX1_N_ASM",
      "USB2_DP","USB2_DM","USB_CC1","USB_CC2","USB_SBU1","USB_SBU2","VBUS_5V_SENSE","PCIE_REFCLK_P","PCIE_REFCLK_N","PCIE_PERST_N",
      "ASM_RST_N","ASM_UART_TX","ASM_UART_RX","ASM_SPI_CS_N_CTRL","ASM_SPI_CLK_CTRL","ASM_SPI_DI_CTRL","ASM_SPI_DO_CTRL","ASM_REXT","ASM_XI","ASM_XO"}
    required |= {f"PCIE_TX{l}_{p}_ASM" for l in range(4) for p in ("P","N")}
    required |= {f"PCIE_RX{l}_{p}_ASM" for l in range(4) for p in ("P","N")}
    u2_nums=[b for b,n in BALL_NET.items() if n in required]
    u2_pins=[(b,BALL_NET[b],"bidirectional") for b in u2_nums]
    j2_map={"5":"PCIE_RX3_N_ASM","7":"PCIE_RX3_P_ASM","11":"PCIE_TX3_N_M2","13":"PCIE_TX3_P_M2","17":"PCIE_RX2_N_ASM","19":"PCIE_RX2_P_ASM","23":"PCIE_TX2_N_M2","25":"PCIE_TX2_P_M2","29":"PCIE_RX1_N_ASM","31":"PCIE_RX1_P_ASM","35":"PCIE_TX1_N_M2","37":"PCIE_TX1_P_M2","41":"PCIE_RX0_N_ASM","43":"PCIE_RX0_P_ASM","47":"PCIE_TX0_N_M2","49":"PCIE_TX0_P_M2","50":"PCIE_PERST_N","53":"PCIE_REFCLK_N","55":"PCIE_REFCLK_P"}
    j2_pins=[(pn,net,"bidirectional") for pn,net in j2_map.items()]
    u31_pins=[("1","ASM_SPI_CS_N_FLASH","input"),("2","ASM_SPI_DI_FLASH","output"),("3","FLASH_WP_N","input"),("4","GND","power_in"),("5","ASM_SPI_DO_FLASH","input"),("6","ASM_SPI_CLK_FLASH","input"),("7","FLASH_HOLD_N","input"),("8","+3V3","power_in")]
    j3_names=["GND","VREF_SENSE","ASM_UART_TX","ASM_UART_RX","ASM_RST_N","ASM_SPI_CS_N_FLASH","ASM_SPI_CLK_FLASH","ASM_SPI_DI_FLASH","ASM_SPI_DO_FLASH","GND"]
    j3_pins=[(str(i+1),n,"bidirectional") for i,n in enumerate(j3_names)]

    defs=[]; endpoint={}
    for lib,ref,pins,w in [("RevA:Molex_105450_0101","J",j1_pins,22),("RevA:ASM2464PD_REQUIRED","U",u2_pins,30),("RevA:M2_MKEY_PCIE_X4","J",j2_pins,24),("RevA:ZD25WQ16CEIGR","U",u31_pins,20),("RevA:SERVICE_2X5","J",j3_pins,20)]:
        txt,ep=lib_box_symbol(lib,ref,pins,w); defs.append(txt); endpoint[lib]=ep
    defs.append(lib_series_symbol())
    L=['(kicad_sch','  (version 20231120)','  (generator "eeschema")','  (generator_version "8.0")',f'  (uuid {SHEET_UUID})','  (paper "A3")','  (lib_symbols',*defs,'  )']
    L += [sch_text("REV-A STRUCTURAL NETTED SCHEMATIC — ROUTING RULES UNLOCKED",20,12,1.35),sch_text("Required USB4 / PCIe / USB2 / SPI / service topology. Power tree intentionally incomplete until regulator evidence is bound.",20,17,0.75)]
    placements={
      "J1":("RevA:Molex_105450_0101",(32,88),"Molex 105450-0101","rev-a-footprints:Molex_105450-0101",j1_pins),
      "U2":("RevA:ASM2464PD_REQUIRED",(200,92),"ASM2464PD","rev-a-footprints:ASM2464PD_BGA273_10x10_P0.46",u2_pins),
      "J2":("RevA:M2_MKEY_PCIE_X4",(365,90),"APCI0113-P001A","rev-a-footprints:LOTES_APCI0113-P001A_MKey_RECONSTRUCTED",j2_pins),
      "U31":("RevA:ZD25WQ16CEIGR",(210,235),"ZD25WQ16CEIGR","rev-a-footprints:ZD25WQ16CEIGR_USON8_3x2_P0.5_RECONSTRUCTED",u31_pins),
      "J3":("RevA:SERVICE_2X5",(310,235),"SERVICE_PROVISIONAL","RevA_Service_2x5_1.27_PROVISIONAL",j3_pins)}
    for ref,(lib,at,val,fp,pins) in placements.items():
        inst,_=sch_instance(lib,ref,val,fp,at,[p[0] for p in pins]); L.append(inst)
        ep=endpoint[lib]
        for pn,name,_etype in pins:
            dx,dy=ep[pn]; L.append(sch_label(name,at[0]+dx,at[1]+dy,0,ref+pn))
    sx0,sy0=95,30
    for i,(ref,val,_x,_y,_rot,n1,n2,size,_layer,_ev) in enumerate(AC_PARTS):
        col=i//8; row=i%8; x=sx0+col*70; y=sy0+row*8
        inst,_=sch_instance("RevA:Series2",ref,val,f"rev-a-footprints:RevA_{size}_2Pad",(x,y),["1","2"]);L.append(inst)
        L.append(sch_label(n1,x-3.81,y,0,ref+'1'));L.append(sch_label(n2,x+3.81,y,180,ref+'2'))
    for i,(ref,val,_x,_y,_rot,n1,n2) in enumerate(SPI_ISO):
        x=245;y=210+i*8
        inst,_=sch_instance("RevA:Series2",ref,val,"rev-a-footprints:RevA_0402_2Pad",(x,y),["1","2"]);L.append(inst)
        L.append(sch_label(n1,x-3.81,y,0,ref+'1'));L.append(sch_label(n2,x+3.81,y,180,ref+'2'))
    for i,(ref,val,_x,_y,_rot,n1,n2,size,_layer,_ev) in enumerate(SUPPORT_PASSIVES):
        x=35+(i%6)*28;y=205+(i//6)*10
        inst,_=sch_instance("RevA:Series2",ref,val,f"rev-a-footprints:RevA_{size}_2Pad",(x,y),["1","2"]);L.append(inst)
        L.append(sch_label(n1,x-3.81,y,0,ref+'1'));L.append(sch_label(n2,x+3.81,y,180,ref+'2'))
    L += [sch_text("USB ESD functions are on PCB as unselected shunt placeholders; exact low-C parts/lands remain a blocker.",20,267,0.72),sch_text("RESET CONTRACT: ASM_RST_N does not grant SPI ownership; reset-time controller tri-state behavior is unresolved.",20,273,0.72),sch_text("VREF_SENSE is exported at J3 but not bound to a rail until the service-voltage source is established.",20,279,0.72),'  (sheet_instances (path "/" (page "1")))',')']
    return "\n".join(L)+"\n"

# ---------- audit ----------
def component_entry(ref,component,footprint,pos,rot,source,confidence,electrical,mechanical,blockers=None):
    return {"component":component,"refdes":ref,"footprint":footprint,"position_mm":{"x":pos[0],"y":pos[1]},"rotation_deg":rot,"source_evidence":source,"confidence":confidence,"electrical_status":electrical,"mechanical_status":mechanical,"unresolved_blockers":blockers or []}

def build_audit():
    comps=[]
    comps.append(component_entry("U2","ASM2464PD","ASM2464PD_BGA273_10x10_P0.46",PLACEMENT["U2"],270,["AUTHORITATIVE_ASM2464PD_DATASHEET_R02","FAB_OUTPUT_LEAVES_PNP_PLACEMENT"],"HIGH","AUTHORITATIVE_BALL_MAP_NETTED_FOR_REQUIRED_DOMAINS","STRUCTURAL_LAND_GEOMETRY_ONLY",["COMPLETE_FAB_LAND_PATTERN_NOT_PUBLISHED_IN_INGESTED_DATASHEET","STACKUP_UNSELECTED","FIELD_SOLVER_NOT_RUN"]))
    comps.append(component_entry("J1","Molex 105450-0101","Molex_105450-0101",PLACEMENT["J1"],0,["REFERENCE_SOURCE_NATIVE_PART_ID","AUTHORITATIVE_MOLEX_DRAWING","OFFICIAL_KICAD_FOOTPRINT_CROSSCHECK"],"HIGH","TYPE_C_PIN_ASSIGNMENT_NETTED","BODY_PAD_AND_PCB_EDGE_DATUM_SOURCE_GROUNDED;KICAD_ORIENTATION_NORMALIZED_FROM_SOURCE_PNP",["SOURCE_PNP_ROTATION_180_NOT_DIRECTLY_PORTABLE_ACROSS_LIBRARY_ORIENTATION","ESD_PART_SELECTION_PENDING"]))
    comps.append(component_entry("J2","LOTES APCI0113-P001A","LOTES_APCI0113-P001A_MKey_RECONSTRUCTED",PLACEMENT["J2"],0,["REFERENCE_SOURCE_NATIVE_PART_ID","LOTES_DISTRIBUTOR_DATASHEET_METADATA","M2_SOCKET3_PIN_ASSIGNMENT_INDEPENDENT_REFERENCE"],"MEDIUM","PCIE_X4_REFCLK_PERST_NETTED","67_CONTACT_0P5MM_H4P75_RECONSTRUCTION",["EXACT_LOTES_PRODUCT_DRAWING_SOLDER_TAIL_AND_POST_LAND_PATTERN_NOT_INGESTED"]))
    comps.append(component_entry("U31","ZD25WQ16CEIGR","ZD25WQ16CEIGR_USON8_3x2_P0.5_RECONSTRUCTED",PLACEMENT["U31"],0,["REFERENCE_SOURCE_NATIVE_EXACT_SUFFIX","ZD25WQ16C_COMPONENT_DATASHEET"],"HIGH","SPI_PINOUT_RESOLVED_AND_NETTED","PACKAGE_BODY_AND_PITCH_RESOLVED_LANDS_RECONSTRUCTED",["MANUFACTURER_USON_LAND_PATTERN_DRAWING_NOT_INGESTED"]))
    for ref,val,x,y,rot,n1,n2,size,layer,ev in AC_PARTS:
        comps.append(component_entry(ref,val,f"RevA_{size}_2Pad",(x,y),rot,[ev,"LEAVES_HIGH_SPEED_ROUTING_XREF"],"MEDIUM","NETTED_AC_COUPLING","REFERENCE_PNP_POSITION_TRANSLATED",["VALUES_TO_BE_RECONFIRMED_AGAINST_REV_A_SI_REQUIREMENTS"]))
    for ref,val,x,y,rot,n1,n2 in SPI_ISO:
        comps.append(component_entry(ref,"SPI removable isolation","RevA_0402_2Pad",(x,y),rot,["ENGINEERING_ASSUMPTION","SERVICE_CONTRACT_REQUIREMENT"],"MEDIUM","NETTED_CONTROLLER_TO_FLASH_SIDE","PLACED_FOR_ACCESS",["FINAL_COMPONENT_STYLE_AND_SERVICE_CONNECTOR_SELECTION_PENDING"]))
    comps.append(component_entry("J3","Service/debug","RevA_Service_2x5_1.27_PROVISIONAL",(4.019917,4.1402),0,["ENGINEERING_ASSUMPTION","SERVICE_CONTRACT"],"MEDIUM","SPI_IS_FLASH_SIDE;UART_RST_NETTED;VREF_PENDING","PROVISIONAL_ACCESSIBLE_PLACEMENT",["VREF_SENSE_SOURCE_NOT_BOUND","CONNECTOR_FORM_FACTOR_SELECTION_PENDING"]))
    for ref,val,x,y,rot,n1,n2,size,layer,ev in SUPPORT_PASSIVES:
        comps.append(component_entry(ref,val,f"RevA_{size}_2Pad",(x,y),rot,[ev],"MEDIUM" if "REFERENCE" in ev or "AUTHORITATIVE" in ev else "LOW",f"NETTED:{n1}<->{n2}","PLACEMENT_STUDY",["FULL_POWER_RAIL_POPULATION_INCOMPLETE"] if ref.startswith("CDEC") else []))
    comps.append(component_entry("X1","25MHz crystal","RevA_Crystal_4P_1.6x1.2",(x1x,x1y),90,["REFERENCE_SOURCE_NATIVE","AUTHORITATIVE_ASM2464PD_CRYSTAL_REQUIREMENTS"],"MEDIUM","XI_XO_AND_GND_PINS_NETTED","REFERENCE_PNP_POSITION_TRANSLATED",["EXACT_CRYSTAL_PART_NUMBER_AND_LOAD_CAPACITANCE_VALIDATION_PENDING"]))
    for i,y in enumerate([24.2,25.0,25.8,26.6],1):
        comps.append(component_entry(f"DUSB4{i}","USB4 ESD function placeholder","ESD_2CH_SELECTION_PENDING",(6.0,y),0,["ENGINEERING_ASSUMPTION_REQUIREMENT"],"LOW","SELECTION_PENDING_NOT_SERIES_ROUTED","FUNCTIONAL_PLACEMENT_STUDY",["EXACT_LOW_CAPACITANCE_ESD_PART_AND_LAND_PATTERN_PENDING"]))
    comps.append(component_entry("DUSB2","USB2 ESD function placeholder","ESD_2CH_SELECTION_PENDING",(8.0,27.0),0,["ENGINEERING_ASSUMPTION_REQUIREMENT"],"LOW","SELECTION_PENDING_NOT_SERIES_ROUTED","FUNCTIONAL_PLACEMENT_STUDY",["EXACT_ESD_PART_PENDING"]))
    hs={}
    for dom,pairs in {"USB4":[f"USB4_{d}{l}" for l in range(2) for d in ("UTX","URX")],"PCIe":[f"PCIE_{d}{l}" for l in range(4) for d in ("TX","RX")],"USB2":["USB2_D"]}.items():
        hs[dom]={"target_impedance_ohm":None,"actual_solved_geometry":None,"layer":None,"pairs":[] if dom!="USB2" else None,"p_length_mm":None,"n_length_mm":None,"p_n_geometric_delta_mm":None,"via_count":None,"reference_plane":"PENDING_STACKUP","routing_status":"LOGICAL_NETTED_UNROUTED","evidence_source":["AUTHORITATIVE_PINOUTS","REFERENCE_TOPOLOGY_WHERE_NOTED"],"fabrication_readiness":"BLOCKED_PENDING_STACKUP_AND_FIELD_SOLVER"}
        if dom!="USB2": hs[dom]["pairs"]=pairs
    return {
      "schema_version":2,
      "artifact_status":"STRUCTURAL_NETTED_NOT_FABRICATION_READY",
      "generated_from":"scripts/generate_rev_a_kicad.py",
      "evidence_hierarchy":["AUTHORITATIVE_ASM2464PD_DOCUMENTATION","AUTHORITATIVE_COMPONENT_DATASHEETS","PHYSICAL_REFERENCE_FAB_OUTPUTS","REFERENCE_SOURCE_NATIVE","CONVERTED_KICAD","VENDOR_FAMILY_GUIDES","ENGINEERING_INFERENCE","PROVISIONAL_ASSUMPTION"],
      "board":{"width_mm":BOARD_W,"height_mm":BOARD_H,"nominal_reference_thickness_mm":BOARD_T,"stackup_status":"UNSELECTED","field_solver_status":"NOT_RUN","production_routing_geometry":"NULL_BY_POLICY"},
      "components":comps,
      "high_speed_domains":hs,
      "service_contract":{"pins":{"1":"GND","2":"VREF_SENSE","3":"ASM_UART_TX","4":"ASM_UART_RX","5":"ASM_RST_N","6":"ASM_SPI_CS_N_FLASH","7":"ASM_SPI_CLK_FLASH","8":"ASM_SPI_DI_FLASH","9":"ASM_SPI_DO_FLASH","10":"GND"},"programmer_attachment":"FLASH_SIDE_OF_RISO1_RISO4","reset_grants_spi_bus_ownership":False,"asm_reset_tristate_behavior":"UNRESOLVED"},
      "first_cycle_blockers":["FABRICATION_STACKUP_UNSELECTED","FIELD_SOLVER_NOT_RUN","KICAD_PRODUCTION_DIFFPAIR_RULES_NOT_LOCKED","EXACT_LOTES_APCI0113_PRODUCT_DRAWING_LAND_PATTERN_NOT_INGESTED","COMPLETE_ASM_BGA_FAB_LAND_PATTERN_NOT_LOCKED","USB4_USB2_ESD_PART_SELECTION_PENDING","FULL_POWER_RAIL_SCHEMATIC_AND_DECOUPLING_POPULATION_INCOMPLETE","VREF_SENSE_BINDING_UNRESOLVED"]
    }

# ---------- review SVGs ----------
def esc(s): return s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
def svg_header(title): return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1100"><rect width="800" height="1100" fill="white"/><text x="30" y="40" font-family="monospace" font-size="24">{esc(title)}</text>'
def to_svg_xy(x,y): return 70+x/BOARD_W*650, 90+(BOARD_H-y)/BOARD_H*900

def review_svg(bottom=False):
    side="BOTTOM" if bottom else "TOP"; S=[svg_header(f"ASM2464 REV-A {side} — structural netted study")]
    x0,y0=to_svg_xy(0,BOARD_H); x1,y1=to_svg_xy(BOARD_W,0)
    S.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1-x0:.1f}" height="{y1-y0:.1f}" fill="#eef5ee" stroke="black" stroke-width="3"/>')
    comps=[("U2",*PLACEMENT["U2"][:2],10,10,"ASM2464PD"),("J1",*PLACEMENT["J1"][:2],9,8,"Molex USB-C"),("J2",*PLACEMENT["J2"][:2],21.6,4.4,"M.2 M-key"),("U31",*PLACEMENT["U31"][:2],2,3,"Flash")]
    for ref,x,y,w,h,label in comps:
        isbottom=ref in ("J2","U31")
        if isbottom != bottom: continue
        cx,cy=to_svg_xy(x,y); sw=w/BOARD_W*650; sh=h/BOARD_H*900
        S.append(f'<rect x="{cx-sw/2:.1f}" y="{cy-sh/2:.1f}" width="{sw:.1f}" height="{sh:.1f}" fill="none" stroke="black" stroke-width="2"/>')
        S.append(f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" font-family="monospace" font-size="14">{ref} {esc(label)}</text>')
    # show AC coupling footprints on their actual side
    for ref,val,x,y,rot,n1,n2,size,layer,ev in AC_PARTS:
        if (layer=="B.Cu") != bottom: continue
        cx,cy=to_svg_xy(x,y); S.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="none" stroke="black"/><text x="{cx+6:.1f}" y="{cy:.1f}" font-family="monospace" font-size="9">{ref}</text>')
    S.append('<text x="30" y="1040" font-family="monospace" font-size="14">No routed high-speed copper is represented. Ratsnest/connectivity only until stackup + solver lock.</text>')
    S.append('</svg>'); return "\n".join(S)

def review_iso_svg():
    S=[svg_header("ASM2464 REV-A 3D GEOMETRY STUDY — not KiCad STEP export")]
    # Isometric-ish board polygon.
    S.append('<polygon points="150,280 570,160 700,420 280,540" fill="#eef5ee" stroke="black" stroke-width="3"/>')
    def prism(x,y,w,h,z,label):
        # simple visual mapping from board coordinates into iso plane
        px=150+x/BOARD_W*420 + y/BOARD_H*130; py=280-x/BOARD_W*120 + y/BOARD_H*260
        sw=w/BOARD_W*420; sh=h/BOARD_H*260; dz=z*18
        return [f'<rect x="{px-sw/2:.1f}" y="{py-sh/2-dz:.1f}" width="{sw:.1f}" height="{sh:.1f}" fill="none" stroke="black" stroke-width="2"/>',f'<text x="{px:.1f}" y="{py-dz:.1f}" text-anchor="middle" font-family="monospace" font-size="13">{esc(label)}</text>']
    for args in [(13.667,14.870,10,10,0.982,"ASM 10x10 x0.982 max"),(11.07,29.187,8.94,7.9,3.3,"Molex 105450-0101"),(11.024,4.698,21.6,4.4,4.75,"LOTES M.2 H4.75"),(20.93,20.277,2,3,0.55,"U31 USON")]: S+=prism(*args)
    S.append('<text x="30" y="1025" font-family="monospace" font-size="14">Geometry review only. Thermal pad compression, connector tail details, and final enclosure interference remain validation blockers.</text>')
    S.append('</svg>'); return "\n".join(S)

# ---------- validation ----------
def balanced_sexpr(text):
    depth=0; instr=False; escp=False
    for ch in text:
        if instr:
            if escp: escp=False
            elif ch=='\\': escp=True
            elif ch=='"': instr=False
        else:
            if ch=='"': instr=True
            elif ch=='(': depth+=1
            elif ch==')': depth-=1
            if depth<0:return False
    return depth==0 and not instr

def validate_outputs(board,sch,audit):
    assert balanced_sexpr(board) and balanced_sexpr(sch)
    assert board.count('(pad "') >= 273+24+67+8
    for ref in ("U2","J1","J2","U31","J3","RISO1","RISO2","RISO3","RISO4"):
        assert f'"{ref}"' in board
    for n in ("USB4_UTX0_P_ASM","PCIE_TX3_N_M2","USB2_DP","ASM_SPI_CS_N_FLASH"):
        assert n in board and n in sch
    assert '(segment ' not in board and '(arc ' not in board and '(via ' not in board
    assert '0.08128' not in board and '0.110744' not in board
    assert audit['board']['production_routing_geometry']=='NULL_BY_POLICY'
    assert audit['service_contract']['programmer_attachment']=='FLASH_SIDE_OF_RISO1_RISO4'
    assert len(BALL_NET)==273
    assert sch.count('(symbol (lib_id') >= 30
    for lib in ('RevA:ASM2464PD_REQUIRED','RevA:Molex_105450_0101','RevA:M2_MKEY_PCIE_X4','RevA:ZD25WQ16CEIGR','RevA:SERVICE_2X5'):
        assert f'(lib_id "{lib}")' in sch
    audited={c['refdes'] for c in audit['components']}
    for ref in [x[0] for x in SUPPORT_PASSIVES] + ['X1']:
        assert ref in audited


def main():
    KDIR.mkdir(parents=True,exist_ok=True); FPDIR.mkdir(parents=True,exist_ok=True); REVIEW.mkdir(parents=True,exist_ok=True)
    board=build_board(); sch=build_schematic(); audit=build_audit()
    validate_outputs(board,sch,audit)
    # Local footprint table keeps schematic footprint properties resolvable.
    (KDIR/'fp-lib-table').write_text('(fp_lib_table\n  (version 7)\n  (lib (name "rev-a-footprints")(type "KiCad")(uri "${KIPRJMOD}/rev-a-footprints.pretty")(options "")(descr "Rev-A local structural footprints"))\n)\n',encoding='utf-8')
    (FPDIR/'RevA_0201_2Pad.kicad_mod').write_text(passive_fp('REF**','VALUE',0,0,0,None,None,'0201','F.Cu'),encoding='utf-8')
    (FPDIR/'RevA_0402_2Pad.kicad_mod').write_text(passive_fp('REF**','VALUE',0,0,0,None,None,'0402','F.Cu'),encoding='utf-8')
    (FPDIR/'ASM2464PD_BGA273_10x10_P0.46.kicad_mod').write_text(bga_footprint(),encoding='utf-8')
    (FPDIR/'Molex_105450-0101.kicad_mod').write_text(usb_c_footprint(),encoding='utf-8')
    (FPDIR/'LOTES_APCI0113-P001A_MKey_RECONSTRUCTED.kicad_mod').write_text(m2_footprint(),encoding='utf-8')
    (FPDIR/'ZD25WQ16CEIGR_USON8_3x2_P0.5_RECONSTRUCTED.kicad_mod').write_text(uson8_footprint(),encoding='utf-8')
    (KDIR/'rev-a.kicad_pcb').write_text(board,encoding='utf-8')
    (KDIR/'rev-a.kicad_sch').write_text(sch,encoding='utf-8')
    (ROOT/'hardware/rev-a/design-audit.json').write_text(json.dumps(audit,indent=2)+"\n",encoding='utf-8')
    (REVIEW/'pcb-top.svg').write_text(review_svg(False),encoding='utf-8')
    (REVIEW/'pcb-bottom.svg').write_text(review_svg(True),encoding='utf-8')
    (REVIEW/'pcb-3d-geometry-study.svg').write_text(review_iso_svg(),encoding='utf-8')
    print(json.dumps({"status":"ok","bga_ball_count":len(BALL_NET),"net_count":len(NET_ID),"board_chars":len(board),"schematic_chars":len(sch)},indent=2))

if __name__=='__main__': main()
