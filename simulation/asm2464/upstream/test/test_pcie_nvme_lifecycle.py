"""Compiled embedded PCIe/Q0 lifecycle contracts; no physical transport.

Synthetic debugger calls isolate the compiled functions with interrupts masked.
The ordinary firmware boot establishes model routing and ASIC queue setup.
No context-valid byte, endpoint configuration, or CQE is supplied by this test.
Fault hooks are test-only inputs, not observations about stock hardware.
"""
from pathlib import Path
import re

import pytest

from test_stock_pcie_enumeration import _boot_pre_bar


LISTING = Path("handmade/build-usb4-demo-embedded/obj/main.rst")
CONTEXT = 0xA83A
ADMIN = 0xA800
GENERATION = 0xA80C


def symbol(name):
    match = re.search(r"^\s*([0-9A-Fa-f]{6})\s+.*_" + re.escape(name) + r":$",
                      LISTING.read_text(), re.MULTILINE)
    assert match, f"missing compiled symbol: {name}"
    return int(match.group(1), 16)


def call(emu, name, arg=0, limit=2_000_000):
    pc, sp = emu.cpu.pc, emu.cpu.SP
    idata, sfr = bytes(emu.memory.idata), bytes(emu.memory.sfr)
    emu.memory.write_sfr(0xA8, 0)  # synthetic call isolation, no ISR claim
    emu.memory.write_sfr(0x82, arg)
    emu.cpu.push(0xFE)
    emu.cpu.push(0xFF)
    emu.cpu.pc = symbol(name)
    try:
        for _ in range(limit):
            assert emu.step(), f"emulator halted during {name}"
            if emu.cpu.pc == 0xFFFE and emu.cpu.SP == sp:
                return emu.memory.read_sfr(0x82)
        raise AssertionError(f"compiled {name} did not return")
    finally:
        emu.memory.idata[:] = idata
        emu.memory.sfr[:] = sfr
        emu.cpu.pc = pc


def assert_retained_init(emu):
    count = len(emu.hw.pcie_cfg_history)
    assert call(emu, "nvme_admin_init") == 0
    assert emu.memory.xdata[CONTEXT] == 1
    assert len(emu.hw.pcie_cfg_history) == count, "retained context re-enumerated"


def test_compiled_same_instance_context_retention_and_q0_recovery():
    emu = _boot_pre_bar()
    assert emu.memory.xdata[CONTEXT] == 1
    assert emu.memory.xdata[ADMIN] == 1
    assert emu.hw.pcie_bridge_configured
    assert emu.hw.pcie_endpoint_bar0 == 0x00D00000
    cfg_count = len(emu.hw.pcie_cfg_history)
    generation = emu.memory.xdata[GENERATION]

    def retained_init():
        nonlocal generation
        assert_retained_init(emu)
        assert len(emu.hw.pcie_cfg_history) == cfg_count
        generation = (generation + 1) & 255
        assert emu.memory.xdata[GENERATION] == generation

    def rebuild():
        nonlocal generation, cfg_count
        assert emu.memory.xdata[CONTEXT] == 0
        assert emu.memory.xdata[ADMIN] == 0
        assert call(emu, "nvme_admin_init") == 0
        assert emu.memory.xdata[CONTEXT] == 1
        assert emu.memory.xdata[ADMIN] == 1
        assert len(emu.hw.pcie_cfg_history) > cfg_count
        cfg_count = len(emu.hw.pcie_cfg_history)
        generation = (generation + 1) & 255
        assert emu.memory.xdata[GENERATION] == generation

    retained_init()
    # A valid matching CQE status error completes and preserves context.
    emu.hw.stock_nvme_force_sc = 2
    assert call(emu, "nvme_identify_controller") == 3
    assert emu.memory.xdata[CONTEXT] == 1
    assert emu.memory.xdata[ADMIN] == 1
    emu.hw.stock_nvme_force_sc = 0
    retained_init()
    assert call(emu, "nvme_identify_controller") == 0

    for fault in ("stock_nvme_completion_timeout", "stock_nvme_queue_error",
                  "stock_nvme_force_stale_phase", "stock_nvme_force_mismatched_cid",
                  "stock_nvme_cq_b296_error"):
        setattr(emu.hw, fault, True)
        assert call(emu, "nvme_identify_controller") != 0, fault
        assert emu.memory.xdata[GENERATION] == generation
        setattr(emu.hw, fault, False)
        rebuild()
        assert call(emu, "nvme_identify_controller") == 0

    # Retained software context cannot make a revoked BAR route usable. The
    # first MMIO read fails, then the next init must re-enumerate.
    emu.hw.reset_pcie_configuration()
    cfg_count = 0  # reset also clears modeled transaction history
    assert call(emu, "nvme_admin_init") == 2
    assert emu.memory.xdata[GENERATION] == generation
    rebuild()

    # Failed enumeration must not mark a partially rebuilt route valid.
    emu.hw.pcie_pio_cpl_status = 0x20
    assert call(emu, "pcie_pre_bar_init") == 6
    assert emu.memory.xdata[CONTEXT] == 0
    assert call(emu, "nvme_admin_init") == 6
    assert emu.memory.xdata[ADMIN] == 0
    cfg_count = len(emu.hw.pcie_cfg_history)
    emu.hw.pcie_pio_cpl_status = 0
    rebuild()

    # Handmade's explicit CFS check is prospective safety policy. This test
    # does not infer it from stock's RDY-only helper.
    emu.hw.nvme_csts |= 2
    assert call(emu, "nvme_admin_init") == 4
    assert emu.memory.xdata[GENERATION] == generation
    emu.hw.nvme_csts &= ~2
    rebuild()


