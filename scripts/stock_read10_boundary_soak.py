#!/usr/bin/env python3
"""Offline enclosure stress against an imported asm2464pd-opal READ10 boundary.

This does not redefine ASM firmware/controller behavior. It replays a passive
state fixture into the enclosure's pinned backend and runs model-only queue and
interrupt-delivery stress to expose integration defects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulation.board.model import Board
from simulation.asm2464.adapter.cpu import CpuBackend
from simulation.asm2464.adapter.route import configure_stock_route, tlp

FIXTURE = ROOT / "simulation/fixtures/opal-b1e4d4d-stock-read10-pio-irq.json"
OUT = ROOT / "validation/stock-read10-boundary-soak.json"
SOURCE = {
    "repository": "mewmix/asm2464pd-opal",
    "branch": "agent/stock-reset-to-identify",
    "commit": "b1e4d4d4387a2d2c7a19c42da6157f1b92e2cfc1",
    "workflow_run": 34395095064,
    "artifact_id": 10121241850,
    "artifact_zip_sha256": "c4fd8d3fdc475e54bc77b7ab81919a9c49b01e6c82b3f0a5cf2a58f725290c57",
    "payload_sha256": "72ee03699bd595ef0231467507af4b74f1acc542e2b23ba5aad2eaf47b8071cf",
}

SEED = (
    (0xB264, 8), (0xB265, 0), (0xB266, 8), (0xB267, 8),
    (0xB26C, 8), (0xB26D, 0x20), (0xB26E, 8), (0xB26F, 0x28),
    (0xB250, 0), (0xB251, 0), (0xCEF3, 8), (0xCEF2, 0x80),
    (0xCEF0, 0), (0xCEEF, 0), (0xC807, 4), (0xB281, 0x10),
)


def controller(hw):
    for off, value in (
        (0x14, 0), (0x24, 0x30003), (0x28, 0x200000),
        (0x2C, 0), (0x30, 0x820000), (0x34, 0), (0x14, 0x460001),
    ):
        tlp(hw, 0x40, hw.nvme_bar0 + off, value)


def submit(cpu, qid=0, op=6, cid=1, prp=None, cdw10=None, cdw11=0, cdw12=0):
    hw = cpu.hw
    sqe = bytearray(64)
    sqe[0] = op
    sqe[2:4] = int(cid & 0xFFFF).to_bytes(2, "little")
    sqe[4:8] = int(qid).to_bytes(4, "little")
    if prp is None:
        prp = 0x820400 if qid else 0x200000
    if cdw10 is None:
        cdw10 = 0 if qid else 1
    sqe[24:32] = int(prp).to_bytes(8, "little")
    sqe[40:44] = int(cdw10).to_bytes(4, "little")
    sqe[44:48] = int(cdw11).to_bytes(4, "little")
    sqe[48:52] = int(cdw12).to_bytes(4, "little")
    cpu.memory.xdata[0xA000:0xA040] = sqe
    for address, value in (
        (0xCEF2, 0x80), (0xB294, 0x30), (0xB296, 0xFF),
        (0xB251, 1), (0xB254, qid + 1),
    ):
        hw.write(address, value)


def create_q1(cpu):
    submit(cpu, op=5, prp=0x820200, cdw10=0x30001, cdw11=1)
    submit(cpu, op=1, prp=0x200200, cdw10=0x30001, cdw11=0x10001)


def ready():
    board = Board(flash_profile="stock_physical")
    cpu = CpuBackend(board)
    cpu.prepare()
    configure_stock_route(cpu.hw)
    for address, value in SEED:
        cpu.hw.write(address, value)
    controller(cpu.hw)
    create_q1(cpu)
    if cpu.hw._stock_nvme_owner(0) is None or cpu.hw._stock_nvme_owner(1) is None:
        raise AssertionError("stock queue ownership did not establish")
    return board, cpu


def healthy_q1_soak(iterations):
    board, cpu = ready()
    hw = cpu.hw
    owner = hw._stock_nvme_owner(1)
    start_history = len(hw.stock_nvme_io_submission_history)
    failures = []
    blocks = 32
    expected = {}
    for lba in range(blocks):
        payload = bytes(((lba * 29 + offset) & 0xFF) for offset in range(512))
        hw.nvme_namespace_data[lba] = payload
        expected[lba] = payload

    for i in range(iterations):
        lba = i % blocks
        submit(cpu, qid=1, op=2, cid=(i % 0xFFFE) + 1, cdw10=lba)
        if not (hw.regs.get(0xB294, 0) & 0x10):
            failures.append({"iteration": i, "reason": "q1_completion_not_signaled"})
            break
        if hw._stock_nvme_owner(1) != owner:
            failures.append({"iteration": i, "reason": "q1_owner_changed"})
            break
        data = bytes(cpu.memory.xdata[0xA400:0xA600])
        if data != expected[lba]:
            failures.append({"iteration": i, "reason": "read_payload_mismatch", "lba": lba})
            break

    delta = len(hw.stock_nvme_io_submission_history) - start_history
    return {
        "iterations_requested": iterations,
        "iterations_completed": iterations if not failures else failures[0]["iteration"],
        "submission_history_delta": delta,
        "q1_owner_stable": hw._stock_nvme_owner(1) == owner,
        "rejected_completion_count": len(hw.stock_nvme_rejected_completions),
        "failures": failures,
        "board_generation": board.downstream.generation,
        "pass": not failures and delta == iterations,
    }


def generation_churn(cycles):
    board, cpu = ready()
    hw = cpu.hw
    failures = []
    start_rejected = len(hw.stock_nvme_rejected_completions)

    for i in range(cycles):
        hw.stock_nvme_defer_completions = True
        submit(cpu, qid=1, op=2, cid=(0x4000 + i) & 0xFFFF, cdw10=i % 8)
        board.set_downstream_link(False)
        board.set_downstream_link(True)
        configure_stock_route(hw)
        controller(hw)
        hw.stock_nvme_defer_completions = False
        create_q1(cpu)
        before = len(hw.stock_nvme_rejected_completions)
        hw.complete_deferred_nvme()
        if len(hw.stock_nvme_rejected_completions) != before + 1:
            failures.append({"cycle": i, "reason": "stale_completion_not_rejected"})
            break
        submit(cpu, qid=1, op=2, cid=(0x6000 + i) & 0xFFFF, cdw10=i % 8)
        if not (hw.regs.get(0xB294, 0) & 0x10):
            failures.append({"cycle": i, "reason": "new_generation_q1_read_failed"})
            break

    rejected_delta = len(hw.stock_nvme_rejected_completions) - start_rejected
    return {
        "cycles_requested": cycles,
        "cycles_completed": cycles if not failures else failures[0]["cycle"],
        "downstream_generation": board.downstream.generation,
        "rejected_stale_completions": rejected_delta,
        "failures": failures,
        "pass": not failures and rejected_delta == cycles,
    }


def _set_sfr(memory, address, value):
    memory.write_sfr(address, value)


def boundary_replay(trace, idle_ticks):
    board = Board(flash_profile="stock_physical")
    backend = CpuBackend(board)
    backend.prepare()
    hw = backend.hw
    cpu = backend.cpu
    idle = trace["idle_after_06e3"]

    # This is deliberately a state replay, not stock execution. Direct register
    # assignment avoids generating controller side effects while testing whether
    # the imported backend autonomously turns the persistent state into EX0.
    for key, value in idle["mmio"].items():
        hw.regs[int(key, 16)] = value
    backend.memory.xdata[0x06E2] = idle["low_ram"]["0x06E2"]
    backend.memory.xdata[0x06E3] = idle["low_ram"]["0x06E3"]
    backend.memory.xdata[0x06E4] = idle["low_ram"]["0x06E4"]
    backend.memory.xdata[0x06E5] = idle["low_ram"]["0x06E5"]
    _set_sfr(backend.memory, 0xA8, idle["sfr"]["IE"])
    _set_sfr(backend.memory, 0xB8, idle["sfr"]["IP"])
    _set_sfr(backend.memory, 0x88, idle["sfr"]["TCON"])
    _set_sfr(backend.memory, 0x81, idle["sfr"]["SP"])
    cpu.in_interrupt = False
    cpu._ext0_pending = False
    hw._pending_usb_interrupt = False

    spontaneous_at = None
    for tick in range(idle_ticks):
        hw.tick(1, cpu)
        if cpu._ext0_pending:
            spontaneous_at = tick + 1
            break

    # Sensitivity check: the existing event bridge should be able to reach EX0
    # when an explicit controller event is supplied. This does NOT claim that
    # USB is the correct source for the READ10 PCIe completion.
    cpu._ext0_pending = False
    cpu.in_interrupt = False
    hw._pending_usb_interrupt = True
    hw.tick(1, cpu)
    explicit_event_latched = bool(cpu._ext0_pending)
    pending_after_delivery = bool(hw._pending_usb_interrupt)

    # Test whether an event presented while firmware is already in an ISR is
    # preserved until firmware exits that ISR. No RETI is synthesized here;
    # this only probes the backend's delivery policy around in_interrupt.
    cpu._ext0_pending = False
    cpu.in_interrupt = True
    hw._pending_usb_interrupt = True
    for _ in range(64):
        hw.tick(1, cpu)
    while_in_isr = {
        "ext0_pending": bool(cpu._ext0_pending),
        "controller_event_pending": bool(hw._pending_usb_interrupt),
    }
    cpu.in_interrupt = False
    hw.tick(1, cpu)
    after_isr_exit = {
        "ext0_pending": bool(cpu._ext0_pending),
        "controller_event_pending": bool(hw._pending_usb_interrupt),
    }

    return {
        "fixture_phase": idle["phase"],
        "fixture_mmio": idle["mmio"],
        "fixture_low_ram": idle["low_ram"],
        "idle_ticks_requested": idle_ticks,
        "spontaneous_ext0_tick": spontaneous_at,
        "spontaneous_ext0_from_persistent_snapshot": spontaneous_at is not None,
        "explicit_event_bridge": {
            "ext0_latched": explicit_event_latched,
            "controller_event_pending_after_delivery": pending_after_delivery,
        },
        "event_arrives_while_in_isr": {
            "after_64_ticks_in_isr": while_in_isr,
            "after_isr_exit_tick": after_isr_exit,
        },
        "pass": spontaneous_at is None and explicit_event_latched,
        "classification": "NON_AUTHORITATIVE_STATE_REPLAY_AGAINST_PINNED_ENCLOSURE_BACKEND",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20000)
    parser.add_argument("--churn-cycles", type=int, default=256)
    parser.add_argument("--idle-ticks", type=int, default=500000)
    args = parser.parse_args()
    if args.iterations < 1 or args.churn_cycles < 1 or args.idle_ticks < 1:
        parser.error("all stress counts must be positive")

    raw = FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE["payload_sha256"]:
        raise SystemExit("READ10 fixture hash mismatch")
    trace = json.loads(raw)
    if trace["hardware_touched"] is not False or trace["physical_evidence_claimed"] is not False:
        raise SystemExit("fixture evidence boundary mismatch")
    if trace["firmware"]["sha256"] != "1a4734745f0c00235754a00bd7e3e995064e1e6ae6f88a6aff163683a22d0037":
        raise SystemExit("fixture firmware hash mismatch")

    lock = json.loads((ROOT / "simulation/asm2464/upstream/import-lock.json").read_text())
    result = {
        "schema": 1,
        "verdict": "ENCLOSURE_STOCK_READ10_BOUNDARY_SOAK_INCOMPLETE",
        "hardware_touched": False,
        "physical_evidence_claimed": False,
        "authoritative_semantics_changed": False,
        "source_trace": {**SOURCE, "fixture_path": str(FIXTURE.relative_to(ROOT))},
        "enclosure_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "enclosure_pinned_upstream_emulator_commit": lock["commit"],
        "evidence_classification": {
            "kind": "ENCLOSURE_MODEL_STRESS_AGAINST_IMPORTED_PASSIVE_TRACE",
            "stock_firmware_execution_in_this_lane": False,
            "purpose": "INDEPENDENT_SOAK_AND_INTERRUPT_DELIVERY_SENSITIVITY",
        },
    }
    result["boundary_replay"] = boundary_replay(trace, args.idle_ticks)
    result["healthy_q1_soak"] = healthy_q1_soak(args.iterations)
    result["generation_churn"] = generation_churn(args.churn_cycles)

    all_pass = all((
        result["boundary_replay"]["pass"],
        result["healthy_q1_soak"]["pass"],
        result["generation_churn"]["pass"],
    ))
    if all_pass:
        result["verdict"] = "ENCLOSURE_STOCK_READ10_BOUNDARY_SOAK_COMPLETED"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if all_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
