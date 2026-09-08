"""
ASM2464PD Hardware Emulation

This module provides realistic hardware emulation for the ASM2464PD.
Only hardware registers (XDATA >= 0x6000) are emulated here.
RAM (XDATA < 0x6000) is handled by the memory system, not this module.
"""

from typing import TYPE_CHECKING, Dict, Set, Callable, Optional
from dataclasses import dataclass, field
from enum import IntEnum
import re
import os
import hashlib
import struct

from pyrite import PyriteDevice

if TYPE_CHECKING:
    from memory import Memory


# Physical SPI NOR geometry confirmed by two independent full-device dumps.
SPI_FLASH_SIZE = 0x80000
SPI_FLASH_ERASED_BYTE = 0xFF


def validate_stock_c24c_bridge_trace(events, config, link_param=0,
                                     initial_lane_mask=0,
                                     restore_b402=False):
    """Validate the ordered controller-visible core of stock C24C/CC83.

    ``events`` contains ``(bank, address, old, new)`` tuples.  Bank zero is
    normal MMIO and bank one is the DPX switch/PHY plane.  ``config`` is the
    four-byte SPI configuration slice at offsets 0x74..0x77, ordered exactly
    as stock 8C5F-8C86 loads it: vendor high/low, device high/low.

    This intentionally stops at C7A4's B436 lane configuration.  C24C's later
    3A2B call initializes stock-private queue tables. Its controller-visible
    history has a separate strict observer; the overlapping low-XDATA tables
    are not pretended to belong to the handmade firmware. Passing this
    validator therefore proves ordered bridge reconstruction only; it does
    not prove that silicon will generate C802.2/C520.1 after a 0206 descriptor.
    """
    if len(config) != 4:
        return False
    vendor_hi, vendor_lo, device_hi, device_lo = config

    # Each entry is bank, address, and either a direct value or an RMW mask.
    # RMW entries are (and_mask, or_value).  Keeping two writes to the same
    # register as two entries is deliberate: final-state equivalence is not
    # accepted for this undocumented bridge transaction.
    expected = [
        (0, 0xB401, (0xFE, 0x01)),
        (0, 0xB401, (0xFE, 0x00)),
        (0, 0xCA06, (0xEF, 0x00)),
        (0, 0xB410, vendor_lo), (0, 0xB411, vendor_hi),
        (0, 0xB420, vendor_lo), (0, 0xB421, vendor_hi),
        (0, 0xB412, device_lo), (0, 0xB413, device_hi),
        (0, 0xB422, device_lo), (0, 0xB423, device_hi),
        (0, 0xB415, 0x06), (0, 0xB416, 0x04), (0, 0xB417, 0x00),
        (0, 0xB425, 0x06), (0, 0xB426, 0x04), (0, 0xB427, 0x00),
        (0, 0xB41A, vendor_lo), (0, 0xB41B, vendor_hi),
        (0, 0xB42A, vendor_lo), (0, 0xB42B, vendor_hi),
        (0, 0xB418, device_lo), (0, 0xB419, device_hi),
        (0, 0xB428, device_lo), (0, 0xB429, device_hi),
        (1, 0x4084, 0x22), (1, 0x5084, 0x22),
        (0, 0xB401, (0xFE, 0x01)),
        (0, 0xB482, (0xFE, 0x01)),
        (0, 0xB482, (0x0F, 0xF0)),
        (0, 0xB401, (0xFE, 0x00)),
        (0, 0xB480, (0xFE, 0x01)),
        (0, 0xB430, (0xFE, 0x00)),
        (0, 0xB298, (0xEF, 0x10)),
        (1, 0x6043, 0x70), (1, 0x6025, (0x7F, 0x80)),
        (0, 0xCA06, (0xEF, 0x00)),
        (0, 0xB480, (0xFE, 0x01)),
        (0, 0xC659, (0xFE, 0x00)),
        (0, 0xB402, (0xFD, 0x00)),
    ]
    # BEA0 retains B434's upper nibble and progressively ORs masks 1,2,4,8
    # until all requested lanes are enabled. It can therefore perform zero to
    # four writes depending on the state inherited from the link path.
    initial_lane = initial_lane_mask & 0x0F
    lane = initial_lane
    for bit in (0x01, 0x02, 0x04, 0x08):
        if lane == 0x0F:
            break
        lane |= bit
        expected.append((0, 0xB434, (0xF0, lane)))
    if restore_b402:
        expected.append((0, 0xB402, (0xFD, 0x02)))
    expected.extend((
        (0, 0xB436, (0xF0, 0x0E)),
        (0, 0xB436, (0x0F, ((~link_param) & 0x0F) << 4)),
    ))
    if len(events) != len(expected):
        return False
    register_state = {}
    for event, requirement in zip(events, expected):
        bank, address, old, new = event
        expected_bank, expected_address, operation = requirement
        if bank != expected_bank or address != expected_address:
            return False
        key = (bank, address)
        if key in register_state and old != register_state[key]:
            return False
        register_state[key] = new
        if isinstance(operation, tuple):
            and_mask, or_value = operation
            wanted = (old & and_mask) | or_value
        else:
            wanted = operation
        if new != wanted:
            return False
    b402_clear = events[39]
    if bool(b402_clear[2] & 0x02) != bool(restore_b402):
        return False
    lane_events = [event for event in events if event[0:2] == (0, 0xB434)]
    if lane_events and (lane_events[0][2] & 0x0F) != initial_lane:
        return False
    return True


def validate_stock_cc83_bridge_trace(events, config):
    """Validate stock CC83 without C24C's caller prefix or lane tail."""
    if len(config) != 4:
        return False
    vendor_hi, vendor_lo, device_hi, device_lo = config
    expected = [
        (0, 0xCA06, (0xEF, 0x00)),
        (0, 0xB410, vendor_lo), (0, 0xB411, vendor_hi),
        (0, 0xB420, vendor_lo), (0, 0xB421, vendor_hi),
        (0, 0xB412, device_lo), (0, 0xB413, device_hi),
        (0, 0xB422, device_lo), (0, 0xB423, device_hi),
        (0, 0xB415, 0x06), (0, 0xB416, 0x04), (0, 0xB417, 0x00),
        (0, 0xB425, 0x06), (0, 0xB426, 0x04), (0, 0xB427, 0x00),
        (0, 0xB41A, vendor_lo), (0, 0xB41B, vendor_hi),
        (0, 0xB42A, vendor_lo), (0, 0xB42B, vendor_hi),
        (0, 0xB418, device_lo), (0, 0xB419, device_hi),
        (0, 0xB428, device_lo), (0, 0xB429, device_hi),
        (1, 0x4084, 0x22), (1, 0x5084, 0x22),
        (0, 0xB401, (0xFE, 0x01)),
        (0, 0xB482, (0xFE, 0x01)),
        (0, 0xB482, (0x0F, 0xF0)),
        (0, 0xB401, (0xFE, 0x00)),
        (0, 0xB480, (0xFE, 0x01)),
        (0, 0xB430, (0xFE, 0x00)),
        (0, 0xB298, (0xEF, 0x10)),
        (1, 0x6043, 0x70), (1, 0x6025, (0x7F, 0x80)),
    ]
    if len(events) != len(expected):
        return False
    register_state = {}
    for event, requirement in zip(events, expected):
        bank, address, old, new = event
        expected_bank, expected_address, operation = requirement
        if (bank, address) != (expected_bank, expected_address):
            return False
        key = (bank, address)
        if key in register_state and old != register_state[key]:
            return False
        register_state[key] = new
        wanted = ((old & operation[0]) | operation[1]
                  if isinstance(operation, tuple) else operation)
        if new != wanted:
            return False
    return True

# =============================================================================
# Register Name Lookup
# =============================================================================
# Parse registers.h to build addr -> name lookup table

_REGISTER_NAMES: Dict[int, str] = {}

def _load_register_names():
    """Parse registers.h and build address-to-name lookup table."""
    global _REGISTER_NAMES
    if _REGISTER_NAMES:
        return  # Already loaded

    # Find registers.h relative to this file
    this_dir = os.path.dirname(os.path.abspath(__file__))
    registers_h = os.path.join(this_dir, '..', 'src', 'include', 'registers.h')

    if not os.path.exists(registers_h):
        return

    # Pattern: #define REG_NAME  XDATA_REG8(0xADDR) or similar
    pattern = re.compile(r'#define\s+(REG_\w+)\s+XDATA_REG\d+\((0x[0-9A-Fa-f]+)\)')

    with open(registers_h, 'r') as f:
        for line in f:
            match = pattern.search(line)
            if match:
                name = match.group(1)
                addr = int(match.group(2), 16)
                _REGISTER_NAMES[addr] = name

def get_register_name(addr: int) -> str:
    """Get register name for address, or empty string if unknown."""
    _load_register_names()
    return _REGISTER_NAMES.get(addr, "")


class USBState(IntEnum):
    """
    USB state machine states.

    Matches firmware I_USB_STATE (IDATA[0x6A]) and USB_STATE_* constants in globals.h.
    State transitions occur in ISR at 0x0E68 and main loop at 0x202A.
    """
    DISCONNECTED = 0  # USB_STATE_DISCONNECTED - No USB connection
    ATTACHED = 1      # USB_STATE_ATTACHED - Cable connected
    POWERED = 2       # USB_STATE_POWERED - Bus powered
    DEFAULT = 3       # USB_STATE_DEFAULT - Default address assigned
    ADDRESS = 4       # USB_STATE_ADDRESS - Device address assigned
    CONFIGURED = 5    # USB_STATE_CONFIGURED - Ready for vendor commands


@dataclass
class USBCommand:
    """USB command queued for firmware processing."""
    cmd: int           # Command type (0xE4=read, 0xE5=write, 0x8A=scsi)
    addr: int          # Target XDATA address
    data: bytes        # Data for write commands
    response: bytes = b''  # Response data for read commands


class USBController:
    """
    USB controller emulation using only MMIO registers.

    This class manages the USB state machine and vendor command injection
    without directly modifying RAM. All state transitions are driven by
    setting MMIO registers that cause the firmware to naturally progress
    through its USB state machine.

    The firmware's USB state machine:
    - I_USB_STATE (IDATA[0x6A]) contains current USB state (0-5)
    - State 5 = USB_STATE_CONFIGURED, ready for vendor commands
    - Firmware transitions states by reading MMIO and updating its own RAM

    Key MMIO registers for USB (see registers.h for full definitions):
    - REG_USB_STATUS (0x9000): Connection status (bit 7=connected, bit 0=active)
    - REG_USB_PERIPH_STATUS (0x9101): Interrupt flags (bit 5 triggers cmd handler)
    - REG_USB_CTRL_PHASE (0x9091): Control transfer phase (bit 0=setup, bit 1=data)
    - REG_USB_DMA_TRIGGER (0x9092): Trigger DMA for descriptor transfer
    - REG_USB_DMA_STATE (0xCE89): DMA state machine (bits control transitions)
    - REG_USB_SETUP_* (0x9E00-0x9E07): USB setup packet buffer
    - REG_USB_EP0_COMPLETE (0xE712): EP0 transfer complete status

    Key globals for USB (see globals.h):
    - G_CMD_SLOT_INDEX (0x05A3): Current command slot index
    - G_CMD_TABLE_BASE (0x05B1): Command table (10 x 34-byte entries)
    - G_USB_CMD_CONFIG (0x07EC): USB command configuration
    - G_DMA_XFER_STATUS (0x0AA0): DMA transfer status
    """

    def __init__(self, hw: 'HardwareState'):
        self.hw = hw
        self.state = USBState.DISCONNECTED
        self.pending_cmd: Optional[USBCommand] = None
        self.enumeration_complete = False
        self.vendor_cmd_active = False

        # Track state machine progress
        self.state_machine_reads = 0
        self.enumeration_step = 0

        # Pending descriptor request from GET_DESCRIPTOR
        self.pending_descriptor_request = None

    def connect(self, speed: int = 2):
        """
        Simulate USB cable connection via MMIO registers.

        This sets the initial MMIO state that triggers USB enumeration
        in the firmware. The firmware will progress through states 0→5.

        Args:
            speed: USB speed mode:
                0 = Full Speed (USB 1.x, 12 Mbps)
                1 = High Speed (USB 2.0, 480 Mbps)
                2 = SuperSpeed (USB 3.x, 5 Gbps)
                3 = SuperSpeed+ (USB 3.1+, 10+ Gbps)
        """
        self.state = USBState.ATTACHED
        self.enumeration_step = 1
        self.usb_speed = speed

        # USB connection status registers
        # NOTE: 0x9000 bit 0 must be SET to enter USB state machine at 0x0E6E
        # At ISR 0x0E68: if bit 0 SET, jump to USB handling at 0x0E6E
        self.hw.regs[0x9000] = 0x81  # Bit 7 (connected), bit 0 SET for USB handling
        self.hw.regs[0x90E0] = speed  # USB speed
        self.hw.regs[0x9100] = speed  # USB link active with speed
        self.hw.regs[0x9105] = 0xFF  # PHY active
        # USB state indicator (0x9118):
        # At ISR 0x0E71, value is used as index into table at 0x5AC9
        # If table[0x9118] >= 8, USB handling is skipped
        # table[0] = 0x08 (skip), table[1] = 0x00 (continue)
        # Set to 1 to enable USB enumeration handling
        self.hw.regs[0x9118] = 0x01  # USB enumeration state (1 = pending setup)

        # USB interrupt and state machine triggers:
        # At 0x0FEB: if 0x9101 bit 6 CLEAR, skip USB init path
        # At 0x0FF2: if 0x90E2 bit 0 CLEAR, skip USB init path
        # So both bit 6 of 0x9101 and bit 0 of 0x90E2 must be SET
        self.hw.regs[0xC802] = 0x05  # USB interrupt pending (bits 0 + 2)
        self.hw.regs[0x9101] = 0x61  # Bit 6 SET (USB init), bit 5 SET, bit 0 for USB active
        self.hw.regs[0x90E2] = 0x03  # Bit 0 SET (USB init trigger), bit 1 SET

        # USB restart trigger at 0xCC5D:
        # At 0x2163-0x216B: if bit 0 CLEAR and bit 1 SET, calls USB restart at 0x2176
        # This sets 0x0A5A=1 which enables the USB init path at 0x2185
        self.hw.regs[0xCC5D] = 0x02  # Bit 1 SET, bit 0 CLEAR - triggers USB restart

        # USB PHY control at 0x91C0:
        # Firmware clears this during init, but at 0x203B it checks bit 1.
        # When state 0x0A59 == 2, if 0x92C2 bit 6 is SET and 0x91C0 bit 1 is SET,
        # firmware calls 0x0322 which progresses the USB state machine.
        self.hw.regs[0x91C0] = 0x02  # Bit 1 SET - enables USB state machine progress

        # USB mode indicators for descriptor handling at 0xA7E5 and 0x87A1:
        # At 0xA7E4-0xA7E5: checks 0xCC91 bit 1 for USB3 mode
        # At 0xA7FD-0xA7FF: checks 0x09F9 bit 6 for USB3 speed
        # If both set, 0x0ACC gets value with bit 1 SET, enabling USB3 descriptor path
        # For USB 2.0: clear these bits so firmware takes USB2 path
        if speed >= 2:  # SuperSpeed or higher
            self.hw.regs[0xCC91] = 0x02  # Bit 1 SET - USB3 mode
            self.hw.regs[0x09F9] = 0x40  # Bit 6 SET - USB3 speed indicator
        else:  # High Speed or Full Speed (USB 2.0)
            self.hw.regs[0xCC91] = 0x00  # Bit 1 CLEAR - USB2 mode
            self.hw.regs[0x09F9] = 0x00  # Bit 6 CLEAR - USB2 speed indicator

        # PCIe enumeration state - simulate that PCIe link is already up
        # In real hardware, PCIe enumeration happens during boot before USB control
        # transfers. The firmware checks this state at 0x185C before taking the
        # descriptor DMA path (0x1865+). Without it, firmware takes alternate path.
        #
        # Flow: 0x3803 checks XDATA[0x053F] or XDATA[0x0553] != 0
        #       If true, sets XDATA[0x0AF7] = 1 at 0x3914-0x3919
        #       This enables the "good" path at 0x185C that uses descriptor DMA
        #
        # CRITICAL: 0xB480 bit 0 must be SET to prevent firmware at 0x20DA from
        # taking the path at 0x20F9 that clears XDATA[0x0AF7] to 0.
        # At 0x20DA: jnb acc.0, 0x20fe -> if bit 0 CLEAR, jump and clear 0x0AF7
        self.hw.regs[0xB480] = 0x03  # Bits 0,1 SET - PCIe link active state

        # Set these to simulate completed PCIe enumeration:
        if self.hw.memory:
            self.hw.memory.xdata[0x0AF7] = 0x01  # PCIe enumeration complete flag
            self.hw.memory.xdata[0x053F] = 0x01  # PCIe link state (port 0)
            # CRITICAL: Command table state at G_CMD_TABLE_BASE + index*0x22 must NOT be 4
            # At 0x35D4-0x35DF: Firmware calls 0x1551 which reads G_CMD_SLOT_INDEX (0x05A3),
            # then calculates G_CMD_TABLE_BASE[index] and if that value equals 4,
            # it calls 0x54BB which clears XDATA[0x0AF7] to 0.
            # Set slot 0 state to 3 (ready) instead of 4 (error/reset).
            self.hw.memory.xdata[0x05A3] = 0x00  # G_CMD_SLOT_INDEX = 0
            self.hw.memory.xdata[0x05B1] = 0x03  # G_CMD_TABLE_BASE[0] = 3 (ready)

        print(f"[{self.hw.cycles:8d}] [USB_CTRL] Connected - MMIO set for enumeration")

    def inject_bot_cbw(self, cdb: bytes, *, tag: int = 1, transfer_length: int = 0,
                       direction_in: bool = False, lun: int = 0,
                       signature: bytes = b'USBC', cbw_length: int = 31,
                       flags: Optional[int] = None,
                       cdb_length: Optional[int] = None):
        """Inject a BOT CBW using the controller's parsed 0x911B layout."""
        if not self.hw.usb_bulk_engine_active:
            raise RuntimeError("BOT endpoint engine is not active")
        if not self.hw.usb_cbw_armed:
            raise RuntimeError("BOT CBW parser is not armed")
        if len(signature) != 4:
            raise ValueError("BOT signature must contain exactly four bytes")
        if not 1 <= len(cdb) <= 16:
            raise ValueError("BOT CDB length must be between 1 and 16 bytes")
        if not 0 <= transfer_length <= 0xFFFFFFFF:
            raise ValueError("BOT transfer length is outside uint32 range")

        regs = self.hw.regs
        regs[0x9119] = (cbw_length >> 8) & 0xFF
        regs[0x911A] = cbw_length & 0xFF
        for i, value in enumerate(signature):
            regs[0x911B + i] = value
        for i in range(4):
            regs[0x911F + i] = (tag >> (8 * i)) & 0xFF
            regs[0x9123 + i] = (transfer_length >> (8 * i)) & 0xFF
        regs[0x9127] = flags if flags is not None else (0x80 if direction_in else 0x00)
        regs[0x9128] = lun & 0xFF
        regs[0x9129] = len(cdb) if cdb_length is None else cdb_length
        for i in range(16):
            regs[0x912A + i] = cdb[i] if i < len(cdb) else 0

        self.hw.usb_stock_cbw_dma_trace = []
        self.hw.usb_stock_cbw_dma_ready = False
        self.hw.usb_stock_cbw_tag_trace = []
        self.hw.usb_stock_cbw_tag_ready = False
        self.hw.usb_stock_bulk_in_arm_trace = []
        self.hw.usb_stock_bulk_in_arm_ready = False
        self.hw.usb_stock_bulk_in_event_ready = False
        self.hw.usb_stock_bot_alt0_service_trace = []
        self.hw.usb_cbw_armed = False
        regs[0xC802] = 0x01
        regs[0x9101] = 0x40
        self.hw._pending_usb_interrupt = True

    def complete_msc_in(self, endpoint: int = 0x81):
        """Signal completion of the last firmware-originated MSC IN transfer."""
        self.hw.regs[0x9118] = endpoint
        # Stock's BOT-alt-zero 0EF4 route requires 9096.0 in addition to
        # C802.0/9101.5. This is a modeled host-completion event, not endpoint
        # state synthesized from descriptors.
        self.hw.regs[0x9096] = self.hw.regs.get(0x9096, 0) | 0x01
        self.hw.usb_ep_ready_ack_pending = True
        self.hw.regs[0xC802] = 0x01
        self.hw.regs[0x9101] = 0x20
        self.hw._pending_usb_interrupt = True

    def inject_bot_data_out(self, data: bytes, *, buffer_offset: int = 0):
        """Deliver one BOT OUT completion into the hardware's XDATA buffer.

        This is one controller completion, not necessarily one complete BOT data
        phase.  Physical SuperSpeed hardware completed a 4096-byte host write
        after its first 1024-byte packet.  Tests that pass 4096 bytes here are
        deliberately exercising idealized state-machine logic, not the observed
        endpoint packetization.
        """
        if len(data) > 0x1000:
            raise ValueError("BOT data-out exceeds the 4 KiB hardware buffer")
        if not 0 <= buffer_offset <= 0x1000 - len(data):
            raise ValueError("BOT data-out buffer range exceeds the 4 KiB hardware buffer")
        if not self.hw.memory:
            raise RuntimeError("BOT data-out requires attached XDATA memory")
        if self.hw.usb_sw_dma_out_destination is not None:
            start = self.hw.usb_sw_dma_out_destination
            self.hw.usb_sw_dma_out_destination = None
            if buffer_offset:
                raise ValueError(
                    "buffer_offset cannot override firmware-selected SW DMA")
        else:
            start = 0x7000 + buffer_offset
        if start + len(data) > len(self.hw.memory.xdata):
            raise ValueError("BOT data-out DMA range exceeds XDATA")
        self.hw.memory.xdata[start:start + len(data)] = data
        self.hw.regs[0x910D] = (len(data) >> 8) & 0xFF
        self.hw.regs[0x910E] = len(data) & 0xFF
        self.hw.regs[0x9093] = 0x02
        self.hw.regs[0xC802] = 0x01
        self.hw.regs[0x9101] = 0x04
        self.hw._pending_usb_interrupt = True

    def advance_enumeration(self):
        """
        Advance USB enumeration state via MMIO.

        Called when firmware polls 0xCE89 to check enumeration progress.
        Each call advances the emulated enumeration sequence.
        """
        self.state_machine_reads += 1

        # Return value for 0xCE89 based on enumeration progress
        value = 0x00

        if self.state_machine_reads >= 3:
            value |= 0x01  # Bit 0 - exit wait loop at 0x348C
            self.enumeration_step = max(self.enumeration_step, 2)

        if self.state_machine_reads >= 5:
            value |= 0x02  # Bit 1 - successful enumeration path at 0x3493
            self.enumeration_step = max(self.enumeration_step, 3)

        if self.state_machine_reads >= 7:
            value |= 0x04  # Bit 2 - state 3→4→5 transitions
            self.enumeration_step = max(self.enumeration_step, 4)
            self.enumeration_complete = True
            self.state = USBState.CONFIGURED

        return value

    def inject_vendor_command(self, cmd_type: int, xdata_addr: int,
                               value: int = 0, size: int = 1):
        """
        Inject a USB vendor command via MMIO registers.

        This sets up the MMIO registers needed for the firmware to process
        a vendor command. The firmware reads these registers and handles
        the command through its normal code path.

        No direct RAM writes are performed - the firmware reads expected
        values through MMIO hooks that simulate hardware behavior.

        Args:
            cmd_type: 0xE4 (read) or 0xE5 (write)
            xdata_addr: Target XDATA address
            value: Value for write commands
            size: Size for read commands
        """
        # Build USB address format: (addr & 0x1FFFF) | 0x500000
        usb_addr = (xdata_addr & 0x1FFFF) | 0x500000

        # Build 6-byte CDB (Command Descriptor Block)
        cdb = bytes([
            cmd_type,
            size if cmd_type == 0xE4 else value,
            (usb_addr >> 16) & 0xFF,
            (usb_addr >> 8) & 0xFF,
            usb_addr & 0xFF,
            0x00
        ])

        print(f"[{self.hw.cycles:8d}] [USB_CTRL] === INJECT VENDOR COMMAND ===")
        print(f"[{self.hw.cycles:8d}] [USB_CTRL] cmd=0x{cmd_type:02X} addr=0x{xdata_addr:04X} "
              f"{'size' if cmd_type == 0xE4 else 'val'}=0x{cdb[1]:02X}")
        print(f"[{self.hw.cycles:8d}] [USB_CTRL] CDB: {cdb.hex()}")

        # =====================================================
        # MMIO REGISTER SETUP FOR VENDOR COMMAND
        # =====================================================

        # Write CDB to USB interface registers (0x910D-0x9112)
        # Firmware reads these at 0x31C0+ to get command data
        for i, b in enumerate(cdb):
            self.hw.regs[0x910D + i] = b

        # Also populate 0x911F-0x9122 (another CDB location read by 0x3186)
        for i, b in enumerate(cdb[:4]):
            self.hw.regs[0x911F + i] = b

        # USB endpoint buffers
        for i, b in enumerate(cdb):
            self.hw.usb_ep_data_buf[i] = b
            self.hw.usb_ep0_buf[i] = b
        self.hw.usb_ep0_len = len(cdb)

        # USB connection and interrupt status
        # NOTE: 0x9000 bit 0 must be CLEAR to reach the 0x5333 vendor handler path
        # At 0x0E68, JB 0xe0.0 jumps away if bit 0 is set
        self.hw.regs[0x9000] = 0x80  # Connected (bit 7), bit 0 CLEAR for vendor path
        self.hw.regs[0x9101] = 0x21  # Bit 5 triggers command handler path
        self.hw.regs[0xC802] = 0x05  # USB interrupt pending

        # USB endpoint status - signals data available
        self.hw.regs[0x9096] = 0x01  # EP0 has data
        self.hw.regs[0x90E2] = 0x01  # Endpoint status bit

        # USB command interface registers
        self.hw.regs[0xE4E0] = cdb[0]  # Command type (0xE4/0xE5)
        self.hw.regs[0xE091] = size    # Read size / write value

        # Original firmware E5 path reads these (0x17FD-0x188B)
        # 0xC47A: Value byte copied to IDATA[0x38] at 0x1801
        # 0xCEB0: Command type copied to IDATA[0x39] at 0x188B
        self.hw.regs[0xC47A] = value if cmd_type == 0xE5 else size
        self.hw.regs[0xCEB0] = 0x05 if cmd_type == 0xE5 else 0x04

        # Target address registers (read at 0x323A-0x3249)
        # CEB2 = high byte of XDATA address
        # CEB3 = low byte of XDATA address
        self.hw.regs[0xCEB2] = (xdata_addr >> 8) & 0xFF
        self.hw.regs[0xCEB3] = xdata_addr & 0xFF

        # Store E5 value separately so it survives firmware clearing 0xC47A
        if cmd_type == 0xE5:
            self.hw.usb_e5_pending_value = value

        # USB EP0 data registers (read by various helpers)
        self.hw.regs[0x9E00] = cdb[0]  # bmRequestType / cmd type
        self.hw.regs[0x9E01] = cdb[1]  # bRequest / size
        self.hw.regs[0x9E02] = cdb[4]  # wValue low / addr low
        self.hw.regs[0x9E03] = cdb[3]  # wValue high / addr mid
        self.hw.regs[0x9E04] = cdb[2]  # wIndex low / addr high
        self.hw.regs[0x9E05] = 0x00    # wIndex high
        self.hw.regs[0x9E06] = size    # wLength low
        self.hw.regs[0x9E07] = 0x00    # wLength high

        # PCIe/DMA status for command processing
        self.hw.regs[0xC47B] = 0x01  # Non-zero for checks
        self.hw.regs[0xC471] = 0x01  # Queue busy
        self.hw.regs[0xB432] = 0x07  # PCIe link status
        self.hw.regs[0xE765] = 0x02  # Ready flag

        # Store command state
        self.hw.usb_cmd_type = cmd_type
        self.hw.usb_cmd_size = size if cmd_type == 0xE4 else 0
        self.hw.usb_cmd_pending = True
        self.vendor_cmd_active = True

        # Reset E5 DMA tracking flag for new command
        self.hw._e5_dma_done = False

        # Reset state machine for fresh command processing
        self.hw.usb_ce89_read_count = 0

        print(f"[{self.hw.cycles:8d}] [USB_CTRL] MMIO registers configured")

        # =====================================================
        # USB Hardware DMA - populate RAM like real hardware
        # =====================================================
        # The USB controller populates these RAM locations via DMA
        # before triggering the interrupt. This is how real hardware works.
        if self.hw.memory:
            # USB state = 5 (configured) - set by USB enumeration
            self.hw.memory.idata[0x6A] = 5

            # USB config check at 0x35C0 - must be 0 for vendor path
            self.hw.memory.xdata[0x07EC] = 0x00

            # CDB area - USB hardware writes CDB to XDATA[0x0002+]
            # The SCSI handler at 0x32E4 reads CDB from this area
            for i, b in enumerate(cdb):
                self.hw.memory.xdata[0x0002 + i] = b

            # Vendor command flag at 0x4583 - bit 3 enables vendor dispatch
            # This overlaps with CDB area but has special meaning
            self.hw.memory.xdata[0x0003] = 0x08

            # Command type marker for table lookup at 0x35D8
            if cmd_type == 0xE4:
                self.hw.memory.xdata[0x05B1] = 0x04
            elif cmd_type == 0xE5:
                self.hw.memory.xdata[0x05B1] = 0x05

            # Command index = 0 for table lookup at 0x1551
            # 0x17B1 copies 0x05A5 to 0x05A3, so set both to 0
            self.hw.memory.xdata[0x05A3] = 0x00
            self.hw.memory.xdata[0x05A5] = 0x00

        return cdb

    def inject_scsi_write_command(self, lba: int, sectors: int, data: bytes):
        """
        Inject a 0x8A SCSI write command via MMIO registers.

        This sets up the MMIO registers and RAM needed for the firmware to process
        a SCSI write command. The firmware reads these registers and handles
        the command through its normal code path.

        Args:
            lba: Logical Block Address to write to
            sectors: Number of sectors to write (each sector is 512 bytes)
            data: Data to write (will be padded to sector boundary)
        """
        import struct

        # Build 16-byte CDB for SCSI write command
        # Format: struct.pack('>BBQIBB', 0x8A, 0, lba, sectors, 0, 0)
        cdb = struct.pack('>BBQIBB', 0x8A, 0x00, lba, sectors, 0x00, 0x00)

        print(f"[{self.hw.cycles:8d}] [USB_CTRL] === INJECT SCSI WRITE COMMAND ===")
        print(f"[{self.hw.cycles:8d}] [USB_CTRL] LBA={lba} sectors={sectors} data_len={len(data)}")
        print(f"[{self.hw.cycles:8d}] [USB_CTRL] CDB: {cdb.hex()}")

        # =====================================================
        # MMIO REGISTER SETUP FOR SCSI COMMAND
        # =====================================================

        # Write CDB to USB interface registers (0x910D-0x911C)
        for i, b in enumerate(cdb):
            self.hw.regs[0x910D + i] = b

        # USB endpoint buffers - write CDB
        for i, b in enumerate(cdb):
            self.hw.usb_ep_data_buf[i] = b
            self.hw.usb_ep0_buf[i] = b
        self.hw.usb_ep0_len = len(cdb)

        # USB connection and interrupt status
        self.hw.regs[0x9000] = 0x80  # Connected (bit 7), bit 0 CLEAR
        self.hw.regs[0x9101] = 0x21  # Bit 5 triggers command handler path
        self.hw.regs[0xC802] = 0x05  # USB interrupt pending

        # USB endpoint status
        self.hw.regs[0x9096] = 0x01  # EP0 has data
        self.hw.regs[0x90E2] = 0x01  # Endpoint status bit

        # Store command state
        self.hw.usb_cmd_type = 0x8A
        self.hw.usb_cmd_size = sectors * 512
        self.hw.usb_cmd_pending = True
        self.vendor_cmd_active = True

        # Reset state machine
        self.hw.usb_ce89_read_count = 0

        print(f"[{self.hw.cycles:8d}] [USB_CTRL] MMIO registers configured for SCSI write")

        # =====================================================
        # RAM SETUP - populate RAM like USB hardware DMA
        # =====================================================
        if self.hw.memory:
            # USB state = 5 (configured)
            self.hw.memory.idata[0x6A] = 5

            # CDB area - USB hardware writes CDB to XDATA
            for i, b in enumerate(cdb):
                self.hw.memory.xdata[0x0002 + i] = b

            # SCSI command flag
            self.hw.memory.xdata[0x0003] = 0x08

            # Command type marker - 0x8A maps to different handler
            self.hw.memory.xdata[0x05B1] = 0x8A

            # Pad data to sector boundary and write to USB data buffer at 0x8000
            padded_size = sectors * 512
            padded_data = data + b'\x00' * (padded_size - len(data))
            for i, b in enumerate(padded_data):
                if 0x8000 + i < 0x10000:  # Stay within XDATA bounds
                    self.hw.memory.xdata[0x8000 + i] = b

            # Store data length info
            self.hw.usb_data_len = len(padded_data)

            print(f"[{self.hw.cycles:8d}] [USB_CTRL] Wrote {len(padded_data)} bytes to USB buffer at 0x8000")

        return cdb

    def inject_scsi_vendor_command(self, opcode: int, cdb: bytes, data: bytes = b'',
                                    is_write: bool = False):
        """
        Inject a SCSI vendor command (E0-E8) via MMIO registers.

        This sets up the MMIO registers needed for the firmware to process
        vendor-specific SCSI commands used by patch.py for firmware updates.

        Vendor opcodes:
            0xE0 - Config Read (128 bytes)
            0xE1 - Config Write (128 bytes)
            0xE2 - Flash Read
            0xE3 - Firmware Write (to SPI flash)
            0xE4 - XDATA Read
            0xE5 - XDATA Write
            0xE6 - NVMe Admin passthrough
            0xE8 - Reset/Commit

        Args:
            opcode: SCSI vendor opcode (0xE0-0xE8)
            cdb: Complete CDB bytes (16 bytes max)
            data: Data for write commands (E1, E3, E5)
            is_write: True if this is a write command with data phase
        """
        cycles = self.hw.cycles
        print(f"[{cycles:8d}] [USB_CTRL] === INJECT SCSI VENDOR COMMAND ===")
        print(f"[{cycles:8d}] [USB_CTRL] Opcode=0x{opcode:02X} CDB={cdb.hex()}")
        if is_write and data:
            print(f"[{cycles:8d}] [USB_CTRL] Write data: {len(data)} bytes")

        # Pad CDB to 16 bytes
        cdb_padded = (cdb + bytes(16))[:16]

        # =====================================================
        # MMIO REGISTER SETUP FOR SCSI VENDOR COMMAND
        # =====================================================

        # Write CDB to USB interface registers (0x910D-0x911C)
        for i, b in enumerate(cdb_padded):
            self.hw.regs[0x910D + i] = b

        # Also write to alternate CDB locations firmware may check
        for i, b in enumerate(cdb_padded):
            self.hw.regs[0x911F + i] = b

        # USB endpoint buffers
        for i, b in enumerate(cdb_padded):
            self.hw.usb_ep_data_buf[i] = b
            self.hw.usb_ep0_buf[i] = b
        self.hw.usb_ep0_len = len(cdb_padded)

        # USB connection and interrupt status
        self.hw.regs[0x9000] = 0x81  # Connected, USB active
        self.hw.regs[0x9101] = 0x21  # Bit 5 triggers command handler
        self.hw.regs[0xC802] = 0x05  # USB interrupt pending
        self.hw.regs[0x9096] = 0x01  # EP0 has data
        self.hw.regs[0x90E2] = 0x01  # Endpoint status

        # Store command state
        self.hw.usb_cmd_type = opcode
        self.hw.usb_cmd_size = len(data) if is_write else 0
        self.hw.usb_cmd_pending = True
        self.vendor_cmd_active = True

        # Reset state machine
        self.hw.usb_ce89_read_count = 0

        # =====================================================
        # RAM SETUP - populate like USB hardware DMA
        # =====================================================
        if self.hw.memory:
            # USB state = 2 (state for SCSI bulk commands)
            # Value 2 triggers the SCSI handler path at 0x32EE
            self.hw.memory.idata[0x6A] = 2

            # CDB area - write to XDATA[0x0002+] where firmware reads it
            for i, b in enumerate(cdb_padded):
                self.hw.memory.xdata[0x0002 + i] = b

            # Vendor command flags
            self.hw.memory.xdata[0x0003] = 0x08  # Enable vendor dispatch

            # Set state for vendor command handling
            # 0x0B02 = state machine: 0=idle, 1=E2 read, 2=E3 write
            if opcode == 0xE2:
                self.hw.memory.xdata[0x0B02] = 1
            elif opcode == 0xE3:
                self.hw.memory.xdata[0x0B02] = 2
            else:
                self.hw.memory.xdata[0x0B02] = 0

            # Magic value for vendor commands
            self.hw.memory.xdata[0xEA90] = 0x5A

            # Write data to USB buffer at 0x8000 for write commands
            if is_write and data:
                for i, b in enumerate(data):
                    if 0x8000 + i < 0x10000:
                        self.hw.memory.xdata[0x8000 + i] = b
                self.hw.usb_data_len = len(data)
                print(f"[{cycles:8d}] [USB_CTRL] Wrote {len(data)} bytes to USB buffer at 0x8000")

        print(f"[{cycles:8d}] [USB_CTRL] MMIO configured for vendor opcode 0x{opcode:02X}")
        return cdb_padded

    def inject_control_transfer(self, bmRequestType: int, bRequest: int, wValue: int,
                                  wIndex: int, wLength: int, data: bytes = b''):
        """
        Inject a USB control transfer (setup packet) through MMIO registers.

        This sets up the firmware's control transfer path:
        - Setup packet at 0x9E00-0x9E07
        - USB interrupt triggers handler at 0x0E33
        - Firmware reads setup packet and processes request

        ALL USB requests (standard and vendor) are passed through to firmware.
        The firmware handles GET_DESCRIPTOR by reading from code ROM via the
        flash mirror region (XDATA 0xE400-0xE500 → Code ROM).

        Args:
            bmRequestType: Request type byte (direction, type, recipient)
            bRequest: Request code (e.g., 0x06 = GET_DESCRIPTOR)
            wValue: Value field (e.g., descriptor type/index)
            wIndex: Index field
            wLength: Data length
            data: Data for OUT transfers
        """
        cycles = self.hw.cycles
        if data and (bmRequestType & 0x80):
            raise ValueError("control IN transfer cannot include OUT data")
        if data and len(data) != wLength:
            raise ValueError("control OUT data length does not match wLength")
        self.hw.pending_control_out_data = bytes(data)
        self.hw.usb_ep0_out_data_active = False
        print(f"[{cycles:8d}] [USB_CTRL] === INJECT CONTROL TRANSFER ===")
        print(f"[{cycles:8d}] [USB_CTRL] bmRequestType=0x{bmRequestType:02X} bRequest=0x{bRequest:02X}")
        print(f"[{cycles:8d}] [USB_CTRL] wValue=0x{wValue:04X} wIndex=0x{wIndex:04X} wLength={wLength}")

        # Write setup packet to MMIO registers
        # The firmware at 0xA5EA-0xA604 reads from 0x9104-0x910B (setup packet buffer)
        # and copies to XDATA 0x0ACE-0x0AD5
        self.hw.regs[0x9104] = bmRequestType
        self.hw.regs[0x9105] = bRequest
        self.hw.regs[0x9106] = wValue & 0xFF
        self.hw.regs[0x9107] = (wValue >> 8) & 0xFF
        self.hw.regs[0x9108] = wIndex & 0xFF
        self.hw.regs[0x9109] = (wIndex >> 8) & 0xFF
        self.hw.regs[0x910A] = wLength & 0xFF
        self.hw.regs[0x910B] = (wLength >> 8) & 0xFF

        # Also write to 0x9E00-0x9E07 (alternate setup packet location)
        self.hw.regs[0x9E00] = bmRequestType
        self.hw.regs[0x9E01] = bRequest
        self.hw.regs[0x9E02] = wValue & 0xFF
        self.hw.regs[0x9E03] = (wValue >> 8) & 0xFF
        self.hw.regs[0x9E04] = wIndex & 0xFF
        self.hw.regs[0x9E05] = (wIndex >> 8) & 0xFF
        self.hw.regs[0x9E06] = wLength & 0xFF
        self.hw.regs[0x9E07] = (wLength >> 8) & 0xFF

        # Also populate usb_ep0_buf which is what _usb_ep0_buf_read returns
        self.hw.usb_ep0_buf[0] = bmRequestType
        self.hw.usb_ep0_buf[1] = bRequest
        self.hw.usb_ep0_buf[2] = wValue & 0xFF
        self.hw.usb_ep0_buf[3] = (wValue >> 8) & 0xFF
        self.hw.usb_ep0_buf[4] = wIndex & 0xFF
        self.hw.usb_ep0_buf[5] = (wIndex >> 8) & 0xFF
        self.hw.usb_ep0_buf[6] = wLength & 0xFF
        self.hw.usb_ep0_buf[7] = (wLength >> 8) & 0xFF

        # USB connection and interrupt status
        # Bit 7 = connected, Bit 0 = active (needed for USB handler path at 0x4864)
        # With bit 0 CLEAR, firmware loops at 0x48CD checking CE89 instead of processing
        self.hw.regs[0x9000] = 0x81  # Connected (bit 7), Active (bit 0)
        self.hw.regs[0xC802] = 0x01  # USB interrupt pending

        # USB speed indicator - needed by 0xA4CC which returns 0x9100 & 0x03
        # 0 = Full Speed, 1 = High Speed, 2 = SuperSpeed, 3 = SuperSpeed+
        # At 0xB400: if speed == 2, sets R7=0 for descriptor DMA
        # Use stored USB speed from connect() or default to HIGH speed (USB 2.0)
        speed = getattr(self, 'usb_speed', 1)  # Default to High Speed if not set
        self.hw.regs[0x9100] = speed

        # USB mode indicators for descriptor handling at 0xA7E4-0xA7FF and 0x87A1
        # These set bits in 0x0ACC that determine USB2 vs USB3 code paths
        if speed >= 2:  # SuperSpeed or higher
            self.hw.regs[0xCC91] = 0x02  # Bit 1 SET - USB3 mode
            self.hw.regs[0x09F9] = 0x40  # Bit 6 SET - USB3 speed indicator
        else:  # High Speed or Full Speed (USB 2.0)
            self.hw.regs[0xCC91] = 0x00  # Bit 1 CLEAR - USB2 mode
            self.hw.regs[0x09F9] = 0x00  # Bit 6 CLEAR - USB2 speed indicator

        # Mark control transfer as active for state machine timing
        # This affects the 0x92C2 read callback bit 6 timing
        self.hw.usb_control_transfer_active = True
        self.hw.usb_ep0_fifo.clear()
        self.hw.usb_92c2_read_count = 0  # Reset for ISR->main loop timing
        self.hw.usb_ce89_read_count = 0  # Reset DMA state machine for new transfer

        # Check if this is a standard request (bmRequestType bits 6:5 = 00)
        request_type = bmRequestType & 0x60
        if request_type == 0x00:
            # Standard USB request (GET_DESCRIPTOR, SET_ADDRESS, etc.)
            # ISR path for GET_DESCRIPTOR (traced from original firmware):
            #   0x0E5E: checks 0x9101 bit 5 → if CLEAR, jumps to 0x0F07
            #   0x0F0B: checks 0x9101 bit 3 → if SET, goes to ISR dispatch (wrong path!)
            #   0x0F4A: if bit 3 CLEAR, reaches here
            #   0x0F4E: checks 0x9101 bit 0 → if CLEAR, jumps to 0x0F91
            #   0x0F91-0x0F95: checks 0x9101 bit 1 → if SET, calls 0x033B (descriptor handler!)
            # So for GET_DESCRIPTOR: need bit 3=0, bit 0=0, bit 1=1 → 0x02
            if bRequest == 0x06:  # GET_DESCRIPTOR
                desc_type = (wValue >> 8) & 0xFF
                desc_index = wValue & 0xFF
                print(f"[{cycles:8d}] [USB_CTRL] GET_DESCRIPTOR: type=0x{desc_type:02X} index=0x{desc_index:02X} len={wLength}")
                # Store the pending descriptor request for later DMA handling
                self.pending_descriptor_request = {
                    'type': desc_type,
                    'index': desc_index,
                    'length': wLength
                }
                # For GET_DESCRIPTOR: bit 1 SET to trigger descriptor handler, bits 0,3 CLEAR
                self.hw.regs[0x9101] = 0x02
                print(f"[{cycles:8d}] [USB_CTRL] Standard request - setting 0x9101=0x02, 0x9301=0x40")
            else:
                # Every standard request here is an EP0 control event. Bit 0
                # would misroute the handmade ISR into its reset handler.
                self.hw.regs[0x9101] = 0x02
                print(f"[{cycles:8d}] [USB_CTRL] Standard request - setting 0x9101=0x02, 0x9301=0x40")
            # 0x9301: Bit 6 triggers interrupt dispatch and DMA
            # Use write() to trigger the callback which handles descriptor DMA
            self.hw.write(0x9301, 0x40)  # Triggers _usb_9301_ep0_arm_write callback for DMA
        elif request_type == 0x20:
            # Class requests must be executed by firmware. In particular, do
            # not fake GET_MAX_LUN or BOT reset-recovery in the emulator.
            self.hw.regs[0x9101] = 0x22
            print(f"[{cycles:8d}] [USB_CTRL] Class request 0x{bRequest:02X} - passing to firmware")
        else:
            # Vendor request
            # Path: 0x0E33 → 0x0E64 → 0x0EF4 → 0x5333 (when 0x9101 bit 5 SET)
            self.hw.regs[0x9101] = 0x22  # Bit 1 = EP0 control, bit 5 SET (vendor path)
            print(f"[{cycles:8d}] [USB_CTRL] Vendor request - setting 0x9101=0x22")

        self.hw.regs[0x91D1] = 0x08  # EP0 setup packet received (bit 3)
        self.hw.regs[0x9118] = 0x01  # Endpoint index (lookup table requires < 8 value)

        # EP0 handler prerequisites
        # NOTE: 0x92C2 bit 6 is handled by _usb_92c2_read callback:
        #   - First read: returns bit 6 CLEAR (for ISR to call 0xBDA4)
        #   - Subsequent reads: returns bit 6 SET (for main loop to call 0x0322)
        self.hw.regs[0x92F8] = 0x0C  # Bits 2-3 set

        # CRITICAL: Main loop at 0xCDE7 checks 0x9091 bits for two-phase USB handling:
        # Phase 1 - Bit 0: Setup packet handler at 0xA5A6
        #   - Parses the USB request, sets 0x07E1 = 5 for GET_DESCRIPTOR
        #   - Firmware loops writing 0x01, waiting for hardware to clear bit 0
        # Phase 2 - Bit 1: DMA response handler at 0xD088
        #   - Checks 0x07E1 == 5, triggers DMA if so
        # 0x9002 bit 1 must be CLEAR to reach the 0x9091 check at 0xCDF5
        self.hw.regs[0x9002] = 0x00  # Bit 1 CLEAR to allow 0x9091 check
        self.hw.regs[0x9091] = 0x01  # Bit 0 SET to trigger setup handler at 0xA5A6
        # Reset phase transition counters
        self.hw._usb_9091_setup_writes = 0
        self.hw._usb_9091_read_count = 0

        # CRITICAL: The main loop at 0xCDC6-0xCDD9 waits for state transition registers:
        # - Checks 0xE712 bit 0 or bit 1 to exit the polling loop
        # - If neither set, checks 0xCC11 bit 1
        # Without these bits, firmware never reaches USB dispatch at 0xCDE7
        self.hw.regs[0xE712] = 0x01  # Bit 0 SET to exit polling loop
        self.hw.regs[0xCC11] = 0x02  # Bit 1 SET as backup exit condition

        # Set command pending
        self.hw.usb_cmd_pending = True
        self.vendor_cmd_active = False

        # USB state = 5 (configured) - required for firmware to process control transfers
        # The firmware checks this state at various decision points in the USB handler
        if self.hw.memory:
            self.hw.memory.idata[0x6A] = 5
            # PCIe enumeration complete flag - needed for descriptor DMA path at 0x185C
            # Without this, firmware takes alternate path that doesn't use CEB2/CEB3
            self.hw.memory.xdata[0x0AF7] = 0x01
            self.hw.memory.xdata[0x053F] = 0x01
            # CRITICAL: Port state at 0x05B1 + port_index*0x22 must NOT be 4
            # At 0x35D4-0x35DF: Firmware checks this and clears 0x0AF7 if state == 4
            self.hw.memory.xdata[0x05A3] = 0x00  # Port index = 0
            self.hw.memory.xdata[0x05B1] = 0x03  # Port 0 state = 3 (link up, not 4)
            # USB speed mode at 0x0AD6 - used by 0xB3FC at 0xB465 to check descriptor mode
            # At 0xB467: if 0x0AD6 >= 3, firmware returns R7=0x03 (wrong value for DMA)
            # This value would normally be set by USB enumeration before control transfers
            # Use stored USB speed from connect()
            usb_speed = getattr(self, 'usb_speed', 1)  # Default to High Speed if not set
            self.hw.memory.xdata[0x0AD6] = usb_speed  # USB speed mode

        # PCIe link state - 0xB480 bit 0 must be SET to prevent firmware at 0x20DA from
        # clearing XDATA[0x0AF7] to 0
        self.hw.regs[0xB480] = 0x03  # Bits 0,1 SET - PCIe link active state

        # Set pending interrupt flag so hardware update triggers actual CPU interrupt
        self.hw._pending_usb_interrupt = True

        print(f"[{cycles:8d}] [USB_CTRL] Control transfer injected (interrupt pending)")