def test_compiled_mutation_unconditional_enumeration_is_rejected():
    emu = _boot_pre_bar()
    code = emu.memory.code
    start = symbol("nvme_admin_init")
    pc = code.find(bytes.fromhex("90a83ae06003"), start, start + 100)
    assert pc >= 0, "compiled context guard changed; review mutation"
    code[pc + 4] = 0x80  # JZ -> SJMP: always enumerate
    with pytest.raises(AssertionError, match="retained context re-enumerated"):
        assert_retained_init(emu)


def test_compiled_mutation_timeout_context_clear_is_required():
    emu = _boot_pre_bar()
    code = emu.memory.code
    start, end = symbol("nvme_admin_command"), symbol("nvme_identify_controller")
    pc = code.find(bytes.fromhex("90a83af0"), start, end)
    assert pc >= 0, "compiled poison store changed; review mutation"
    code[pc + 3] = 0  # NOP: invalidation silently removed
    emu.hw.stock_nvme_completion_timeout = True
    assert call(emu, "nvme_identify_controller") == 2
    assert emu.memory.xdata[ADMIN] == 0
    assert emu.memory.xdata[CONTEXT] == 1  # precisely the forbidden stale state


def test_compiled_media_creates_q1_before_ready_and_rebuilds_generation():
    emu = _boot_pre_bar()
    assert call(emu, "media_ready") == 1
    assert emu.memory.xdata[0xA82B] == 1
    assert emu.memory.xdata[0xA82E] == emu.memory.xdata[GENERATION]
    history = len(emu.hw.stock_nvme_submission_history)
    states = []
    original = emu.cpu.write_xdata
    def record_state(address, value):
        if address == 0xA88F:
            states.append(value)
        original(address, value)
    emu.cpu.write_xdata = record_state
    call(emu, "media_poll")
    emu.cpu.write_xdata = original
    assert emu.memory.xdata[0xA88F] == 3
    assert states and all(state == 3 for state in states)
    assert len(emu.hw.stock_nvme_submission_history) == history

    assert call(emu, "nvme_admin_init") == 0
    assert call(emu, "media_ready") == 0  # old Q1 generation cannot open media
    call(emu, "media_poll")
    assert call(emu, "media_ready") == 1
    assert emu.memory.xdata[0xA82E] == emu.memory.xdata[GENERATION]
    # Failed CQ creation cannot advertise READY or silently retry every poll.
    assert call(emu, "nvme_admin_init") == 0
    emu.hw.stock_nvme_force_sc = 2
    call(emu, "media_poll")
    assert emu.memory.xdata[0xA88F] == 0x7F
    assert emu.memory.xdata[0xA890] == 4
    assert call(emu, "media_ready") == 0
    assert emu.memory.xdata[0xA82B] == 0
    history = len(emu.hw.stock_nvme_submission_history)
    call(emu, "media_poll")
    assert len(emu.hw.stock_nvme_submission_history) == history
