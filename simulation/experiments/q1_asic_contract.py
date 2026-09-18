"""Model-only candidate ASM2464 Queue-1 ASIC contract.

This module is deliberately isolated from the normal board/imported emulator path.
It tests whether one coherent queue-engine architecture can satisfy the current
stock-firmware constraints.  Nothing here is physical evidence or authoritative
ASM2464 semantics.
"""
from __future__ import annotations

from dataclasses import dataclass

DEPTH = 32
SQE_SIZE = 64
CONTEXT0_STAGING = 0xA000
CONTEXT1_STAGING = 0xA800


def decode_shifted_queue_base(msb: int, lsb: int) -> int:
    """Candidate B26x decode: 16-bit literal represents queue_base >> 12."""
    return (((msb & 0xFF) << 8) | (lsb & 0xFF)) << 12


def ring_interval(old: int, new: int, depth: int = DEPTH) -> list[int]:
    """Return ring slots in [old,new), including wrap and allowing batching."""
    if depth <= 0:
        raise ValueError("depth must be positive")
    old %= depth
    new %= depth
    slots = []
    cursor = old
    while cursor != new:
        slots.append(cursor)
        cursor = (cursor + 1) % depth
        if len(slots) > depth:
            raise AssertionError("ring interval exceeded one revolution")
    return slots


def staging_address(context: int, slot: int) -> int:
    if context not in (0, 1):
        raise ValueError("only the two firmware staging contexts are modeled")
    if not 0 <= slot < DEPTH:
        raise ValueError("slot outside 32-entry queue")
    return (CONTEXT1_STAGING if context else CONTEXT0_STAGING) + slot * SQE_SIZE


def advance_index_phase(index: int, phase: int, count: int = 1, depth: int = DEPTH) -> tuple[int, int]:
    """Candidate ring advancement with phase toggle on each wrap."""
    index %= depth
    phase &= 1
    for _ in range(count):
        index += 1
        if index == depth:
            index = 0
            phase ^= 1
    return index, phase


@dataclass(frozen=True)
class QueueGeometry:
    sq_base: int
    cq_base: int
    depth: int = DEPTH
    qid: int = 1
    cqid: int = 1

    @classmethod
    def from_b26x(cls, regs: dict[int, int]) -> "QueueGeometry":
        return cls(
            sq_base=decode_shifted_queue_base(regs[0xB26C], regs[0xB26D]),
            cq_base=decode_shifted_queue_base(regs[0xB26E], regs[0xB26F]),
        )


@dataclass(frozen=True)
class ReadCommand:
    opcode: int
    cid: int
    nsid: int
    prp1: int
    prp2: int
    slba: int
    nlb_zero_based: int

    @classmethod
    def decode(cls, sqe: bytes) -> "ReadCommand":
        if len(sqe) != SQE_SIZE:
            raise ValueError("NVMe SQE must be 64 bytes")
        return cls(
            opcode=sqe[0],
            cid=int.from_bytes(sqe[2:4], "little"),
            nsid=int.from_bytes(sqe[4:8], "little"),
            prp1=int.from_bytes(sqe[24:32], "little"),
            prp2=int.from_bytes(sqe[32:40], "little"),
            slba=int.from_bytes(sqe[40:48], "little"),
            nlb_zero_based=int.from_bytes(sqe[48:50], "little"),
        )

    @property
    def blocks(self) -> int:
        return self.nlb_zero_based + 1


@dataclass(frozen=True)
class Completion:
    cid: int
    status_code: int = 0
    status_type: int = 0
    phase: int = 1

    def project_b80c_f(self) -> bytes:
        """Candidate four-byte firmware projection constrained by proven extraction."""
        sc = self.status_code & 0xFF
        sct = self.status_type & 0x7
        b80e = (self.phase & 1) | ((sc & 0x7F) << 1)
        b80f = ((sc >> 7) & 1) | (sct << 1)
        return bytes((self.cid & 0xFF, (self.cid >> 8) & 0xFF, b80e, b80f))


class SparseHostMemory:
    """Abstract PCIe/host address space; intentionally unrelated to ASM XDATA."""

    def __init__(self):
        self._bytes: dict[int, int] = {}

    def write(self, address: int, data: bytes) -> None:
        if address < 0:
            raise ValueError("negative address")
        for offset, value in enumerate(data):
            self._bytes[address + offset] = value

    def read(self, address: int, size: int) -> bytes:
        return bytes(self._bytes.get(address + offset, 0) for offset in range(size))


class CandidateQ1Engine:
    """Minimal model-only Queue-1 engine for hypothesis testing."""

    def __init__(self, geometry: QueueGeometry, media: dict[int, bytes] | None = None):
        self.geometry = geometry
        self.media = dict(media or {})
        self.host_memory = SparseHostMemory()
        self.staging: dict[int, bytes] = {}
        self.hw_producer = 0
        self.hw_consumer = 0
        self.cq_tail = 0
        self.cq_phase = 1
        self.completions: list[Completion | None] = [None] * geometry.depth
        self.transport_log: list[dict] = []

    def stage(self, context: int, slot: int, sqe: bytes) -> None:
        if len(sqe) != SQE_SIZE:
            raise ValueError("SQE must be 64 bytes")
        self.staging[staging_address(context, slot)] = bytes(sqe)

    def notify_producer(self, context: int, next_index: int) -> list[int]:
        slots = ring_interval(self.hw_producer, next_index, self.geometry.depth)
        for slot in slots:
            source = staging_address(context, slot)
            sqe = self.staging.get(source)
            if sqe is None:
                raise RuntimeError(f"producer exposed unstaged slot {slot}")
            downstream = self.geometry.sq_base + slot * SQE_SIZE
            cmd = ReadCommand.decode(sqe)
            self.transport_log.append({
                "context": context,
                "slot": slot,
                "staging_address": source,
                "candidate_downstream_address": downstream,
                "sqe": sqe,
            })
            self._execute(cmd)
        self.hw_producer = next_index % self.geometry.depth
        return slots

    def _execute(self, cmd: ReadCommand) -> None:
        if cmd.opcode != 0x02:
            raise NotImplementedError("experiment only implements NVMe READ")
        if cmd.nsid != 1:
            raise NotImplementedError("experiment only implements NSID 1")
        if cmd.prp2 != 0:
            raise NotImplementedError("multi-PRP READ is outside this experiment")
        data = bytearray()
        for lba in range(cmd.slba, cmd.slba + cmd.blocks):
            block = self.media.get(lba)
            if block is None:
                raise RuntimeError(f"no model-input media for LBA {lba}")
            if len(block) != 512:
                raise ValueError("model-input media blocks must be 512 bytes")
            data.extend(block)
        self.host_memory.write(cmd.prp1, bytes(data))
        completion = Completion(cid=cmd.cid, phase=self.cq_phase)
        if self.completions[self.cq_tail] is not None:
            raise RuntimeError("candidate CQ overflow")
        self.completions[self.cq_tail] = completion
        self.cq_tail, self.cq_phase = advance_index_phase(
            self.cq_tail, self.cq_phase, 1, self.geometry.depth
        )

    def project_completion(self, slot: int) -> bytes:
        completion = self.completions[slot % self.geometry.depth]
        if completion is None:
            raise RuntimeError("completion slot is empty")
        return completion.project_b80c_f()

    def notify_consumer(self, next_index: int) -> list[int]:
        slots = ring_interval(self.hw_consumer, next_index, self.geometry.depth)
        for slot in slots:
            self.completions[slot] = None
        self.hw_consumer = next_index % self.geometry.depth
        return slots