@dataclass
class HardwareState:
    """
    Hardware state for ASM2464PD emulation.

    Only emulates actual hardware registers (addresses >= 0x6000).
    RAM variables are handled by the memory system.
    """

    # Logging
    log_reads: bool = False
    log_writes: bool = False
    log_uart: bool = True
    log_pcie: bool = True  # Log PCIe DMA operations

    # Proxy mode - when True, disable all fake USB/interrupt injection
    # Real hardware handles everything, we just proxy MMIO
    proxy_mode: bool = False

    # VID/PID override - intercept firmware USB descriptor writes to patch VID/PID
    # Format: (vid, pid) or None for no override
    vidpid_override: tuple = None

    # Reference to memory system (set by Emulator during init)
    # Used for reading XDATA (e.g., USB descriptors)
    _memory: 'Memory' = None

    # Cycle counter for timing-based responses
    cycles: int = 0

    # Hardware state
    usb_connected: bool = False
    usb_connect_delay: int = 500000  # Cycles before USB plug-in event (after init)

    # Polling counters - track how many times an address is polled
    poll_counts: Dict[int, int] = field(default_factory=dict)

    # Register values - only for hardware registers >= 0x6000
    regs: Dict[int, int] = field(default_factory=dict)
    dpx_regs: Dict[int, int] = field(default_factory=dict)

    # Callbacks for specific addresses
    read_callbacks: Dict[int, Callable[['HardwareState', int], int]] = field(default_factory=dict)
    write_callbacks: Dict[int, Callable[['HardwareState', int, int], None]] = field(default_factory=dict)

    # USB command queue
    usb_cmd_queue: list = field(default_factory=list)
    usb_cmd_pending: bool = False
    # The controller exposes a 512-byte EP0 staging window at 0x9E00-0x9FFF.
    # Keeping only the first max-packet here silently discarded the tail of
    # development-profile NVMe identify and data responses.
    usb_ep0_buf: bytearray = field(default_factory=lambda: bytearray(0x200))
    usb_ep0_len: int = 0
    usb_data_buf: bytearray = field(default_factory=lambda: bytearray(4096))  # Data buffer
    usb_data_len: int = 0
    usb_ep_data_buf: bytearray = field(default_factory=lambda: bytearray(2048))  # EP data buffer (0xD800)
    usb_sw_dma_out_destination: Optional[int] = None

    # Memory reference for E4/E5 commands (set by create_hardware_hooks)
    memory: 'Memory' = None

    # UART output buffer for line-based output
    uart_buffer: str = ""

    # USB command injection timing
    usb_injected: bool = False

    # USB controller instance (created in __post_init__)
    usb_controller: 'USBController' = None

    # USB command state for MMIO hooks
    usb_cmd_type: int = 0  # Current command type (0xE4, 0xE5, etc.)
    usb_cmd_size: int = 0  # Size for E4 read commands
    usb_e5_pending_value: int = 0  # Pending E5 value to write (preserved until read)

    # USB endpoint selection tracking
    usb_ep_selected: int = 0  # Currently selected endpoint index (0-31)

    # USB command injection from command line (set by emulator CLI)
    usb_inject_cmd: tuple = None  # (cmd_type, addr, val_or_size)
    usb_inject_delay: int = 1000  # Cycles after USB connect to inject

    # USB state machine emulation
    # Tracks firmware USB state to know when to set register bits
    usb_state_machine_phase: int = 0  # 0=init, 1=waiting, 2=enumerating, 3=ready
    usb_ce89_read_count: int = 0  # Count reads of 0xCE89 for state transitions
    usb_92c2_read_count: int = 0  # Count reads of 0x92C2 for ISR->main loop transition
    usb_ce00_read_count: int = 0  # Count reads of 0xCE00 for DMA completion
    scsi_dma_log: list = field(default_factory=list)
    scsi_dma_stall: bool = False
    scsi_dma_source_offset: int = 0

    # USB EP0 FIFO buffer - reserved for potential future use
    # Note: USB descriptor data is sent via hardware DMA from ROM, not firmware FIFO writes
    usb_ep0_fifo: bytearray = field(default_factory=bytearray)

    # USB control transfer active flag - affects 0x92C2 callback timing for ISR->main loop
    usb_control_transfer_active: bool = False

    # USB descriptor state
    # NOTE: The emulator does NOT track descriptor requests or generate responses.
    # The firmware handles GET_DESCRIPTOR by reading from code ROM and DMAing
    # the response to the USB buffer. See "USB Descriptor Handling Philosophy" above.
    usb_ep0_response: bytearray = field(default_factory=bytearray)  # Response data for host
    usb_transfer_complete: bool = False  # Set when firmware signals transfer complete
    # Firmware-originated MSC transfers captured when it writes the C42C trigger.
    usb_msc_transfers: list = field(default_factory=list)
    # Physical BOT endpoint state.  Descriptor presence does not imply that
    # the controller parser or transfer path exists: firmware must activate
    # 90E3 and explicitly arm the next CBW.
    usb_bulk_engine_active: bool = False
    usb_cbw_armed: bool = False
    usb_ep_ready_ack_pending: bool = False
    usb_endpoint_generation: int = 0
    usb_msc_trigger_log: list = field(default_factory=list)
    usb_msc_doorbell_trace: list = field(default_factory=list)
    usb_msc_pending_transfer: dict = None
    usb_sw_bulk_in_log: list = field(default_factory=list)
    usb_sw_bulk_in_fault: bool = False
    usb_sw_bulk_in_setup: dict = None
    # Exact stock buffered-IN preparation (0206 software branch) followed by
    # the BOT 47D5 -> 3258 service transaction.
    usb_stock_buffered_in_trace: list = field(default_factory=list)
    usb_stock_buffered_in_pending: dict = None
    usb_stock_buffered_in_log: list = field(default_factory=list)
    # BOT-alt-zero 4D3E -> 3258 service, independent of 0206.
    usb_stock_bot_alt0_service_trace: list = field(default_factory=list)
    usb_stock_c42c_completion_ack_pending: bool = False
    # Select the inferred hardware source raised after a valid 0206 history.
    # Tests exercise both statically proven consumers and a missing-source
    # failure; this selector is not physical evidence of either producer.
    usb_stock_completion_event_source: str = "c42c"
    # Stock BDA4's controller-visible prerequisite is distinct from the later
    # 0206 descriptor.  Physical silicon retained 90E1 when this state was
    # missing, so the model must be able to represent both outcomes.
    usb_stock_msc_prerequisite_trace: list = field(default_factory=list)
    usb_stock_52ef_trace: list = field(default_factory=list)
    usb_stock_52ef_ready: bool = False
    usb_stock_52ef_df0b_latched: bool = False
    usb_stock_5372_trace: list = field(default_factory=list)
    usb_stock_5372_ready: bool = False
    usb_stock_5372_df0b_latched: bool = False
    usb_stock_e300_trace: list = field(default_factory=list)
    usb_stock_e300_ready: bool = False
    usb_stock_e300_df0b_latched: bool = False
    usb_stock_4c8d_route_trace: list = field(default_factory=list)
    usb_stock_4c8d_route_ready: bool = False
    usb_stock_4c8d_c8cf_latched: bool = False
    usb_stock_c8cf_trace: list = field(default_factory=list)
    usb_stock_c8cf_ready: bool = False
    usb_stock_c8cf_cold_latched: bool = False
    usb_stock_cold_trace: list = field(default_factory=list)
    usb_stock_8fcf_trace: list = field(default_factory=list)
    usb_stock_8fcf_ready: bool = False
    usb_stock_dc3a_trace: list = field(default_factory=list)
    usb_stock_cold_cc83_trace: list = field(default_factory=list)
    usb_stock_cf91_trace: list = field(default_factory=list)
    usb_stock_df0b_stage: int = 0
    usb_stock_eeb5_eeaf_ready: bool = False
    usb_stock_dc3a_ready: bool = False
    usb_stock_ee8a_ready: bool = False
    usb_stock_cc32_ready: bool = False
    usb_stock_link_ready_observed: bool = False
    usb_stock_cold_cc83_ready: bool = False
    usb_stock_cold_ready: bool = False
    usb_stock_c24c_trace: list = field(default_factory=list)
    usb_stock_c24c_validated_trace: list = field(default_factory=list)
    usb_stock_msc_dma_ready: bool = False
    usb_stock_c24c_ready: bool = False
    usb_stock_msc_initial_transition_ready: bool = False
    usb_stock_b1c5_terminal_trace: list = field(default_factory=list)
    usb_stock_b1c5_terminal_ready: bool = False
    usb_stock_3a2b_channel_trace: list = field(default_factory=list)
    usb_stock_3a2b_channel_ready: bool = False
    usb_stock_3a2b_dma_trace: list = field(default_factory=list)
    usb_stock_3a2b_dma_ready: bool = False
    usb_stock_3a2b_scsi_dma_trace: list = field(default_factory=list)
    usb_stock_3a2b_scsi_dma_ready: bool = False
    usb_stock_cbw_dma_trace: list = field(default_factory=list)
    usb_stock_cbw_dma_ready: bool = False
    usb_stock_cbw_tag_trace: list = field(default_factory=list)
    usb_stock_cbw_tag_ready: bool = False
    usb_stock_bulk_in_arm_trace: list = field(default_factory=list)
    usb_stock_bulk_in_arm_ready: bool = False
    usb_stock_bulk_in_event_ready: bool = False
    # Default campaigns model prompt silicon completion. Focused regressions
    # may withhold it to reproduce a late 9101.2/9093.3 event.
    usb_stock_bulk_in_event_auto: bool = True
    usb_stock_msc_generation: int = 0
    usb_nvme_bulk_in_log: list = field(default_factory=list)
    usb_nvme_bulk_in_trace: list = field(default_factory=list)
    usb_nvme_bulk_in_ordered_log: list = field(default_factory=list)
    usb_nvme_bulk_in_fault: bool = False
    pending_control_out_data: bytes = b""

    # Config descriptor capture - firmware writes config descriptor to 0x9E00 but then
    # corrupts it before DMA. Capture the valid descriptor when written.
    usb_captured_config_desc: bytearray = field(default_factory=bytearray)
    usb_capture_config_active: bool = False  # True when we're capturing config desc writes

    # Full USB3 config descriptor loaded from ROM with corrected wTotalLength.
    # ROM at 0x58CF has wTotalLength=44 (only alt_setting 0), but alt_setting 1
    # data continues immediately after. We load the full descriptor (121 bytes)
    # and fix wTotalLength so the host knows to request all of it.
    usb_ss_config_from_rom: bytes = field(default_factory=bytes)

    # USB2 High Speed config descriptor loaded from ROM.
    # ROM at 0x5948 has USB 2.0 config descriptor with 512-byte max packet sizes.
    # Used when connected at USB 2.0 High Speed (dummy_hcd limitation).
    usb_hs_config_from_rom: bytes = field(default_factory=bytes)

    def reset_stock_msc_generation(self):
        """Revoke inferred MSC readiness across a firmware reset generation."""
        self.usb_stock_msc_prerequisite_trace = []
        self.usb_stock_52ef_trace = []
        self.usb_stock_52ef_ready = False
        self.usb_stock_52ef_df0b_latched = False
        self.usb_stock_5372_trace = []
        self.usb_stock_5372_ready = False
        self.usb_stock_5372_df0b_latched = False
        self.usb_stock_e300_trace = []
        self.usb_stock_e300_ready = False
        self.usb_stock_e300_df0b_latched = False
        self.usb_stock_4c8d_route_trace = []
        self.usb_stock_4c8d_route_ready = False
        self.usb_stock_4c8d_c8cf_latched = False
        self.usb_stock_c8cf_trace = []
        self.usb_stock_c8cf_ready = False
        self.usb_stock_c8cf_cold_latched = False
        self.usb_stock_cold_trace = []
        self.usb_stock_8fcf_trace = []
        self.usb_stock_8fcf_ready = False
        self.usb_stock_dc3a_trace = []
        self.usb_stock_cold_cc83_trace = []
        self.usb_stock_cf91_trace = []
        self.usb_stock_df0b_stage = 0
        self.usb_stock_eeb5_eeaf_ready = False
        self.usb_stock_dc3a_ready = False
        self.usb_stock_ee8a_ready = False
        self.usb_stock_cc32_ready = False
        self.usb_stock_link_ready_observed = False
        self.usb_stock_cold_cc83_ready = False
        self.usb_stock_cold_ready = False
        self.usb_stock_c24c_trace = []
        self.usb_stock_c24c_validated_trace = []
        self.usb_stock_msc_dma_ready = False
        self.usb_stock_c24c_ready = False
        self.usb_stock_msc_initial_transition_ready = False
        self.usb_stock_b1c5_terminal_trace = []
        self.usb_stock_b1c5_terminal_ready = False
        self.usb_stock_3a2b_channel_trace = []
        self.usb_stock_3a2b_channel_ready = False
        self.usb_stock_3a2b_dma_trace = []
        self.usb_stock_3a2b_dma_ready = False
        self.usb_stock_3a2b_scsi_dma_trace = []
        self.usb_stock_3a2b_scsi_dma_ready = False
        self.usb_stock_cbw_dma_trace = []
        self.usb_stock_cbw_dma_ready = False
        self.usb_stock_cbw_tag_trace = []
        self.usb_stock_cbw_tag_ready = False
        self.usb_stock_bulk_in_arm_trace = []
        self.usb_stock_bulk_in_arm_ready = False
        self.usb_stock_bulk_in_event_ready = False
        self.usb_stock_buffered_in_trace = []
        self.usb_stock_buffered_in_pending = None
        self.usb_stock_bot_alt0_service_trace = []
        self.usb_stock_c42c_completion_ack_pending = False

    def record_usb_stock_b1c5_terminal(self, addr: int, old: int, value: int):
        """Track BC07's ordered 09FA=4, 0AE1={1,2} terminal commit."""
        event = (addr, old, value)
        if addr == 0x09FA and value == 0x04:
            self.usb_stock_b1c5_terminal_trace = [event]
            self.usb_stock_b1c5_terminal_ready = False
            return
        if not self.usb_stock_b1c5_terminal_trace:
            return
        self.usb_stock_b1c5_terminal_trace.append(event)
        self.usb_stock_b1c5_terminal_ready = bool(
            len(self.usb_stock_b1c5_terminal_trace) == 2 and
            addr == 0x0AE1 and value in (0x01, 0x02))
        if not self.usb_stock_b1c5_terminal_ready:
            self.usb_stock_b1c5_terminal_trace = []

    def _record_usb_stock_3a2b_channel(self, addr: int, old: int, value: int):
        """Track 3A2B's reset prefix and eight channel-select writes."""
        relevant = (0xC8D8, 0xC8D7, 0xC8D6, 0xC8D5)
        if addr not in relevant or self.usb_stock_3a2b_channel_ready:
            return
        event = (addr, old, value)
        if (not self.usb_stock_3a2b_channel_trace and
                addr == 0xC8D8 and value == (old & ~0x08)):
            self.usb_stock_3a2b_channel_trace = [event]
        elif self.usb_stock_3a2b_channel_trace:
            self.usb_stock_3a2b_channel_trace.append(event)
        else:
            return
        trace = self.usb_stock_3a2b_channel_trace
        if len(trace) > 16:
            self.usb_stock_3a2b_channel_trace = []
            return
        if len(trace) != 16:
            return
        expected_addresses = [
            0xC8D8, 0xC8D8, 0xC8D8, 0xC8D7,
            0xC8D6, 0xC8D6, 0xC8D6, 0xC8D5,
            0xC8D8, 0xC8D8, 0xC8D8, 0xC8D8,
            0xC8D6, 0xC8D6, 0xC8D6, 0xC8D6]
        prefix_clears = (
            (0, 0x08), (1, 0x04), (2, 0x01),
            (4, 0x08), (5, 0x04), (6, 0x01))
        selector_ops = (
            (8, 0x02, True), (9, 0x02, True),
            (10, 0x02, False), (11, 0x02, False),
            (12, 0x02, True), (13, 0x02, True),
            (14, 0x02, False), (15, 0x02, False))
        self.usb_stock_3a2b_channel_ready = bool(
            [event[0] for event in trace] == expected_addresses and
            trace[3][2] == 0 and trace[7][2] == 0 and
            # C8D6/D8 contain hardware-owned status bits which may change
            # between the firmware's read and write. Pin the targeted clear
            # and its position without requiring unrelated bits to retain the
            # emulator's pre-read stored value.
            all(not (trace[index][2] & bit)
                for index, bit in prefix_clears) and
            all(self._usb_msc_transition(trace[index],
                                         expected_addresses[index], bit, set_bit)
                for index, bit, set_bit in selector_ops))

    def _record_usb_stock_3a2b_dma(self, addr: int, old: int, value: int):
        """Validate all eight 4AA0 selector/C8B2-C8B8 transactions."""
        relevant = {
            0xC8D8, 0xC8D6, 0xC8B2, 0xC8B3, 0xC8B4, 0xC8B5,
            0xC8B6, 0xC8B7, 0xC8B8}
        if addr not in relevant or self.usb_stock_3a2b_dma_ready:
            return
        # The first channel selector follows the eight-write reset prefix.
        if not self.usb_stock_3a2b_dma_trace:
            if (len(self.usb_stock_3a2b_channel_trace) != 9 or
                    addr not in (0xC8D8, 0xC8D6)):
                return
        self.usb_stock_3a2b_dma_trace.append((addr, old, value))
        trace = self.usb_stock_3a2b_dma_trace
        if len(trace) > 96:
            self.usb_stock_3a2b_dma_trace = []
            return
        if len(trace) != 96:
            return

        params = (
            (0xC8D8, True,  0xA0, 0x00, 0x0F, 0xFF),
            (0xC8D8, True,  0xB0, 0x00, 0x01, 0xFF),
            (0xC8D8, False, 0xA0, 0x00, 0x0F, 0xFF),
            (0xC8D8, False, 0xB0, 0x00, 0x01, 0xFF),
            (0xC8D6, True,  0xB8, 0x00, 0x03, 0xFF),
            (0xC8D6, True,  0xBC, 0x00, 0x00, 0x7F),
            (0xC8D6, False, 0xB8, 0x00, 0x03, 0xFF),
            (0xC8D6, False, 0xBC, 0x00, 0x00, 0x7F))
        valid = True
        expected_addresses = [
            None, 0xC8B7, 0xC8B6, 0xC8B6, 0xC8B6, 0xC8B6,
            0xC8B2, 0xC8B3, 0xC8B4, 0xC8B5, 0xC8B8, 0xC8B6]
        for index, expected in enumerate(params):
            chunk = trace[index * 12:(index + 1) * 12]
            selector, selected, aux, aux1, count_hi, count_lo = expected
            addresses = [event[0] for event in chunk]
            expected_addresses[0] = selector
            valid &= addresses == expected_addresses
            valid &= self._usb_msc_transition(
                chunk[0], selector, 0x02, selected)
            valid &= self._usb_msc_transition(chunk[2], 0xC8B6, 0x04, True)
            valid &= self._usb_msc_transition(chunk[3], 0xC8B6, 0x01, False)
            valid &= self._usb_msc_transition(chunk[4], 0xC8B6, 0x02, False)
            valid &= self._usb_msc_transition(chunk[5], 0xC8B6, 0x80, True)
            valid &= [event[2] for event in chunk[1:2] + chunk[6:11]] == [
                0x00, aux, aux1, count_hi, count_lo, 0x01]
            valid &= self._usb_msc_transition(chunk[11], 0xC8B6, 0x80, False)
        self.usb_stock_3a2b_dma_ready = bool(valid)

    def _record_usb_stock_3a2b_scsi_dma(self, addr: int, old: int,
                                        value: int):
        """Validate 3A2B's ordered CE40-CE43 per-slot reset tail."""
        if addr not in (0xCE40, 0xCE41, 0xCE42, 0xCE43):
            return
        if self.usb_stock_3a2b_scsi_dma_ready:
            return
        if not self.usb_stock_3a2b_dma_ready:
            self.usb_stock_3a2b_scsi_dma_trace = []
            return
        event = (addr, old, value)
        expected_addr = 0xCE40 + len(self.usb_stock_3a2b_scsi_dma_trace)
        if addr == expected_addr and value == 0:
            self.usb_stock_3a2b_scsi_dma_trace.append(event)
        elif addr == 0xCE40 and value == 0:
            self.usb_stock_3a2b_scsi_dma_trace = [event]
        else:
            self.usb_stock_3a2b_scsi_dma_trace = []
        self.usb_stock_3a2b_scsi_dma_ready = bool(
            len(self.usb_stock_3a2b_scsi_dma_trace) == 4)

    # PCIe DMA state
    pcie_dma_pending: bool = False  # DMA operation in progress
    pcie_dma_source: int = 0  # Source address in PCIe space
    pcie_dma_size: int = 0  # Size of transfer
    pcie_dma_dest: int = 0x8000  # Destination in XDATA (USB data buffer)

    # Simulated PCIe memory (for E4 read responses)
    # This would contain the data that would be read from the NVMe device
    pcie_memory: Dict[int, int] = field(default_factory=dict)

    # Minimal NVMe admin model used by handmade firmware tests.
    nvme_bar0: int = 0x00D00000
    nvme_cc: int = 0
    nvme_csts: int = 0
    nvme_controller_generation: int = 0
    nvme_asq: int = 0x00200000
    nvme_acq: int = 0x00820000
    nvme_cq_tail: int = 0
    nvme_cq_phase: int = 1
    nvme_admin_history: list = field(default_factory=list)
    nvme_last_security_send: bytes = b""
    nvme_security_send_history: list = field(default_factory=list)
    nvme_identify_serial: bytes = b"S65DNXMW601706".ljust(20, b" ")
    nvme_namespace_blocks: int = 1000215216
    nvme_namespace_lba_shift: int = 9
    nvme_namespace_present: bool = True
    nvme_io_cq_tail: int = 0
    nvme_io_cq_phase: int = 1
    nvme_io_cq_created: bool = False
    nvme_io_sq_created: bool = False
    nvme_io_history: list = field(default_factory=list)
    nvme_io_data_length: int = 0
    nvme_namespace_data: Dict[int, bytes] = field(default_factory=dict)
    nvme_io_status_fault: int = 0
    nvme_io_bus_reset_on_submit: bool = False
    pyrite_device: PyriteDevice = field(default_factory=PyriteDevice)

    # PCIe LTSSM State Machine & Link Emulation
    pcie_device_present: bool = True
    pcie_ltssm_state: int = 0x00
    pcie_ltssm_step: int = 0
    pcie_ltssm_auto_advance: bool = True
    pcie_ltssm_forced: Optional[int] = None
    pcie_ltssm_history: list = field(default_factory=list)
    pcie_e764_history: list = field(default_factory=list)
    pcie_rxpll_reset_history: list = field(default_factory=list)
    pcie_rxpll_e716_history: list = field(default_factory=list)
    pcie_rxpll_e716_pulse_complete: bool = False
    pcie_rxpll_reset_asserted: bool = False
    pcie_rxpll_reset_poll_observed: bool = False
    pcie_rxpll_reset_complete: bool = False
    pcie_rxpll_training_complete: bool = False
    pcie_acdf_history: list = field(default_factory=list)
    pcie_acdf_order_complete: bool = False
    pcie_link_recovery_history: list = field(default_factory=list)
    pcie_link_recovery_required: bool = False
    pcie_link_recovery_complete: bool = False

    # PCIe Programmed-I/O (PIO) Contract & Fault Hooks
    pcie_pio_history: list = field(default_factory=list)
    pcie_allow_legacy_4dw: bool = False
    pcie_pio_fault_timeout: bool = False
    pcie_pio_cpl_timeout: bool = False
    pcie_pio_b296_error: bool = False
    pcie_pio_cpl_status: int = 0x00
    pcie_pio_cpl_fmt: int = 0x04
    pcie_pio_cpl_dw0: int = 0x00
    pcie_pio_cpl_dw1: int = 0x00
    pcie_pio_stale_data: bool = False

    # PCIe Bridge Configuration Space (Type 1 Bridge)
    pcie_bridge_primary_bus: int = 0
    pcie_bridge_secondary_bus: int = 0
    pcie_bridge_subordinate_bus: int = 0
    pcie_bridge_command: int = 0
    pcie_bridge_status: int = 0x0010
    pcie_bridge_sec_latency: int = 0
    pcie_bridge_raw_mem_base: int = 0
    pcie_bridge_raw_mem_limit: int = 0
    pcie_bridge_mem_base: int = 0
    pcie_bridge_mem_limit: int = 0
    pcie_bridge_configured: bool = False

    # PCIe Endpoint Configuration Space (Type 0 Endpoint on Secondary Bus)
    pcie_endpoint_present: bool = True
    pcie_endpoint_devfn: int = 0x00
    pcie_endpoint_vendor_id: int = 0x144D
    pcie_endpoint_device_id: int = 0xA809
    pcie_endpoint_command: int = 0
    pcie_endpoint_status: int = 0x0010
    pcie_endpoint_class_code: int = 0x010802
    pcie_endpoint_rev_id: int = 0x00
    pcie_endpoint_bar0: int = 0
    pcie_endpoint_bar0_size: int = 0x01000000
    pcie_endpoint_bar0_probed: bool = False
    pcie_endpoint_bar0_is_io: bool = False
    pcie_endpoint_bar0_is_prefetchable: bool = False
    pcie_endpoint_bar0_is_64bit: bool = False
    pcie_endpoint_bars: list = field(default_factory=lambda: [0]*5)
    pcie_endpoint_capptr: int = 0x40
    pcie_endpoint_cap_id: int = 0x10
    pcie_endpoint_next_cap: int = 0x00
    pcie_endpoint_devctl: int = 0
    pcie_cfg_history: list = field(default_factory=list)
    # Routing validity is independent of whether configuration is currently
    # present. Reset must not turn absent BAR/window state into an MMIO bypass.
    # Isolated legacy encoder fixtures may explicitly opt out of this model.
    pcie_routing_gate_enforced: bool = True

    # Stock NVMe Queue Engine State & Fault Hooks (Queue 0 at 0xA000 / 0xB800)
    stock_nvme_queue_enabled: bool = False
    stock_nvme_setup_progress: int = 0
    stock_nvme_sq_tail: int = 0
    stock_nvme_cq_head: int = 0
    stock_nvme_cq_tail: int = 0
    stock_nvme_cq_phase: int = 1
    stock_nvme_head_history: list = field(default_factory=list)
    stock_nvme_submission_history: list = field(default_factory=list)
    stock_nvme_b296_timeout: bool = False
    stock_nvme_b296_error: bool = False
    stock_nvme_cq_b296_timeout: bool = False
    stock_nvme_cq_b296_error: bool = False
    stock_nvme_completion_timeout: bool = False
    stock_nvme_queue_error: bool = False
    stock_nvme_force_stale_phase: bool = False
    stock_nvme_force_mismatched_cid: bool = False
    stock_nvme_force_sct: int = 0
    stock_nvme_force_sc: int = 0

    # Stock NVMe Queue-1 I/O Engine State & Fault Hooks (Queue 1 at 0xA000 / 0xB840)
    stock_nvme_io_sq_tail: int = 0
    stock_nvme_io_cq_head: int = 0
    stock_nvme_io_cq_tail: int = 0
    stock_nvme_io_cq_phase: int = 1
    stock_nvme_io_head_history: list = field(default_factory=list)
    stock_nvme_io_submission_history: list = field(default_factory=list)
    stock_b254_trigger_log: list = field(default_factory=list)
    conventional_nvme_io_doorbell_count: int = 0
    stock_nvme_io_b296_timeout: bool = False
    stock_nvme_io_b296_error: bool = False
    stock_nvme_io_cq_b296_timeout: bool = False
    stock_nvme_io_cq_b296_error: bool = False
    stock_nvme_io_completion_timeout: bool = False
    stock_nvme_io_queue_error: bool = False
    stock_nvme_io_force_stale_phase: bool = False
    stock_nvme_io_force_mismatched_cid: bool = False
    stock_nvme_io_force_sct: int = 0
    stock_nvme_io_force_sc: int = 0
    stock_nvme_io_bus_reset_on_submit: bool = False

    # ============================================
    # SPI Flash Emulation
    # The ASM2464PD has a SPI flash chip for firmware storage.
    # patch.py uses E2 (read) and E3 (write) commands to access flash.
    # Physical target size: 512 KiB (0x80000 bytes), erased state 0xFF.
    # ============================================
    spi_flash: bytearray = field(
        default_factory=lambda: bytearray([SPI_FLASH_ERASED_BYTE]) * SPI_FLASH_SIZE
    )
    spi_flash_addr: int = 0  # Current flash address (set by address registers)
    spi_flash_write_pending: bool = False  # Write operation in progress
    spi_flash_write_count: int = 0  # Bytes written in current operation
    spi_command_log: list = field(default_factory=list)
    spi_program_fault: bool = False  # Drop page programs for readback-failure tests

    # Execution tracing
    trace_enabled: bool = False  # Global trace enable
    trace_points: Dict[int, str] = field(default_factory=dict)  # PC addr -> label
    trace_callback: Callable = None  # Optional callback(hw, pc, label) for trace points

    # XDATA write tracing - tracks writes to specific RAM addresses
    xdata_trace_enabled: bool = False
    xdata_trace_addrs: Dict[int, str] = field(default_factory=dict)  # addr -> name
    xdata_write_log: list = field(default_factory=list)  # Log of traced writes

    def __post_init__(self):
        """Initialize hardware register defaults."""
        self._init_registers()
        self._setup_callbacks()
        # Create USB controller after self is initialized
        self.usb_controller = USBController(self)

    def _init_registers(self):
        """
        Set default values for hardware registers.
        Only addresses >= 0x6000 are hardware registers.
        """
        self.pcie_e764_history.clear()
        self.pcie_rxpll_reset_history.clear()
        self.pcie_rxpll_e716_history.clear()
        self.pcie_rxpll_e716_pulse_complete = False
        self.pcie_rxpll_reset_asserted = False
        self.pcie_rxpll_reset_poll_observed = False
        self.pcie_rxpll_reset_complete = False
        self.pcie_rxpll_training_complete = False
        self.pcie_acdf_history.clear()
        self.pcie_acdf_order_complete = False
        self.pcie_link_recovery_history.clear()
        self.pcie_link_recovery_required = False
        self.pcie_link_recovery_complete = False
        self.reset_pcie_configuration()

        # ============================================
        # USB Controller Registers (0x9xxx)
        # ============================================
        self.regs[0x9000] = 0x00  # USB status - bit 7 = connected
        self.regs[0x90E0] = 0x00  # USB speed
        self.regs[0x9100] = 0x00  # USB link status
        self.regs[0x9105] = 0x00  # USB PHY status
        self.regs[0x91C0] = 0x02  # USB PHY control
        self.regs[0x91D0] = 0x00  # USB PHY config

        # ============================================
        # Power Management Registers (0x92xx)
        # ============================================
        self.regs[0x92C0] = 0x81  # Power enable
        self.regs[0x92C1] = 0x03  # Clocks enabled
        self.regs[0x92C2] = 0x40  # Power state - bit 6 enables PD task path at 0xBF44
        self.regs[0x92C5] = 0x04  # PHY powered
        self.regs[0x92E0] = 0x02  # Power domain
        self.regs[0x92F7] = 0x40  # Power status
        self.regs[0x92FB] = 0x01  # Power sequence complete (checked at 0x9C42)

        # ============================================
        # PD Event Registers (0xE4xx)
        # ============================================
        # These control the debug output at 0xAE89/0xAF5E
        # Set initial PD event to trigger debug output
        self.regs[0xE40F] = 0x00  # PD event type - will be set during PD events
        self.regs[0xE410] = 0x00  # PD sub-event

        # ============================================
        # PCIe Registers (0xBxxx)
        # ============================================
        self.regs[0xB238] = 0x00  # PCIe trigger - not busy
        self.regs[0xB254] = 0x00  # PCIe trigger write
        self.regs[0xB296] = 0x00  # PCIe status - bit 2 set when DMA complete
        self.regs[0xB401] = 0x01  # PCIe tunnel enabled
        self.regs[0xB480] = 0x00  # PCIe link initially down (bit 0 = 0)
        self.regs[0xB450] = 0x00  # PCIe LTSSM initially Detect.Quiet (0x00)
        # This allows USB state machine to return R7=5 at 0x3FC6 instead of state=11

        # ============================================
        # UART Registers (0xC0xx)
        # ============================================
        self.regs[0xC000] = 0x00  # UART TX data
        self.regs[0xC001] = 0x00  # UART TX data (alt)
        self.regs[0xC009] = 0x60  # UART LSR - TX empty, ready

        # ============================================
        # NVMe Controller Registers (0xC4xx, 0xC5xx)
        # ============================================
        self.regs[0xC412] = 0x02  # NVMe ready
        self.regs[0xC471] = 0x00  # NVMe queue busy - bit 0 = queue busy
        self.regs[0xC47A] = 0x00  # NVMe command status
        self.regs[0xC520] = 0x80  # NVMe link ready

        # ============================================
        # PHY Registers (0xC6xx)
        # ============================================
        self.regs[0xC620] = 0x00  # PHY control
        self.regs[0xC655] = 0x08  # PHY config
        self.regs[0xC65A] = 0x09  # PHY config
        self.regs[0xC6B3] = 0x30  # PHY status - bits 4,5 set

        # ============================================
        # Interrupt/DMA/Flash Registers (0xC8xx)
        # ============================================
        self.regs[0xC800] = 0x00  # Interrupt status
        self.regs[0xC802] = 0x00  # Interrupt status 2
        self.regs[0xC806] = 0x00  # System interrupt status
        self.regs[0xC80A] = 0x00  # PCIe/NVMe interrupt - bit 6 triggers PD debug
        self.regs[0xC8A9] = 0x00  # Flash CSR - not busy
        self.regs[0xC8AA] = 0x00  # Flash command
        self.regs[0xC8AB] = 0x00  # Flash address high
        self.regs[0xC8AC] = 0x00  # Flash address mid
        self.regs[0xC8AD] = 0x00  # Flash address low
        self.regs[0xC8AE] = 0x00  # Flash data register
        self.regs[0xC8B8] = 0x00  # Flash/DMA status
        self.regs[0xC8D6] = 0x04  # DMA status - done

        # ============================================
        # USB Power Delivery (PD) Registers (0xCAxx)
        # ============================================
        self.regs[0xCA00] = 0x00  # PD control
        self.regs[0xCA06] = 0x00  # PD status
        self.regs[0xCA0A] = 0x00  # PD interrupt control
        self.regs[0xCA0D] = 0x00  # PD interrupt status 1 - bit 3 = interrupt pending
        self.regs[0xCA0E] = 0x00  # PD interrupt status 2 - bit 2 = interrupt pending
        self.regs[0xCA81] = 0x00  # PD extended status

        # ============================================
        # Timer/CPU Control Registers (0xCCxx, 0xCDxx)
        # ============================================
        self.regs[0xCC11] = 0x00  # Timer 0 CSR
        self.regs[0xCC17] = 0x00  # Timer 1 CSR
        self.regs[0xCC1D] = 0x00  # Timer 2 CSR
        self.regs[0xCC23] = 0x00  # Timer 3 CSR
        self.regs[0xCC33] = 0x04  # CPU exec status
        self.regs[0xCC37] = 0x00  # CPU control
        self.regs[0xCC3B] = 0x00  # CPU control 2
        self.regs[0xCC3D] = 0x00  # CPU control 3
        self.regs[0xCC3E] = 0x00  # CPU control 4
        self.regs[0xCC3F] = 0x00  # CPU control 5
        self.regs[0xCC81] = 0x00  # Timer/DMA control
        self.regs[0xCC82] = 0x00  # Timer/DMA address low
        self.regs[0xCC83] = 0x00  # Timer/DMA address high
        self.regs[0xCC89] = 0x00  # Timer/DMA status - bit 1 = complete
        self.regs[0xCD31] = 0x01  # PHY init status - bit 0 = ready

        # ============================================
        # SCSI/DMA Registers (0xCExx)
        # ============================================
        self.regs[0xCE5D] = 0xFF  # Debug enable mask - all levels enabled
        self.regs[0xCE89] = 0x01  # SCSI DMA status - bit 0 = ready

        # NOTE: 0x707x addresses are NOT hardware registers!
        # They are flash buffer RAM (0x7000-0x7FFF) loaded from flash config.
        # Flash buffer is handled as regular XDATA, not MMIO.

        # ============================================
        # Debug/Command Engine Registers (0xE4xx)
        # ============================================
        self.regs[0xE40F] = 0x00  # PD event type (for debug output)
        self.regs[0xE410] = 0x00  # PD sub-event (for debug output)
        self.regs[0xE41C] = 0x00  # Command engine status

        # ============================================
        # System Status Registers (0xE7xx)
        # ============================================
        self.regs[0xE710] = 0x00  # System status
        self.regs[0xE712] = 0x00  # USB EP0 transfer status (bits 0,1 = complete)
        self.regs[0xE717] = 0x00  # System status 2
        self.regs[0xE751] = 0x00  # System status 3
        self.regs[0xE764] = 0x00  # System status 4
        self.regs[0xE795] = 0x21  # Flash ready + USB state 3 flag (bit 5)
        self.regs[0xE7E3] = 0x80  # PHY link ready

        # ============================================
        # PHY Completion / Debug Registers (0xE3xx)
        # ============================================
        self.regs[0xE302] = 0x40  # PHY completion status - bit 6 = complete

    def _setup_callbacks(self):
        """Setup read/write callbacks for hardware with special behavior."""
        # UART TX - capture output
        self.write_callbacks[0xC000] = self._uart_tx
        self.write_callbacks[0xC001] = self._uart_tx

        # PCIe status - complete after trigger
        self.read_callbacks[0xB296] = self._pcie_status_read
        self.write_callbacks[0xB254] = self._pcie_trigger_write

        # The synthetic stock-style queue engine is unavailable until firmware
        # executes the exact observed 83-byte setup tail in order.
        for setup_addr in (
                0xB264, 0xB265, 0xB266, 0xB267,
                0xB26C, 0xB26D, 0xB26E, 0xB26F,
                0xB250, 0xB251, 0xCEF0, 0xCEEF, 0xC807, 0xB281):
            self.write_callbacks[setup_addr] = self._stock_nvme_setup_write

        # PCIe DMA trigger - E4/E5 command DMA
        self.write_callbacks[0xB296] = self._pcie_dma_trigger

        # PCIe LTSSM State (0xB450) and control callbacks
        self.read_callbacks[0xB450] = self._pcie_ltssm_read
        self.write_callbacks[0xB450] = self._pcie_ltssm_write
        self.write_callbacks[0xB480] = self._pcie_perst_write
        self.write_callbacks[0xB403] = self._pcie_acdf_write
        self.write_callbacks[0xCA06] = self._pcie_acdf_write
        self.write_callbacks[0xCA81] = self._pcie_acdf_write
        self.write_callbacks[0xC656] = self._pcie_power_3v3_write
        self.write_callbacks[0xC65B] = self._pcie_power_companion_write
        self.write_callbacks[0xC659] = self._pcie_lane_gate_write
        self.write_callbacks[0xCC37] = self._pcie_cc37_write
        self.write_callbacks[0xE716] = self._pcie_e716_write
        self.write_callbacks[0xE764] = self._pcie_e764_write

        # Flash command configuration and transaction trigger.
        self.read_callbacks[0xC8A9] = self._flash_csr_read
        self.write_callbacks[0xC8A9] = self._flash_csr_write
        self.write_callbacks[0xC8AA] = self._flash_cmd_write

        # DMA status
        self.read_callbacks[0xC8D6] = self._dma_status_read

        # Flash/DMA busy - auto-clear
        self.read_callbacks[0xC8B8] = self._busy_reg_read

        # System interrupt status - clear on read
        self.read_callbacks[0xC806] = self._int_status_read

        # Stock NVMe Queue Engine Callbacks (CEF2, CEF3, B294)
        self.write_callbacks[0xCEF2] = self._cef2_write
        self.write_callbacks[0xCEF3] = self._cef3_write
        self.write_callbacks[0xB294] = self._b294_write

        # Timer CSRs
        for addr in [0xCC11, 0xCC17, 0xCC1D, 0xCC23, 0xCC5D]:
            self.read_callbacks[addr] = self._timer_csr_read
            self.write_callbacks[addr] = self._timer_csr_write

        # Unconditional controller-visible BDA4/DF86 prerequisite.  These
        # addresses have no other emulator side effects; CD31 and the timer
        # CSRs feed the same recorder from their existing callbacks.
        for addr in (0xC6A8, 0x92C8, 0xCC16, 0xCC18, 0xCC19,
                     0x92C4, 0x9201, 0xCC22, 0xCC1C, 0xCC1E, 0xCC1F,
                     0xCC5C, 0xCC5E, 0xCC5F):
            self.write_callbacks[addr] = self._usb_stock_prerequisite_write

        # Timer/DMA status register (0xCC89) - set complete bit after polling
        self.read_callbacks[0xCC89] = self._timer_dma_status_read

        # PHY init status - also handles descriptor DMA trigger on write
        self.read_callbacks[0xCD31] = self._phy_status_read
        self.write_callbacks[0xCD31] = self._phy_cmd_write

        # Command engine status
        self.read_callbacks[0xE41C] = self._cmd_engine_read

        # PD interrupt status - set by USB PD events
        self.read_callbacks[0xCA0D] = self._pd_interrupt_read
        self.read_callbacks[0xCA0E] = self._pd_interrupt_read

        # USB state machine MMIO registers (see registers.h for definitions)
        # REG_USB_DMA_STATE (0xCE89): USB/DMA status - controls state transitions
        #   USB_DMA_STATE_READY (bit 0): Must be set to exit wait loop (0x348C)
        #   USB_DMA_STATE_CBW (bit 1): 1=CBW received, 0=bulk data (0x3493)
        #   USB_DMA_STATE_ERROR (bit 2): DMA error in copy loop (0x3546)
        self.read_callbacks[0xCE89] = self._usb_ce89_read
        # REG_USB_DMA_ERROR (0xCE86): USB status - bit 4 checked at 0x349D
        self.read_callbacks[0xCE86] = self._usb_ce86_read
        # REG_XFER_STATUS_CE6C (0xCE6C): USB controller ready - bit 7 must be set
        self.read_callbacks[0xCE6C] = self._usb_ce6c_read
        # REG_SCSI_DMA_CTRL (0xCE00): DMA control register - returns 0 after completion
        # Firmware writes 0x03 to start DMA at 0x3531-0x3533, polls at 0x3534-0x3538
        self.read_callbacks[0xCE00] = self._usb_ce00_read
        self.write_callbacks[0xCE00] = self._usb_ce00_write
        # REG_SCSI_DMA_XFER_CNT (0xCE55): DMA transfer byte count after CE88/CE89 handshake
        # Read at 0x34B9 and stored to G_USB_WORK_009F as loop limit
        self.read_callbacks[0xCE55] = self._usb_ce55_read
        # REG_BULK_DMA_HANDSHAKE (0xCE88): DMA trigger - write resets state
        # At 0x1806: firmware writes to CE88 before polling REG_USB_DMA_STATE
        self.write_callbacks[0xCE88] = self._usb_ce88_write

        # USB setup/EP0 staging window (0x9E00-0x9FFF). Hardware writes the
        # eight-byte setup packet at its start; firmware reuses the complete
        # 512-byte window for control responses.
        for addr in range(0x9E00, 0xA000):
            self.read_callbacks[addr] = self._usb_ep0_buf_read
            self.write_callbacks[addr] = self._usb_ep0_buf_write

        # USB EP0 CSR (0x9E10)
        self.read_callbacks[0x9E10] = self._usb_ep0_csr_read
        self.write_callbacks[0x9E10] = self._usb_ep0_csr_write

        # USB EP data buffer (0xD800-0xDFFF) - endpoint data for bulk/control transfers
        for addr in range(0xD800, 0xE000):
            self.read_callbacks[addr] = self._usb_ep_data_buf_read
            self.write_callbacks[addr] = self._usb_ep_data_buf_write
        self.write_callbacks[0x90E1] = self._usb_sw_dma_trigger_write
        for addr in (0x9006, 0x905A, 0xC509, 0xC8D4):
            self.write_callbacks[addr] = self._usb_stock_buffered_sequence_write
        for addr in (0x9007, 0x9008, 0x9093, 0x9094):
            self.write_callbacks[addr] = self._usb_stock_bulk_in_arm_write

        # USB endpoint selection/status registers
        self.read_callbacks[0xC4EC] = self._usb_ep_status_read
        self.write_callbacks[0xC4ED] = self._usb_ep_index_write
        self.read_callbacks[0xC4EE] = self._usb_ep_id_low_read
        self.read_callbacks[0xC4EF] = self._usb_ep_id_high_read

        # USB endpoint data ready registers (0x90A1-0x90C0)
        # These indicate which endpoints have data available
        for addr in range(0x90A1, 0x90C1):
            self.read_callbacks[addr] = self._usb_ep_data_ready_read
        self.write_callbacks[0x90A1] = self._usb_sw_bulk_in_trigger_write
        self.write_callbacks[0x90B0] = self._usb_nvme_sequence_write
        # Stock dispatch_0206 per-slot NVMe-to-USB triggers.
        for addr in range(0x9137, 0x9157):
            self.write_callbacks[addr] = self._usb_nvme_bulk_in_trigger_write
        self.write_callbacks[0x90E3] = self._usb_bulk_ep_command_write

        # USB endpoint status registers (0x9096-0x90A0)
        # These control whether command handler path is taken (0 = process cmd)
        for addr in range(0x9096, 0x90A1):
            self.read_callbacks[addr] = self._usb_ep_status_reg_read
        self.write_callbacks[0x9096] = self._usb_ep_ready_ack_write

        # USB EP buffer address registers (0x905B/0x905C)
        # Firmware writes DMA source address here, hardware DMAs from this address
        self.write_callbacks[0x905B] = self._usb_ep_buf_addr_write
        self.write_callbacks[0x905C] = self._usb_ep_buf_addr_write

        # USB E5 value register (0xC47A)
        # The firmware clears this register (writes 0xFF) before reading it.
        # We need to preserve the injected value until it's read by the E5 handler.
        self.read_callbacks[0xC47A] = self._usb_e5_value_read
        self.write_callbacks[0xC47A] = self._usb_e5_value_write

        # MSC bulk-IN trigger. This observes bytes produced by firmware; it does
        # not synthesize SCSI responses.
        self.write_callbacks[0x900B] = self._usb_msc_sequence_write
        self.write_callbacks[0x901A] = self._usb_msc_sequence_write
        self.write_callbacks[0xC42A] = self._usb_msc_sequence_write
        self.write_callbacks[0xC42C] = self._usb_msc_trigger_write
        self.write_callbacks[0xC42D] = self._usb_msc_sequence_write

        # USB EP0 transfer status (0xE712)
        # The firmware polls this waiting for bits 0 and 1 to be set
        # indicating EP0 control transfer complete
        self.read_callbacks[0xE712] = self._usb_ep0_transfer_status_read

        # USB PHY control (0x91C0)
        # Firmware clears this at 0xCA8C but needs bit 1 SET for USB state machine
        # at 0x203B to progress from state 2 (0x0A59=2).
        self.read_callbacks[0x91C0] = self._usb_91c0_read

        # USB power state (0x92C2)
        # ISR at 0xE42A needs bit 6 CLEAR to call descriptor init (0xBDA4)
        # Main loop at 0x202A needs bit 6 SET to call 0x0322 for transfer
        # After ISR completes (2+ reads), return bit 6 SET
        self.read_callbacks[0x92C2] = self._usb_92c2_read

        # NOTE: 0xC001 is UART TX only, not USB EP0 FIFO. Testing confirmed that
        # firmware outputs debug messages to 0xC001 even during control transfer
        # handling. USB descriptor data is sent via hardware DMA directly from the
        # descriptor table in ROM (around 0x0864), not through firmware byte copies.
        # The exact DMA mechanism needs further investigation.

        # USB EP0 DMA control (0x9092) - may trigger hardware DMA
        # The actual source address for descriptor DMA is likely set via other registers
        self.write_callbacks[0x9092] = self._usb_ep0_dma_trigger_write
        self.read_callbacks[0x9092] = self._usb_ep0_dma_status_read

        # USB control state register (0x9091)
        # Two-phase control transfer handling:
        #   Bit 0: Setup phase - triggers 0xA5A6 (setup packet handler)
        #   Bit 1: Data phase - triggers 0xD088 (DMA for descriptor response)
        # Firmware loops writing 0x01 waiting for hardware to clear bit 0
        self.read_callbacks[0x9091] = self._usb_9091_read
        self.write_callbacks[0x9091] = self._usb_9091_write

        # USB endpoint status (0x9301)
        # Bit 6 triggers interrupt dispatch to device descriptor handler
        # Hardware clears bit 6 after read (acknowledge behavior)
        # Write of 0x40 (bit 6) arms EP0 for descriptor transfer
        self.read_callbacks[0x9301] = self._usb_9301_status_read
        self.write_callbacks[0x9301] = self._usb_9301_ep0_arm_write

        # Flash/Code ROM mirror region (0xE400-0xE700)
        # This XDATA region mirrors code ROM with offset 0xDDFC
        # Used for reading USB descriptors stored in code ROM
        # Examples:
        #   XDATA 0xE423 → Code ROM 0x0627 (device descriptor)
        #   XDATA 0xE437 → Code ROM 0x063B (language ID)
        #   XDATA 0xE6xx → Code ROM 0x08xx (additional descriptors)
        for addr in range(0xE400, 0xE700):
            self.read_callbacks[addr] = self._flash_rom_mirror_read

    # ============================================
    # Execution Tracing
    # ============================================
    def add_trace_point(self, pc: int, label: str):
        """
        Add a trace point at a specific PC address.

        When execution reaches this PC, the label will be logged.
        """
        self.trace_points[pc] = label

    def add_e4_trace_points(self):
        """
        Add trace points for E4 command processing.

        These cover the vendor handler and E4 read path.
        """
        self.trace_points.update({
            0x35B7: "VENDOR_HANDLER",
            0x35C0: "check_07EC",
            0x35C5: "call_17B1",
            0x35CB: "call_043F",
            0x35CF: "check_R7_after_043F",
            0x35D4: "call_1551",
            0x35DA: "E4_CHECK",
            0x35DF: "call_54BB",
            0x35E2: "setup_pcie_regs",
            0x35F9: "call_3C1E",
            0x35FC: "check_R7_after_3C1E",
            0x3601: "check_0AA0",
            0x360A: "setup_xfer",
            0x3649: "call_1741_cleanup",
            0x36E4: "vendor_exit",
            0x54BB: "E4_READ_HANDLER",
            0x3C1E: "pcie_transfer",
        })
        self.trace_enabled = True

    def check_trace(self, pc: int) -> str:
        """
        Check if PC matches a trace point and log if enabled.

        Returns the label if a trace point was hit, else None.
        """
        if not self.trace_enabled:
            return None

        if pc in self.trace_points:
            label = self.trace_points[pc]
            print(f"[{self.cycles:8d}] [TRACE] 0x{pc:04X}: {label}")

            # Call custom callback if registered
            if self.trace_callback:
                self.trace_callback(self, pc, label)

            return label
        return None

    # ============================================
    # XDATA Write Tracing
    # ============================================
    def add_xdata_trace(self, addr: int, name: str):
        """
        Add a trace point for XDATA writes.

        When firmware writes to this address, it will be logged.
        """
        self.xdata_trace_addrs[addr] = name

    def add_vendor_xdata_traces(self):
        """
        Add trace points for vendor command related XDATA addresses.

        These cover the key RAM locations used in E4/E5 command processing.
        Names match definitions in globals.h.
        """
        self.xdata_trace_addrs.update({
            0x0002: "G_IO_CMD_STATE",           # CDB[0]
            0x0003: "G_EP_STATUS_CTRL",         # Vendor flag
            0x0004: "G_WORK_0004",              # CDB[2]
            0x05A3: "G_CMD_SLOT_INDEX",         # Current command slot (0-9)
            0x05A5: "G_CMD_INDEX_SRC",          # Command index source
            0x05B1: "G_CMD_TABLE_BASE[0]",      # Command table entry 0
            0x05B2: "G_CMD_TABLE_BASE[0]+1",    # Command table entry 0, byte 1
            0x05B3: "G_CMD_TABLE_BASE[0]+2",    # Command table entry 0, byte 2
            0x05D3: "G_CMD_TABLE_BASE[1]",      # Command table entry 1
            0x07EC: "G_USB_CMD_CONFIG",         # USB command configuration
            0x0AA0: "G_DMA_XFER_STATUS",        # DMA transfer status
        })
        # Also trace command table range (10 entries x 34 bytes each)
        for i in range(10):
            base = 0x05B1 + i * 0x22  # G_CMD_TABLE_ENTRY_SIZE = 0x22
            if base not in self.xdata_trace_addrs:
                self.xdata_trace_addrs[base] = f"G_CMD_TABLE_BASE[{i}]"
        self.xdata_trace_enabled = True

    def trace_xdata_write(self, addr: int, value: int, pc: int = 0):
        """
        Log an XDATA write if tracing is enabled for this address.

        Called by memory system write hooks.
        """
        if not self.xdata_trace_enabled:
            return

        if addr in self.xdata_trace_addrs:
            name = self.xdata_trace_addrs[addr]
            entry = f"[{self.cycles:8d}] [PC=0x{pc:04X}] WRITE {name} (0x{addr:04X}) = 0x{value:02X}"
            self.xdata_write_log.append(entry)
            print(entry)
        elif 0x05B1 <= addr < 0x05B1 + 0x22 * 10:
            # Command table range
            idx = addr - 0x05B1
            entry_num = idx // 0x22
            offset = idx % 0x22
            entry = f"[{self.cycles:8d}] [PC=0x{pc:04X}] WRITE CMD_TABLE[{entry_num}]+{offset} (0x{addr:04X}) = 0x{value:02X}"
            self.xdata_write_log.append(entry)
            print(entry)

    def print_xdata_trace_log(self):
        """Print the accumulated XDATA write log."""
        print("\n=== XDATA WRITE LOG ===")
        for entry in self.xdata_write_log:
            print(entry)

    # ============================================
    # UART Callbacks
    # ============================================
    def _uart_tx(self, hw: 'HardwareState', addr: int, value: int):
        """Handle UART transmit with message buffering.

        NOTE: 0xC001 was previously thought to be shared with USB EP0 FIFO,
        but testing shows it's UART-only. The firmware outputs debug messages
        like "[InternalPD_StateInit]" to 0xC001 even during USB control transfer
        handling. The actual USB EP0 descriptor data is sent via hardware DMA
        directly from the descriptor table in ROM (around 0x0864), not through
        firmware-driven byte copying to 0xC001.
        """
        if self.log_uart:
            if value == 0x0A:  # Newline - print buffered line
                if self.uart_buffer:
                    print(f"[{self.cycles:8d}] [UART] {self.uart_buffer}")
                    self.uart_buffer = ""
            elif value == 0x0D:  # Carriage return - ignore
                pass
            elif 0x20 <= value < 0x7F:  # Printable ASCII
                self.uart_buffer += chr(value)
                # Flush on ']' to show complete [message] blocks
                if chr(value) == ']':
                    print(f"[{self.cycles:8d}] [UART] {self.uart_buffer}")
                    self.uart_buffer = ""
            # For very long lines, flush periodically
            if len(self.uart_buffer) > 200:
                print(f"[{self.cycles:8d}] [UART] {self.uart_buffer}")
                self.uart_buffer = ""
        else:
            try:
                if 0x20 <= value < 0x7F or value in (0x0A, 0x0D):
                    print(chr(value), end='', flush=True)
            except:
                pass

    # ============================================
    # PCIe Callbacks
    # ============================================
    def _pcie_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """PCIe status read at 0xB296."""
        return self.regs.get(addr, 0x00)

    def _pcie_trigger_write(self, hw: 'HardwareState', addr: int, value: int):
        """Execute one programmed-I/O TLP or stock queue trigger."""
        self.regs[addr] = value
        self.stock_b254_trigger_log.append(value)
        if value in (0x01, 0x11):
            if not self.stock_nvme_queue_enabled:
                self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x01
            elif value == 0x01:
                self._stock_nvme_sq_doorbell()
            else:
                self._stock_nvme_cq_doorbell()
            return

        if value in (0x02, 0x12):
            if not self.stock_nvme_queue_enabled:
                self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x01
            elif value == 0x02:
                self._stock_nvme_io_sq_doorbell()
            else:
                self._stock_nvme_io_cq_doorbell()
            return

        fmt = self.regs.get(0xB210, 0)
        tlp_ctrl = self.regs.get(0xB213, 0)
        tlp_length = self.regs.get(0xB216, 0)
        byte_en = self.regs.get(0xB217, 0)
        b218 = self.regs.get(0xB218, 0)
        b219 = self.regs.get(0xB219, 0)
        target = ((b218 << 24) |
                  (b219 << 16) |
                  (self.regs.get(0xB21A, 0) << 8) |
                  self.regs.get(0xB21B, 0))

        record = {
            "fmt": fmt,
            "tlp_ctrl": tlp_ctrl,
            "tlp_length": tlp_length,
            "byte_en": byte_en,
            "target": target,
            "b218": b218,
            "b219": b219,
            "value": value,
        }
        self.pcie_pio_history.append(record)

        # Fault injection hooks
        if self.pcie_pio_fault_timeout:
            return

        if self.pcie_pio_b296_error:
            self.regs[0xB296] = 0x05  # bit 0 (ERROR) + bit 2 (ENGINE_DONE)
            self.regs[0xB22A] = 0x20
            return

        # Configuration Space Operations:
        # CfgRd0 (0x04), CfgWr0 (0x44), CfgRd1 (0x05), CfgWr1 (0x45)
        if fmt in (0x04, 0x05, 0x44, 0x45):
            valid_cfg = (tlp_ctrl == 1 and tlp_length == 0x20)
            if not valid_cfg:
                self.regs[0xB22A] = 0x20
                self.regs[0xB22B] = 0x00
                self.regs[0xB22C] = 0x00
                self.regs[0xB22D] = 0x00
                self.regs[0xB296] = 0x05  # ERROR + DONE
                return

            reg_dw = ((self.regs.get(0xB21A, 0) << 8) | self.regs.get(0xB21B, 0)) >> 2
            self.pcie_cfg_history.append({
                "fmt": fmt,
                "bus": b218,
                "devfn": b219,
                "reg_dw": reg_dw,
                "byte_en": byte_en,
            })

            # Type 0 (Bridge root port)
            if fmt in (0x04, 0x44):
                if b218 != self.pcie_bridge_primary_bus or b219 != 0:
                    self.regs[0xB22A] = 0x20  # UR
                    self.regs[0xB22B] = self.pcie_pio_cpl_fmt
                    self.regs[0xB22C] = 0x00
                    self.regs[0xB22D] = 0x00
                    self.regs[0xB284] = 1 if fmt == 0x04 else 0
                    self.regs[0xB296] = 0x04 if self.pcie_pio_cpl_timeout else 0x06
                    return

                if fmt == 0x04:  # CfgRd0
                    data = 0
                    if reg_dw == 0:
                        data = 0x2464174C
                    elif reg_dw == 1:
                        data = (self.pcie_bridge_status << 16) | (self.pcie_bridge_command & 0xFFFF)
                    elif reg_dw == 6:
                        data = (self.pcie_bridge_sec_latency << 24) | (self.pcie_bridge_subordinate_bus << 16) | (self.pcie_bridge_secondary_bus << 8) | self.pcie_bridge_primary_bus
                    elif reg_dw == 8:
                        data = (self.pcie_bridge_raw_mem_limit << 16) | (self.pcie_bridge_raw_mem_base & 0xFFFF)
                    self.regs[0xB220] = (data >> 24) & 0xFF
                    self.regs[0xB221] = (data >> 16) & 0xFF
                    self.regs[0xB222] = (data >> 8) & 0xFF
                    self.regs[0xB223] = data & 0xFF
                    self.regs[0xB22A] = self.pcie_pio_cpl_status
                    self.regs[0xB22B] = self.pcie_pio_cpl_fmt
                    self.regs[0xB22C] = self.pcie_pio_cpl_dw0
                    self.regs[0xB22D] = self.pcie_pio_cpl_dw1
                    self.regs[0xB284] = 1
                    self.regs[0xB296] = 0x04 if self.pcie_pio_cpl_timeout else 0x06
                    return
                else:  # CfgWr0
                    write_succeeded = (
                        self.pcie_pio_cpl_status == 0x00 and
                        not self.pcie_pio_cpl_timeout and
                        not self.pcie_pio_b296_error
                    )
                    if write_succeeded:
                        self.pcie_bridge_configured = True
                    val = ((self.regs.get(0xB220, 0) << 24) |
                           (self.regs.get(0xB221, 0) << 16) |
                           (self.regs.get(0xB222, 0) << 8) |
                           self.regs.get(0xB223, 0))
                    if write_succeeded:
                        if reg_dw == 1:
                            self.pcie_bridge_command = val & 0xFFFF
                        elif reg_dw == 6:
                            self.pcie_bridge_primary_bus = val & 0xFF
                            self.pcie_bridge_secondary_bus = (val >> 8) & 0xFF
                            self.pcie_bridge_subordinate_bus = (val >> 16) & 0xFF
                        elif reg_dw == 8:
                            self.pcie_bridge_raw_mem_base = val & 0xFFFF
                            self.pcie_bridge_raw_mem_limit = (val >> 16) & 0xFFFF
                            self.pcie_bridge_mem_base = (val & 0xFFF0) << 16
                            self.pcie_bridge_mem_limit = (((val >> 16) & 0xFFF0) << 16) | 0xFFFFF
                    self.regs[0xB22A] = self.pcie_pio_cpl_status
                    self.regs[0xB22B] = self.pcie_pio_cpl_fmt
                    self.regs[0xB22C] = self.pcie_pio_cpl_dw0
                    self.regs[0xB22D] = self.pcie_pio_cpl_dw1
                    self.regs[0xB284] = 0
                    self.regs[0xB296] = 0x04 if self.pcie_pio_cpl_timeout else 0x06
                    return

            # Type 1 (Endpoint on secondary bus)
            else:
                is_match = (
                    self.pcie_endpoint_present and
                    b218 == self.pcie_bridge_secondary_bus and
                    b219 == self.pcie_endpoint_devfn
                )
                if not is_match:
                    self.regs[0xB22A] = 0x20  # UR status
                    self.regs[0xB22B] = self.pcie_pio_cpl_fmt
                    self.regs[0xB22C] = 0x00
                    self.regs[0xB22D] = 0x00
                    self.regs[0xB284] = 1 if fmt == 0x05 else 0
                    self.regs[0xB296] = 0x04 if self.pcie_pio_cpl_timeout else 0x06
                    return

                if fmt == 0x05:  # CfgRd1
                    data = 0
                    if reg_dw == 0:
                        data = (self.pcie_endpoint_device_id << 16) | self.pcie_endpoint_vendor_id
                    elif reg_dw == 1:
                        data = (self.pcie_endpoint_status << 16) | (self.pcie_endpoint_command & 0xFFFF)
                    elif reg_dw == 2:
                        data = (self.pcie_endpoint_class_code << 8) | (self.pcie_endpoint_rev_id & 0xFF)
                    elif reg_dw == 4:  # BAR0
                        if self.pcie_endpoint_bar0_probed:
                            mask = (~(self.pcie_endpoint_bar0_size - 1)) & 0xFFFFFFFF
                            if self.pcie_endpoint_bar0_is_io:
                                mask |= 0x01
                            if self.pcie_endpoint_bar0_is_prefetchable:
                                mask |= 0x08
                            if self.pcie_endpoint_bar0_is_64bit:
                                mask |= 0x04
                            data = mask
                        else:
                            data = self.pcie_endpoint_bar0
                    elif 5 <= reg_dw <= 9:
                        data = self.pcie_endpoint_bars[reg_dw - 5]
                    elif reg_dw == 13:  # CapPtr (0x34)
                        data = self.pcie_endpoint_capptr & 0xFF
                    elif reg_dw == (self.pcie_endpoint_capptr >> 2):
                        data = ((self.pcie_endpoint_next_cap & 0xFF) << 8) | (self.pcie_endpoint_cap_id & 0xFF)
                    elif reg_dw == (self.pcie_endpoint_capptr >> 2) + 2:
                        data = self.pcie_endpoint_devctl
                    self.regs[0xB220] = (data >> 24) & 0xFF
                    self.regs[0xB221] = (data >> 16) & 0xFF
                    self.regs[0xB222] = (data >> 8) & 0xFF
                    self.regs[0xB223] = data & 0xFF
                    self.regs[0xB22A] = self.pcie_pio_cpl_status
                    self.regs[0xB22B] = self.pcie_pio_cpl_fmt
                    self.regs[0xB22C] = self.pcie_pio_cpl_dw0
                    self.regs[0xB22D] = self.pcie_pio_cpl_dw1
                    self.regs[0xB284] = 1
                    self.regs[0xB296] = 0x04 if self.pcie_pio_cpl_timeout else 0x06
                    return
                else:  # CfgWr1
                    val = ((self.regs.get(0xB220, 0) << 24) |
                           (self.regs.get(0xB221, 0) << 16) |
                           (self.regs.get(0xB222, 0) << 8) |
                           self.regs.get(0xB223, 0))
                    write_succeeded = (
                        self.pcie_pio_cpl_status == 0x00 and
                        not self.pcie_pio_cpl_timeout and
                        not self.pcie_pio_b296_error
                    )
                    if write_succeeded:
                        if reg_dw == 1:
                            self.pcie_endpoint_command = val & 0xFFFF
                        elif reg_dw == 4:
                            if val == 0xFFFFFFFF:
                                self.pcie_endpoint_bar0_probed = True
                            else:
                                self.pcie_endpoint_bar0 = val
                                self.pcie_endpoint_bar0_probed = False
                        elif 5 <= reg_dw <= 9:
                            self.pcie_endpoint_bars[reg_dw - 5] = val
                        elif reg_dw == (self.pcie_endpoint_capptr >> 2) + 2:
                            self.pcie_endpoint_devctl = val
                    self.regs[0xB22A] = self.pcie_pio_cpl_status
                    self.regs[0xB22B] = self.pcie_pio_cpl_fmt
                    self.regs[0xB22C] = self.pcie_pio_cpl_dw0
                    self.regs[0xB22D] = self.pcie_pio_cpl_dw1
                    self.regs[0xB284] = 0
                    self.regs[0xB296] = 0x04 if self.pcie_pio_cpl_timeout else 0x06
                    return

        # Contract enforcement:
        # Stock 3DW MRd (0x00) vs 3DW MWr (0x40).
        # Requires B213=1, B216=0x20, B217=0x0F, B218=0x00, B219=0xD0.
        valid_3dw = (
            tlp_ctrl == 1 and
            tlp_length == 0x20 and
            byte_en == 0x0F
        )

        if valid_3dw and (self.pcie_routing_gate_enforced or getattr(self, "pcie_bridge_configured", False)):
            bridge_mse = bool(self.pcie_bridge_command & 0x02)
            bridge_window_valid = (self.pcie_bridge_mem_base <= target <= self.pcie_bridge_mem_limit)
            endpoint_mse = bool(self.pcie_endpoint_command & 0x02)
            bar0_mask = (~(self.pcie_endpoint_bar0_size - 1)) & 0xFFFFFFFF
            bar0_match = (
                self.pcie_endpoint_bar0 != 0 and
                ((target & bar0_mask) == (self.pcie_endpoint_bar0 & bar0_mask))
            )
            if not (bridge_mse and bridge_window_valid and endpoint_mse and bar0_match):
                # Routing gate failed: return Unsupported Request (UR) completion
                self.regs[0xB22A] = 0x20  # UR status
                self.regs[0xB22B] = 0x04 if fmt == 0x00 else 0x00
                self.regs[0xB22C] = 0x00
                self.regs[0xB22D] = 0x00
                self.regs[0xB284] = 1 if fmt == 0x00 else 0
                if self.pcie_pio_cpl_timeout:
                    self.regs[0xB296] = 0x04
                else:
                    self.regs[0xB296] = 0x06
                return

        if fmt == 0x00 and valid_3dw:
            result = self._nvme_mmio_read32(target)
            if not self.pcie_pio_stale_data:
                self.regs[0xB220] = (result >> 24) & 0xFF
                self.regs[0xB221] = (result >> 16) & 0xFF
                self.regs[0xB222] = (result >> 8) & 0xFF
                self.regs[0xB223] = result & 0xFF
            self.regs[0xB22A] = self.pcie_pio_cpl_status
            self.regs[0xB22B] = self.pcie_pio_cpl_fmt
            self.regs[0xB22C] = self.pcie_pio_cpl_dw0
            self.regs[0xB22D] = self.pcie_pio_cpl_dw1
            self.regs[0xB284] = 1
            if self.pcie_pio_cpl_timeout:
                self.regs[0xB296] = 0x04  # ENGINE_DONE without VALID_CPL
            else:
                self.regs[0xB296] = 0x06  # bit 1 (VALID_CPL) + bit 2 (ENGINE_DONE)
        elif fmt == 0x40 and valid_3dw:
            data = ((self.regs.get(0xB220, 0) << 24) |
                    (self.regs.get(0xB221, 0) << 16) |
                    (self.regs.get(0xB222, 0) << 8) |
                    self.regs.get(0xB223, 0))
            self._nvme_mmio_write32(target, data)
            self.regs[0xB22A] = self.pcie_pio_cpl_status
            self.regs[0xB22B] = 0x00
            self.regs[0xB22C] = self.pcie_pio_cpl_dw0
            self.regs[0xB22D] = self.pcie_pio_cpl_dw1
            self.regs[0xB284] = 0
            self.regs[0xB296] = 0x04  # bit 2 (ENGINE_DONE)
        elif self.pcie_allow_legacy_4dw and fmt in (0x20, 0x60):
            # Legacy 4DW compatibility mode when explicitly allowed
            if fmt == 0x20:
                result = self._nvme_mmio_read32(target)
                self.regs[0xB220] = (result >> 24) & 0xFF
                self.regs[0xB221] = (result >> 16) & 0xFF
                self.regs[0xB222] = (result >> 8) & 0xFF
                self.regs[0xB223] = result & 0xFF
                self.regs[0xB22A], self.regs[0xB22B] = 0x00, 0x04
                self.regs[0xB22C], self.regs[0xB22D] = 0x00, 0x00
                self.regs[0xB284] = 1
                self.regs[0xB296] = 0x06
            else:
                data = ((self.regs.get(0xB220, 0) << 24) |
                        (self.regs.get(0xB221, 0) << 16) |
                        (self.regs.get(0xB222, 0) << 8) |
                        self.regs.get(0xB223, 0))
                self._nvme_mmio_write32(target, data)
                self.regs[0xB22A], self.regs[0xB22B] = 0x00, 0x00
                self.regs[0xB22C], self.regs[0xB22D] = 0x00, 0x00
                self.regs[0xB284] = 0
                self.regs[0xB296] = 0x04
        else:
            # Contract violation: malformed 4DW, wrong length, missing prerequisites
            self.regs[0xB22A] = 0x20  # UR status
            self.regs[0xB22B] = 0x00
            self.regs[0xB22C] = 0x00
            self.regs[0xB22D] = 0x00
            self.regs[0xB296] = 0x05  # bit 0 (ERROR) + bit 2 (ENGINE_DONE)

    def reset_pcie_configuration(self):
        """Reset all PCIe bridge and endpoint configuration state to power-on defaults."""
        self.pcie_bridge_primary_bus = 0
        self.pcie_bridge_secondary_bus = 0
        self.pcie_bridge_subordinate_bus = 0
        self.pcie_bridge_command = 0
        self.pcie_bridge_status = 0x0010
        self.pcie_bridge_sec_latency = 0
        self.pcie_bridge_raw_mem_base = 0
        self.pcie_bridge_raw_mem_limit = 0
        self.pcie_bridge_mem_base = 0
        self.pcie_bridge_mem_limit = 0
        self.pcie_bridge_configured = False

        self.pcie_endpoint_present = True
        self.pcie_endpoint_devfn = 0x00
        self.pcie_endpoint_command = 0
        self.pcie_endpoint_bar0 = 0
        self.pcie_endpoint_bar0_probed = False
        self.pcie_endpoint_bar0_size = 0x01000000
        self.pcie_endpoint_bar0_is_io = False
        self.pcie_endpoint_bar0_is_prefetchable = False
        self.pcie_endpoint_bar0_is_64bit = False
        self.pcie_endpoint_bars = [0] * 5
        self.pcie_endpoint_capptr = 0x40
        self.pcie_endpoint_cap_id = 0x10
        self.pcie_endpoint_next_cap = 0x00
        self.pcie_endpoint_devctl = 0
        self.pcie_cfg_history.clear()

    # ============================================
    # PCIe LTSSM Stage-Specific Model Callbacks
    # ============================================
    def check_detect_quiet_to_active(self) -> dict:
        """
        Prerequisites for LTSSM transition: Detect.Quiet (0x00) -> Detect.Active (0x01).

        Physical / Stock Derivation:
        - Downstream device physically present (receiver termination present).
        - 3.3V power rail enabled (0xC656 bit 5) [Stock: 0x52FF cold power rail].
        - Downstream lane/power gate enabled (0xC659 bit 0).
        - Downstream reset released (0xB480 bit 0 set). Stock C24C/CC83 sets
          this operational bit; the clear at 0x365F follows NVMe shutdown.
        """
        device_present = self.pcie_device_present
        p_3v3 = bool(self.regs.get(0xC656, 0) & 0x20)
        lane_gate = bool(self.regs.get(0xC659, 0) & 0x01)
        perst_deasserted = bool(self.regs.get(0xB480, 0) & 0x01)
        ready = bool(device_present and p_3v3 and lane_gate and perst_deasserted)
        return {
            "ready": ready,
            "device_present": device_present,
            "power_3v3": p_3v3,
            "lane_gate": lane_gate,
            "perst_deasserted": perst_deasserted,
        }

    def check_detect_active_to_polling(self) -> dict:
        """
        Prerequisites for LTSSM transition: Detect.Active (0x01) -> Polling (0x10).

        Physical / Stock Derivation:
        - Detect.Quiet -> Detect.Active prerequisites satisfied.
        - Stock's bank-1 ECC3 RXPLL reset asserted CC37.2, observed the
          E712/CC11 completion poll, and deasserted CC37.2.
        - Stock CB6D/E53A RXPLL transaction completed with the exact E764
          low-nibble history 8,8,8,A,B,9. A literal 1C is rejected.
        - Tunnel stability bit set (0xB403 bit 0) [Stock: B403 bridge stability].
        - Tunnel link state cleared (0xB430 == 0x00) [Stock: 0xCCBF].
        - ACDF/C7A4 selected the native Gen3-x2 B434 lane mask 0x0C.
        """
        stage1 = self.check_detect_quiet_to_active()
        e764_training = bool(
            self.pcie_rxpll_reset_complete and
            self.pcie_rxpll_training_complete and
            (self.regs.get(0xE764, 0) & 0x0F) == 0x09
        )
        tunnel_ctrl_b403 = bool(self.regs.get(0xB403, 0) & 0x01)
        tunnel_link_state_b430 = (self.regs.get(0xB430, 0) == 0x00)
        native_x2_lane_mask = (self.regs.get(0xB434, 0) & 0x0F) == 0x0C
        ready = bool(stage1["ready"] and e764_training and tunnel_ctrl_b403 and
                     tunnel_link_state_b430 and native_x2_lane_mask)
        return {
            "ready": ready,
            "stage1": stage1["ready"],
            "rxpll_reset_complete": self.pcie_rxpll_reset_complete,
            "e764_training": e764_training,
            "tunnel_ctrl_b403": tunnel_ctrl_b403,
            "tunnel_link_state_b430": tunnel_link_state_b430,
            "native_x2_lane_mask": native_x2_lane_mask,
        }

    def check_polling_to_l0(self) -> dict:
        """
        Prerequisites for LTSSM transition: Polling (0x10) -> L0 (0x78).

        Physical / Stock Derivation:
        - Detect.Active -> Polling prerequisites satisfied.
        - PHY TLP routing enabled (DPX 0x6025 bit 7) [Stock: 0x6025.7].
        - RXPHY lane tuning committed on all lanes (DPX 0x78AF/79AF/7AAF/7BAF != 0) [Stock: RXPHY lane commits].
        - Native predecessor released PHY isolation (DPX 0x7041 bit 6 clear)
          and armed DPX 0x1507 bits 2 then 1.
        - CPU mode and control configured (0xCA81 != 0, 0xCA06 != 0) [Stock: CA81/CA06].
        """
        stage2 = self.check_detect_active_to_polling()
        phy_tlp_routing = bool(self.dpx_regs.get(0x6025, 0) & 0x80)
        stock_gen3_selection = (self.dpx_regs.get(0x40B0, 0) & 0x0F) == 0x03
        rxphy_commits = bool(
            self.dpx_regs.get(0x78AF, 0) != 0 and
            self.dpx_regs.get(0x79AF, 0) != 0 and
            self.dpx_regs.get(0x7AAF, 0) != 0 and
            self.dpx_regs.get(0x7BAF, 0) != 0
        )
        phy_isolation_released = not bool(self.dpx_regs.get(0x7041, 0) & 0x40)
        native_link_event_armed = (self.dpx_regs.get(0x1507, 0) & 0x06) == 0x06
        ca81_ready = bool(self.regs.get(0xCA81, 0) != 0)
        ca06_ready = bool(self.regs.get(0xCA06, 0) != 0)
        ready = bool(stage2["ready"] and phy_tlp_routing and
                     stock_gen3_selection and rxphy_commits and
                     phy_isolation_released and native_link_event_armed and
                     ca81_ready and ca06_ready)
        return {
            "ready": ready,
            "stage2": stage2["ready"],
            "phy_tlp_routing": phy_tlp_routing,
            "stock_gen3_selection": stock_gen3_selection,
            "rxphy_commits": rxphy_commits,
            "phy_isolation_released": phy_isolation_released,
            "native_link_event_armed": native_link_event_armed,
            "ca81_ready": ca81_ready,
            "ca06_ready": ca06_ready,
        }

    def check_pcie_prerequisites(self) -> dict:
        """Combined prerequisite check for full L0 link readiness."""
        s1 = self.check_detect_quiet_to_active()
        s2 = self.check_detect_active_to_polling()
        s3 = self.check_polling_to_l0()
        return {
            "all_met": s3["ready"],
            "stage1_detect_active": s1["ready"],
            "stage2_polling": s2["ready"],
            "stage3_l0": s3["ready"],
            "device_present": s1["device_present"],
            "power_3v3": s1["power_3v3"],
            "lane_gate": s1["lane_gate"],
            "perst_deasserted": s1["perst_deasserted"],
            "e764_training": s2["e764_training"],
            "tunnel_ctrl_b403": s2["tunnel_ctrl_b403"],
            "tunnel_link_state_b430": s2["tunnel_link_state_b430"],
            "native_x2_lane_mask": s2["native_x2_lane_mask"],
            "phy_tlp_routing": s3["phy_tlp_routing"],
            "stock_gen3_selection": s3["stock_gen3_selection"],
            "rxphy_commits": s3["rxphy_commits"],
            "phy_isolation_released": s3["phy_isolation_released"],
            "native_link_event_armed": s3["native_link_event_armed"],
            "ca81_ready": s3["ca81_ready"],
            "ca06_ready": s3["ca06_ready"],
        }

    def _pcie_ltssm_read(self, hw: 'HardwareState', addr: int) -> int:
        """PCIe LTSSM State (0xB450) read callback (hardware status register)."""
        if self.pcie_ltssm_forced is not None:
            self.regs[0xB450] = self.pcie_ltssm_forced
            return self.pcie_ltssm_forced

        stage1 = self.check_detect_quiet_to_active()
        stage2 = self.check_detect_active_to_polling()
        stage3 = self.check_polling_to_l0()

        if not stage1["ready"]:
            if self.pcie_ltssm_state != 0x00:
                self.pcie_ltssm_state = 0x00
                self.pcie_ltssm_step = 0
                self.pcie_ltssm_history.append(0x00)
                self.regs[0xE765] = self.regs.get(0xE765, 0) & ~0x02
                self.dpx_regs[0x6020] = 0x00
            self.regs[0xB450] = 0x00
            return 0x00

        if not stage2["ready"]:
            # Reaches Detect.Active (0x01)
            if self.pcie_ltssm_state != 0x01:
                self.pcie_ltssm_state = 0x01
                self.pcie_ltssm_step = 1
                self.pcie_ltssm_history.append(0x01)
                self.regs[0xE765] = self.regs.get(0xE765, 0) & ~0x02
                self.dpx_regs[0x6020] = 0x00
            self.regs[0xB450] = 0x01
            return 0x01

        if not stage3["ready"]:
            # Reaches Polling.Active (0x10)
            if self.pcie_ltssm_state != 0x10:
                if self.pcie_ltssm_step == 0:
                    self.pcie_ltssm_history.append(0x01)
                self.pcie_ltssm_state = 0x10
                self.pcie_ltssm_step = 2
                self.pcie_ltssm_history.append(0x10)
                self.regs[0xE765] = self.regs.get(0xE765, 0) & ~0x02
                self.dpx_regs[0x6020] = 0x00
            self.regs[0xB450] = 0x10
            return 0x10

        # All stages ready: advance step-by-step to L0 (0x78)
        if self.pcie_ltssm_auto_advance:
            if self.pcie_ltssm_step == 0:
                self.pcie_ltssm_state = 0x01
                self.pcie_ltssm_step = 1
                self.pcie_ltssm_history.append(0x01)
            elif self.pcie_ltssm_step == 1:
                self.pcie_ltssm_state = 0x10
                self.pcie_ltssm_step = 2
                self.pcie_ltssm_history.append(0x10)
            elif self.pcie_ltssm_step == 2:
                self.pcie_ltssm_state = 0x78
                self.pcie_ltssm_step = 3
                self.pcie_ltssm_history.append(0x78)
                self.regs[0xE765] = self.regs.get(0xE765, 0) | 0x02
                self.dpx_regs[0x6020] = 0x24
        else:
            self.pcie_ltssm_state = 0x78
            self.regs[0xE765] = self.regs.get(0xE765, 0) | 0x02
            self.dpx_regs[0x6020] = 0x24

        self.regs[0xB450] = self.pcie_ltssm_state
        return self.pcie_ltssm_state

    def _pcie_ltssm_write(self, hw: 'HardwareState', addr: int, value: int):
        """Firmware write to 0xB450 is ignored; register is hardware-read-only."""
        pass

    def force_ltssm_state(self, value: Optional[int]):
        """Explicit emulator-only helper to force LTSSM state for legacy tests."""
        self.pcie_ltssm_forced = value
        if value is not None:
            self.pcie_ltssm_state = value
            self.regs[0xB450] = value
            if value == 0x78:
                self.pcie_ltssm_step = 3
                self.regs[0xE765] = self.regs.get(0xE765, 0) | 0x02
                self.dpx_regs[0x6020] = 0x24
            elif value == 0x00:
                self.pcie_ltssm_step = 0
                self.regs[0xE765] = self.regs.get(0xE765, 0) & ~0x02
                self.dpx_regs[0x6020] = 0x00
            self.pcie_ltssm_history.append(value)
        else:
            self.pcie_ltssm_step = 0

    def trigger_pcie_link_loss(self):
        """Simulate downstream PCIe link loss."""
        self.pcie_ltssm_forced = None
        self.pcie_ltssm_state = 0x00
        self.pcie_ltssm_step = 0
        self.regs[0xB450] = 0x00
        self.regs[0xE765] = self.regs.get(0xE765, 0) & ~0x02
        self.dpx_regs[0x6020] = 0x00
        self.pcie_ltssm_history.append(0x00)
        self.reset_pcie_configuration()

    def _pcie_perst_write(self, hw: 'HardwareState', addr: int, value: int):
        self.regs[addr] = value
        if value & 0x01:
            self._pcie_acdf_event("perst_release")
        else:  # downstream reset held / link unavailable
            self.pcie_acdf_history.clear()
            self.pcie_acdf_order_complete = False
            if self.pcie_ltssm_state != 0x00:
                self.trigger_pcie_link_loss()

    def _pcie_acdf_event(self, event: str):
        self.pcie_acdf_history.append(event)
        self.pcie_acdf_history = self.pcie_acdf_history[-12:]
        required = ["perst_release", "ca81_clear", "ca06_20",
                    "b403_set", "40b0_03"]
        self.pcie_acdf_order_complete = (
            self.pcie_acdf_history[-len(required):] == required)

    def _pcie_acdf_write(self, hw: 'HardwareState', addr: int, value: int):
        self.regs[addr] = value
        if not self.pcie_acdf_history:
            return
        if addr == 0xCA81:
            self._pcie_acdf_event("ca81_clear" if not (value & 0x01)
                                  else "ca81_other")
        elif addr == 0xCA06:
            self._pcie_acdf_event("ca06_20" if (value & 0xE0) == 0x20
                                  else "ca06_other")
        elif addr == 0xB403:
            self._pcie_acdf_event("b403_set" if value & 0x01
                                  else "b403_clear")

    def _pcie_power_3v3_write(self, hw: 'HardwareState', addr: int, value: int):
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        if (old & 0x20) and not (value & 0x20):
            self.pcie_link_recovery_required = True
            self.pcie_link_recovery_complete = False
            self.pcie_link_recovery_history = ["rail_off"]
        elif not (old & 0x20) and (value & 0x20) and \
                self.pcie_link_recovery_required:
            self._pcie_link_recovery_event("rail_3v3_on")
        if not (value & 0x20):  # 3.3V power disabled
            if self.pcie_ltssm_state != 0x00:
                self.trigger_pcie_link_loss()

    def _pcie_power_companion_write(self, hw: 'HardwareState', addr: int,
                                    value: int):
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        if not (old & 0x20) and (value & 0x20) and \
                self.pcie_link_recovery_required:
            self._pcie_link_recovery_event("rail_companion_on")

    def _pcie_link_recovery_event(self, event: str):
        """Require stock's off-C24C / paired-rail-on-C24C generations."""
        if not self.pcie_link_recovery_required:
            return
        if event == "c24c":
            if self.pcie_link_recovery_history == ["rail_off"]:
                event = "c24c_off"
            elif self.pcie_link_recovery_history[-2:] == \
                    ["rail_3v3_on", "rail_companion_on"]:
                event = "c24c_on"
        self.pcie_link_recovery_history.append(event)
        self.pcie_link_recovery_history = self.pcie_link_recovery_history[-8:]
        required = ["rail_off", "c24c_off", "rail_3v3_on",
                    "rail_companion_on", "c24c_on"]
        self.pcie_link_recovery_complete = (
            self.pcie_link_recovery_history == required)

    def _pcie_lane_gate_write(self, hw: 'HardwareState', addr: int, value: int):
        self.regs[addr] = value
        if not (value & 0x01):  # downstream lane/power gate disabled
            if self.pcie_ltssm_state != 0x00:
                self.trigger_pcie_link_loss()

    def _pcie_cc37_write(self, hw: 'HardwareState', addr: int, value: int):
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self.pcie_rxpll_reset_history.append(value & 0x04)
        self.pcie_rxpll_reset_history = self.pcie_rxpll_reset_history[-8:]
        if value & 0x04:
            self.pcie_rxpll_reset_asserted = True
            self.pcie_rxpll_reset_poll_observed = False
            self.pcie_rxpll_e716_history.clear()
            self.pcie_rxpll_e716_pulse_complete = False
            self.pcie_rxpll_reset_complete = False
        elif old & 0x04:
            self.pcie_rxpll_reset_complete = bool(
                self.pcie_rxpll_reset_asserted and
                self.pcie_rxpll_e716_pulse_complete and
                self.pcie_rxpll_reset_poll_observed)
            self.pcie_rxpll_reset_asserted = False

    def _pcie_e716_write(self, hw: 'HardwareState', addr: int, value: int):
        self.regs[addr] = value
        if self.pcie_rxpll_reset_asserted:
            self.pcie_rxpll_e716_history.append(value & 0x03)
            self.pcie_rxpll_e716_history = self.pcie_rxpll_e716_history[-4:]
            self.pcie_rxpll_e716_pulse_complete = (
                self.pcie_rxpll_e716_history[-2:] == [0x00, 0x03])

    def _pcie_e764_write(self, hw: 'HardwareState', addr: int, value: int):
        self.regs[addr] = value
        self.pcie_e764_history.append(value & 0x0F)
        self.pcie_e764_history = self.pcie_e764_history[-16:]

        # CB6D/E53A reaches 0x0A before its 0x07CF wait. Model E762.4 as
        # becoming ready only when the stock prefix and its independent bridge
        # prerequisites are present; C659 is deliberately enabled afterward.
        if self.pcie_e764_history[-4:] == [0x08, 0x08, 0x08, 0x0A]:
            rxpll_can_lock = bool(
                self.pcie_device_present and
                (self.regs.get(0xC656, 0) & 0x20) and
                (self.regs.get(0xB480, 0) & 0x01) and
                self.pcie_acdf_order_complete and
                (self.regs.get(0xB403, 0) & 0x01) and
                self.regs.get(0xB430, 0) == 0 and
                (self.regs.get(0xB434, 0) & 0x0F) == 0x0C
            )
            if rxpll_can_lock:
                self.regs[0xE762] = self.regs.get(0xE762, 0) | 0x10

        self.pcie_rxpll_training_complete = bool(
            self.pcie_e764_history[-6:] ==
            [0x08, 0x08, 0x08, 0x0A, 0x0B, 0x09] and
            (not self.pcie_link_recovery_required or
             self.pcie_link_recovery_complete))
        if not self.pcie_rxpll_training_complete:
            if self.pcie_ltssm_state != 0x00:
                self.trigger_pcie_link_loss()

    def _nvme_mmio_read32(self, address: int) -> int:
        if not self.nvme_bar0 <= address < self.nvme_bar0 + 0x2000:
            return 0
        offset = address - self.nvme_bar0
        return {
            0x00: 0x3C033FFF, 0x04: 0x00000030, 0x08: 0x00010400,
            0x14: self.nvme_cc, 0x1C: self.nvme_csts,
            0x28: self.nvme_asq, 0x2C: 0, 0x30: self.nvme_acq, 0x34: 0,
        }.get(offset, 0)

    def _nvme_mmio_write32(self, address: int, value: int):
        if not self.nvme_bar0 <= address < self.nvme_bar0 + 0x2000:
            return
        offset = address - self.nvme_bar0
        if offset == 0x14:
            if (self.nvme_cc ^ value) & 1:
                self.nvme_controller_generation += 1
                self.nvme_cq_tail = 0
                self.nvme_cq_phase = 1
                self.nvme_io_cq_tail = 0
                self.nvme_io_cq_phase = 1
                self.nvme_io_cq_created = False
                self.nvme_io_sq_created = False
                self.stock_nvme_sq_tail = 0
                self.stock_nvme_cq_head = 0
                self.stock_nvme_cq_tail = 0
                self.stock_nvme_cq_phase = 1
                self.stock_nvme_io_sq_tail = 0
                self.stock_nvme_io_cq_head = 0
                self.stock_nvme_io_cq_tail = 0
                self.stock_nvme_io_cq_phase = 1
            self.nvme_cc = value
            self.nvme_csts = (self.nvme_csts | 1) if value & 1 else (self.nvme_csts & ~1)
        elif offset == 0x28:
            self.nvme_asq = value
        elif offset == 0x30:
            self.nvme_acq = value
        elif offset == 0x1000:
            self._nvme_admin_doorbell(value)
        elif offset == 0x1008:
            self._nvme_io_doorbell(value)

    def _cef2_write(self, hw: 'HardwareState', addr: int, value: int):
        """W1C acknowledge on CEF2 (0xCEF2): writing 0x80 clears CEF2.7 and C806.5."""
        self._stock_nvme_setup_observe(addr, value)
        if value & 0x80:
            self.regs[0xCEF2] = self.regs.get(0xCEF2, 0) & ~0x80
            self.regs[0xC806] = self.regs.get(0xC806, 0) & ~0x20

    def _cef3_write(self, hw: 'HardwareState', addr: int, value: int):
        """W1C acknowledge on CEF3 (0xCEF3): writing 0x08 clears CEF3.3 error flag."""
        self._stock_nvme_setup_observe(addr, value)
        if value & 0x08:
            self.regs[0xCEF3] = self.regs.get(0xCEF3, 0) & ~0x08

    def _b294_write(self, hw: 'HardwareState', addr: int, value: int):
        """W1C acknowledge on B294 (0xB294): writing 0x10 clears bit 4, 0x20 clears bit 5."""
        cur = self.regs.get(0xB294, 0)
        if value & 0x10:
            cur &= ~0x10
        if value & 0x20:
            cur &= ~0x20
        self.regs[0xB294] = cur

    def _stock_nvme_setup_write(self, hw: 'HardwareState', addr: int, value: int):
        """Record ordinary register writes participating in the stock setup seed."""
        self.regs[addr] = value
        self._stock_nvme_setup_observe(addr, value)

    def _stock_nvme_setup_observe(self, addr: int, value: int):
        """Enable the queue model only after the exact setup writes occur in order."""
        expected = (
            (0xB264, 0x08), (0xB265, 0x00), (0xB266, 0x08), (0xB267, 0x08),
            (0xB26C, 0x08), (0xB26D, 0x20), (0xB26E, 0x08), (0xB26F, 0x28),
            (0xB250, 0x00), (0xB251, 0x00), (0xCEF3, 0x08), (0xCEF2, 0x80),
            (0xCEF0, 0x00), (0xCEEF, 0x00), (0xC807, 0x04), (0xB281, 0x10),
        )
        current = (addr, value & 0xFF)
        if current == expected[self.stock_nvme_setup_progress]:
            self.stock_nvme_setup_progress += 1
            if self.stock_nvme_setup_progress == len(expected):
                self.stock_nvme_queue_enabled = True
                self.stock_nvme_setup_progress = 0
        elif current == expected[0]:
            self.stock_nvme_setup_progress = 1
        else:
            self.stock_nvme_setup_progress = 0

    def _stock_nvme_sq_doorbell(self):
        """Model stock ASIC NVMe Queue 0 submission via B254=0x01."""
        if not self.memory:
            return
        tail = self.regs.get(0xB251, 0) & 0x03
        self.stock_nvme_sq_tail = tail

        if self.stock_nvme_b296_timeout:
            return
        if self.stock_nvme_b296_error:
            self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x05
            return
        self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x04

        # Read SQE from fixed XDATA 0xA000
        sqe = bytes(self.memory.xdata[0xA000:0xA040])
        opcode = sqe[0]
        cid = int.from_bytes(sqe[2:4], "little")
        nsid = int.from_bytes(sqe[4:8], "little")
        prp1 = int.from_bytes(sqe[24:32], "little")
        cdw10 = int.from_bytes(sqe[40:44], "little")
        cdw11 = int.from_bytes(sqe[44:48], "little")

        record = {
            "opcode": opcode,
            "cid": cid,
            "nsid": nsid,
            "prp1": prp1,
            "cdw10": cdw10,
            "cdw11": cdw11,
            "sq_tail": tail,
        }
        self.stock_nvme_submission_history.append(record)
        self.nvme_admin_history.append({
            "opcode": opcode,
            "nsid": nsid,
            "cdw10": cdw10,
            "cdw11": cdw11,
        })

        sct, sc = 0, 0
        if opcode == 0x06 and cdw10 == 1:
            # Identify Controller
            data = bytearray(512)
            data[4:24] = self.nvme_identify_serial
            data[24:64] = b"SAMSUNG MZALQ512HBLU-00BL2".ljust(40, b" ")
            data[64:72] = b"7L2QFXM7"
            data[256:258] = (0x0017).to_bytes(2, "little")
            if not self._stock_nvme_dma_write(prp1, data):
                sct, sc = 0, 0x02
        elif opcode == 0x06 and cdw10 == 0 and nsid == 1:
            # Identify Namespace
            if not self.nvme_namespace_present:
                sct, sc = 0, 0x0B
            else:
                data = bytearray(512)
                blocks = self.nvme_namespace_blocks
                data[0:8] = blocks.to_bytes(8, "little")
                data[8:16] = blocks.to_bytes(8, "little")
                data[16:24] = blocks.to_bytes(8, "little")
                data[25] = 0
                data[26] = 0
                data[128:130] = (0).to_bytes(2, "little")
                data[130] = self.nvme_namespace_lba_shift
                data[131] = 0
                if not self._stock_nvme_dma_write(prp1, data):
                    sct, sc = 0, 0x02
        elif opcode == 0x05:
            # Create I/O CQ
            if self.nvme_io_cq_created:
                sc = 0x02
            else:
                self.nvme_io_cq_tail = 0
                self.nvme_io_cq_phase = 1
                self.stock_nvme_io_cq_head = 0
                self.stock_nvme_io_cq_tail = 0
                self.stock_nvme_io_cq_phase = 1
                self.regs[0xB294] = 0
                self.nvme_io_cq_created = True
        elif opcode == 0x01:
            # Create I/O SQ
            if self.nvme_io_sq_created:
                sc = 0x02
            else:
                self.stock_nvme_io_sq_tail = 0
                self.nvme_io_sq_created = True
        elif opcode == 0x82 and (cdw10 >> 24) == 1:
            # Security Receive
            comid = (cdw10 >> 8) & 0xFFFF
            if comid == 1:
                features = bytes.fromhex(
                    "0001100c110000000000000000000000"
                    "0002100c070000000000000000000000"
                    "0303101010040001000000000000000000000000"
                    "0402100c010000000000000000000000"
                    "0404102000000a0a00000002000000020000000000000000000000000000000000000000")
                data = bytearray(512)
                data[:4] = (44 + len(features)).to_bytes(4, "big")
                data[48:48 + len(features)] = features
                if not self._stock_nvme_dma_write(prp1, data):
                    sct, sc = 0, 0x02
            elif comid == self.pyrite_device.comid:
                try:
                    response = self.pyrite_device.receive(comid, min(cdw11, 512))
                    if not self._stock_nvme_dma_write(prp1, response):
                        sct, sc = 0, 0x02
                except ValueError:
                    sct, sc = 0, 0x01
            else:
                sct, sc = 0, 0x01
        elif opcode == 0x81:
            # Security Send
            length = min(cdw11, 512)
            self.nvme_last_security_send = bytes(self.memory.xdata[0xF400:0xF400 + length])
            self.nvme_security_send_history.append(self.nvme_last_security_send)
            comid = (cdw10 >> 8) & 0xFFFF
            if (cdw10 >> 24) == 1 and comid == self.pyrite_device.comid:
                try:
                    self.pyrite_device.send(comid, self.nvme_last_security_send)
                except ValueError:
                    sct, sc = 0, 0x01
        else:
            sct, sc = 0, 0x01

        # Completion fault injection
        if self.stock_nvme_completion_timeout:
            return
        if self.stock_nvme_queue_error:
            self.regs[0xCEF3] = self.regs.get(0xCEF3, 0) | 0x08
            return

        eff_phase = (self.stock_nvme_cq_phase ^ 1) if self.stock_nvme_force_stale_phase else self.stock_nvme_cq_phase
        eff_cid = ((cid ^ 0xFFFF) if self.stock_nvme_force_mismatched_cid else cid) & 0xFFFF
        if self.stock_nvme_force_sct:
            sct = self.stock_nvme_force_sct
        if self.stock_nvme_force_sc:
            sc = self.stock_nvme_force_sc

        # Build 16-byte CQE at 0xB800 + 16 * stock_nvme_cq_tail
        cqe_addr = 0xB800 + self.stock_nvme_cq_tail * 16
        cqe = bytearray(16)
        cqe[12:14] = eff_cid.to_bytes(2, "little")
        status_word = (eff_phase & 0x01) | ((sc & 0xFF) << 1) | ((sct & 0x07) << 9)
        cqe[14:16] = status_word.to_bytes(2, "little")
        for i, b in enumerate(cqe):
            self.regs[cqe_addr + i] = b
            self.memory.xdata[cqe_addr + i] = b

        # Advance internal CQ tail and toggle phase on wrap
        self.stock_nvme_cq_tail = (self.stock_nvme_cq_tail + 1) % 4
        if self.stock_nvme_cq_tail == 0:
            self.stock_nvme_cq_phase ^= 1

        # Assert CEF2.7 and C806.5
        self.regs[0xCEF2] = self.regs.get(0xCEF2, 0) | 0x80
        self.regs[0xC806] = self.regs.get(0xC806, 0) | 0x20

    def _stock_nvme_dma_write(self, prp1: int, data: bytes) -> bool:
        """Model only the two firmware-observed PRP/XDATA associations."""
        if prp1 == 0x00200000:
            base = 0xF000
        elif prp1 == 0x00820400:
            base = 0xA400
        else:
            return False
        self.memory.xdata[base:base + len(data)] = data
        return True

    def _stock_nvme_cq_doorbell(self):
        """Model stock ASIC NVMe Queue 0 CQ head update via B254=0x11."""
        head = self.regs.get(0xB251, 0) & 0x03
        self.stock_nvme_cq_head = head
        self.stock_nvme_head_history.append(head)

        if self.stock_nvme_cq_b296_timeout:
            return
        if self.stock_nvme_cq_b296_error:
            self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x05
            return
        self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x04

    def _stock_nvme_io_sq_doorbell(self):
        """Model stock ASIC NVMe Queue 1 submission via B254=0x02."""
        if not self.memory:
            return
        tail = self.regs.get(0xB251, 0) & 0x03
        self.stock_nvme_io_sq_tail = tail

        if self.stock_nvme_io_b296_timeout:
            return
        if self.stock_nvme_io_b296_error:
            self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x05
            return
        self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x04

        # Read SQE from fixed XDATA 0xA000
        sqe = bytes(self.memory.xdata[0xA000:0xA040])
        opcode = sqe[0]
        cid = int.from_bytes(sqe[2:4], "little")
        nsid = int.from_bytes(sqe[4:8], "little")
        prp1 = int.from_bytes(sqe[24:32], "little")
        lba = int.from_bytes(sqe[40:48], "little")
        nlb = int.from_bytes(sqe[48:50], "little")

        record = {
            "opcode": opcode,
            "cid": cid,
            "nsid": nsid,
            "prp1": prp1,
            "lba": lba,
            "nlb": nlb,
            "sq_tail": tail,
        }
        self.stock_nvme_io_submission_history.append(record)
        self.nvme_io_history.append({
            "opcode": opcode,
            "nsid": nsid,
            "lba": lba,
            "nlb": nlb,
            "prp1": prp1,
        })

        self.nvme_io_data_length = 0
        blocks = nlb + 1
        status = self.nvme_io_status_fault
        if not status and (not self.nvme_io_cq_created or not self.nvme_io_sq_created):
            status = 0x02
        if not status and (not self.nvme_namespace_present or nsid != 1):
            status = 0x0B
        max_blocks = 2 if opcode == 0x02 else 6
        if not status and opcode != 0x00 and (
                blocks > max_blocks or lba >= self.nvme_namespace_blocks or
                blocks > self.nvme_namespace_blocks - lba):
            status = 0x80
        if not status and opcode == 0x00:
            if prp1 != 0:
                status = 0x02
        elif not status and opcode == 0x02:
            if prp1 != 0x00820400:
                status = 0x02
            else:
                data = b"".join(
                    self.nvme_namespace_data.get(lba + block, bytes(512))
                    for block in range(blocks))
                self.memory.xdata[0xA400:0xA400 + len(data)] = data
                self.nvme_io_data_length = len(data)
                self.regs[0xC4EE] = 0xA4
                self.regs[0xC4EF] = 0x00
        elif not status and opcode == 0x01:
            if prp1 != 0x00200400:
                status = 0x02
            else:
                for block in range(blocks):
                    start = 0xF400 + block * 512
                    self.nvme_namespace_data[lba + block] = bytes(
                        self.memory.xdata[start:start + 512])
                self.nvme_io_data_length = blocks * 512
        elif not status:
            status = 0x01

        # Model a USB reset after Queue-1 submission but before any CQE/event
        # becomes visible. This exercises the firmware's generation guard
        # against stale post-reset data and CSW production.
        if self.stock_nvme_io_bus_reset_on_submit:
            self.stock_nvme_io_bus_reset_on_submit = False
            self.regs[0xC802] = 0x01
            self.regs[0x91D1] = 0x01
            self.regs[0x9101] = 0x01
            self._pending_usb_interrupt = True
            return

        # Completion fault injection
        if self.stock_nvme_io_completion_timeout:
            return
        if self.stock_nvme_io_queue_error:
            self.regs[0xB294] = self.regs.get(0xB294, 0) | 0x20
            return

        eff_phase = (self.stock_nvme_io_cq_phase ^ 1) if self.stock_nvme_io_force_stale_phase else self.stock_nvme_io_cq_phase
        eff_cid = ((cid ^ 0xFFFF) if self.stock_nvme_io_force_mismatched_cid else cid) & 0xFFFF
        sct = self.stock_nvme_io_force_sct
        sc = self.stock_nvme_io_force_sc if self.stock_nvme_io_force_sc else status

        # Build 16-byte CQE at 0xB840 + 16 * stock_nvme_io_cq_tail
        cqe_addr = 0xB840 + self.stock_nvme_io_cq_tail * 16
        cqe = bytearray(16)
        cqe[12:14] = eff_cid.to_bytes(2, "little")
        status_word = (eff_phase & 0x01) | ((sc & 0xFF) << 1) | ((sct & 0x07) << 9)
        cqe[14:16] = status_word.to_bytes(2, "little")
        for i, b in enumerate(cqe):
            self.regs[cqe_addr + i] = b
            self.memory.xdata[cqe_addr + i] = b

        # Advance internal CQ tail and toggle phase on wrap
        self.stock_nvme_io_cq_tail = (self.stock_nvme_io_cq_tail + 1) % 4
        if self.stock_nvme_io_cq_tail == 0:
            self.stock_nvme_io_cq_phase ^= 1

        # Assert B294 bit 4 (Queue 1 completion)
        self.regs[0xB294] = self.regs.get(0xB294, 0) | 0x10

    def _stock_nvme_io_cq_doorbell(self):
        """Model stock ASIC NVMe Queue 1 CQ head update via B254=0x12."""
        head = self.regs.get(0xB251, 0) & 0x03
        self.stock_nvme_io_cq_head = head
        self.stock_nvme_io_head_history.append(head)

        if self.stock_nvme_io_cq_b296_timeout:
            return
        if self.stock_nvme_io_cq_b296_error:
            self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x05
            return
        self.regs[0xB296] = self.regs.get(0xB296, 0) | 0x04

    def _nvme_admin_doorbell(self, tail: int):
        if not self.memory:
            return
        slot = (tail - 1) % 4
        sqe = bytes(self.memory.xdata[0xF000 + slot * 64:0xF000 + slot * 64 + 64])
        opcode, cid = sqe[0], int.from_bytes(sqe[2:4], "little")
        nsid = int.from_bytes(sqe[4:8], "little")
        cdw10 = int.from_bytes(sqe[40:44], "little")
        cdw11 = int.from_bytes(sqe[44:48], "little")
        prp1 = int.from_bytes(sqe[24:32], "little")
        self.nvme_admin_history.append({
            "opcode": opcode, "nsid": nsid, "cdw10": cdw10, "cdw11": cdw11,
        })
        status = 0
        if opcode == 0x06 and cdw10 == 1:
            data = bytearray(512)
            data[4:24] = self.nvme_identify_serial
            data[24:64] = b"SAMSUNG MZALQ512HBLU-00BL2".ljust(40, b" ")
            data[64:72] = b"7L2QFXM7"
            data[256:258] = (0x0017).to_bytes(2, "little")
            self.memory.xdata[0xA400:0xA600] = data
        elif opcode == 0x06 and cdw10 == 0 and nsid == 1:
            if not self.nvme_namespace_present:
                status = 0x0B
            else:
                data = bytearray(512)
                blocks = self.nvme_namespace_blocks
                data[0:8] = blocks.to_bytes(8, "little")
                data[8:16] = blocks.to_bytes(8, "little")
                data[16:24] = blocks.to_bytes(8, "little")
                data[25] = 0  # NLBAF: one format
                data[26] = 0  # FLBAS: format zero
                data[128:130] = (0).to_bytes(2, "little")
                data[130] = self.nvme_namespace_lba_shift
                data[131] = 0
                self.memory.xdata[0xA400:0xA600] = data
        elif opcode == 0x05:
            if (cdw10 & 0xFFFF) != 1 or (cdw10 >> 16) != 3 or \
                    prp1 != 0x00820200 or not (cdw11 & 1) or \
                    self.nvme_io_cq_created:
                status = 0x02
            else:
                self.nvme_io_cq_tail = 0
                self.nvme_io_cq_phase = 1
                self.nvme_io_cq_created = True
        elif opcode == 0x01:
            if (cdw10 & 0xFFFF) != 1 or (cdw10 >> 16) != 3 or \
                    prp1 != 0x00200200 or not (cdw11 & 1) or \
                    (cdw11 >> 16) != 1 or not self.nvme_io_cq_created or \
                    self.nvme_io_sq_created:
                status = 0x02
            else:
                self.nvme_io_sq_created = True
        elif opcode == 0x82 and (cdw10 >> 24) == 1:
            comid = (cdw10 >> 8) & 0xFFFF
            if comid == 1:
                features = bytes.fromhex(
                    "0001100c110000000000000000000000"
                    "0002100c070000000000000000000000"
                    "0303101010040001000000000000000000000000"
                    "0402100c010000000000000000000000"
                    "0404102000000a0a00000002000000020000000000000000000000000000000000000000")
                data = bytearray(512)
                data[:4] = (44 + len(features)).to_bytes(4, "big")
                data[48:48 + len(features)] = features
                self.memory.xdata[0xA400:0xA600] = data
            elif comid == self.pyrite_device.comid:
                try:
                    response = self.pyrite_device.receive(comid, min(cdw11, 512))
                    self.memory.xdata[0xA400:0xA400 + len(response)] = response
                except ValueError:
                    status = 0x01
            else:
                status = 0x01
        elif opcode == 0x81:
            length = min(cdw11, 512)
            self.nvme_last_security_send = bytes(self.memory.xdata[0xF400:0xF400 + length])
            self.nvme_security_send_history.append(self.nvme_last_security_send)
            comid = (cdw10 >> 8) & 0xFFFF
            if (cdw10 >> 24) == 1 and comid == self.pyrite_device.comid:
                try:
                    self.pyrite_device.send(comid, self.nvme_last_security_send)
                except ValueError:
                    status = 0x01
        else:
            status = 0x01
        cqe = 0xA000 + self.nvme_cq_tail * 16
        dw3 = cid | (self.nvme_cq_phase << 16) | (status << 17)
        self.memory.xdata[cqe + 12:cqe + 16] = dw3.to_bytes(4, "little")
        self.nvme_cq_tail = (self.nvme_cq_tail + 1) % 4
        if self.nvme_cq_tail == 0:
            self.nvme_cq_phase ^= 1

    def _nvme_io_doorbell(self, tail: int):
        self.conventional_nvme_io_doorbell_count += 1
        if not self.memory:
            return
        slot = (tail - 1) % 4
        sqe = bytes(self.memory.xdata[0xF200 + slot * 64:0xF200 + slot * 64 + 64])
        opcode = sqe[0]
        cid = int.from_bytes(sqe[2:4], "little")
        nsid = int.from_bytes(sqe[4:8], "little")
        prp1 = int.from_bytes(sqe[24:32], "little")
        lba = int.from_bytes(sqe[40:48], "little")
        nlb = int.from_bytes(sqe[48:50], "little")
        self.nvme_io_history.append({
            "opcode": opcode, "nsid": nsid, "lba": lba, "nlb": nlb,
            "prp1": prp1,
        })
        self.nvme_io_data_length = 0
        blocks = nlb + 1
        status = self.nvme_io_status_fault
        if not status and (not self.nvme_io_cq_created or not self.nvme_io_sq_created):
            status = 0x02
        if not status and (not self.nvme_namespace_present or nsid != 1):
            status = 0x0B
        max_blocks = 2 if opcode == 0x02 else 6
        if not status and opcode != 0x00 and (
                blocks > max_blocks or lba >= self.nvme_namespace_blocks or
                blocks > self.nvme_namespace_blocks - lba):
            status = 0x80
        if not status and opcode == 0x00:
            if prp1 != 0:
                status = 0x02
        elif not status and opcode == 0x02:
            if prp1 != 0x00820400:
                status = 0x02
            else:
                data = b"".join(
                    self.nvme_namespace_data.get(lba + block, bytes(512))
                    for block in range(blocks))
                self.memory.xdata[0xA400:0xA400 + len(data)] = data
                self.nvme_io_data_length = len(data)
                # Controller-owned queue source returned by C4EE:C4EF after
                # firmware selects the completed slot through C4ED.
                self.regs[0xC4EE] = 0xA4
                self.regs[0xC4EF] = 0x00
        elif not status and opcode == 0x01:
            if prp1 != 0x00200400:
                status = 0x02
            else:
                for block in range(blocks):
                    start = 0xF400 + block * 512
                    self.nvme_namespace_data[lba + block] = bytes(
                        self.memory.xdata[start:start + 512])
                self.nvme_io_data_length = blocks * 512
        elif not status:
            status = 0x01

        cqe = 0xA200 + self.nvme_io_cq_tail * 16
        dw3 = cid | (self.nvme_io_cq_phase << 16) | (status << 17)
        self.memory.xdata[cqe + 12:cqe + 16] = dw3.to_bytes(4, "little")
        self.nvme_io_cq_tail = (self.nvme_io_cq_tail + 1) % 4
        if self.nvme_io_cq_tail == 0:
            self.nvme_io_cq_phase ^= 1
        if self.nvme_io_bus_reset_on_submit:
            self.nvme_io_bus_reset_on_submit = False
            self.regs[0xC802] = 0x01
            self.regs[0x91D1] = 0x01
            self.regs[0x9101] = 0x01
            self._pending_usb_interrupt = True

    def _pcie_dma_trigger(self, hw: 'HardwareState', addr: int, value: int):
        """
        PCIe DMA trigger at 0xB296.

        When value 0x08 is written, this triggers a PCIe DMA transfer for E4/E5 commands.
        - E4 (read): Copy from XDATA to USB buffer (for host to read)
        - E5 (write): Write value from CDB to XDATA

        The target address comes from the CDB in USB registers 0x910F-0x9111.
        For E4, 0x910E contains the size to read.
        For E5, 0x910E contains the value to write (single byte).
        """
        # Status bits 0..2 are W1C (write-1-to-clear)
        if value & 0x07:
            self.regs[addr] = self.regs.get(addr, 0) & ~(value & 0x07)
        else:
            self.regs[addr] = value

        # Value 0x08 is the E4/E5 DMA trigger
        if value == 0x08:
            # Get target address from CDB (big-endian: 0x910F=high, 0x9110=mid, 0x9111=low)
            addr_high = self.regs.get(0x910F, 0)
            addr_mid = self.regs.get(0x9110, 0)
            addr_low = self.regs.get(0x9111, 0)
            target_addr = (addr_high << 16) | (addr_mid << 8) | addr_low

            # Check command type to determine operation
            cmd_type = self.usb_cmd_type

            if cmd_type == 0xE5:
                # E5 WRITE: Write single byte from CDB to XDATA
                write_value = self.regs.get(0x910E, 0)
                xdata_addr = target_addr & 0xFFFF

                if self.log_pcie:
                    print(f"[{self.cycles:8d}] [PCIe] E5 WRITE: 0x{write_value:02X} -> XDATA[0x{xdata_addr:04X}]")

                # Perform the write
                if self.memory:
                    self.memory.xdata[xdata_addr] = write_value

                # Signal completion
                self.regs[0xB296] = 0x06  # PCIe DMA complete (bits 1+2)

                # Clear command pending after successful write
                if self.usb_cmd_pending:
                    self.usb_cmd_pending = False
                    print(f"[{self.cycles:8d}] [PCIe] E5 command completed")

            else:
                # E4 READ: Copy from XDATA to USB buffer
                size = self.regs.get(0x910E, 0)

                if self.log_pcie:
                    print(f"[{self.cycles:8d}] [PCIe] DMA TRIGGER: src=0x{target_addr:06X} size={size}")

                # Perform the DMA - copy from simulated PCIe memory to USB buffer
                self._perform_pcie_dma(target_addr, size)

                # Signal completion - multiple bits checked by different code paths
                # Bit 2 checked at 0xE3A7 (JNB ACC.2), bit 1 checked at 0xBFE6 (ANL #0x02)
                self.regs[0xB296] = 0x06  # PCIe DMA complete (bits 1+2)

                # Clear command pending after successful DMA
                if self.usb_cmd_pending:
                    self.usb_cmd_pending = False
                    self.usb_cmd_type = 0  # Reset command type
                    print(f"[{self.cycles:8d}] [PCIe] USB command completed, clearing pending flag")

    def _perform_pcie_dma(self, source_addr: int, size: int):
        """
        Perform PCIe DMA transfer to USB buffer.

        For E4 read commands (address 0x50xxxx), reads from XDATA[xxxx].
        For other addresses, uses simulated PCIe memory or test patterns.
        Data is copied to USB data buffer at 0x8000.
        """
        if not self.memory:
            if self.log_pcie:
                print(f"[{self.cycles:8d}] [PCIe] ERROR: No memory reference for DMA")
            return

        dest_addr = 0x8000  # USB data buffer

        # Check if this is an E4 XDATA read (address 0x50xxxx)
        is_xdata_read = (source_addr >> 16) == 0x50

        for i in range(size):
            if is_xdata_read:
                # E4 command: read from chip's XDATA memory
                # Address format: 0x50XXXX -> XDATA[XXXX]
                xdata_addr = (source_addr + i) & 0xFFFF
                value = self.memory.xdata[xdata_addr]
            else:
                # PCIe memory read (e.g., NVMe config space)
                pcie_addr = source_addr + i
                if pcie_addr in self.pcie_memory:
                    value = self.pcie_memory[pcie_addr]
                else:
                    # Generate test pattern for unmapped PCIe addresses
                    value = (pcie_addr & 0xFF) ^ (i & 0xFF)

            # Write to USB data buffer
            self.memory.xdata[dest_addr + i] = value

        # TEST MODE: Set DMA completion flag in RAM
        # Real hardware would signal completion through MMIO registers,
        # which firmware reads and then sets this RAM flag itself.
        # For testing, we set it directly.
        self.memory.xdata[0x0AA0] = size if size > 0 else 1

        if self.log_pcie:
            addr_type = "XDATA" if is_xdata_read else "PCIe"
            xdata_addr = source_addr & 0xFFFF if is_xdata_read else source_addr
            print(f"[{self.cycles:8d}] [PCIe] DMA COMPLETE: {size} bytes from {addr_type}[0x{xdata_addr:04X}] to 0x{dest_addr:04X}")
            if size > 0:
                sample = ' '.join(f'{self.memory.xdata[dest_addr + i]:02X}' for i in range(min(size, 16)))
                print(f"[{self.cycles:8d}] [PCIe] Data: {sample}")

    # ============================================
    # Flash/DMA Callbacks
    # ============================================
    def _flash_csr_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Flash CSR read - returns status.
        Bit 0: Busy (0 = idle, 1 = operation in progress)
        """
        # Flash is always ready (operations complete instantly in emulation)
        return 0x00

    def _flash_cmd_write(self, hw: 'HardwareState', addr: int, value: int):
        """Load the SPI opcode. Writing C8A9 starts the transaction."""
        self.regs[addr] = value

    def _flash_csr_write(self, hw: 'HardwareState', addr: int, value: int):
        """Execute the configured SPI transaction into/from XDATA 0x7000."""
        self.regs[addr] = value
        if not (value & 0x01):
            return

        command = self.regs.get(0xC8AA, 0)
        self.spi_command_log.append(command)
        flash_addr = ((self.regs.get(0xC8AB, 0) << 16) |
                      (self.regs.get(0xC8A2, 0) << 8) |
                      self.regs.get(0xC8A1, 0))
        data_len = ((self.regs.get(0xC8A3, 0) << 8) |
                    self.regs.get(0xC8A4, 0))
        self.spi_flash_addr = flash_addr

        if command == 0x03 and self.memory:
            for i in range(data_len):
                source = flash_addr + i
                self.memory.xdata[0x7000 + i] = (
                    self.spi_flash[source] if source < len(self.spi_flash) else 0xFF
                )
        elif command == 0x05 and self.memory and data_len:
            self.memory.xdata[0x7000] = 0x1C
        elif command == 0x9F and self.memory:
            jedec = b'\xB3\x60\x13'
            for i in range(min(data_len, len(jedec))):
                self.memory.xdata[0x7000 + i] = jedec[i]
        elif command == 0x20:
            sector_start = flash_addr & ~0xFFF
            if sector_start + 0x1000 <= len(self.spi_flash):
                self.spi_flash[sector_start:sector_start + 0x1000] = b'\xFF' * 0x1000
        elif command == 0xD8:
            block_start = flash_addr & ~0xFFFF
            if block_start + 0x10000 <= len(self.spi_flash):
                self.spi_flash[block_start:block_start + 0x10000] = b'\xFF' * 0x10000
        elif command == 0xC7:
            self.spi_flash[:] = b'\xFF' * len(self.spi_flash)
        elif command == 0x02 and self.memory:
            if self.spi_program_fault:
                self.regs[addr] = 0x00
                return
            buffer_offset = (self.regs.get(0xC8AF, 0) << 8) | self.regs.get(0xC8AE, 0)
            for i in range(data_len):
                destination = flash_addr + i
                if destination >= len(self.spi_flash):
                    break
                self.spi_flash[destination] &= self.memory.xdata[0x7000 + buffer_offset + i]

        self.regs[addr] = 0x00

    def load_flash_from_file(self, path: str):
        """Load flash contents from a file (e.g., fw.bin)."""
        with open(path, 'rb') as f:
            data = f.read(SPI_FLASH_SIZE + 1)
        if len(data) > SPI_FLASH_SIZE:
            raise ValueError(
                f"flash image is {len(data)} bytes; physical capacity is {SPI_FLASH_SIZE} bytes"
            )

        self.spi_flash[:] = bytes([SPI_FLASH_ERASED_BYTE]) * SPI_FLASH_SIZE
        self.spi_flash[:len(data)] = data
        print(f"[SPI_FLASH] Loaded {len(data)} bytes from {path}")

    def save_flash_to_file(self, path: str):
        """Save flash contents to a file."""
        try:
            with open(path, 'wb') as f:
                f.write(self.spi_flash)
            print(f"[SPI_FLASH] Saved {len(self.spi_flash)} bytes to {path}")
        except Exception as e:
            print(f"[SPI_FLASH] Failed to save to {path}: {e}")

    def _dma_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """DMA status - done."""
        return 0x04

    def _busy_reg_read(self, hw: 'HardwareState', addr: int) -> int:
        """Busy register - auto-clear after polling."""
        count = self.poll_counts.get(addr, 0)
        value = self.regs.get(addr, 0)
        if count >= 3 and (value & 0x01):
            value &= ~0x01
            self.regs[addr] = value
        return value

    def _flash_rom_mirror_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Flash/Code ROM mirror read.

        XDATA region 0xE400-0xE500 mirrors code ROM with offset 0xDDFC.
        This is used for reading USB descriptors stored in code ROM.
        Example: XDATA 0xE423 → Code ROM 0x0627 (device descriptor)

        Formula: code_addr = xdata_addr - 0xDDFC
        """
        code_addr = addr - 0xDDFC
        if self.memory and 0 <= code_addr < len(self.memory.code):
            value = self.memory.code[code_addr]
            if self.log_reads:
                print(f"[{self.cycles:8d}] [FLASH] Read 0x{addr:04X} → Code[0x{code_addr:04X}] = 0x{value:02X}")
            return value
        return 0x00

    # ============================================
    # Interrupt Callbacks
    # ============================================
    def _int_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """System interrupt status - clear on read."""
        value = self.regs.get(addr, 0)
        if value & 0x01:
            self.regs[addr] = value & ~0x01
        return value

    def _pd_interrupt_read(self, hw: 'HardwareState', addr: int) -> int:
        """PD interrupt status - returns current state."""
        return self.regs.get(addr, 0)

    # ============================================
    # USB State Machine MMIO Callbacks
    # ============================================
    def _usb_ce89_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB/DMA status register 0xCE89.

        Controls USB state machine transitions:
        - Bit 0: Must be SET to exit wait loop at 0x348C (JNB 0xe0.0)
        - Bit 1: Must be CLEAR for success at 0x3493 (jnb acc.1 takes good path)
                 If SET, firmware jumps to 0x35A1 (failure path)
        - Bit 2: DMA/transfer status at 0x48D1 (JNB ACC.2)
                 SET = DMA in progress, CLEAR = DMA complete
                 Controls state 3→4 transition at 0x3588 (JNB 0xe0.2)

        State machine flow:
        1. Firmware writes 0 to 0xCE88, then polls 0xCE89 bit 0
        2. When bit 0 set, checks bit 1 - must be CLEAR for success
        3. Then checks bit 4 of 0xCE86 - must be CLEAR
        4. Bit 2: set briefly to signal state 3→4, then clear to signal completion

        For USB control transfers (e.g., GET_DESCRIPTOR):
        1. Firmware sets up DMA via CCxx registers
        2. Polls 0xCE89 bit 2 - SET means busy, CLEAR means complete
        3. When bit 2 clears, firmware knows transfer is done
        """
        self.usb_ce89_read_count += 1

        # Start with base value
        value = 0x00

        # Enable state machine progression when USB connected OR command pending
        # This allows firmware to transition through USB states naturally
        if self.usb_connected or self.usb_cmd_pending:
            # Bit 0 - set after a few reads to exit wait loop at 0x348C
            if self.usb_ce89_read_count >= 3:
                value |= 0x01

            # Bit 1 - E5 path control
            # At 0x1862: jb acc.1, 0x1884 - if bit 1 SET, take E5 path
            # For E5 commands, we SET bit 1 to direct firmware to the E5 handler
            # For E4 commands, we keep bit 1 CLEAR to take the E4 path
            if self.usb_cmd_type == 0xE5:
                value |= 0x02  # Set bit 1 for E5 path

            # Bit 2 - DMA/transfer busy status
            # SET during counts 5-14 to allow state transitions
            # CLEAR after count >= 15 to signal DMA/transfer completion
            # This allows firmware to exit the polling loop at 0x48D1
            if 5 <= self.usb_ce89_read_count < 15:
                value |= 0x04
            # After count 15, bit 2 stays clear to signal completion

        if self.usb_stock_cbw_dma_trace and value & 0x01:
            # Stock 348F rereads CE89 and branches to the CBW signature
            # validator when bit 1 identifies a buffered CBW.
            value |= 0x02
            self.usb_stock_cbw_dma_trace.append((0xCE89, value))
            self.usb_stock_cbw_dma_ready = bool(
                self.usb_stock_cbw_dma_trace[0] == (0xCE88, 0x00) and
                value & 0x03 == 0x03)

        if self.log_reads or self.usb_cmd_pending:
            # Add PC for better tracing
            pc = 0
            if hasattr(self, '_cpu_ref') and self._cpu_ref:
                pc = self._cpu_ref.pc
            print(f"[{self.cycles:8d}] [USB_SM] Read 0xCE89 = 0x{value:02X} (count={self.usb_ce89_read_count}, PC=0x{pc:04X})")

        return value

    # ============================================
    # IMPORTANT: USB Descriptor Handling Philosophy
    # ============================================
    # The emulator must NOT search for USB descriptors in ROM/XDATA.
    # The FIRMWARE is responsible for handling GET_DESCRIPTOR requests:
    #
    # 1. Firmware reads setup packet from MMIO (0x9E00-0x9E07)
    # 2. Firmware looks up descriptor in its own code ROM
    # 3. Firmware writes descriptor to USB transmit buffer via MMIO
    # 4. USB hardware DMA sends the data to host
    #
    # If you find yourself searching for descriptors in the emulator,
    # you are doing something WRONG. Fix the MMIO emulation so the
    # firmware's USB handler can complete successfully.
    #
    # The emulator's job is to:
    # - Provide correct MMIO register values for firmware to read
    # - Capture data that firmware writes to USB output registers
    # - Signal completion via status registers
    #
    # DO NOT implement _find_descriptor_in_xdata or similar functions!
    # ============================================

    def _usb_ce86_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB status register 0xCE86.

        Bit 4: Checked at 0x349D (JNB 0xe0.4) - must be clear for normal path.
        """
        # Return 0 to allow normal USB initialization path
        # Bit 4 clear means no error/busy condition
        return 0x00

    def _usb_ce6c_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB controller status register 0xCE6C.

        Bit 7: USB controller ready for transfers
               Checked at 0x1855: jnb acc.7, 0x1884
               Checked at 0x2FB6: jb acc.7, 0x2FBC
               Must be SET when USB is connected and ready.

        This is hardware state - the USB controller sets this when
        it's ready to process transfers.
        """
        if self.usb_connected or self.usb_cmd_pending:
            return 0x80  # Bit 7 set - USB ready
        return 0x00

    def _usb_ce00_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        DMA control register 0xCE00.

        Firmware writes 0x03 to start a DMA transfer (at 0x3531-0x3533),
        then polls at 0x3534-0x3538 waiting for this register to become 0.
        When the register is 0, DMA is complete.

        The polling loop is:
            0x3534: mov dptr, #0xce00
            0x3537: movx a, @dptr
            0x3538: jnz 0x3534  ; loop while non-zero
        """
        self.usb_ce00_read_count += 1

        if self.scsi_dma_stall and self.regs.get(0xCE00, 0) == 0x03:
            return 0x03
        # Return 0 after a few reads to simulate DMA completion
        if self.usb_ce00_read_count >= 2:
            self.regs[0xCE00] = 0
            return 0x00  # DMA complete
        return self.regs.get(0xCE00, 0x03)  # DMA in progress

    def _usb_ce00_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        Model one stock CE00 USB-landing-buffer to controller-SRAM transfer.

        The copy is derived only from the descriptor programmed by firmware:
        CE76-CE79 select the PCI target, CE72 selects normal transfer mode,
        CE83 carries flow control, and 900B must enable all MSC paths. The
        bounded handmade namespace-WRITE contract accepts the contiguous PCI
        0x00200400-0x00200FFF QID1 data-OUT window exposed at XDATA F400.
        """
        self.regs[0xCE00] = value
        self.usb_ce00_read_count = 0  # Reset counter for new DMA operation
        if value != 0x03:
            return
        target = (
            self.regs.get(0xCE76, 0) |
            (self.regs.get(0xCE77, 0) << 8) |
            (self.regs.get(0xCE78, 0) << 16) |
            (self.regs.get(0xCE79, 0) << 24)
        )
        descriptor = {
            "control": value,
            "source": 0x7000 + self.scsi_dma_source_offset,
            "target": target,
            "transfer_mode": self.regs.get(0xCE72, 0),
            "flow": self.regs.get(0xCE83, 0),
            "msc_cfg": self.regs.get(0x900B, 0),
        }
        valid = (
            self.memory is not None and
            target == 0x00200400 + self.scsi_dma_source_offset and
            self.scsi_dma_source_offset < 0x0C00 and
            descriptor["transfer_mode"] == 0 and
            descriptor["flow"] & 0x70 == 0 and
            descriptor["msc_cfg"] & 0x07 == 0x07
        )
        descriptor["valid"] = valid
        self.scsi_dma_log.append(descriptor)
        if not valid or self.scsi_dma_stall:
            return
        source = 0x7000 + self.scsi_dma_source_offset
        destination = 0xF400 + self.scsi_dma_source_offset
        self.memory.xdata[destination:destination + 512] = (
            self.memory.xdata[source:source + 512])
        self.scsi_dma_source_offset += 512
        target += 512
        self.regs[0xCE76] = target & 0xFF
        self.regs[0xCE77] = (target >> 8) & 0xFF
        self.regs[0xCE78] = (target >> 16) & 0xFF
        self.regs[0xCE79] = (target >> 24) & 0xFF

    def _usb_ce55_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Transfer slot count register 0xCE55.

        Read at 0x34B9 and stored to XDATA[0x009F] to determine the
        outer loop limit at 0x34F8-0x3553. Each iteration processes
        one transfer slot.

        For a simple control transfer, return 1 to limit to single iteration.
        """
        print(f"[{self.cycles:8d}] [USB_CE55] Read CE55 = 0x01 (transfer slots)")
        return 0x01  # 1 transfer slot for control transfers

    def _usb_ce88_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        DMA trigger register 0xCE88.

        Firmware writes to CE88 before polling CE89 at 0x1806/0x1807.
        This write triggers a new DMA/transfer sequence, so we reset
        the CE89 read count to allow the new polling sequence to
        progress through the state machine correctly.
        """
        self.regs[0xCE88] = value
        self.scsi_dma_source_offset = 0
        if (not self.usb_cbw_armed and
                bytes(self.regs.get(0x911B + i, 0) for i in range(4)) ==
                b"USBC"):
            self.usb_stock_cbw_dma_trace = [(0xCE88, value)]
            self.usb_stock_cbw_dma_ready = False
        # Reset CE89 count for new transfer sequence
        self.usb_ce89_read_count = 0
        if self.log_writes:
            print(f"[{self.cycles:8d}] [USB_HW] CE88 write = 0x{value:02X}, reset CE89 counter")

    # ============================================
    # Timer Callbacks
    # ============================================
    def record_usb_stock_5372(self, addr: int, old: int, value: int):
        """Recognize exact 4BD4 defaults then the E795-derived 5372 pair."""
        addresses = (
            (0x0AE1, 0x07E4, 0x05A5, 0x05A6) +
            tuple(range(0x05B0, 0x0632)) +
            (0x0B3A, 0x0B39, 0x0ACC, 0x0ACD, 0x07EC,
             0x07B6, 0x07B7, 0x07B8, 0x09F9, 0x09FA, 0x09FB,
             0x086E, 0x086F, 0x086E, 0x07F6))
        status = self.regs.get(0xE795, 0)
        if not (status & 0x01):
            selected = 0x65
        else:
            selected = 0x64 if status & 0x02 else 0x62
            if status & 0x20:
                selected -= 1
        values = (
            (0, 0, 0, 0) + (0,) * 130 +
            (0, 0, 0, 0x0F, 0, 0, 0, 0, 4, 4, 0,
             selected, 0x24, selected, 1))
        event = (addr, old, value)
        if addr == addresses[0] and value == values[0]:
            self.usb_stock_5372_trace = [event]
            self.usb_stock_5372_ready = False
            self.usb_stock_5372_df0b_latched = False
            return
        # Later B1C5 writes 0AE1={1,2}; it is a downstream terminal commit,
        # not a malformed restart of the already completed classifier.
        if self.usb_stock_5372_ready:
            return
        trace = self.usb_stock_5372_trace
        index = len(trace)
        if trace and index < len(addresses) and \
                addr == addresses[index] and value == values[index]:
            trace.append(event)
            if len(trace) == len(addresses):
                self.usb_stock_5372_ready = True
        elif addr in addresses:
            self.usb_stock_5372_trace = []
            self.usb_stock_5372_ready = False

    def _record_usb_stock_cold(self, bank: int, addr: int, old: int,
                               value: int):
        """Prove C8CF then the DF0B -> EEB5/EEAF -> DC3A -> CC83 -> CF91 path."""
        e300_addresses = (0xC004, 0xC007, 0xCA2E)
        e300_ops = ((0xFD, 0x02), (0xF7, 0x00), (0xFE, 0x01))
        e300_event = (addr, old, value)
        if bank == 0 and self.usb_stock_5372_ready and \
                addr == 0xC004 and value == ((old & 0xFD) | 0x02):
            self.usb_stock_e300_trace = [e300_event]
            self.usb_stock_e300_ready = False
            self.usb_stock_e300_df0b_latched = False
        elif self.usb_stock_e300_trace and not self.usb_stock_e300_ready:
            index = len(self.usb_stock_e300_trace)
            if bank == 0 and index < len(e300_addresses) and \
                    addr == e300_addresses[index]:
                and_mask, or_value = e300_ops[index]
                if value == ((old & and_mask) | or_value):
                    self.usb_stock_e300_trace.append(e300_event)
                    if len(self.usb_stock_e300_trace) == len(e300_addresses):
                        self.usb_stock_e300_ready = True
                else:
                    self.usb_stock_e300_trace = []
            else:
                self.usb_stock_e300_trace = []

        policy_addresses = (0xC65B, 0xC656, 0xC65B, 0xC62D)
        policy_ops = (
            (0xF7, 0x08), (0xDF, 0x00),
            (0xDF, 0x20), (0xE0, 0x07),
        )
        policy_event = (addr, old, value)
        if bank == 0 and not self.usb_stock_52ef_ready and \
                addr == 0xC65B and value == ((old & 0xF7) | 0x08):
            self.usb_stock_52ef_trace = [policy_event]
            self.usb_stock_52ef_df0b_latched = False
        elif self.usb_stock_52ef_trace and not self.usb_stock_52ef_ready:
            index = len(self.usb_stock_52ef_trace)
            if bank == 0 and index < len(policy_addresses) and \
                    addr == policy_addresses[index]:
                and_mask, or_value = policy_ops[index]
                if value == ((old & and_mask) | or_value):
                    self.usb_stock_52ef_trace.append(policy_event)
                    if len(self.usb_stock_52ef_trace) == len(policy_addresses):
                        self.usb_stock_52ef_ready = True
                else:
                    self.usb_stock_52ef_trace = []
            else:
                self.usb_stock_52ef_trace = []

        route_addresses = (
            0xCC35, 0xC801, 0xC800, 0xCA60,
            0xCA60, 0xC800, 0xCC3B, 0xCC3B,
        )
        route_ops = (
            (0xFE, 0x00), (0xEF, 0x10), (0xFB, 0x04), (0xF8, 0x06),
            (0xF7, 0x08), (0xFF, 0x01), (0xFF, 0x01), (0xFD, 0x02),
        )
        route_event = (addr, old, value)
        if bank == 0 and addr == 0xCC35 and value == (old & 0xFE):
            self.usb_stock_4c8d_route_trace = [route_event]
            self.usb_stock_4c8d_route_ready = False
            self.usb_stock_4c8d_c8cf_latched = False
        elif self.usb_stock_4c8d_route_trace and \
                not self.usb_stock_4c8d_route_ready:
            index = len(self.usb_stock_4c8d_route_trace)
            if bank == 0 and index < len(route_addresses) and \
                    addr == route_addresses[index]:
                and_mask, or_value = route_ops[index]
                if value == ((old & and_mask) | or_value):
                    self.usb_stock_4c8d_route_trace.append(route_event)
                    if len(self.usb_stock_4c8d_route_trace) == \
                            len(route_addresses):
                        self.usb_stock_4c8d_route_ready = True
                else:
                    self.usb_stock_4c8d_route_trace = []
            else:
                self.usb_stock_4c8d_route_trace = []

        c8cf_addresses = (
            0x92C6, 0x92C7, 0x9201, 0x9201, 0x92C1, 0x920C, 0x920C,
            0xC20C, 0xC208, 0x92C0, 0x92C1, 0x92C5, 0x9241, 0x9241,
        )
        c8cf_ops = (
            (0x00, 0x05), (0x00, 0x00), (0xFE, 0x00), (0xFD, 0x00),
            (0xFD, 0x02), (0xFD, 0x00), (0xFE, 0x00), (0xBF, 0x40),
            (0xEF, 0x00), (0xFE, 0x01), (0xFE, 0x01), (0xFB, 0x04),
            (0xEF, 0x10), (0x3F, 0xC0),
        )
        event = (addr, old, value)
        if bank == 0 and addr == 0x92C6 and value == 0x05:
            self.usb_stock_4c8d_c8cf_latched = \
                self.usb_stock_4c8d_route_ready
            self.usb_stock_4c8d_route_ready = False
            self.usb_stock_4c8d_route_trace = []
            self.usb_stock_c8cf_trace = [event]
            self.usb_stock_c8cf_ready = False
            self.usb_stock_c8cf_cold_latched = False
        elif self.usb_stock_c8cf_trace and not self.usb_stock_c8cf_ready:
            index = len(self.usb_stock_c8cf_trace)
            if bank == 0 and index < len(c8cf_addresses) and \
                    addr == c8cf_addresses[index]:
                and_mask, or_value = c8cf_ops[index]
                if value == ((old & and_mask) | or_value):
                    self.usb_stock_c8cf_trace.append(event)
                    if len(self.usb_stock_c8cf_trace) == len(c8cf_addresses):
                        self.usb_stock_c8cf_ready = \
                            self.usb_stock_4c8d_c8cf_latched
                else:
                    self.usb_stock_c8cf_trace = []
            else:
                self.usb_stock_c8cf_trace = []

        if bank == 0 and addr == 0xC805 and \
                value == ((old & 0xF9) | 0x02):
            self.usb_stock_52ef_df0b_latched = self.usb_stock_52ef_ready
            self.usb_stock_5372_df0b_latched = self.usb_stock_5372_ready
            self.usb_stock_e300_df0b_latched = self.usb_stock_e300_ready
            self.usb_stock_52ef_ready = False
            self.usb_stock_52ef_trace = []
            self.usb_stock_5372_ready = False
            self.usb_stock_5372_trace = []
            self.usb_stock_e300_ready = False
            self.usb_stock_e300_trace = []
            self.usb_stock_df0b_stage = \
                1 if (self.usb_stock_52ef_df0b_latched and
                      self.usb_stock_5372_df0b_latched and
                      self.usb_stock_e300_df0b_latched) else 0
        elif bank == 0 and addr == 0xC8A6 and value == 0x04 and \
                self.usb_stock_df0b_stage == 1:
            self.usb_stock_df0b_stage = 2

        # C21B is the first controller-visible write of EEB5/DAC8.  Starting
        # it revokes the complete cold generation until all 710 stock writes,
        # the physical-ready read, standalone CC83, and CF91 recur.
        if bank == 0 and addr == 0xC21B and \
                value == ((old & 0x3F) | 0xC0):
            self.usb_stock_52ef_ready = False
            self.usb_stock_5372_ready = False
            self.usb_stock_e300_ready = False
            self.usb_stock_c8cf_cold_latched = self.usb_stock_c8cf_ready
            self.usb_stock_c8cf_ready = False
            self.usb_stock_c8cf_trace = []
            self.usb_stock_cold_trace = [(bank, addr, old, value)]
            self.usb_stock_eeb5_eeaf_ready = False
            self.usb_stock_8fcf_trace = []
            self.usb_stock_8fcf_ready = False
            self.usb_stock_dc3a_ready = False
            self.usb_stock_ee8a_ready = False
            self.usb_stock_cc32_ready = False
            self.usb_stock_link_ready_observed = False
            self.usb_stock_cold_cc83_ready = False
            self.usb_stock_cold_ready = False
            self.usb_stock_msc_dma_ready = False
            self.usb_stock_c24c_ready = False
            self.usb_stock_msc_initial_transition_ready = False
            self.usb_stock_b1c5_terminal_trace = []
            self.usb_stock_b1c5_terminal_ready = False
            self.usb_stock_3a2b_channel_trace = []
            self.usb_stock_3a2b_channel_ready = False
            self.usb_stock_3a2b_dma_trace = []
            self.usb_stock_3a2b_dma_ready = False
            self.usb_stock_3a2b_scsi_dma_trace = []
            self.usb_stock_3a2b_scsi_dma_ready = False
            self.usb_stock_cbw_dma_trace = []
            self.usb_stock_cbw_dma_ready = False
            self.usb_stock_cbw_tag_trace = []
            self.usb_stock_cbw_tag_ready = False
            self.usb_stock_bulk_in_arm_trace = []
            self.usb_stock_bulk_in_arm_ready = False
            self.usb_stock_bulk_in_event_ready = False
            self.usb_stock_dc3a_trace = []
            self.usb_stock_cold_cc83_trace = []
            self.usb_stock_cf91_trace = []
            return

        trace = self.usb_stock_cold_trace
        if trace and not self.usb_stock_eeb5_eeaf_ready:
            trace.append((bank, addr, old, value))
            if len(trace) == 710:
                digest = hashlib.sha256()
                for event_bank, event_addr, _prior, event_value in trace:
                    digest.update(struct.pack(">BHB", event_bank,
                                              event_addr, event_value))
                self.usb_stock_eeb5_eeaf_ready = bool(
                    self.usb_stock_c8cf_cold_latched and
                    self.usb_stock_df0b_stage == 2 and
                    digest.hexdigest() in {
                        # Cold-zero register image and the idempotent 91D1
                        # reconstruction image.  Both contain the same 710
                        # ordered writes; preserved RMW bits account for the
                        # distinct result digests.
                        "9b666f589d41e33360229c14efe5114ea9e95a2c662aa712e8dc4b91527a46c4",
                        "ffc5e198363f0488491a5c19d69a234ed894897506eddeefbf088501b1b7c989",
                    })
            elif len(trace) > 710:
                self.usb_stock_cold_trace = []

        # Preserved configuration drives stock 8FCF through one final
        # address-zero, 0x80-byte command-03 read after the twelve 9A8B
        # transactions.  Its three RMW writes close the history before DC3A.
        config_addresses = (
            0xC8AD, 0xC8AE, 0xC8AF, 0xC8AA, 0xC8AC,
            0xC8A1, 0xC8A2, 0xC8AB, 0xC8A3, 0xC8A4, 0xC8A9,
            0xC8AD, 0xC8AD, 0xC8AD, 0xC8AD,
            0xC65A, 0xCC35, 0x905F,
        )
        config_values = (
            0, 0, 0, 3, 3, 0, 0, 0, 0, 0x80, 1,
            0, 0, 0, 0,
        )
        config_event = (addr, old, value)
        if bank == 0 and self.usb_stock_eeb5_eeaf_ready and \
                not self.usb_stock_8fcf_ready:
            config_trace = self.usb_stock_8fcf_trace
            config_index = len(config_trace)
            if not config_trace and addr == config_addresses[0] and value == 0:
                self.usb_stock_8fcf_trace = [config_event]
            elif config_trace and config_index < len(config_addresses) and \
                    addr == config_addresses[config_index]:
                if config_index < len(config_values):
                    valid = value == config_values[config_index]
                else:
                    masks = (0xF7, 0xFE, 0xF7)
                    valid = value == (old & masks[config_index - 15])
                if valid:
                    config_trace.append(config_event)
                    if len(config_trace) == len(config_addresses):
                        self.usb_stock_8fcf_ready = True
                else:
                    self.usb_stock_8fcf_trace = (
                        [config_event] if addr == config_addresses[0] and
                        value == 0 else [])
            elif config_trace and addr in config_addresses:
                self.usb_stock_8fcf_trace = (
                    [config_event] if addr == config_addresses[0] and
                    value == 0 else [])

        dc_addresses = (
            0xCD31, 0xCD31, 0xCD30, 0xCD32, 0xCD33, 0xCC2A,
            0xCC2C, 0xCC2D, 0xC655, 0xC620, 0xC65A,
        )

        def dc_transition_valid(index, candidate):
            address, prior, new = candidate
            if address != dc_addresses[index]:
                return False
            rmw = {
                2: (0xF8, 0x05), 5: (0xF8, 0x04),
                8: (0xFE, 0x01), 9: (0xE0, 0x00),
                10: (0xFE, 0x01),
            }
            direct = {0: 0x04, 1: 0x02, 3: 0x00, 4: 0xC7,
                      6: 0xC7, 7: 0xC7}
            if index in rmw:
                and_mask, or_value = rmw[index]
                return new == ((prior & and_mask) | or_value)
            return new == direct[index]

        if bank == 0 and addr == 0xCD31 and value == 0x04 and \
                self.usb_stock_eeb5_eeaf_ready and \
                self.usb_stock_8fcf_ready and \
                not self.usb_stock_dc3a_ready:
            self.usb_stock_dc3a_trace = [(addr, old, value)]
        elif self.usb_stock_dc3a_trace and not self.usb_stock_dc3a_ready:
            candidate = (addr, old, value)
            index = len(self.usb_stock_dc3a_trace)
            if bank == 0 and index < len(dc_addresses) and \
                    dc_transition_valid(index, candidate):
                self.usb_stock_dc3a_trace.append(candidate)
                if len(self.usb_stock_dc3a_trace) == len(dc_addresses):
                    self.usb_stock_dc3a_ready = True
            elif addr != 0xCD31:
                self.usb_stock_dc3a_trace = []

        if bank == 0 and self.usb_stock_dc3a_ready and \
                not self.usb_stock_ee8a_ready and addr == 0xC801 and \
                value == ((old & 0xBF) | 0x40):
            self.usb_stock_ee8a_ready = True
            self.usb_stock_cc32_ready = False
        elif bank == 0 and self.usb_stock_ee8a_ready and addr == 0xCC32 and \
                value == (old & 0xFE):
            self.usb_stock_cc32_ready = True

        cc83_relevant = {
            0xCA06, 0xB410, 0xB411, 0xB420, 0xB421, 0xB412,
            0xB413, 0xB422, 0xB423, 0xB415, 0xB416, 0xB417,
            0xB425, 0xB426, 0xB427, 0xB41A, 0xB41B, 0xB42A,
            0xB42B, 0xB418, 0xB419, 0xB428, 0xB429, 0x4084,
            0x5084, 0xB401, 0xB482, 0xB480, 0xB430, 0xB298,
            0x6043, 0x6025,
        }
        if self.usb_stock_dc3a_ready and self.usb_stock_ee8a_ready and \
                self.usb_stock_cc32_ready and \
                self.usb_stock_link_ready_observed and \
                not self.usb_stock_cold_cc83_ready and addr in cc83_relevant:
            self.usb_stock_cold_cc83_trace.append((bank, addr, old, value))
            if len(self.usb_stock_cold_cc83_trace) > 34:
                del self.usb_stock_cold_cc83_trace[:-34]
            if validate_stock_cc83_bridge_trace(
                    self.usb_stock_cold_cc83_trace,
                    bytes.fromhex("4c176324")):
                self.usb_stock_cold_cc83_ready = True

        cf_addresses = (
            0xB264, 0xB265, 0xB266, 0xB267, 0xB26C, 0xB26D,
            0xB26E, 0xB26F, 0xB250, 0xB251, 0xCEF3, 0xCEF2,
            0xCEF0, 0xCEEF, 0xC807, 0xB281,
        )
        cf_direct = (0x08, 0x00, 0x08, 0x08, 0x08, 0x20,
                     0x08, 0x28, 0x00, 0x00, 0x08, 0x80)

        def cf_transition_valid(index, candidate):
            address, prior, new = candidate
            if address != cf_addresses[index]:
                return False
            rmw = {12: (0xF7, 0x00), 13: (0x7F, 0x00),
                   14: (0xFB, 0x04), 15: (0xCF, 0x10)}
            if index in rmw:
                and_mask, or_value = rmw[index]
                return new == ((prior & and_mask) | or_value)
            return new == cf_direct[index]

        if bank == 0 and addr == 0xB264 and value == 0x08 and \
                self.usb_stock_cold_cc83_ready:
            self.usb_stock_cf91_trace = [(addr, old, value)]
        elif self.usb_stock_cf91_trace and not self.usb_stock_cold_ready:
            candidate = (addr, old, value)
            index = len(self.usb_stock_cf91_trace)
            if bank == 0 and index < len(cf_addresses) and \
                    cf_transition_valid(index, candidate):
                self.usb_stock_cf91_trace.append(candidate)
                if len(self.usb_stock_cf91_trace) == len(cf_addresses):
                    self.usb_stock_cold_ready = bool(
                        self.usb_stock_c8cf_cold_latched and
                        self.usb_stock_5372_df0b_latched and
                        self.usb_stock_e300_df0b_latched)
                    self.usb_stock_c8cf_ready = self.usb_stock_cold_ready
                    self.usb_stock_4c8d_route_ready = \
                        self.usb_stock_cold_ready
                    self.usb_stock_52ef_ready = self.usb_stock_cold_ready
                    self.usb_stock_5372_ready = self.usb_stock_cold_ready
                    self.usb_stock_e300_ready = self.usb_stock_cold_ready
                    self.usb_stock_ee8a_ready = self.usb_stock_cold_ready
                    self.usb_stock_cc32_ready = self.usb_stock_cold_ready
            elif addr != 0xB264:
                self.usb_stock_cf91_trace = []

    def _record_usb_stock_prerequisite(self, addr: int, old: int,
                                       value: int):
        """Recognize stock BDFD-BE1C by ordered transitions, not end state."""
        event = (addr, old, value)
        trace = self.usb_stock_msc_prerequisite_trace
        expected_addresses = (
            0xC6A8, 0x92C8, 0x92C8, 0xCD31, 0xCD31,
            0xCC17, 0xCC17, 0xCC16, 0xCC18, 0xCC19,
            0x92C4, 0x9201, 0x9201,
            0xCC23, 0xCC23, 0xCC22, 0xCC22,
            0xCC1D, 0xCC1D, 0xCC5D, 0xCC5D,
            0xCC1C, 0xCC1E, 0xCC1F, 0xCC5C, 0xCC5E, 0xCC5F,
        )

        def transition_valid(index, candidate):
            address, prior, new = candidate
            if address != expected_addresses[index]:
                return False
            rmw = {
                0: (0xFF, 0x01), 1: (0xFE, 0x00), 2: (0xFD, 0x00),
                7: (0xF8, 0x04), 10: (0xFE, 0x00),
                11: (0xFE, 0x01), 12: (0xFE, 0x00),
                15: (0xEF, 0x00), 16: (0xF8, 0x07),
                21: (0xF8, 0x06), 24: (0xF8, 0x04),
            }
            direct = {
                3: 0x04, 4: 0x02, 5: 0x04, 6: 0x02,
                8: 0x01, 9: 0x90, 13: 0x04, 14: 0x02,
                17: 0x04, 18: 0x02, 19: 0x04, 20: 0x02,
                22: 0x00, 23: 0x8B, 25: 0x00, 26: 0xC7,
            }
            if index in rmw:
                and_mask, or_value = rmw[index]
                return new == ((prior & and_mask) | or_value)
            return new == direct[index]

        if addr == 0xC6A8 and transition_valid(0, event):
            trace = [event]
        elif trace and len(trace) < len(expected_addresses) and \
                transition_valid(len(trace), event):
            if len(trace) == 1:
                # The second BDA4 write proves this is a new full generation,
                # rather than E682's standalone C6A8 enable. Revoke every
                # downstream readiness predicate before accepting the rest.
                self.usb_stock_msc_dma_ready = False
                self.usb_stock_c24c_ready = False
                self.usb_stock_msc_initial_transition_ready = False
                self.usb_stock_b1c5_terminal_trace = []
                self.usb_stock_b1c5_terminal_ready = False
                self.usb_stock_3a2b_channel_trace = []
                self.usb_stock_3a2b_channel_ready = False
                self.usb_stock_3a2b_dma_trace = []
                self.usb_stock_3a2b_dma_ready = False
                self.usb_stock_3a2b_scsi_dma_trace = []
                self.usb_stock_3a2b_scsi_dma_ready = False
                self.usb_stock_cbw_dma_trace = []
                self.usb_stock_cbw_dma_ready = False
                self.usb_stock_cbw_tag_trace = []
                self.usb_stock_cbw_tag_ready = False
                self.usb_stock_bulk_in_arm_trace = []
                self.usb_stock_bulk_in_arm_ready = False
                self.usb_stock_bulk_in_event_ready = False
                self.usb_stock_c24c_trace = []
                self.usb_stock_c24c_validated_trace = []
            trace.append(event)
        else:
            trace = []
        self.usb_stock_msc_prerequisite_trace = trace
        if len(trace) == len(expected_addresses):
            self.usb_stock_msc_dma_ready = self.usb_stock_cold_ready
            if self.usb_stock_msc_dma_ready:
                self.usb_stock_msc_generation += 1

    def _record_usb_stock_c24c(self, bank: int, addr: int, old: int,
                               value: int):
        """Recognize a complete compiled C24C bridge transaction."""
        relevant = {
            0xB401, 0xCA06, 0xB410, 0xB411, 0xB420, 0xB421,
            0xB412, 0xB413, 0xB422, 0xB423,
            0xB415, 0xB416, 0xB417, 0xB425, 0xB426, 0xB427,
            0xB41A, 0xB41B, 0xB42A, 0xB42B,
            0xB418, 0xB419, 0xB428, 0xB429,
            0x4084, 0x5084, 0xB482, 0xB480, 0xB430, 0xB298,
            0x6043, 0x6025, 0xC659, 0xB402, 0xB434, 0xB436,
        }
        if addr not in relevant:
            return
        trace = self.usb_stock_c24c_trace
        trace.append((bank, addr, old, value))
        if len(trace) > 96:
            del trace[:-96]
        # C7A4 itself pulses B401 when reducing a lane mask, so that pair
        # alone is not a new C24C generation. Revoke only when the following
        # CA06 write proves CC83 has begun the full bridge transaction.
        if len(trace) >= 3 and \
                trace[-3][0:2] == (0, 0xB401) and \
                trace[-2][0:2] == (0, 0xB401) and \
                trace[-1][0:2] == (0, 0xCA06) and \
                trace[-3][3] == ((trace[-3][2] & 0xFE) | 0x01) and \
                trace[-2][3] == (trace[-2][2] & 0xFE):
            self.usb_stock_c24c_ready = False
            self.usb_stock_msc_initial_transition_ready = False
        config = bytes.fromhex("4c176324")
        for lane in range(16):
            for restore in (False, True):
                for length in range(42, min(len(trace), 47) + 1):
                    if validate_stock_c24c_bridge_trace(
                            trace[-length:], config,
                            link_param=self.regs.get(0xB404, 0),
                            initial_lane_mask=lane,
                            restore_b402=restore):
                        self.usb_stock_c24c_ready = bool(
                            self.usb_stock_cold_ready and
                            self.usb_stock_msc_dma_ready)
                        self.usb_stock_c24c_validated_trace = list(
                            trace[-length:])
                        self._pcie_link_recovery_event("c24c")
                        return

    def _usb_stock_prerequisite_write(self, hw: 'HardwareState', addr: int,
                                      value: int):
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_usb_stock_prerequisite(addr, old, value)

    def _timer_csr_read(self, hw: 'HardwareState', addr: int) -> int:
        """Timer CSR - auto-set ready bit after polling."""
        count = self.poll_counts.get(addr, 0)
        value = self.regs.get(addr, 0)
        if addr == 0xCC1D:
            # Timer 2 is the master enable window. While enabled (0x01), it stays running (0x01).
            # If idle (0x00), it returns 0x00.
            return value & 0x01
        elif addr == 0xCC5D:
            # Timer 4 is the periodic cadence timer. When enabled (bit 0 set), it transitions
            # to expired (bit 1 set, bit 0 clear) after counting (e.g. count >= 2).
            # When idle / unstarted (0x00), it remains 0x00.
            if (value & 0x01) and count >= 2:
                value = 0x02  # Expired
                self.regs[addr] = 0x02
            return value
        else:
            # Timer 0, 1, 3 (CC11, CC17, CC23) polling for delays
            if count >= 2:
                value |= 0x02  # Set ready/complete bit
                self.regs[addr] = value
            return value

    def _timer_csr_write(self, hw: 'HardwareState', addr: int, value: int):
        """Timer CSR write."""
        old = self.regs.get(addr, 0)
        if addr in (0xCC1D, 0xCC5D):
            cur = old
            if value & 0x04:  # Clear command: clears counter and expired status
                cur &= ~0x02
            if value & 0x02:  # Acknowledge command: clears expired status
                cur &= ~0x02
            if value & 0x01:  # Start/Enable command: starts timer (running=1, expired=0)
                cur = (cur & ~0x02) | 0x01
            elif (value & 0x04) or (value & 0x02):
                # Only clearing or acknowledging without start leaves running=0
                cur &= ~0x01
            elif value == 0:
                cur = 0
            self.regs[addr] = cur
        else:
            if value & 0x04:  # Clear flag
                value &= ~0x02
            self.regs[addr] = value
        self.poll_counts[addr] = 0
        if addr in (0xCC17, 0xCC23, 0xCC1D, 0xCC5D):
            self._record_usb_stock_prerequisite(addr, old, value)

    def _timer_dma_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """Timer/DMA status (0xCC89) - set complete bit after polling."""
        count = self.poll_counts.get(addr, 0)
        value = self.regs.get(addr, 0)
        # The firmware polls for bit 1 (0x02) to be set - indicating DMA complete
        if count >= 2:
            value |= 0x02  # Set complete bit
            self.regs[addr] = value
        return value

    # ============================================
    # PHY/CPU Callbacks
    # ============================================
    def _phy_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """PHY status - bit 0 = ready, bit 1 = busy."""
        # Return ready state: bit 0 set, bit 1 clear
        return 0x01

    def _phy_cmd_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        PHY command register write (0xCD31).

        This is a PHY/hardware control register. The firmware writes commands
        to it during USB operations. USB descriptor data is sent via hardware DMA
        directly from the descriptor table in ROM (around 0x0864).
        """
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_usb_stock_prerequisite(addr, old, value)

    def _cmd_engine_read(self, hw: 'HardwareState', addr: int) -> int:
        """Command engine - auto-clear bit 0 after polling."""
        count = self.poll_counts.get(addr, 0)
        value = self.regs.get(addr, 0)
        if count >= 3 and (value & 0x01):
            value &= ~0x01
            self.regs[addr] = value
        return value

    # ============================================
    # USB Command Injection
    # ============================================
    def queue_usb_command(self, cmd: int, addr: int, data: bytes = b''):
        """
        Queue a USB command for firmware processing.

        Commands (from python/usb.py):
        - 0xE4: Read from XDATA (addr = 0x5XXXXX maps to firmware XDATA)
        - 0xE5: Write to XDATA
        - 0x8A: SCSI write command

        Address mapping: (addr & 0x1FFFF) | 0x500000 in usb.py
        So 0x5XXXXX -> XDATA 0xXXXX (lower 17 bits)
        """
        usb_cmd = USBCommand(cmd=cmd, addr=addr, data=data)
        self.usb_cmd_queue.append(usb_cmd)

        if self.log_writes:
            print(f"[USB] Queued cmd=0x{cmd:02X} addr=0x{addr:04X} len={len(data)}")

        # Trigger USB interrupt to wake up firmware
        self._trigger_usb_interrupt()

    def queue_e4_read(self, xdata_addr: int, size: int = 1):
        """
        Queue an E4 read command (read XDATA).

        Format from usb.py: struct.pack('>BBBHB', 0xE4, size, addr >> 16, addr & 0xFFFF, 0)
        """
        # Pack command into EP0 buffer format
        cmd_bytes = bytes([
            0xE4,                      # Command
            size,                      # Size to read
            (xdata_addr >> 16) & 0xFF, # High byte (usually 0x05 for XDATA)
            (xdata_addr >> 8) & 0xFF,  # Mid byte
            xdata_addr & 0xFF,         # Low byte
            0x00                       # Reserved
        ])
        self.queue_usb_command(0xE4, xdata_addr & 0xFFFF, cmd_bytes)

    def queue_e5_write(self, xdata_addr: int, value: int):
        """
        Queue an E5 write command (write XDATA).

        Format from usb.py: struct.pack('>BBBHB', 0xE5, value, addr >> 16, addr & 0xFFFF, 0)
        """
        cmd_bytes = bytes([
            0xE5,                      # Command
            value & 0xFF,              # Value to write
            (xdata_addr >> 16) & 0xFF, # High byte (usually 0x05 for XDATA)
            (xdata_addr >> 8) & 0xFF,  # Mid byte
            xdata_addr & 0xFF,         # Low byte
            0x00                       # Reserved
        ])
        self.queue_usb_command(0xE5, xdata_addr & 0xFFFF, cmd_bytes)

    def queue_init_sequence(self):
        """
        Queue the USB initialization sequence from usb.py.

        Init sequence:
        - WriteOp(0x54b, b' ')   -> write 0x20 to 0x054B
        - WriteOp(0x54e, b'\x04') -> write 0x04 to 0x054E
        - WriteOp(0x0, b'\x01')  -> write 0x01 to 0x0000
        """
        print("[USB] === QUEUING INIT SEQUENCE ===")
        self.queue_e5_write(0x054B, 0x20)
        self.queue_e5_write(0x054E, 0x04)
        self.queue_e5_write(0x0000, 0x01)

    def inject_usb_command(self, cmd_type: int, xdata_addr: int, value: int = 0, size: int = 1):
        """
        Inject a USB vendor command (E4 read / E5 write) through MMIO registers.

        This sets up the firmware's vendor command path:
        0x0E5A (USB int) → 0x0E64 (bit5 SET) → 0x0EF4 (bit0 CLEAR)
        → 0x5333 (state check) → 0x4583 (vendor dispatch) → 0x35B7 (vendor handler)

        Only MMIO registers are set - no direct RAM writes. The firmware reads
        expected values through read hooks that simulate hardware behavior.

        cmd_type: 0xE4 (read) or 0xE5 (write)
        xdata_addr: Target XDATA address
        value: Value to write (for E5 commands)
        size: Bytes to read (for E4 commands)
        """
        # Ensure USB is connected before injecting a command
        # This sets up the necessary MMIO state for USB state machine
        if not self.usb_connected:
            self.usb_connected = True
            self.usb_controller.connect(
                getattr(self.usb_controller, "usb_speed", 2))
            print(f"[{self.cycles:8d}] [USB] Auto-connected USB for command injection")

        # Use USBController for the MMIO setup
        cdb = self.usb_controller.inject_vendor_command(
            cmd_type, xdata_addr, value, size
        )

        # Trigger USB interrupt
        self._pending_usb_interrupt = True

        # Note: USBController.inject_vendor_command() already handles RAM writes
        # when use_direct_ram=True, so no duplicate writes needed here

        print(f"[{self.cycles:8d}] [USB] Vendor command ready, triggering interrupt")

    def inject_scsi_write(self, lba: int, sectors: int, data: bytes):
        """
        Inject a 0x8A SCSI write command through MMIO registers.

        This sets up the firmware's SCSI command path. Data is written
        to the USB buffer at 0x8000 for DMA to the NVMe device.

        Args:
            lba: Logical Block Address to write to
            sectors: Number of 512-byte sectors to write
            data: Data to write (will be padded to sector boundary)
        """
        # Ensure USB is connected before injecting a command
        if not self.usb_connected:
            self.usb_connected = True
            self.usb_controller.connect(
                getattr(self.usb_controller, "usb_speed", 2))
            print(f"[{self.cycles:8d}] [USB] Auto-connected USB for SCSI command")

        # Use USBController for the MMIO setup
        cdb = self.usb_controller.inject_scsi_write_command(lba, sectors, data)

        # Trigger USB interrupt
        self._pending_usb_interrupt = True

        print(f"[{self.cycles:8d}] [USB] SCSI write command ready, triggering interrupt")

    def inject_scsi_vendor_cmd(self, opcode: int, cdb: bytes, data: bytes = b'',
                                is_write: bool = False):
        """
        Inject a SCSI vendor command (E0-E8) through MMIO registers.

        This sets up the firmware's vendor SCSI command path for commands
        used by patch.py for firmware updates.

        Args:
            opcode: SCSI vendor opcode (0xE0-0xE8)
            cdb: Complete CDB bytes (16 bytes max)
            data: Data for write commands (E1, E3, E5)
            is_write: True if this is a write command with data phase
        """
        # Ensure USB is connected before injecting a command
        if not self.usb_connected:
            self.usb_connected = True
            self.usb_controller.connect()
            print(f"[{self.cycles:8d}] [USB] Auto-connected USB for SCSI vendor command")

        # Use USBController for the MMIO setup
        cdb_padded = self.usb_controller.inject_scsi_vendor_command(
            opcode, cdb, data, is_write
        )

        # Trigger USB interrupt
        self._pending_usb_interrupt = True

        print(f"[{self.cycles:8d}] [USB] SCSI vendor command 0x{opcode:02X} ready, triggering interrupt")
        return cdb_padded

    def _trigger_usb_interrupt(self):
        """Trigger USB interrupt to process queued command."""
        if not self.usb_connected:
            return

        # Set USB endpoint interrupt bits
        # REG_INT_USB_STATUS (0xC802) bit 0 = endpoint 0 data ready
        # REG_USB_STATUS (0x9000) bit 0 = USB active
        self.regs[0xC802] |= 0x01  # EP0 data ready
        self.regs[0x9000] |= 0x01  # USB active

        # Set EP0 has data flag
        # REG_USB_EP0_CSR (0x9E10) - EP0 control/status
        self.regs[0x9E10] = 0x01  # Data available

        self.usb_cmd_pending = True

    def _process_usb_command(self):
        """
        Process next USB command in queue.
        Called when firmware reads USB endpoint buffer.
        """
        if not self.usb_cmd_queue:
            return None

        cmd = self.usb_cmd_queue.pop(0)
        print(f"[USB] Processing cmd=0x{cmd.cmd:02X} addr=0x{cmd.addr:04X}")

        # Copy command to EP0 buffer
        for i, b in enumerate(cmd.data[:64]):
            self.usb_ep0_buf[i] = b
        self.usb_ep0_len = len(cmd.data)

        # Handle E4 read - prepare response data
        if cmd.cmd == 0xE4 and self.memory:
            size = cmd.data[1] if len(cmd.data) > 1 else 1
            response = bytearray(size)
            for i in range(size):
                response[i] = self.memory.read_xdata(cmd.addr + i)
            cmd.response = bytes(response)
            print(f"[USB] E4 read response: {response.hex()}")

        # Handle E5 write - perform the write directly
        if cmd.cmd == 0xE5 and self.memory:
            value = cmd.data[1] if len(cmd.data) > 1 else 0
            self.memory.write_xdata(cmd.addr, value)
            print(f"[USB] E5 wrote 0x{value:02X} to 0x{cmd.addr:04X}")

        if not self.usb_cmd_queue:
            self.usb_cmd_pending = False

        return cmd

    # ============================================
    # USB Endpoint Callbacks
    # ============================================
    def _usb_ep0_buf_read(self, hw: 'HardwareState', addr: int) -> int:
        """Read from the USB EP0 staging window (0x9E00-0x9FFF)."""
        offset = addr - 0x9E00
        if offset < len(self.usb_ep0_buf):
            return self.usb_ep0_buf[offset]
        return 0x00

    def _usb_ep0_buf_write(self, hw: 'HardwareState', addr: int, value: int):
        """Write to the USB EP0 staging window (0x9E00-0x9FFF).

        This captures config descriptor writes. The firmware writes the config
        descriptor to 0x9E00, but then corrupts it before DMA. We capture the
        FIRST write to each offset - later overwrites are ignored.
        """
        self.regs[addr] = value
        offset = addr - 0x9E00
        self.usb_ep0_buf[offset] = value

        # Track which bytes have been captured (to ignore later overwrites)
        if not hasattr(self, '_usb_config_captured_offsets'):
            self._usb_config_captured_offsets = set()

        # Check for start of config descriptor (bLength=0x09, bDescriptorType=0x02)
        if offset == 0 and value == 0x09:
            # Might be config descriptor - start capturing
            self.usb_captured_config_desc = bytearray(256)
            self.usb_captured_config_desc[0] = value
            self.usb_capture_config_active = True
            self._usb_config_captured_offsets = {0}
        elif offset == 1 and self.usb_capture_config_active:
            if value == 0x02 and 1 not in self._usb_config_captured_offsets:
                # Confirmed config descriptor (bDescriptorType = 2)
                self.usb_captured_config_desc[1] = value
                self._usb_config_captured_offsets.add(1)
            elif value != 0x02:
                # Not a config descriptor, stop capturing
                self.usb_capture_config_active = False
                self.usb_captured_config_desc = bytearray()
                self._usb_config_captured_offsets = set()
        elif self.usb_capture_config_active and 2 <= offset < 256:
            # Only capture first write to each offset (ignore later corruptions)
            if offset not in self._usb_config_captured_offsets:
                self.usb_captured_config_desc[offset] = value
                self._usb_config_captured_offsets.add(offset)
        elif offset == 0 and value != 0x09:
            # Different descriptor or setup packet - stop capturing
            if self.usb_capture_config_active:
                # Keep the captured data but mark capture as complete
                self.usb_capture_config_active = False

    def load_config_descriptor_from_rom(self):
        """Load USB3 config descriptor from ROM and fix wTotalLength.

        The ROM at 0x58CF has USB3 config descriptor with wTotalLength=44,
        which only includes alt_setting 0 (BBB). However, alt_setting 1 (UAS)
        data continues immediately after at 0x58FB.

        This method parses the ROM to find the actual end of the config
        descriptor (including alt_setting 1), creates a copy with the
        correct wTotalLength, and stores it for use when returning
        config descriptor data to the host.
        """
        if self._memory is None:
            print("[USB] Warning: Cannot load config from ROM - no memory reference")
            return

        # USB3 config descriptor starts at 0x58CF in ROM
        USB3_CONFIG_OFFSET = 0x58CF

        # Parse the descriptor chain to find actual total length
        rom = self._memory.code
        if len(rom) < USB3_CONFIG_OFFSET + 9:
            print("[USB] Warning: ROM too small for config descriptor")
            return

        # Valid descriptor types for config descriptor contents
        valid_types = {0x02, 0x04, 0x05, 0x30, 0x24}  # config, interface, endpoint, SS companion, class-specific

        i = USB3_CONFIG_OFFSET
        total_len = 0
        while i < len(rom) - 1:
            bLength = rom[i]
            bDescriptorType = rom[i + 1]

            # Stop at invalid descriptors or when we hit next config descriptor
            if bLength == 0 or bDescriptorType not in valid_types:
                break

            # Stop if we hit another config descriptor (USB2 config at 0x5948)
            if bDescriptorType == 0x02 and i > USB3_CONFIG_OFFSET:
                break

            i += bLength
            total_len = i - USB3_CONFIG_OFFSET

        if total_len < 44:
            print(f"[USB] Warning: Parsed config descriptor too small ({total_len} bytes)")
            return

        # Extract the full descriptor and fix wTotalLength
        desc = bytearray(rom[USB3_CONFIG_OFFSET:USB3_CONFIG_OFFSET + total_len])
        old_len = desc[2] | (desc[3] << 8)
        desc[2] = total_len & 0xFF
        desc[3] = (total_len >> 8) & 0xFF

        self.usb_ss_config_from_rom = bytes(desc)
        print(f"[USB] Loaded USB3 config descriptor from ROM: {total_len} bytes (wTotalLength fixed {old_len} -> {total_len})")

        # Also load USB2 High Speed config descriptor from 0x5948
        # This has correct 512-byte max packet sizes for USB 2.0
        USB2_CONFIG_OFFSET = 0x5948

        if len(rom) < USB2_CONFIG_OFFSET + 9:
            print("[USB] Warning: ROM too small for USB2 config descriptor")
            return

        # Parse USB2 descriptor chain
        i = USB2_CONFIG_OFFSET
        total_len_usb2 = 0
        while i < len(rom) - 1:
            bLength = rom[i]
            bDescriptorType = rom[i + 1]

            # Valid types for USB2 config: config(0x02), interface(0x04), endpoint(0x05), class-specific(0x24)
            # No SS companion (0x30) in USB2
            valid_types_usb2 = {0x02, 0x04, 0x05, 0x24}

            if bLength == 0 or bDescriptorType not in valid_types_usb2:
                break

            # Stop if we hit another config descriptor
            if bDescriptorType == 0x02 and i > USB2_CONFIG_OFFSET:
                break

            i += bLength
            total_len_usb2 = i - USB2_CONFIG_OFFSET

        if total_len_usb2 < 32:
            print(f"[USB] Warning: Parsed USB2 config descriptor too small ({total_len_usb2} bytes)")
            return

        # Extract the USB2 descriptor and fix wTotalLength if needed
        desc_usb2 = bytearray(rom[USB2_CONFIG_OFFSET:USB2_CONFIG_OFFSET + total_len_usb2])
        old_len_usb2 = desc_usb2[2] | (desc_usb2[3] << 8)
        desc_usb2[2] = total_len_usb2 & 0xFF
        desc_usb2[3] = (total_len_usb2 >> 8) & 0xFF

        self.usb_hs_config_from_rom = bytes(desc_usb2)
        print(f"[USB] Loaded USB2 config descriptor from ROM: {total_len_usb2} bytes (wTotalLength: {old_len_usb2} -> {total_len_usb2})")

    def _extend_config_descriptor(self, base_desc: bytearray, requested_len: int) -> bytes:
        """Return config descriptor appropriate for current USB speed.

        Uses USB2 config (0x5948) for High Speed with 512-byte endpoints,
        or USB3 config (0x58CF) for SuperSpeed with 1024-byte endpoints.
        """
        # Check USB speed from controller
        usb_ctrl = getattr(self, 'usb_controller', None)
        usb_speed = getattr(usb_ctrl, 'usb_speed', 2) if usb_ctrl else 2

        # USB 2.0 High Speed (speed < 2) uses USB2 config descriptor
        if usb_speed < 2 and self.usb_hs_config_from_rom:
            print(f"[USB] Using USB2 High Speed config descriptor (speed={usb_speed})")
            return self.usb_hs_config_from_rom[:min(requested_len, len(self.usb_hs_config_from_rom))]

        # USB 3.0 SuperSpeed (speed >= 2) uses USB3 config descriptor
        if self.usb_ss_config_from_rom:
            return self.usb_ss_config_from_rom[:min(requested_len, len(self.usb_ss_config_from_rom))]

        # Fallback: return what firmware wrote (shouldn't happen normally)
        if len(base_desc) < 9:
            return bytes(base_desc[:requested_len])
        return bytes(base_desc[:min(requested_len, len(base_desc))])

    def _usb_ep0_csr_read(self, hw: 'HardwareState', addr: int) -> int:
        """Read USB EP0 CSR - check if command pending."""
        if getattr(self, "usb_ep0_out_data_active", False):
            return self.usb_ep0_buf[0x10]
        # Process next command when firmware reads CSR
        if self.usb_cmd_pending and self.usb_cmd_queue:
            self._process_usb_command()
            return 0x01  # Data ready
        return 0x00

    def _usb_ep0_csr_write(self, hw: 'HardwareState', addr: int, value: int):
        """Write dual-use EP0 CSR/response-buffer byte 16."""
        # Firmware builds EP0 responses contiguously at 0x9E00. Preserve byte
        # 16 for the later DMA even though this address also has CSR effects.
        if self.memory:
            self.memory.xdata[addr] = value
        self.usb_ep0_buf[0x10] = value
        if (getattr(self, "usb_capture_config_active", False) and
                len(self.usb_captured_config_desc) > 0x10):
            self.usb_captured_config_desc[0x10] = value
            self._usb_config_captured_offsets.add(0x10)
        if value & 0x80:  # Clear data ready
            self.regs[0x9E10] = 0x00
            # Trigger next command if queued
            if self.usb_cmd_queue:
                self._trigger_usb_interrupt()

    def _usb_ep0_transfer_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB EP0 transfer status (0xE712).

        The firmware polls this register waiting for bits 0 and 1 to be set,
        indicating the USB EP0 control transfer is complete.
        This happens after calling 0xE581 which initiates the DMA transfer.
        """
        count = self.poll_counts.get(addr, 0)
        value = self.regs.get(addr, 0)
        if self.regs.get(0xCC37, 0) & 0x04:
            self.pcie_rxpll_reset_poll_observed = True
        # After a few polls, set both bits to indicate transfer complete
        if count >= 2:
            value |= 0x03  # Set bits 0 and 1 (transfer complete)
            self.regs[addr] = value
        return value

    def _usb_91c0_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB PHY control read (0x91C0).

        At address 0x203B, firmware checks bit 1 of this register when
        the USB state machine is in state 2 (0x0A59=2).
        If bit 1 is SET, it calls 0x0322 which progresses the state machine.

        The firmware clears this register at 0xCA8C, but we need to return
        bit 1 SET when USB is connected to allow state machine progress.
        """
        if self.usb_connected:
            return 0x02  # Bit 1 SET - enables USB state machine progress
        return self.regs.get(addr, 0)

    def _usb_92c2_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB power state read (0x92C2).

        This register controls two different code paths:
        1. ISR at 0xE42A: checks bit 6 - if CLEAR, calls 0xBDA4 (state RESET)
           If bit 6 is SET, ISR skips the reset and returns immediately
        2. Main loop at 0x202A: checks bit 6 - if SET, calls 0x0322 (transfer)

        CRITICAL: 0xBDA4 is a STATE RESET function that clears 0x0AF7 and many
        other variables. We must NOT let it run during control transfers!

        During control transfers:
        - Return bit 6 SET to SKIP 0xBDA4 reset and preserve 0x0AF7=1
        - Main loop also needs bit 6 SET to call transfer handler
        """
        if self.usb_control_transfer_active:
            self.usb_92c2_read_count += 1
            # ALWAYS return bit 6 SET during control transfers to prevent
            # the state reset at 0xBDA4 from clearing 0x0AF7
            return 0x40
        return self.regs.get(addr, 0x40)  # Default: bit 6 SET (PD task enabled)

    def _usb_ep0_fifo_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        USB EP0 data FIFO write (UNUSED).

        NOTE: This function is not currently registered as a callback.
        Testing revealed that 0xC001 is UART TX only - USB descriptor data
        is sent via hardware DMA directly from ROM, not via firmware byte copies.
        Kept for potential future use if we discover the actual EP0 FIFO register.
        """
        self.usb_ep0_fifo.append(value)
        if self.log_writes:
            print(f"[{self.cycles:8d}] [USB] EP0 FIFO write: 0x{value:02X} (total: {len(self.usb_ep0_fifo)} bytes)")

    def _usb_ep0_dma_trigger_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        USB EP0 DMA control write (0x9092).

        Writing 0x04 triggers DMA transfer from EP0 FIFO to USB data buffer (0x8000).
        The transfer length is read from 0x9003-0x9004.
        Hardware sets bit 2 while busy, then clears it when complete.
        """
        self.regs[addr] = value

        if value == 0x01:
            # Descriptor send trigger - firmware wrote 0x01 to 0x9092
            # Firmware should have already configured:
            #   0x905B/0x905C = DMA source address (code ROM address of descriptor)
            #   0x9004 = transfer length
            # We DMA from the firmware-specified address to USB buffer at 0x8000

            # Read DMA source address from firmware-configured registers
            dma_addr_hi = self.regs.get(0x905B, 0)
            dma_addr_lo = self.regs.get(0x905C, 0)
            dma_src_addr = (dma_addr_hi << 8) | dma_addr_lo

            # Read transfer length from firmware-configured register
            dma_len = self.regs.get(0x9004, 0)
            if dma_len == 0:
                # Fallback: use stored wLength from pending descriptor request
                # (can't read from 0x9E06-0x9E07 because firmware overwrote with descriptor data)
                usb_ctrl = getattr(self, 'usb_controller', None)
                if usb_ctrl and usb_ctrl.pending_descriptor_request:
                    dma_len = usb_ctrl.pending_descriptor_request.get('length', 64)
                else:
                    # Last resort: read bLength from first byte of descriptor at 0x9E00
                    # This works for single descriptors like device/string
                    bLength = self.regs.get(0x9E00, 0)
                    if 2 <= bLength <= 255:
                        dma_len = bLength
                    else:
                        dma_len = 64  # Default max packet size

            print(f"[{self.cycles:8d}] [USB] Descriptor DMA trigger (0x9092=0x01): src=0x{dma_src_addr:04X} len={dma_len}")

            if self.memory and dma_src_addr > 0 and dma_len > 0:
                # Firmware specified a code ROM address - DMA from there
                desc_data = bytes(self.memory.code[dma_src_addr:dma_src_addr + dma_len])
                for i, b in enumerate(desc_data):
                    self.memory.xdata[0x8000 + i] = b
                print(f"[{self.cycles:8d}] [USB] DMA'd {len(desc_data)} bytes from code 0x{dma_src_addr:04X} to 0x8000: {desc_data[:min(32, len(desc_data))].hex()}")
            elif dma_src_addr == 0 and dma_len > 0:
                # Firmware set src to 0 - DMA from EP0 buffer at 0x9E00 where firmware wrote data
                # Check if we have captured config descriptor (firmware writes it but then corrupts)
                usb_ctrl = getattr(self, 'usb_controller', None)
                desc_type = None
                if usb_ctrl and usb_ctrl.pending_descriptor_request:
                    desc_type = usb_ctrl.pending_descriptor_request.get('type', None)

                if desc_type == 0x02 and len(self.usb_captured_config_desc) >= dma_len:
                    # Use captured config descriptor (firmware corrupts 0x9E00 before DMA)
                    # Add UAS alt_setting 1 with 4 endpoints for patch.py compatibility
                    desc_data = self._extend_config_descriptor(self.usb_captured_config_desc, dma_len)
                    print(f"[{self.cycles:8d}] [USB] Using captured config descriptor ({dma_len} bytes)")
                else:
                    # Use the dual-use EP0 response buffer. Byte 16 shares
                    # 0x9E10 with CSR side effects but remains response data.
                    desc_data = bytes(
                        self.usb_ep0_buf[i] if i < len(self.usb_ep0_buf)
                        else self.memory.xdata[0x9E00 + i]
                        for i in range(dma_len))

                for i, b in enumerate(desc_data):
                    self.memory.xdata[0x8000 + i] = b
                print(f"[{self.cycles:8d}] [USB] DMA'd {dma_len} bytes from EP0 buffer 0x9E00 to 0x8000: {desc_data[:min(32, dma_len)].hex()}")

            self.usb_control_transfer_active = False
            self.regs[0x9000] = self.regs.get(0x9000, 0) & ~0x01
            # NOTE: Don't clear usb_captured_config_desc here - firmware may trigger
            # DMA multiple times for one request. Capture is reset when new config
            # descriptor is written (offset 0 with value 0x09).

        elif value == 0x04:
            # DMA trigger - read length from 0x9003-0x9004
            # Firmware register map uses 0x9003 as high and 0x9004 as low.
            len_hi = self.regs.get(0x9003, 0)
            len_lo = self.regs.get(0x9004, 0)
            length = (len_hi << 8) | len_lo

            print(f"[{self.cycles:8d}] [USB] EP0 DMA trigger: length={length}, FIFO has {len(self.usb_ep0_fifo)} bytes")

            # Copy FIFO data to USB data buffer at 0x8000
            if self.memory and len(self.usb_ep0_fifo) > 0:
                copy_len = min(length, len(self.usb_ep0_fifo))
                for i in range(copy_len):
                    self.memory.xdata[0x8000 + i] = self.usb_ep0_fifo[i]

                print(f"[{self.cycles:8d}] [USB] EP0 DMA: copied {copy_len} bytes to 0x8000")
                print(f"[{self.cycles:8d}] [USB] EP0 DMA: data = {bytes(self.usb_ep0_fifo[:copy_len]).hex()}")

                # Clear the FIFO after transfer
                self.usb_ep0_fifo.clear()

                # Clear control transfer active flag since DMA is complete
                self.usb_control_transfer_active = False
                self.regs[0x9000] = self.regs.get(0x9000, 0) & ~0x01
            elif self.memory:
                # Handmade firmware stages EP0 responses directly in XDATA
                # 0x9E00 rather than writing the emulated FIFO byte-by-byte.
                for i in range(length):
                    self.memory.xdata[0x8000 + i] = (
                        self.usb_ep0_buf[i] if i < len(self.usb_ep0_buf)
                        else self.memory.xdata[0x9E00 + i])
                self.usb_control_transfer_active = False
                self.regs[0x9000] = self.regs.get(0x9000, 0) & ~0x01
                print(f"[{self.cycles:8d}] [USB] EP0 DMA: copied {length} bytes from 0x9E00 to 0x8000")

            # Set bit 2 (busy) - will be cleared on next read after poll
            self.regs[addr] = value | 0x04

        elif value == 0x02 and self.pending_control_out_data:
            payload = self.pending_control_out_data
            self.usb_ep0_out_data_active = True
            for offset, byte in enumerate(payload):
                self.regs[0x9E00 + offset] = byte
                self.usb_ep0_buf[offset] = byte
                if self.memory:
                    self.memory.xdata[0x9E00 + offset] = byte
            self.pending_control_out_data = b""
            self.regs[0x9091] = 0x04
            self.regs[0xC802] |= 0x01
            self._pending_usb_interrupt = True
            print(f"[{self.cycles:8d}] [USB] EP0 OUT DMA: delivered {len(payload)} bytes")

    def _usb_ep0_dma_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB EP0 DMA status read (0x9092).

        Firmware polls this waiting for bit 2 to clear (DMA complete).
        After the initial write of 0x04, the hardware will clear bit 2
        when the transfer is done.
        """
        count = self.poll_counts.get(addr, 0)
        value = self.regs.get(addr, 0)

        # After a few polls, clear bit 2 (DMA complete)
        if count >= 2 and (value & 0x04):
            value &= ~0x04  # Clear bit 2
            self.regs[addr] = value
            print(f"[{self.cycles:8d}] [USB] EP0 DMA complete (bit 2 cleared)")

        return value

    def _usb_9091_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB control state register read (0x9091).

        Two-phase control transfer handling:
          Phase 1 (bit 0): Setup packet handler at 0xA5A6
          Phase 2 (bit 1): DMA response handler at 0xD088

        The firmware loops at 0xA5E2-0xA60B writing 0x01 and waiting for bit 0 to clear.
        When bit 0 clears and bit 1 is set, 0xD088 is called for DMA response.
        """
        value = self.regs.get(addr, 0)

        # Track read count for phase transition
        count = getattr(self, '_usb_9091_read_count', 0)
        self._usb_9091_read_count = count + 1

        # Phase transition: after setup handler has processed the request,
        # clear bit 0 and set bit 1 to trigger data phase
        # The setup handler writes 0x01 repeatedly, so we detect that pattern
        if getattr(self, '_usb_9091_setup_writes', 0) >= 3 and (value & 0x01):
            if self.pending_control_out_data:
                value = 0x04  # Host-to-device DATA_OUT phase.
                self.usb_ep0_out_data_active = True
                for offset, byte in enumerate(self.pending_control_out_data):
                    self.regs[0x9E00 + offset] = byte
                    if self.memory:
                        self.memory.xdata[0x9E00 + offset] = byte
                self.pending_control_out_data = b""
            else:
                value = 0x02  # Status/IN response phase.
            self.regs[addr] = value
            self._usb_9091_setup_writes = 0  # Reset for next transfer
            print(f"[{self.cycles:8d}] [USB] 0x9091 phase transition: setup→data (0x01→0x02)")

        return value

    def _usb_9091_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        USB control state register write (0x9091).

        The firmware writes 0x01 to 0x9091 in a loop at 0xA5E2-0xA60B, waiting
        for hardware to complete the setup phase. After enough writes, we
        transition to the data phase by modifying the read value.
        """
        self.regs[addr] = value

        # Count writes of 0x01 (setup phase polling)
        if value == 0x01:
            count = getattr(self, '_usb_9091_setup_writes', 0)
            self._usb_9091_setup_writes = count + 1
            if self.log_writes:
                print(f"[{self.cycles:8d}] [USB] 0x9091 write 0x01 (setup poll #{count + 1})")
            if self.pending_control_out_data:
                # The handmade ISR acknowledges SETUP once and returns. Hand
                # the payload to DATA_OUT and raise its control event now;
                # waiting for the stock firmware's three-write polling loop
                # leaves F0 permanently stuck in setup.
                payload = self.pending_control_out_data
                self.usb_ep0_out_data_active = True
                for offset, byte in enumerate(payload):
                    self.regs[0x9E00 + offset] = byte
                    self.usb_ep0_buf[offset] = byte
                    if self.memory:
                        self.memory.xdata[0x9E00 + offset] = byte
                self.pending_control_out_data = b""
                self.regs[addr] = 0x04
                self.regs[0xC802] |= 0x01
                self._pending_usb_interrupt = True
        elif value == 0x04 and self.usb_ep0_out_data_active:
            # The firmware has consumed the host-to-device data stage. Model
            # the hardware's following status-IN event instead of leaving the
            # transfer stuck in DATA_OUT forever. This is required to execute
            # handlers such as F0 which intentionally consume their payload
            # only after DATA_OUT has completed.
            self.usb_ep0_out_data_active = False
            self.regs[addr] = 0x10
            self.regs[0xC802] |= 0x01
            self._pending_usb_interrupt = True

    def _usb_9301_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        USB endpoint status read (0x9301).

        Bit 6 triggers the interrupt dispatch to device descriptor handler (0x0359).
        After reading, hardware clears bit 6 (acknowledge behavior).
        This allows the main loop at 0xD83B to proceed after the interrupt dispatch.
        """
        value = self.regs.get(addr, 0)

        # Clear bit 6 after reading (hardware acknowledge)
        if value & 0x40:
            self.regs[addr] = value & ~0x40
            if self.log_reads:
                print(f"[{self.cycles:8d}] [USB] 0x9301 read=0x{value:02X}, bit 6 cleared")

        return value

    def _usb_9301_ep0_arm_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        USB endpoint 0 arm/control write (0x9301).

        When bit 6 (0x40) is written, this arms EP0 for data transfer.
        The firmware handles GET_DESCRIPTOR by setting up DMA from its code ROM.
        The emulator does NOT assist with descriptor reading - the firmware must
        do all the work itself via proper MMIO/DMA emulation.
        """
        self.regs[addr] = value

        if value & 0x40:
            print(f"[{self.cycles:8d}] [USB] EP0 armed (9301=0x{value:02X})")

            # Log the request type for debugging (but don't process it!)
            bmRequestType = self.regs.get(0x9E00, 0)
            bRequest = self.regs.get(0x9E01, 0)

            if bmRequestType == 0x80 and bRequest == 0x06:  # GET_DESCRIPTOR
                desc_type = self.regs.get(0x9E03, 0)
                desc_index = self.regs.get(0x9E02, 0)
                wLength = self.regs.get(0x9E06, 0) | (self.regs.get(0x9E07, 0) << 8)
                print(f"[{self.cycles:8d}] [USB] GET_DESCRIPTOR: type=0x{desc_type:02X} "
                      f"index={desc_index} len={wLength} (firmware will handle via DMA)")
                # NOTE: The emulator does NOT populate the buffer here!
                # The firmware reads descriptors from its code ROM and sets up DMA.
                # If descriptors aren't appearing, fix the firmware DMA path, not here.

            # Mark control transfer completion status
            # - IN transfers (bit 7 set): Stay active until DMA completes (at 0x9092 write)
            # - OUT transfers (bit 7 clear): Complete when EP0 armed for status stage
            wLength = self.regs.get(0x9E06, 0) | (self.regs.get(0x9E07, 0) << 8)
            if bmRequestType & 0x80:
                # IN transfer (GET_DESCRIPTOR etc.) - stay active until DMA completes
                # The flag will be cleared by _usb_ep0_dma_trigger_write when DMA finishes
                pass
            else:
                # OUT transfer - complete if no data stage (wLength=0)
                # or firmware has processed the data
                if wLength == 0:
                    # No-data OUT transfer (SET_ADDRESS, SET_CONFIGURATION, etc.)
                    self.usb_control_transfer_active = False
                    self.usb_cmd_pending = False
                    self.regs[0x9000] = self.regs.get(0x9000, 0) & ~0x01
                    print(f"[{self.cycles:8d}] [USB] OUT transfer complete (no data stage)")

    # ============================================================
    # DEPRECATED: _read_descriptor_from_firmware
    # This function violates the pure DMA principle documented above.
    # The firmware must handle descriptor reading itself via DMA.
    # DO NOT USE THIS FUNCTION - it only exists to document the
    # descriptor offsets found during analysis.
    #
    # Known descriptor offsets in fw.bin:
    # - Device descriptor: 0x0627 (18 bytes)
    # - Config descriptor: 0x5948 (BBB mode, 32 bytes)
    # ============================================================

    # ============================================================
    # DEPRECATED: _usb_get_descriptor_data
    # This function contained hardcoded USB descriptor data which
    # violates the pure DMA principle. The firmware must generate
    # all descriptor responses itself via DMA from its code ROM.
    # DO NOT resurrect this function!
    # ============================================================

    def _usb_ep_data_buf_read(self, hw: 'HardwareState', addr: int) -> int:
        """Read from USB EP data buffer (0xD800-0xDFFF)."""
        offset = addr - 0xD800
        if offset < len(self.usb_ep_data_buf):
            value = self.usb_ep_data_buf[offset]
            # Always log reads from command area (first 8 bytes)
            if offset < 8:
                print(f"[{self.cycles:8d}] [USB] Read EP buf 0x{addr:04X} = 0x{value:02X}")
            return value
        return 0x00

    @staticmethod
    def _usb_msc_transition(event, address, bit, set_bit):
        event_address, old, new = event
        expected = old | bit if set_bit else old & ~bit
        return event_address == address and new == expected

    def _record_stock_buffered_write(self, addr: int, old: int, value: int):
        """Validate stock 0206 preparation and retain the later 47D5 writes."""
        event = (addr, old, value)
        pending = self.usb_stock_buffered_in_pending
        if pending is not None:
            if addr in (0x9006, 0xD808, 0xD809, 0xD80A, 0xD80B, 0x90A1):
                pending["service_events"].append(event)
            return

        service_addresses = (0xD808, 0xD809, 0xD80A, 0xD80B, 0x90A1)
        service_trace = self.usb_stock_bot_alt0_service_trace
        if addr == 0xD808:
            self.usb_stock_bot_alt0_service_trace = [event]
        elif service_trace:
            expected = service_addresses[len(service_trace)]
            if addr == expected:
                service_trace.append(event)
            elif addr in service_addresses:
                self.usb_stock_bot_alt0_service_trace = []

        if addr == 0x901A:
            self.usb_stock_buffered_in_trace = [event]
            return
        trace = self.usb_stock_buffered_in_trace
        if not trace:
            return
        trace.append(event)
        if len(trace) > 32:
            self.usb_stock_buffered_in_trace = []
            return
        if addr != 0xC8D4 or value != 0:
            return

        expected = (
            (0x901A, trace[0][2]),
            (0xC8D4, 0xA0), (0x905B, 0x80), (0x905C, 0x00),
            (0xD802, 0x80), (0xD803, 0x00),
            (0xD804, 0x00), (0xD805, 0x00),
            (0xD806, 0x00), (0xD807, 0x00), (0xD80F, 0x00),
            (0xD800, 0x03), (0xC509, 0x01), (0x905A, 0x10),
            (0x90E1, 0x01), (0xC509, 0x00), (0xC8D4, 0x00),
        )
        ordered = [(address, new) for address, _old, new in trace]
        descriptor_valid = (
            0 < trace[0][2] <= 0xFF and
            ordered == list(expected) and
            bool(self.regs.get(0xC800, 0) & 0x04) and
            bool(self.regs.get(0xC801, 0) & 0x10) and
            self.usb_bulk_engine_active)
        prerequisite_ready = bool(
            self.usb_stock_52ef_ready and
            self.usb_stock_5372_ready and
            self.usb_stock_e300_ready and
            self.usb_stock_4c8d_route_ready and
            self.usb_stock_c8cf_ready and
            self.usb_stock_cold_ready and
            self.usb_stock_msc_dma_ready and
            self.usb_stock_c24c_ready and
            self.usb_stock_3a2b_channel_ready and
            self.usb_stock_3a2b_dma_ready and
            self.usb_stock_3a2b_scsi_dma_ready and
            self.usb_stock_cbw_dma_ready and
            self.usb_stock_cbw_tag_ready and
            self.usb_stock_bulk_in_event_ready and
            self.usb_stock_msc_initial_transition_ready)
        preamble_valid = descriptor_valid and prerequisite_ready
        entry = {
            "source": 0x8000,
            "length": trace[0][2],
            "prepare_writes": ordered,
            "service_writes": [],
            "descriptor_valid": descriptor_valid,
            "cold_prerequisite_ready": self.usb_stock_cold_ready,
            "controller_c8cf_ready": self.usb_stock_c8cf_ready,
            "controller_4c8d_route_ready": self.usb_stock_4c8d_route_ready,
            "controller_52ef_ready": self.usb_stock_52ef_ready,
            "controller_5372_ready": self.usb_stock_5372_ready,
            "controller_e300_ready": self.usb_stock_e300_ready,
            "dma_prerequisite_ready": self.usb_stock_msc_dma_ready,
            "c24c_ready": self.usb_stock_c24c_ready,
            "queue_dispatch_ready": bool(
                self.usb_stock_3a2b_channel_ready and
                self.usb_stock_3a2b_dma_ready and
                self.usb_stock_3a2b_scsi_dma_ready),
            "scsi_dma_reset_ready": self.usb_stock_3a2b_scsi_dma_ready,
            "cbw_dma_ready": self.usb_stock_cbw_dma_ready,
            "cbw_tag_ready": self.usb_stock_cbw_tag_ready,
            "bulk_in_arm_writes": list(self.usb_stock_bulk_in_arm_trace),
            "bulk_in_arm_ready": self.usb_stock_bulk_in_arm_ready,
            "bulk_in_event_ready": self.usb_stock_bulk_in_event_ready,
            "initial_transition_ready":
                self.usb_stock_msc_initial_transition_ready,
            "preamble_valid": preamble_valid,
            "service_valid": False,
            "completion_writes": [],
            "next_cbw_armed": False,
            "valid": False,
            "completion_state": (
                "ready" if preamble_valid else
                "stalled_missing_cold_path" if descriptor_valid and
                not self.usb_stock_cold_ready else
                "stalled_missing_queue_dispatch" if descriptor_valid and
                not (self.usb_stock_3a2b_channel_ready and
                     self.usb_stock_3a2b_dma_ready and
                     self.usb_stock_3a2b_scsi_dma_ready) else
                "stalled_missing_cbw_dma" if descriptor_valid and
                not self.usb_stock_cbw_dma_ready else
                "stalled_missing_cbw_tag" if descriptor_valid and
                not self.usb_stock_cbw_tag_ready else
                "stalled_missing_bulk_in_event" if descriptor_valid and
                not self.usb_stock_bulk_in_event_ready else
                "stalled_missing_msc_init" if descriptor_valid else
                "rejected_descriptor"),
        }
        self.usb_stock_buffered_in_log.append(entry)
        self.usb_stock_buffered_in_trace = []
        if not preamble_valid:
            return
        # The 5281 arm and its later interrupt acknowledgement belong to one
        # data/CSW generation. A subsequent IN phase must reproduce both.
        self.usb_stock_bulk_in_arm_trace = []
        self.usb_stock_bulk_in_arm_ready = False
        self.usb_stock_bulk_in_event_ready = False
        self.usb_stock_buffered_in_pending = {
            "log": entry,
            "service_events": [],
        }
        # Exact 0206 commits the descriptor at 90E1.  Keep its readable value
        # independent of completion notification: the 2026-08-12 physical
        # probe delivered all 36 data bytes while the immediate E4 snapshot
        # still read 90E1=1.  Endpoint activation may clear it at a later
        # lifecycle boundary, but data retirement itself demonstrably need not.
        # Stock has two independent BOT routes into 47D5 under C802.2. Exercise
        # each consumer independently, plus a missing-source failure, without
        # claiming which hardware producer physical silicon will use.
        source = self.usb_stock_completion_event_source
        self.regs[0xC520] = self.regs.get(0xC520, 0) & ~0x02
        self.regs[0xC42C] = self.regs.get(0xC42C, 0) & ~0x01
        entry["completion_source_acknowledged"] = False
        if source == "c520":
            self.regs[0xC520] |= 0x02
            entry["completion_source"] = "C520.1"
        elif source == "c42c":
            self.regs[0xC42C] |= 0x01
            self.usb_stock_c42c_completion_ack_pending = True
            entry["completion_source"] = "C42C.0"
        elif source == "none":
            entry["completion_source"] = "none"
            entry["completion_state"] = "stalled_missing_completion_source"
            return
        else:
            raise RuntimeError(f"unknown stock completion source {source!r}")
        self.regs[0xC802] = 0x04
        self.regs[0x9101] = 0x00
        self._pending_usb_interrupt = True

    def _usb_stock_buffered_sequence_write(self, hw: 'HardwareState',
                                           addr: int, value: int):
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        if addr == 0x9006:
            self.regs[0x9101] = self.regs.get(0x9101, 0) & ~0x40
            if not (self.regs.get(0x9101, 0) & 0x7F):
                self.regs[0xC802] = self.regs.get(0xC802, 0) & ~0x01
        self._record_stock_buffered_write(addr, old, value)
        self._record_nvme_buffered_write(addr, old, value)

    def _usb_stock_bulk_in_arm_write(self, hw: 'HardwareState', addr: int,
                                     value: int):
        """Model stock 5281 Data-IN producer and its later 9101.2/9093.3 event ack."""
        if self.usb_stock_bulk_in_arm_ready and addr == 0x9093 and \
                (value & 0x08) and (self.regs.get(0x9101, 0) & 0x04):
            self.usb_stock_bulk_in_event_ready = True
            self.regs[0x9093] = self.regs.get(0x9093, 0) & ~0x08
            self.regs[0x9101] &= ~0x04
            if not (self.regs.get(0x9101, 0) & 0x3F):
                self.regs[0xC802] &= ~0x01
            if self.usb_stock_buffered_in_log:
                self.usb_stock_buffered_in_log[-1]["bulk_in_event_ready"] = True
            self.usb_stock_bulk_in_arm_ready = False
            self.usb_stock_bulk_in_arm_trace = []
            return
        elif addr == 0x9093 and (value & 0x02):
            self.regs[0x9093] = self.regs.get(0x9093, 0) & ~0x02
            return
        else:
            self.regs[addr] = value

        if self.usb_stock_bulk_in_arm_ready:
            return
        expected_addresses = (0x9007, 0x9008, 0x9093, 0x9094)
        trace = self.usb_stock_bulk_in_arm_trace
        event = (addr, value)
        if not trace:
            if addr == 0x9007:
                self.usb_stock_bulk_in_arm_trace = [event]
            return
        index = len(trace)
        valid = addr == expected_addresses[index]
        if addr == 0x9093:
            valid = valid and value == 0x08
        elif addr == 0x9094:
            valid = valid and value == 0x02
        if valid:
            trace.append(event)
            if len(trace) == len(expected_addresses):
                self.usb_stock_bulk_in_arm_ready = True
                length = ((self.regs.get(0x9007, 0) << 8) |
                          self.regs.get(0x9008, 0))
                service_valid = bool(
                    not (self.regs.get(0x9000, 0) & 0x01) and
                    0 < length <= 0x400 and
                    self.usb_bulk_engine_active and
                    self.usb_stock_cbw_dma_ready and
                    self.usb_stock_cbw_tag_ready)
                entry = {
                    "path": "STOCK_5281_BULK_IN",
                    "source": 0x8000,
                    "length": length,
                    "prepare_writes": [],
                    "service_writes": [],
                    "bulk_in_arm_writes": list(self.usb_stock_bulk_in_arm_trace),
                    "bulk_in_arm_ready": True,
                    "bulk_in_event_ready": False,
                    "bot_alt0_selected": not bool(
                        self.regs.get(0x9000, 0) & 0x01),
                    "engine_active": self.usb_bulk_engine_active,
                    "cbw_dma_ready": self.usb_stock_cbw_dma_ready,
                    "cbw_tag_ready": self.usb_stock_cbw_tag_ready,
                    "service_valid": service_valid,
                    "valid": service_valid,
                    "completion_state": (
                        "completed" if service_valid else "rejected_5281_service"),
                    "completion_writes": [],
                    "next_cbw_armed": False,
                }
                self.usb_stock_buffered_in_log.append(entry)
                if service_valid and not self.usb_sw_bulk_in_fault and self.memory:
                    payload = bytes(self.read(0x8000 + offset)
                                    for offset in range(length))
                    self.usb_msc_transfers.append(payload)

                self.regs[0xC802] = self.regs.get(0xC802, 0) | 0x01
                if self.usb_stock_bulk_in_event_auto:
                    self.regs[0x9101] = self.regs.get(0x9101, 0) | 0x04
                    self._pending_usb_interrupt = True
        elif addr in expected_addresses:
            self.usb_stock_bulk_in_arm_trace = []
            self.usb_stock_bulk_in_arm_ready = False
            self.usb_stock_bulk_in_event_ready = False

    def _record_nvme_buffered_write(self, addr: int, old: int, value: int):
        """Require the complete ordered 0206 queue branch, not final state."""
        trace = self.usb_nvme_bulk_in_trace
        if addr == 0xC8D4 and value & 0x80 and value != 0xA0:
            self.usb_nvme_bulk_in_trace = [(addr, old, value)]
            return
        if not trace:
            return
        trace.append((addr, old, value))
        if len(trace) > 24:
            self.usb_nvme_bulk_in_trace = []
            return
        if addr != 0xC8D4 or value != 0:
            return

        slot = trace[0][2] & 0x3F
        ordered = [(address, new) for address, _old, new in trace]
        expected = [
            (0xC8D4, 0x80 | slot), (0xC4ED, slot),
            (0xD802, 0xA4), (0xD803, 0x00),
            (0xD804, 0x00), (0xD805, 0x00),
            (0xD806, 0x00), (0xD807, 0x00), (0xD80F, 0x00),
            (0xD800, 0x03), (0xC509, 0x01),
            (0x90B0 + slot, 0x10), (0x9137 + slot, 0x01),
            (0xC509, 0x00), (0xC8D4, 0x00),
        ]
        valid = (
            slot == 0 and ordered == expected and
            self.memory is not None and self.nvme_io_data_length in (512, 1024))
        self.usb_nvme_bulk_in_ordered_log.append({
            "slot": slot, "ordered_writes": ordered, "valid": valid})
        self.usb_nvme_bulk_in_trace = []
        descriptor = {
            "slot": slot,
            "trigger": 1,
            "source": ((self.usb_ep_data_buf[2] << 8) |
                       self.usb_ep_data_buf[3]),
            "dma_config": trace[0][2],
            "dma_slot": trace[1][2] & 0x3F,
            "slot_mode": 0x10,
            "xfer_ctrl": 1,
            "buffer_ctrl": self.usb_ep_data_buf[0],
            "descriptor_tail": bytes(self.usb_ep_data_buf[4:8] +
                                     self.usb_ep_data_buf[15:16]),
            "length": self.nvme_io_data_length,
            "valid": valid,
        }
        self.usb_nvme_bulk_in_log.append(descriptor)
        if valid and not self.usb_nvme_bulk_in_fault:
            payload = bytes(self._read_xdata_for_dma(0xA400 + offset)
                            for offset in range(self.nvme_io_data_length))
            self.usb_msc_transfers.append(payload)
            if self.usb_stock_bulk_in_event_auto:
                self.regs[0x9093] = self.regs.get(0x9093, 0) | 0x08
                self.regs[0x9101] = self.regs.get(0x9101, 0) | 0x04
                self._pending_usb_interrupt = True

    def _usb_nvme_sequence_write(self, hw: 'HardwareState', addr: int,
                                 value: int):
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_nvme_buffered_write(addr, old, value)

    def _usb_msc_sequence_write(self, hw: 'HardwareState', addr: int,
                                value: int):
        """Record and finish the exact stock CODE 494D-49BC transaction."""
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_stock_buffered_write(addr, old, value)
        event = (addr, old, value)
        self.usb_msc_doorbell_trace.append(event)
        if len(self.usb_msc_doorbell_trace) > 64:
            del self.usb_msc_doorbell_trace[:-64]

        pending = self.usb_msc_pending_transfer
        if pending is None:
            return
        pending["post_events"].append(event)
        if not self._usb_msc_transition(event, 0xC42D, 0x01, False):
            pending["log"]["postamble_valid"] = False
            self.usb_msc_pending_transfer = None
            return

        entry = pending["log"]
        entry["postamble_valid"] = True
        entry["ordered_writes"] = [
            (address, new) for address, _old, new in
            pending["pre_events"] + pending["post_events"]]
        entry["valid"] = bool(
            entry["preamble_valid"] and entry["engine_active"])
        entry["initialization_transition"] = bool(
            self.usb_stock_msc_dma_ready and self.usb_stock_c24c_ready and
            entry["preamble_valid"] and
            entry["length"] == 13 and pending["payload"][:4] == b"USBS")
        if entry["initialization_transition"]:
            self.usb_stock_msc_initial_transition_ready = True
        if entry["valid"]:
            self.usb_msc_transfers.append(pending["payload"])
        # C42C is a consumed doorbell/status source, not a sticky software
        # ownership bit. Physical snapshots and stock's later status tests see
        # it deassert after the ordered transaction completes.
        self.regs[0xC42C] = self.regs.get(0xC42C, 0) & ~0x01
        self.usb_msc_pending_transfer = None
        if self.log_writes:
            print(f"[{self.cycles:8d}] [USB_MSC] Firmware sent "
                  f"{entry['length']} bytes (valid={entry['valid']}): "
                  f"{pending['payload'].hex()}")

    def _usb_msc_trigger_write(self, hw: 'HardwareState', addr: int,
                               value: int):
        """Latch C42C only after the exact ordered stock control sequence."""
        old = self.regs.get(addr, 0)
        if self.usb_stock_c42c_completion_ack_pending and value == 0x01:
            self.regs[addr] = old & ~0x01
            self.usb_stock_c42c_completion_ack_pending = False
            self.regs[0xC802] = self.regs.get(0xC802, 0) & ~0x04
            if self.usb_stock_buffered_in_log:
                self.usb_stock_buffered_in_log[-1][
                    "completion_source_acknowledged"] = True
            return
        self.regs[addr] = value
        event = (addr, old, value)
        self.usb_msc_doorbell_trace.append(event)
        if len(self.usb_msc_doorbell_trace) > 64:
            del self.usb_msc_doorbell_trace[:-64]
        if not value & 0x01:
            return

        pre_events = self.usb_msc_doorbell_trace[-18:]
        expected = (
            (0x900B, 0x02, True),
            (0x900B, 0x04, True),
            (0xC42A, 0x01, True),
            (0x900B, 0x01, True),
            (0xC42A, 0x02, True),
            (0xC42A, 0x04, True),
            (0xC42A, 0x08, True),
            (0xC42A, 0x10, True),
            (0x900B, 0x02, False),
            (0x900B, 0x04, False),
            (0xC42A, 0x01, False),
            (0x900B, 0x01, False),
            (0xC42A, 0x02, False),
            (0xC42A, 0x04, False),
            (0xC42A, 0x08, False),
            (0xC42A, 0x10, False),
        )
        preamble_valid = (
            len(pre_events) == 18 and
            all(self._usb_msc_transition(pre_events[index], *transition)
                for index, transition in enumerate(expected)) and
            pre_events[-2][0] == 0x901A and
            0 < pre_events[-2][2] <= 0xFF and
            pre_events[-1][0] == 0xC42C and pre_events[-1][2] == 0x01)
        length = self.regs.get(0x901A, 0)
        payload = bytes(self.usb_ep_data_buf[:length])
        entry = {
            "length": length,
            "msc_cfg": self.regs.get(0x900B, 0),
            "doorbell": self.regs.get(0xC42A, 0),
            "engine_active": self.usb_bulk_engine_active,
            "preamble_valid": preamble_valid,
            "postamble_valid": False,
            "ordered_writes": [],
            "valid": False,
        }
        self.usb_msc_trigger_log.append(entry)
        if preamble_valid:
            self.usb_msc_pending_transfer = {
                "log": entry,
                "payload": payload,
                "pre_events": pre_events,
                "post_events": [],
            }

    def _usb_sw_bulk_in_trigger_write(self, hw: 'HardwareState', addr: int,
                                      value: int):
        """Model the recovery firmware's hardware-oriented SW DMA bulk IN."""
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_stock_buffered_write(addr, old, value)
        pending = self.usb_stock_buffered_in_pending
        if pending is not None:
            events = pending["service_events"]
            entry = pending["log"]
            entry["service_writes"] = [
                (address, new) for address, _old, new in events]
            service_valid = (
                value == 0x01 and len(events) == 6 and
                self._usb_msc_transition(events[0], 0x9006, 0x01, True) and
                [event[0] for event in events[1:]] ==
                [0xD808, 0xD809, 0xD80A, 0xD80B, 0x90A1] and
                self.usb_ep_data_buf[0] == 0x03 and
                self.usb_ep_data_buf[2:8] == bytes((0x80, 0, 0, 0, 0, 0)) and
                self.regs.get(0xC8D4, 0) == 0)
            entry["service_valid"] = service_valid
            entry["valid"] = bool(entry["preamble_valid"] and service_valid)
            entry["completion_state"] = (
                "completed" if entry["valid"] else "rejected_service")
            if entry["valid"] and not self.usb_sw_bulk_in_fault:
                source, length = entry["source"], entry["length"]
                payload = bytes(self.read(source + offset)
                                for offset in range(length))
                self.usb_msc_transfers.append(payload)
            self.usb_stock_buffered_in_pending = None
            self.regs[0xC520] = self.regs.get(0xC520, 0) & ~0x02
            if entry.get("completion_source") == "C520.1":
                self.regs[0xC802] = self.regs.get(0xC802, 0) & ~0x04
            return
        alt_trace = self.usb_stock_bot_alt0_service_trace
        if len(alt_trace) == 5 and alt_trace[-1][0] == 0x90A1:
            ordered = [(address, new) for address, _old, new in alt_trace]
            residue = (ordered[0][1] | (ordered[1][1] << 8) |
                       (ordered[2][1] << 16) | (ordered[3][1] << 24))
            length = 13
            bot_tx_state = self.memory.xdata[0xA840] if self.memory else None

            if bot_tx_state == 1:
                # BOT_TX_DATA is produced by 5281 for firmware-buffered data,
                # or by the separately validated ordered NVMe-slot path.
                # Neither data producer may be replaced by 3258.
                path = "BOT_ALT0_4D3E_3258"
                service_valid = False
                completion_state = "rejected_3258_during_data_phase"
            elif bot_tx_state == 2:
                # BOT_TX_CSW: state classifies the direct 3258 service; the
                # residue commit to D808-D80B independently validates the CSW.
                path = "BOT_ALT0_4D3E_3258"
                service_valid = bool(
                    value == 0x01 and not (self.regs.get(0x9000, 0) & 0x01) and
                    [address for address, _new in ordered] ==
                    [0xD808, 0xD809, 0xD80A, 0xD80B, 0x90A1] and
                    self.usb_bulk_engine_active and
                    self.usb_stock_cbw_dma_ready and
                    self.usb_stock_cbw_tag_ready)
                completion_state = (
                    "completed" if service_valid else "rejected_bot_alt0_service")
            else:
                # A prepared legacy 47D5 generation is consumed by the
                # usb_stock_buffered_in_pending branch above.  Status bits by
                # themselves do not authorize an otherwise unclassified 3258.
                path = "BOT_ALT0_UNCLASSIFIED_3258"
                service_valid = False
                completion_state = "rejected_3258_outside_proven_phase"

            entry = {
                "path": path,
                "source": 0x8000,
                "length": length,
                "residue": residue,
                "prepare_writes": [],
                "service_writes": ordered,
                "bulk_in_arm_writes": [],
                "bulk_in_arm_ready": False,
                "bulk_in_event_ready": False,
                "bot_alt0_selected": not bool(
                    self.regs.get(0x9000, 0) & 0x01),
                "engine_active": self.usb_bulk_engine_active,
                "cbw_dma_ready": self.usb_stock_cbw_dma_ready,
                "cbw_tag_ready": self.usb_stock_cbw_tag_ready,
                "service_valid": service_valid,
                "valid": service_valid,
                "completion_state": completion_state,
                "completion_writes": [],
                "next_cbw_armed": False,
            }
            self.usb_stock_buffered_in_log.append(entry)
            if service_valid and not self.usb_sw_bulk_in_fault and self.memory:
                payload = bytes(self.read(0x8000 + offset)
                                for offset in range(13))
                self.usb_msc_transfers.append(payload)
            self.usb_stock_bot_alt0_service_trace = []
            self.usb_stock_buffered_in_trace = []
            return
        if value != 0x01 or self.regs.get(0xC8D4, 0) != 0xA0:
            return
        source = ((self.regs.get(0x905B, 0) << 8) |
                  self.regs.get(0x905C, 0))
        length = self.regs.get(0x901A, 0)
        setup = self.usb_sw_bulk_in_setup or {}
        self.usb_sw_bulk_in_setup = None
        self.usb_sw_bulk_in_log.append({
            "source": source,
            "length": length,
            "dma_config": self.regs.get(0xC8D4, 0),
            "ep_config": self.regs.get(0x905A, 0),
            "xfer_ctrl": self.regs.get(0xC509, 0),
            "descriptor_direction": self.usb_ep_data_buf[0],
            "descriptor_source": setup.get("descriptor_source"),
            "descriptor_length": setup.get("length"),
            "descriptor_prepared": setup.get("valid", False),
        })
        if self.usb_sw_bulk_in_fault:
            return
        if not setup.get("valid", False):
            return
        if not self.memory or source + length > len(self.memory.xdata):
            return
        payload = bytes(self.read(source + offset) for offset in range(length))
        self.usb_msc_transfers.append(payload)
        self.regs[0x9118] = 0x81
        self.regs[0x9101] = self.regs.get(0x9101, 0) | 0x20

    def _usb_nvme_bulk_in_trigger_write(self, hw: 'HardwareState', addr: int,
                                        value: int):
        """Record 0206's slot trigger; transfer waits for ordered cleanup."""
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_nvme_buffered_write(addr, old, value)

    def _usb_bulk_ep_command_write(self, hw: 'HardwareState', addr: int,
                                   value: int):
        self.regs[addr] = value
        if value == 0x01:
            # Endpoint activation starts a new transport generation.  The
            # Linux physical lifecycle cleared a readable 90E1 commit before
            # 90E3=2 re-armed the parser.  Do not let a stale
            # descriptor or inferred service event leak across that boundary.
            self.usb_endpoint_generation += 1
            if self.usb_stock_buffered_in_log:
                entry = self.usb_stock_buffered_in_log[-1]
                if entry.get("completion_state", "").startswith("stalled_"):
                    entry["revoked_by_endpoint_activation"] = True
            self.usb_stock_buffered_in_trace = []
            self.usb_stock_buffered_in_pending = None
            self.usb_stock_bot_alt0_service_trace = []
            self.regs[0x90E1] = 0x00
            self.regs[0xC520] = self.regs.get(0xC520, 0) & ~0x02
            self.regs[0xC802] = self.regs.get(0xC802, 0) & ~0x04
            self.regs[0x90A1] = 0x00
            self.usb_bulk_engine_active = True
            self.usb_cbw_armed = False
            self.usb_ep_ready_ack_pending = False
        if value == 0x02:
            self.usb_cbw_armed = self.usb_bulk_engine_active
            self.regs[0x9101] = self.regs.get(0x9101, 0) & ~0x20
            if self.usb_stock_buffered_in_log:
                entry = self.usb_stock_buffered_in_log[-1]
                if entry.get("valid") and not entry.get("next_cbw_armed"):
                    entry["completion_writes"].append((addr, value))
                    entry["next_cbw_armed"] = self.usb_cbw_armed

    def _usb_ep_buf_addr_write(self, hw: 'HardwareState', addr: int, value: int):
        """Write to USB EP buffer address registers (0x905B/0x905C).

        Firmware writes the DMA source address here:
        - 0x905B = high byte of source address
        - 0x905C = low byte of source address

        When DMA is triggered (via D800), data is read from this address.
        """
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_stock_buffered_write(addr, old, value)
        if addr == 0x905B:
            print(f"[{self.cycles:8d}] [DMA] EP buf addr high = 0x{value:02X}")
        else:
            print(f"[{self.cycles:8d}] [DMA] EP buf addr low = 0x{value:02X}")

    def _usb_sw_dma_trigger_write(self, hw: 'HardwareState', addr: int,
                                  value: int):
        """Latch a bulk-OUT destination when firmware arms 90E1.

        The live 905B:905C registers are shared by subsequent controller
        operations.  D802:D803 are the descriptor bytes consumed at the
        trigger and remain the authoritative address for the in-flight OUT.
        """
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_stock_buffered_write(addr, old, value)
        if (self.usb_sw_dma_out_destination is None and value == 0x01 and
                self.regs.get(0xC8D4, 0) == 0xA0 and
                self.regs.get(0x905A, 0) == 0x08 and
                self.usb_ep_data_buf[0] == 0x04):
            self.usb_sw_dma_out_destination = (
                (self.usb_ep_data_buf[2] << 8) | self.usb_ep_data_buf[3])

    def _record_usb_stock_cbw_tag_write(self, addr: int, value: int):
        """Require stock's 3186 tag staging and pre-dispatch D80C clear."""
        expected = (
            (0xD804, self.regs.get(0x911F, 0)),
            (0xD805, self.regs.get(0x9120, 0)),
            (0xD806, self.regs.get(0x9121, 0)),
            (0xD807, self.regs.get(0x9122, 0)),
            (0xD80C, 0x00),
        )
        if self.usb_stock_cbw_tag_ready:
            return
        trace = self.usb_stock_cbw_tag_trace
        event = (addr, value)
        if not trace:
            if self.usb_stock_cbw_dma_ready and event == expected[0]:
                self.usb_stock_cbw_tag_trace = [event]
            return
        index = len(trace)
        if index < len(expected) and event == expected[index]:
            trace.append(event)
            if len(trace) == len(expected):
                self.usb_stock_cbw_tag_ready = True
        elif addr in {item[0] for item in expected}:
            self.usb_stock_cbw_tag_trace = []
            self.usb_stock_cbw_tag_ready = False

    def _usb_ep_data_buf_write(self, hw: 'HardwareState', addr: int, value: int):
        """Write to USB EP data buffer (0xD800-0xDFFF).

        D800 is the DMA control register. Writing 0x03 or 0x04 triggers DMA:
        - 0x03: DMA from address in 0x905B/0x905C to USB buffer
        - 0x04: DMA from address in 0xC4EA/0xC4EB (for E5 writes)

        This is PURE DMA - addresses come entirely from firmware register writes.
        The emulator does NOT determine addresses based on USB request type.
        """
        offset = addr - 0xD800
        if addr in (0xD804, 0xD805, 0xD806, 0xD807, 0xD80C):
            self._record_usb_stock_cbw_tag_write(addr, value)
        if offset < len(self.usb_ep_data_buf):
            old = self.usb_ep_data_buf[offset]
            self.usb_ep_data_buf[offset] = value
            if offset <= 15:
                self._record_stock_buffered_write(addr, old, value)
                self._record_nvme_buffered_write(addr, old, value)

        if addr == 0xD800 and value == 0x03:
            source = ((self.regs.get(0x905B, 0) << 8) |
                      self.regs.get(0x905C, 0))
            descriptor_source = ((self.usb_ep_data_buf[2] << 8) |
                                 self.usb_ep_data_buf[3])
            length = self.regs.get(0x901A, 0)
            self.usb_sw_bulk_in_setup = {
                "descriptor_source": descriptor_source,
                "length": length,
                "valid": (
                    self.regs.get(0xC8D4, 0) == 0xA0 and
                    self.regs.get(0x905A, 0) == 0x10 and
                    bool(self.regs.get(0xC509, 0) & 0x01) and
                    descriptor_source == source and
                    length != 0),
            }

        # DMA trigger at D800
        if (addr == 0xD800 and value in (0x03, 0x04) and
                not (value == 0x04 and self.regs.get(0x905A, 0) == 0x08)):
            # Get source address from registers firmware wrote
            src_hi = self.regs.get(0x905B, 0)
            src_lo = self.regs.get(0x905C, 0)
            src_addr = (src_hi << 8) | src_lo

            if src_addr > 0 and self.memory:
                # Software-DMA IN uses 901A; legacy descriptor paths retain
                # their D807/default length behavior.
                if value == 0x03 and self.regs.get(0xC8D4, 0) == 0xA0:
                    xfer_len = self.regs.get(0x901A, 0)
                else:
                    xfer_len = self.regs.get(0xD807, 0)
                if xfer_len == 0:
                    xfer_len = 64  # Default EP0 max packet size

                print(f"[{self.cycles:8d}] [DMA] Trigger D800=0x{value:02X}: "
                      f"src=0x{src_addr:04X} len={xfer_len}")

                # Perform DMA: read from source, write to USB buffer at 0x8000
                for i in range(xfer_len):
                    # Read from XDATA (includes flash mirror via callbacks)
                    byte = self._read_xdata_for_dma(src_addr + i)
                    self.memory.xdata[0x8000 + i] = byte

                print(f"[{self.cycles:8d}] [DMA] Copied {xfer_len} bytes from 0x{src_addr:04X} to 0x8000")

        # E5 write DMA (uses different address registers)
        if addr == 0xD800 and value == 0x04 and self.usb_cmd_type == 0xE5:
            if not getattr(self, '_e5_dma_done', False):
                data = self.regs.get(0xC4E8, 0)
                addr_hi = self.regs.get(0xC4EA, 0)
                addr_lo = self.regs.get(0xC4EB, 0)
                target_addr = (addr_hi << 8) | addr_lo

                if data != 0xFF and target_addr > 0:
                    if self.memory and target_addr < 0x6000:
                        self.memory.xdata[target_addr] = data
                        self._e5_dma_done = True
                        self.usb_cmd_pending = False  # E5 command complete
                        self.usb_cmd_type = 0  # Reset command type
                        print(f"[{self.cycles:8d}] [DMA] E5 write: 0x{data:02X} to XDATA[0x{target_addr:04X}]")
                        print(f"[{self.cycles:8d}] [USB] E5 command completed")

    def _read_xdata_for_dma(self, addr: int) -> int:
        """Read from XDATA for DMA, using callbacks if registered."""
        # Check for callback (e.g., flash mirror)
        if addr in self.read_callbacks:
            return self.read_callbacks[addr](self, addr)
        # Direct XDATA read
        if self.memory and addr < len(self.memory.xdata):
            return self.memory.xdata[addr]
        return 0x00

    def _usb_ep_status_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Read USB EP status register 0xC4EC - indicates USB data availability.

        The EP loop at 0x18A5 checks C4EC bit 0 to see if there's USB data:
        - Bit 0 SET (0x01): Continue EP loop processing (for E4 commands)
        - Bit 0 CLEAR (0x00): Jump to 0x194F (E5 command handler path)

        For E5 commands, we need to return 0x00 so the firmware takes the
        E5 path at 0x18A8 → 0x194F → 0x197A (E5 handler).
        """
        # Track EP loop iterations
        if self.usb_cmd_pending:
            if not hasattr(self, '_c4ec_read_count'):
                self._c4ec_read_count = 0
            self._c4ec_read_count += 1

            # For E5 commands, return 0x00 to take the E5 path at 0x18A8
            # This triggers: 0x18A8 ljmp 0x194F → 0x197A E5 check
            if self.usb_cmd_type == 0xE5:
                value = 0x00
                print(f"[{self.cycles:8d}] [USB] Read 0xC4EC = 0x{value:02X} (E5 path - bit 0 CLEAR)")
                return value

            # For E4 commands, return 0x01 for the first several reads to allow
            # full command processing through the EP loop
            if self._c4ec_read_count <= 3:
                value = 0x01
                print(f"[{self.cycles:8d}] [USB] Read 0xC4EC = 0x{value:02X} (EP loop iter {self._c4ec_read_count})")
            else:
                # After enough iterations, return 0 to exit EP loop
                value = 0x00
                print(f"[{self.cycles:8d}] [USB] Read 0xC4EC = 0x{value:02X} (exit EP loop)")
            return value

        # Normal read when no command pending
        return self.regs.get(addr, 0x00)

    def _usb_ep_index_write(self, hw: 'HardwareState', addr: int, value: int):
        """Write USB EP index register 0xC4ED - selects which endpoint to query."""
        # Low 5 bits are the endpoint index (0-31)
        self.usb_ep_selected = value & 0x1F
        old = self.regs.get(addr, 0)
        self.regs[addr] = value
        self._record_nvme_buffered_write(addr, old, value)
        if self.usb_cmd_pending:
            print(f"[{self.cycles:8d}] [USB] Select EP index {self.usb_ep_selected}")

    def _usb_ep_id_low_read(self, hw: 'HardwareState', addr: int) -> int:
        """Read USB EP ID low byte (0xC4EE) for currently selected endpoint."""
        # Only an active EP0 control transfer owns the RAM-backed endpoint ID.
        # usb_cmd_pending can remain latched after its status stage and must not
        # override a later MSC/NVMe queue-source read.
        # This matches what firmware expects (it compares 0xC4EE/0xC4EF with 0x0056/0x0057)
        if self.usb_control_transfer_active and self.usb_ep_selected == 0 and \
                self.memory:
            # Read the expected value from RAM and return it so comparison passes
            expected = self.memory.xdata[0x0056]
            print(f"[{self.cycles:8d}] [USB] EP0 ID low = 0x{expected:02X} (from RAM 0x0056)")
            return expected
        return self.regs.get(addr, 0xFF)

    def _usb_ep_id_high_read(self, hw: 'HardwareState', addr: int) -> int:
        """Read USB EP ID high byte (0xC4EF) for currently selected endpoint."""
        # Only an active EP0 control transfer owns the RAM-backed endpoint ID.
        # This matches what firmware expects (it compares 0xC4EE/0xC4EF with 0x0056/0x0057)
        if self.usb_control_transfer_active and self.usb_ep_selected == 0 and \
                self.memory:
            # Read the expected value from RAM and return it so comparison passes
            expected = self.memory.xdata[0x0057]
            print(f"[{self.cycles:8d}] [USB] EP0 ID high = 0x{expected:02X} (from RAM 0x0057)")
            return expected
        return self.regs.get(addr, 0xFF)

    def _usb_ep_data_ready_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Read USB endpoint data ready register (0x90A1-0x90C0).
        Returns bit 0 = 1 when USB command is pending for that endpoint.
        """
        ep_index = addr - 0x90A1  # EP0 is at 0x90A1, EP1 at 0x90A2, etc.
        value = self.regs.get(addr, 0)

        # When USB command pending and this is the target endpoint, keep bit 0 set
        if self.usb_cmd_pending and ep_index == 0:
            value |= 0x01  # Bit 0 = data ready
            if self.log_reads:
                print(f"[{self.cycles:8d}] [USB] EP{ep_index} data ready = 0x{value:02X} (cmd pending)")
        return value

    def _usb_ep_status_reg_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Read USB endpoint status register (0x9096-0x90A0).

        At 0x18F5 firmware reads this register, and at 0x18F6 "jz 0x191B" skips
        command processing if the value is 0. So we need NON-ZERO to process commands.

        The bit mask table at 0x5BC9 is: 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80
        For EP index N, bit (N % 8) must be set in register 0x9096 + (N / 8).
        """
        ep_index = addr - 0x9096  # EP0 is at 0x9096, EP1 at 0x9097, etc.
        value = self.regs.get(addr, 0)

        # When USB command pending and this is EP0, return non-zero to enable command processing
        # The firmware ANDs this value with a bit mask (0x01 for EP0) and checks if non-zero
        if self.usb_cmd_pending and ep_index == 0:
            value = 0x01  # Bit 0 set for EP0
            print(f"[{self.cycles:8d}] [USB] EP{ep_index} status reg 0x{addr:04X} = 0x{value:02X} (cmd pending)")
            return value
        return value

    def _usb_ep_ready_ack_write(self, hw: 'HardwareState', addr: int,
                                value: int):
        """Model 9096's event acknowledgement without erasing init writes."""
        if self.usb_ep_ready_ack_pending:
            self.regs[addr] = self.regs.get(addr, 0) & ~value
            self.usb_ep_ready_ack_pending = False
            self.regs[0x9101] = self.regs.get(0x9101, 0) & ~0x20
            if not (self.regs.get(0x9101, 0) & 0x3F):
                self.regs[0xC802] = self.regs.get(0xC802, 0) & ~0x01
            if self.usb_stock_buffered_in_log:
                entry = self.usb_stock_buffered_in_log[-1]
                if entry.get("valid"):
                    entry.setdefault("completion_acknowledgements", []).append(
                        (addr, value))
        else:
            self.regs[addr] = value

    def _usb_e5_value_read(self, hw: 'HardwareState', addr: int) -> int:
        """
        Read USB E5 value register 0xC47A.

        When an E5 command is pending, this returns the injected value.
        The firmware reads this at 0x1800 (movx a, @dptr after mov dptr, #0xc47a)
        and stores it to IDATA[0x38] at 0x1801.

        The firmware clears this register (writes 0xFF) at 0x1178 before calling
        the EP loop at 0x17DB. We preserve the injected value until it's read
        by the E5 handler at 0x17FD-0x1801.

        After the value is read, we clear usb_cmd_pending to allow the firmware
        to exit the command loop. Unlike E4 which uses DMA at 0xB296 to signal
        completion, E5 commands complete when the value is read.
        """
        if self.usb_cmd_pending and self.usb_cmd_type == 0xE5:
            value = self.usb_e5_pending_value
            print(f"[{self.cycles:8d}] [USB] Read E5 value reg 0xC47A = 0x{value:02X} (pending E5)")

            # For E5 commands, don't clear pending yet - let the firmware continue
            # processing. The command completes when the DMA write happens (D800=0x04)
            # or after a timeout. Track that we've delivered the value.
            self._e5_value_delivered = True

            return value

        # Normal read
        return self.regs.get(addr, 0x00)

    def _usb_e5_value_write(self, hw: 'HardwareState', addr: int, value: int):
        """
        Write USB E5 value register 0xC47A.

        The firmware writes 0xFF to this register at 0x1176-0x1178 to clear it
        after processing each command. We preserve the pending E5 value by
        ignoring clears (0xFF writes) while an E5 command is pending.
        """
        if self.usb_cmd_pending and self.usb_cmd_type == 0xE5 and value == 0xFF:
            # Ignore clear while E5 command is pending
            print(f"[{self.cycles:8d}] [USB] Ignoring write 0xFF to 0xC47A (E5 pending)")
            return

        # Normal write - update the register
        self.regs[addr] = value

    # ============================================
    # Main Read/Write Interface
    # ============================================
    def read(self, addr: int) -> int:
        """Read from hardware register."""
        addr &= 0xFFFF

        bank = 1 if self.memory and self.memory.sfr[0x93 - 0x80] else 0
        if bank:
            value = self.dpx_regs.get(addr, 0x00)
            # Bank-one descriptor-engine kick is a hardware-owned completion
            # bit.  It previously auto-cleared accidentally through ordinary
            # XDATA; keep that silicon behavior now that all DPX MMIO is routed
            # through the hardware model.
            if addr == 0x1238 and (value & 0x01):
                key = (1, addr)
                self.poll_counts[key] = self.poll_counts.get(key, 0) + 1
                if self.poll_counts[key] >= 5:
                    value &= 0xFE
                    self.dpx_regs[addr] = value
                    self.poll_counts[key] = 0
            return value

        # Only handle hardware registers (>= 0x6000)
        if addr < 0x6000:
            return 0x00  # Should not be called for RAM

        self.poll_counts[addr] = self.poll_counts.get(addr, 0) + 1

        # Debug: trace CE55 reads
        if addr == 0xCE55:
            has_callback = addr in self.read_callbacks
            print(f"[{self.cycles:8d}] [DEBUG] Reading CE55, callback registered: {has_callback}")

        if addr in self.read_callbacks:
            value = self.read_callbacks[addr](self, addr)
        elif addr in self.regs:
            value = self.regs[addr]
        else:
            value = 0x00

        if self.log_reads:
            print(f"[{self.cycles:8d}] [HW] Read  0x{addr:04X} = 0x{value:02X}")

        if addr == 0xC6B3 and self.usb_stock_dc3a_ready and \
                (value & 0x30):
            self.usb_stock_link_ready_observed = True

        return value

    def write(self, addr: int, value: int):
        """Write to hardware register."""
        addr &= 0xFFFF
        value &= 0xFF

        bank = 1 if self.memory and self.memory.sfr[0x93 - 0x80] else 0
        if bank:
            old = self.dpx_regs.get(addr, 0x00)
            self.dpx_regs[addr] = value
            if addr == 0x40B0:
                self._pcie_acdf_event(
                    "40b0_03" if (value & 0x0F) == 0x03 else "40b0_other")
            if addr == 0x1238:
                self.poll_counts[(1, addr)] = 0
            self._record_usb_stock_cold(1, addr, old, value)
            self._record_usb_stock_c24c(1, addr, old, value)
            return

        # Only handle hardware registers (>= 0x6000)
        if addr < 0x6000:
            return  # Should not be called for RAM

        if self.log_writes:
            print(f"[{self.cycles:8d}] [HW] Write 0x{addr:04X} = 0x{value:02X}")

        old = self.regs.get(addr, 0x00)
        self._record_usb_stock_cold(0, addr, old, value)
        self._record_usb_stock_c24c(0, addr, old, value)
        self._record_usb_stock_3a2b_channel(addr, old, value)
        self._record_usb_stock_3a2b_dma(addr, old, value)
        self._record_usb_stock_3a2b_scsi_dma(addr, old, value)

        if addr in self.write_callbacks:
            self.write_callbacks[addr](self, addr, value)
        else:
            self.regs[addr] = value

    # ============================================
    # Tick - Advance Hardware State
    # ============================================
    def tick(self, cycles: int, cpu=None):
        """Advance hardware state by cycles."""
        self.cycles += cycles

        # In proxy mode, skip all fake USB/interrupt injection
        # Real hardware handles everything
        if self.proxy_mode:
            return

        # USB plug-in event after delay
        # Skip if a USB command is already pending to avoid interfering with it
        if not self.usb_connected and self.cycles > self.usb_connect_delay and not self.usb_cmd_pending:
            self.usb_connected = True
            print(f"\n[{self.cycles:8d}] [HW] === USB PLUG-IN EVENT ===")

            # Preserve the speed explicitly selected by the test. Replaying
            # connect() with its SuperSpeed default made FS/HS descriptor
            # tests silently exercise the SS path after the delayed event.
            self.usb_controller.connect(
                getattr(self.usb_controller, "usb_speed", 2))

            # Set NVMe queue busy - triggers the usb_ep_loop_180d(1) call
            self.regs[0xC471] = 0x01  # Bit 0 - queue busy

            # Re-enable PD task path by setting 0x91C0 bit 1
            # The firmware clears this at 0xCA8B during init, but we need it set
            # for the main loop at 0x2027 to call the PD task at 0x0322
            self.regs[0x91C0] = 0x02  # Bit 1 - enables PD task in main loop

            # Set PD interrupt pending - this triggers the PD handler
            # Bit 2 (0x04) is the fallback path at 0x9354 when 0x0A9D != 0x01/0x02
            # Bit 3 (0x08) is for port 1 when 0x0A9D == 0x01
            self.regs[0xCA0D] = 0x0C  # Bits 2+3 - PD interrupt (covers both paths)
            self.regs[0xCA0E] = 0x04  # Bit 2 - PD interrupt for port 2

            # Set debug trigger
            self.regs[0xC80A] = 0x40  # Bit 6 - triggers PD debug output at 0x935E

            # Set PD event info for debug output
            # These are read by 0xAE89 to print [PD_int:XX:XX] and determine message type
            self.regs[0xE40F] = 0x01  # PD event type (bit 0 = Source_Cap)
            self.regs[0xE410] = 0x00  # PD sub-event

            print(f"[{self.cycles:8d}] [HW] USB: 0x9000=0x81, C802=0x05, C471=0x01, CA0D=0x0C, E40F=0x01")
            print(f"[{self.cycles:8d}] [HW] USB state machine: firmware will poll 0xCE89 to transition states")

            # Trigger External Interrupt 0 to invoke the interrupt handler at 0x0E33
            # This requires IE register (0xA8) to have EA (bit 7) and EX0 (bit 0) set
            if cpu:
                # Enable global interrupts (EA) and EX0 in IE register
                ie = self.memory.read_sfr(0xA8) if self.memory else 0
                ie |= 0x81  # EA (bit 7) + EX0 (bit 0)
                if self.memory:
                    self.memory.write_sfr(0xA8, ie)
                cpu._ext0_pending = True
                print(f"[{self.cycles:8d}] [HW] Triggered EX0 interrupt (IE=0x{ie:02X})")

        # Periodic timer interrupt
        if self.cycles % 1000 == 0:
            self.regs[0xC806] |= 0x01

        # Inject USB command after USB connected and additional delay
        # Only inject if usb_inject_cmd was set (via --usb-cmd option)
        if self.usb_connected and not self.usb_injected and self.usb_inject_cmd:
            if self.cycles > self.usb_connect_delay + self.usb_inject_delay:
                self.usb_injected = True
                cmd_type, addr, val_or_size = self.usb_inject_cmd
                print(f"\n[{self.cycles:8d}] [HW] === INJECTING USB COMMAND ===")
                if cmd_type == 0xE4:
                    self.inject_usb_command(0xE4, addr, size=val_or_size)
                elif cmd_type == 0xE5:
                    self.inject_usb_command(0xE5, addr, value=val_or_size)
                else:
                    print(f"[HW] Unknown USB command type: 0x{cmd_type:02X}")

        # Trigger EX0 interrupt after USB command injection
        if hasattr(self, '_pending_usb_interrupt') and self._pending_usb_interrupt and cpu:
            self._pending_usb_interrupt = False
            # Enable global interrupts (EA) and EX0 in IE register
            ie = self.memory.read_sfr(0xA8) if self.memory else 0
            ie |= 0x81  # EA (bit 7) + EX0 (bit 0)
            if self.memory:
                self.memory.write_sfr(0xA8, ie)
            cpu._ext0_pending = True
            print(f"[{self.cycles:8d}] [HW] Triggered EX0 interrupt for USB command (IE=0x{ie:02X})")



def create_hardware_hooks(memory: 'Memory', hw: HardwareState, proxy: 'UARTProxy' = None, proxy_mask: list = None):
    """
    Register hardware hooks with memory system.
    Only hooks hardware register addresses (>= 0x6000).

    Args:
        memory: Memory system to hook
        hw: HardwareState for emulation mode
        proxy: Optional UARTProxy for real hardware mode
               If provided, MMIO reads/writes go to real hardware instead of emulation
        proxy_mask: List of (start, end) tuples for address ranges to emulate instead of proxy
                    Used to discover minimum set of registers that need real hardware
    """
    if proxy_mask is None:
        proxy_mask = []

    # Hardware register ranges (all >= 0x6000)
    # In proxy mode, we proxy EVERYTHING from 0x6000-0xFFFF to real hardware.
    # In emulation mode, we only hook known MMIO ranges.
    if proxy is not None:
        # Proxy mode: pass through ALL XDATA >= 0x6000 to real hardware.
        # DPX-banked accesses (any address with DPX=1) are handled by
        # dedicated CMD_READ_DPX/CMD_WRITE_DPX commands in memory.py.
        mmio_ranges = [
            (0x6000, 0x10000),  # Everything from 0x6000-0xFFFF goes to real hardware
        ]
    else:
        mmio_ranges = [
            (0x8000, 0x9000),   # USB/SCSI Data Buffer
            (0x9000, 0x9400),   # USB Interface
            (0x92C0, 0x9300),   # Power Management
            (0x9E00, 0xA000),   # USB Control Buffer
            (0xB200, 0xB900),   # PCIe Passthrough
            (0xC000, 0xC100),   # UART
            (0xC100, 0xC400),   # Link/PHY controller
            (0xC400, 0xC600),   # NVMe Interface
            (0xC600, 0xC700),   # PHY Extended
            (0xC800, 0xC900),   # Interrupt/DMA/Flash
            (0xCA00, 0xCB00),   # PD Controller
            (0xCC00, 0xCF00),   # Timer/CPU/SCSI
            (0xD800, 0xE000),   # USB Endpoint Data Buffer
            (0xE300, 0xE400),   # PHY Completion / Debug
            (0xE400, 0xE500),   # Command Engine
            (0xE700, 0xE800),   # System Status
        ]

    # Set memory reference for USB commands
    hw.memory = memory
    memory.dpx_hardware = hw

    # ============================================
    # XDATA Write Tracing
    # ============================================
    # Hook XDATA writes to trace firmware RAM updates.
    # This helps understand how firmware populates key addresses.
    def make_xdata_write_trace_hook(hw_ref, mem_ref, original_write):
        """Create a write hook that traces writes and calls original."""
        def hook(addr, value):
            # Call trace function if enabled
            if hw_ref.xdata_trace_enabled:
                # Get PC from CPU if available
                pc = 0
                if hasattr(hw_ref, '_cpu_ref') and hw_ref._cpu_ref:
                    pc = hw_ref._cpu_ref.pc
                hw_ref.trace_xdata_write(addr, value, pc)
            # Perform actual write
            return original_write(addr, value)
        return hook

    def make_read_hook(hw_ref):
        def hook(addr):
            return hw_ref.read(addr)
        return hook

    def make_write_hook(hw_ref):
        def hook(addr, value):
            hw_ref.write(addr, value)
        return hook

    # ============================================
    # Proxy Mode Hooks (real hardware via UART)
    # ============================================
    def make_proxy_read_hook(proxy_ref, hw_ref):
        """Create a read hook that proxies to real hardware."""
        def hook(addr):
            value = proxy_ref.read(addr)
            if proxy_ref.debug >= 2:
                pc = hw_ref._cpu_ref.pc if hw_ref._cpu_ref else 0
                cyc = hw_ref.cycles
                reg_name = get_register_name(addr)
                if reg_name:
                    print(f"[{cyc:8d}] PC=0x{pc:04X} Read  0x{addr:04X} = 0x{value:02X}  {reg_name}")
                else:
                    print(f"[{cyc:8d}] PC=0x{pc:04X} Read  0x{addr:04X} = 0x{value:02X}")
            return value
        return hook

    def make_proxy_write_hook(proxy_ref, hw_ref):
        """Create a write hook that proxies to real hardware."""
        # Shadow 0x9E00 buffer header to detect device descriptor
        ep0_shadow = {}
        def hook(addr, value):
            # Track writes to USB EP0 buffer header bytes
            if 0x9E00 <= addr <= 0x9E01:
                ep0_shadow[addr] = value

            # Patch VID/PID only when buffer contains a device descriptor
            # Device descriptor: bLength=0x12 at 0x9E00, bDescriptorType=0x01 at 0x9E01
            # Bytes 8-9 = idVendor (LE), bytes 10-11 = idProduct (LE)
            if hw_ref.vidpid_override is not None and 0x9E08 <= addr <= 0x9E0B:
                if ep0_shadow.get(0x9E00) == 0x12 and ep0_shadow.get(0x9E01) == 0x01:
                    vid, pid = hw_ref.vidpid_override
                    if addr == 0x9E08: value = vid & 0xFF
                    elif addr == 0x9E09: value = (vid >> 8) & 0xFF
                    elif addr == 0x9E0A: value = pid & 0xFF
                    elif addr == 0x9E0B: value = (pid >> 8) & 0xFF

            proxy_ref.write(addr, value)
            if proxy_ref.debug >= 2:
                pc = hw_ref._cpu_ref.pc if hw_ref._cpu_ref else 0
                cyc = hw_ref.cycles
                reg_name = get_register_name(addr)
                if reg_name:
                    print(f"[{cyc:8d}] PC=0x{pc:04X} Write 0x{addr:04X} = 0x{value:02X}  {reg_name}")
                else:
                    print(f"[{cyc:8d}] PC=0x{pc:04X} Write 0x{addr:04X} = 0x{value:02X}")
        return hook

    # Select hooks based on proxy mode
    if proxy is not None:
        # Proxy mode - MMIO goes to real hardware (except certain ranges)
        print(f"[HW] Using UART proxy for MMIO access")
        memory.proxy = proxy  # For DPX-banked XDATA access in memory.py
        proxy_read_hook = make_proxy_read_hook(proxy, hw)
        proxy_write_hook = make_proxy_write_hook(proxy, hw)
        emu_read_hook = make_read_hook(hw)
        emu_write_hook = make_write_hook(hw)

        def should_emulate(addr):
            """Check if address should be emulated instead of proxied."""
            # UART (0xC000-0xC00F) - proxy uses these for communication
            if 0xC000 <= addr <= 0xC00F:
                return True
            # CPU interrupt/DMA control - may cause reset when written via proxy
            if addr == 0xCC81:
                return True
            # CPU bus mode control - writing bit 0 changes bus access mode,
            # causing subsequent MMIO reads (e.g. 0xC65A) to hang the proxy CPU
            if addr == 0xCA2E:
                return True
            # Check user-specified mask ranges
            for mask_start, mask_end in proxy_mask:
                if mask_start <= addr < mask_end:
                    return True
            return False

        # Print mask info if any
        if proxy_mask:
            print(f"[HW] Proxy mask (emulated instead of proxied):")
            for start, end in proxy_mask:
                print(f"[HW]   0x{start:04X}-0x{end:04X}")

        for start, end in mmio_ranges:
            for addr in range(start, end):
                if should_emulate(addr):
                    memory.xdata_read_hooks[addr] = emu_read_hook
                    memory.xdata_write_hooks[addr] = emu_write_hook
                else:
                    memory.xdata_read_hooks[addr] = proxy_read_hook
                    memory.xdata_write_hooks[addr] = proxy_write_hook

        # ============================================
        # SFR Proxy Hooks
        # ============================================
        # Key SFRs must be proxied to real hardware for interrupts to work.
        # The emulator runs the code but hardware state (IE, timers, etc.)
        # must be synchronized with real hardware.

        # SFRs to proxy (interrupt and timer related)
        # Note: DPX (0x93) is NOT proxied - DPX-banked XDATA accesses use
        # dedicated CMD_READ_DPX/CMD_WRITE_DPX commands instead.
        proxy_sfrs = [
            0xA8,  # IE - Interrupt Enable
            0xB8,  # IP - Interrupt Priority
            0x88,  # TCON - Timer Control
            0x89,  # TMOD - Timer Mode
            0x8A,  # TL0 - Timer 0 Low
            0x8B,  # TL1 - Timer 1 Low
            0x8C,  # TH0 - Timer 0 High
            0x8D,  # TH1 - Timer 1 High
        ]

        def make_sfr_proxy_write_hook(proxy_ref, sfr_addr, hw_ref, cache):
            """Create SFR write hook that proxies to real hardware and caches value."""
            def hook(addr, value):
                cache[sfr_addr] = value  # Cache the written value
                proxy_ref.sfr_write(sfr_addr, value)
                if proxy_ref.debug >= 3:
                    pc = hw_ref._cpu_ref.pc if hw_ref._cpu_ref else 0
                    cyc = hw_ref.cycles
                    print(f"[{cyc:8d}] PC=0x{pc:04X} SFR Write 0x{sfr_addr:02X} = 0x{value:02X}")
            return hook

        # Cache for SFR values - return last written value instead of proxying reads
        sfr_cache = {}

        def make_sfr_proxy_read_hook(proxy_ref, sfr_addr, hw_ref, cache):
            """Create SFR read hook that returns cached value (no proxy read needed)."""
            def hook(addr):
                # Return cached value (default 0 if never written)
                value = cache.get(sfr_addr, 0)
                if proxy_ref.debug >= 3:
                    pc = hw_ref._cpu_ref.pc if hw_ref._cpu_ref else 0
                    cyc = hw_ref.cycles
                    print(f"[{cyc:8d}] PC=0x{pc:04X} SFR Read  0x{sfr_addr:02X} = 0x{value:02X} (cached)")
                return value
            return hook

        for sfr_addr in proxy_sfrs:
            memory.sfr_write_hooks[sfr_addr] = make_sfr_proxy_write_hook(proxy, sfr_addr, hw, sfr_cache)
            memory.sfr_read_hooks[sfr_addr] = make_sfr_proxy_read_hook(proxy, sfr_addr, hw, sfr_cache)

        print(f"[HW] Proxying {len(proxy_sfrs)} SFRs to real hardware")
    else:
        # Emulation mode - use HardwareState
        read_hook = make_read_hook(hw)
        write_hook = make_write_hook(hw)

        for start, end in mmio_ranges:
            for addr in range(start, end):
                memory.xdata_read_hooks[addr] = read_hook
                memory.xdata_write_hooks[addr] = write_hook
        # These low addresses are ordinary XDATA only with DPX=0. C24C uses
        # them exclusively through P1_REG8_* (DPX=1), so hook the four proven
        # switch-plane locations explicitly as well as normal >=0x8000 MMIO.
        for addr in (0x4084, 0x5084, 0x6043, 0x6025):
            memory.xdata_read_hooks[addr] = read_hook
            memory.xdata_write_hooks[addr] = write_hook

    # Debug hooks for XDATA can be added here when needed
    # Example: Trace reads/writes to specific addresses
    # memory.xdata_write_hooks[0x0AF7] = make_debug_hook(hw, memory)

    if proxy is None:
        def b1c5_terminal_write(addr, value):
            old = memory.xdata[addr]
            memory.xdata[addr] = value
            hw.record_usb_stock_b1c5_terminal(addr, old, value)

        for addr in (0x09FA, 0x0AE1):
            memory.xdata_write_hooks[addr] = b1c5_terminal_write

        classifier_addresses = set(
            (0x0AE1, 0x07E4, 0x05A5, 0x05A6) +
            tuple(range(0x05B0, 0x0632)) +
            (0x0B3A, 0x0B39, 0x0ACC, 0x0ACD, 0x07EC,
             0x07B6, 0x07B7, 0x07B8, 0x09F9, 0x09FA, 0x09FB,
             0x086E, 0x086F, 0x07F6))

        def make_classifier_write(previous):
            def classifier_write(addr, value):
                old = memory.xdata[addr]
                if previous is None:
                    memory.xdata[addr] = value
                else:
                    previous(addr, value)
                hw.record_usb_stock_5372(addr, old, value)
            return classifier_write

        for addr in classifier_addresses:
            memory.xdata_write_hooks[addr] = make_classifier_write(
                memory.xdata_write_hooks.get(addr))

        # USB3 mode hook for 0x0ACC - only in emulation mode
        # In proxy mode, real hardware handles USB mode detection
        def usb3_mode_read_hook(addr):
            if hw.usb_control_transfer_active:
                return 0x02
            return memory.xdata[addr]
        memory.xdata_read_hooks[0x0ACC] = usb3_mode_read_hook
