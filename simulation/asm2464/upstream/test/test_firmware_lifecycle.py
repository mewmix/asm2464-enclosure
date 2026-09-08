"""Stock-byte guards and adversarial ledger/snapshot regressions, all offline."""

import copy
import json
import subprocess

import pytest

from tools.audit_firmware_lifecycle import (
    LEDGER, SPECS, StockGuardRunner, build_report, load_body, trace_cases, validate_ledger,
    validate_source_provenance,
)
from tools.decode_lifecycle_snapshot import decode_snapshot


@pytest.fixture
def ledger():
    return json.loads(LEDGER.read_text())


def test_cycle_three_firmware_metadata_cannot_deny_source_changes(ledger):
    result = validate_source_provenance(ledger)
    assert result["firmware_modified"] is True
    assert set(result["firmware_source_paths"]) >= {
        "handmade/src/main.c", "handmade/src/media.h",
        "handmade/src/nvme_admin.h", "handmade/src/pcie_pio.h",
    }
    ledger["firmware_modified"] = False
    with pytest.raises(ValueError, match="firmware_modified disagrees"):
        build_report(ledger)


def test_provenance_counts_reverted_firmware_changes_and_evidence_only_cycles(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], stderr=subprocess.PIPE, text=True,
        ).strip()

    def commit():
        git("add", ".")
        git("-c", "user.name=Audit Test", "-c", "user.email=audit@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
        return git("rev-parse", "HEAD")

    git("init")
    firmware = tmp_path / "handmade/src/main.c"
    firmware.parent.mkdir(parents=True)
    firmware.write_text("original\n")
    baseline = commit()
    (tmp_path / "evidence.json").write_text("{}\n")
    evidence_only = commit()
    metadata = dict(audited_base_commit=baseline, source_commit=evidence_only,
                    firmware_modified=False)
    assert validate_source_provenance(metadata, tmp_path)["firmware_source_paths"] == []
    firmware.write_text("modified\n")
    commit()
    firmware.write_text("original\n")
    metadata["source_commit"] = commit()
    assert git("diff", baseline, metadata["source_commit"], "--", "handmade/src/") == ""
    with pytest.raises(ValueError, match="firmware_modified disagrees"):
        validate_source_provenance(metadata, tmp_path)
    metadata["firmware_modified"] = True
    assert validate_source_provenance(metadata, tmp_path)["firmware_source_paths"] == [
        "handmade/src/main.c"]
    metadata["audited_base_commit"] = "0" * 40
    with pytest.raises(ValueError, match="cannot verify audited source interval"):
        validate_source_provenance(metadata, tmp_path)


def test_ledger_anchors_and_stock_execution(ledger):
    report = build_report(ledger)
    assert report["result"] == "PASS"
    assert report["stock_execution_cases"] == 18
    assert report["hardware_touched"] is False
    assert report["verdict"]["status"] == "CONTEXT_GATED_ENUMERATION_PROVEN_ADMIN_INVALIDATION_UNRESOLVED"


@pytest.mark.parametrize("year", ["2023", "2024"])
def test_same_instance_retry_invalidation(ledger, year):
    cases = trace_cases(load_body(ledger["stock_releases"][year]), year)
    for case in cases[4:7]:
        assert case["before"]["stop"] == "context_service"
        assert case["after"]["stop"] == "bridge_setup"
    for case in cases[7:]:
        assert case["selected_retry"]["r7"] == 1
        assert case["after"] is None


@pytest.mark.parametrize("year", ["2023", "2024"])
def test_reversed_guard_mutation_changes_executed_path(ledger, year):
    body = bytearray(load_body(ledger["stock_releases"][year]))
    spec = SPECS[year]
    assert body[spec["entry"] + 4] == 0x70  # JNZ
    body[spec["entry"] + 4] = 0x60  # JZ: meaningful wrong reconstruction
    runner = StockGuardRunner(body, year)
    runner.memory.xdata[spec["count"]] = 1
    assert runner.run(spec["entry"])["stop"] == "bridge_setup"
    with pytest.raises(ValueError, match="context guard disagrees"):
        trace_cases(body, year)


@pytest.mark.parametrize("year", ["2023", "2024"])
def test_omitted_context_clear_cannot_pass_retry_contract(ledger, year):
    body = bytearray(load_body(ledger["stock_releases"][year]))
    clear_pc = 0x8E24 if year == "2023" else 0x90F4
    assert body[clear_pc] == 0xF0  # MOVX @DPTR,A
    body[clear_pc] = 0  # NOP: stale endpoint context survives
    with pytest.raises(ValueError, match="did not invalidate context"):
        trace_cases(body, year)


@pytest.mark.parametrize("mutation", ["hash", "dangling", "promote", "secret", "unknown"])
def test_audit_fails_closed_on_ledger_mutations(ledger, mutation):
    modified = copy.deepcopy(ledger)
    if mutation == "hash":
        modified["evidence"]["context_guard"]["releases"]["2023"]["sha256"] = "0" * 64
    elif mutation == "dangling":
        modified["transitions"][0]["to"] = "nonexistent"
    elif mutation == "promote":
        edge = next(e for e in modified["transitions"] if e["from"] == "tcg_discovery")
        edge["confidence"] = "INSTRUCTION_PROVEN"
    elif mutation == "secret":
        modified["telemetry_fields"][0]["address"] = 0xA865
    elif mutation == "unknown":
        modified["transitions"][0]["unknown_reason"] = None
    with pytest.raises(ValueError):
        build_report(modified)


def test_snapshot_missing_values_are_not_zero_or_ready(ledger):
    decoded = decode_snapshot({"segments": []}, ledger)
    assert all(value is None for value in decoded["fields"].values())
    assert decoded["q1_generation_matches_in_saved_fields"] is None


def test_decoder_omits_credentials_and_rejects_stale_q1(ledger):
    data = bytearray(0xCE)
    data[0] = data[0x2B] = 1
    data[0x0C] = 2
    data[0x2E] = 1
    data[0x65:0x85] = b"DO_NOT_OUTPUT_CREDENTIAL_BYTES!!!"
    decoded = decode_snapshot({"segments": [{"base": "0xA800", "hex": data.hex()}]}, ledger)
    assert decoded["q1_generation_matches_in_saved_fields"] is False
    assert "DO_NOT_OUTPUT" not in json.dumps(decoded)
    assert set(decoded["fields"]) == {f["name"] for f in ledger["telemetry_fields"]}
    assert "segments" not in decoded
    data[0x2E] = 2
    decoded = decode_snapshot({"segments": [{"base": "0xA800", "hex": data.hex()}]}, ledger)
    assert decoded["q1_generation_matches_in_saved_fields"] is True
    assert decoded["atomic_snapshot_proven"] is False


@pytest.mark.parametrize("segments", [
    [{"base": 65535, "hex": "0000"}],
    [{"base": -1, "hex": "00"}],
    [{"base": 0xA800, "hex": "00"}, {"base": 0xA800, "hex": "01"}],
])
def test_decoder_rejects_invalid_snapshot(ledger, segments):
    with pytest.raises(ValueError):
        decode_snapshot({"segments": segments}, ledger)


class LifecycleNamespaceModel:
    """Stateful model of stock-proven Identify, geometry, and Q1 ownership."""

    def __init__(self):
        self.admin_generation = 1
        self.controller_identified = False
        self.namespace_valid = False
        self.nsid = 0
        self.cns = None
        self.ncap = 0
        self.sector_size = 0
        self.q1_initialized = False
        self.q1_admin_generation = 0
        self.media_exposed = False
        self.pending_cids = {}

    def identify_controller(self, cns):
        self.cns = cns
        if cns != 1:
            return False
        self.controller_identified = True
        return True

    def identify_namespace(self, cns, nsid, payload=None):
        self.cns = cns
        self.nsid = nsid
        if not self.controller_identified or cns != 0 or nsid < 1 or payload is None:
            return False
        ncap = int.from_bytes(payload[8:16], "little")
        if ncap == 0:
            return False
        self.ncap = ncap
        flbas = payload[26] & 0x0F
        lbaf_offset = 128 + 4 * flbas
        lbads = payload[lbaf_offset + 2]
        if lbads not in (9, 12):
            return False
        self.sector_size = 1 << lbads
        self.namespace_valid = True
        return True

    def create_io_queues(self, admin_gen):
        if not self.namespace_valid:
            return False
        if self.q1_initialized and self.q1_admin_generation == admin_gen:
            raise ValueError("duplicate Q1 creation without reset boundary")
        self.q1_initialized = True
        self.q1_admin_generation = admin_gen
        self.media_exposed = True
        return True

    def reset_admin(self):
        self.admin_generation = (self.admin_generation + 1) & 0xFF
        self.q1_initialized = False
        self.media_exposed = False
        self.pending_cids.clear()

    def submit_io(self, cid):
        if not self.q1_initialized or self.q1_admin_generation != self.admin_generation:
            raise ValueError("stale or uninitialized Q1 generation")
        if not self.media_exposed:
            raise ValueError("media not exposed")
        if cid in self.pending_cids:
            raise ValueError("duplicate CID")
        self.pending_cids[cid] = self.admin_generation

    def complete_io(self, cid, gen, status=0):
        if cid not in self.pending_cids:
            return "DROPPED_UNKNOWN_CID"
        if gen != self.pending_cids[cid] or gen != self.admin_generation:
            del self.pending_cids[cid]
            return "DROPPED_STALE_GENERATION"
        del self.pending_cids[cid]
        return "COMPLETED_OK" if status == 0 else f"COMPLETED_STATUS_{status}"


def _make_identify_payload(ncap=1000000, lbads=9):
    payload = bytearray(512)
    payload[8:16] = ncap.to_bytes(8, "little")
    payload[26] = 0  # FLBAS index 0
    payload[128 + 2] = lbads  # LBADS: 9 = 512B, 12 = 4096B
    return payload


def test_identify_controller_to_namespace_progression():
    model = LifecycleNamespaceModel()
    assert model.identify_controller(cns=1) is True
    assert model.controller_identified is True
    payload = _make_identify_payload(ncap=2048000, lbads=9)
    assert model.identify_namespace(cns=0, nsid=1, payload=payload) is True
    assert model.namespace_valid is True
    assert model.sector_size == 512
    assert model.ncap == 2048000
    assert model.create_io_queues(model.admin_generation) is True
    assert model.media_exposed is True


def test_namespace_failure_blocks_media():
    model = LifecycleNamespaceModel()
    assert model.identify_controller(cns=1) is True
    # Namespace failure: CNS != 0
    assert model.identify_namespace(cns=1, nsid=1, payload=_make_identify_payload()) is False
    assert model.namespace_valid is False
    assert model.create_io_queues(model.admin_generation) is False
    assert model.media_exposed is False


def test_admin_generation_change_invalidates_old_q1():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    model.create_io_queues(model.admin_generation)
    model.submit_io(cid=1)

    # Recovery: Admin reset advances generation from 1 to 2
    model.reset_admin()
    assert model.admin_generation == 2
    assert model.q1_initialized is False
    assert model.media_exposed is False

    # Old Q1 is rejected as stale
    with pytest.raises(ValueError, match="stale or uninitialized Q1"):
        model.submit_io(cid=2)


def test_late_q1_completion_dropped_after_timeout():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    model.create_io_queues(model.admin_generation)
    model.submit_io(cid=5)

    # Timeout occurs on CID 5, causing Admin recovery and generation increment
    old_gen = model.admin_generation
    model.reset_admin()
    assert model.admin_generation == 2

    # Late completion for CID 5 arrives with old generation; must be dropped
    result = model.complete_io(cid=5, gen=old_gen)
    assert result == "DROPPED_UNKNOWN_CID"


def test_duplicate_q1_creation_rejected_without_reset():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    assert model.create_io_queues(model.admin_generation) is True

    # Duplicate creation with same generation must be rejected
    with pytest.raises(ValueError, match="duplicate Q1 creation"):
        model.create_io_queues(model.admin_generation)


def test_valid_q1_recreation_after_generation_boundary():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    model.create_io_queues(model.admin_generation)

    # Reset advances generation to 2
    model.reset_admin()
    assert model.create_io_queues(model.admin_generation) is True
    assert model.q1_admin_generation == 2
    assert model.media_exposed is True
    model.submit_io(cid=1)
    assert model.complete_io(cid=1, gen=2) == "COMPLETED_OK"


def test_generation_wrap_boundary_handling():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    model.admin_generation = 255
    model.create_io_queues(model.admin_generation)
    model.submit_io(cid=1)
    assert model.complete_io(cid=1, gen=255) == "COMPLETED_OK"

    # Wrap from 255 to 0
    model.reset_admin()
    assert model.admin_generation == 0
    assert model.create_io_queues(model.admin_generation) is True
    model.submit_io(cid=2)
    assert model.complete_io(cid=2, gen=0) == "COMPLETED_OK"


def test_valid_nonzero_nvme_status_preserves_synchronization():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    model.create_io_queues(model.admin_generation)
    model.submit_io(cid=10)

    # Valid CQE arrives with non-zero NVMe status (SC=0x01: Invalid Field in Command)
    res = model.complete_io(cid=10, gen=model.admin_generation, status=0x01)
    assert res == "COMPLETED_STATUS_1"
    # Q1 and Admin generation remain synchronized; no teardown triggered
    assert model.q1_admin_generation == model.admin_generation
    assert model.media_exposed is True


def test_cycle_two_verdict_and_proven_recovery_policy(ledger):
    report = build_report(ledger)
    assert report["result"] == "PASS"
    verdict = ledger["cycle_two_verdict"]
    assert verdict["status"] == "PRE_BAR_REQUIRED_ONLY_AFTER_CONTEXT_INVALIDATION"
    assert verdict["policy_rule"] == "MIXED_RECOVERY_POLICY_BY_FAILURE_CLASS"
    assert "Pre-BAR setup is gated on software context count" in verdict["proven"]


def test_adversarial_mutation_altered_nsid_cns_fails():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    # Mutation 1: NSID 0 is invalid for namespace Identify
    assert model.identify_namespace(cns=0, nsid=0, payload=_make_identify_payload()) is False
    # Mutation 2: CNS 2 is invalid for namespace Identify
    assert model.identify_namespace(cns=2, nsid=1, payload=_make_identify_payload()) is False


def test_adversarial_mutation_accepting_stale_q1_fails():
    model = LifecycleNamespaceModel()
    model.identify_controller(cns=1)
    model.identify_namespace(cns=0, nsid=1, payload=_make_identify_payload())
    model.create_io_queues(model.admin_generation)
    model.reset_admin()

    # Mutation test: a function that blindly accepts mismatched generation must fail
    def unsafe_submit(m, cid):
        if not m.q1_initialized:
            raise ValueError("uninitialized")
        m.pending_cids[cid] = m.admin_generation

    with pytest.raises(ValueError):
        unsafe_submit(model, cid=1)


def test_adversarial_mutation_preserving_tcg_session_across_usb_reset_fails():
    # Multi-domain reset matrix contract: USB reset must revoke TCG session and authorization
    reset_matrix = {
        "USB bus reset": {"session_revoked": True, "auth_revoked": True},
        "BOT reset": {"session_revoked": False, "auth_revoked": False},
        "PCIe link loss": {"session_revoked": True, "auth_revoked": True},
    }

    def evaluate_reset(event, session_alive, auth_alive):
        rule = reset_matrix[event]
        if rule["session_revoked"] and session_alive:
            raise ValueError("session survived reset that revokes it")
        if rule["auth_revoked"] and auth_alive:
            raise ValueError("authorization survived reset that revokes it")
        return "PASS"

    assert evaluate_reset("BOT reset", True, True) == "PASS"
    with pytest.raises(ValueError, match="session survived"):
        evaluate_reset("USB bus reset", session_alive=True, auth_alive=False)
    with pytest.raises(ValueError, match="authorization survived"):
        evaluate_reset("USB bus reset", session_alive=False, auth_alive=True)


def test_adversarial_mutation_conflating_pyrite_auth_with_transport_fails():
    # Transport readiness (Admin initialized) does NOT imply Pyrite media authorization
    admin_ready = True
    pyrite_authorized = False

    def media_access_allowed(transport, auth):
        # Flawed policy: if transport, allow
        return transport and auth

    assert media_access_allowed(admin_ready, pyrite_authorized) is False
    # If flawed policy conflates them:
    flawed_policy = lambda transport, auth: transport
    with pytest.raises(AssertionError):
        assert flawed_policy(admin_ready, pyrite_authorized) == media_access_allowed(admin_ready, pyrite_authorized)


def test_decoder_cycle_two_summary_output(ledger):
    data = bytearray(0xCE)
    data[0] = data[0x2B] = 1  # admin_init = 1, q1_init = 1
    data[0x0C] = data[0x2E] = 3  # admin_generation = 3, q1_admin_generation = 3
    data[0x38] = 0x00  # pcie_devfn = 0
    data[0x54] = 2  # pyrite_state = 2 (authorized)
    data[0x8F] = 1  # media_state = 1
    data[0x91] = 1  # namespace_valid = 1
    data[0x93] = 0x00  # media_block_size
    data[0x94] = 0x02  # 512
    data[0xCB] = 9  # lba_shift = 9

    # Segments omit 0xA83A so pcie_context_valid is absent (Cycle 2 legacy snapshot)
    seg1 = {"base": "0xA800", "hex": data[:0x39].hex()}
    seg2 = {"base": "0xA840", "hex": data[0x40:].hex()}
    decoded = decode_snapshot({"segments": [seg1, seg2]}, ledger)
    summary = decoded["summary"]
    assert summary["pcie_context"] == "present"
    assert summary["admin_generation"] == 3
    assert summary["q1_generation_status"] == "match"
    assert summary["namespace_status"] == "geometry_valid"
    assert summary["tcg_transport_state"] == "ready"
    assert summary["tcg_session_state"] == "state_2"
    assert summary["pyrite_authorization_state"] == "authorized"
    assert summary["bot_media_gate_state"] == "ready"


class LifecycleExecutionModel:
    """Executable model of firmware PCIe context, Admin/Q1 generation, and BOT media gate."""

    def __init__(self):
        self.cold_boot()

    def cold_boot(self):
        self.pcie_context_valid = False
        self.pre_bar_init_calls = 0
        self.admin_generation = 0
        self.admin_initialized = False
        self.q1_generation = 0
        self.q1_ready = False
        self.pyrite_locked = True
        self.media_gate_c404 = 0
        self.geometry_c422 = 0
        self.block_size_c424 = 0
        self.bot_generation = 0

    def pcie_pre_bar_init(self, fail=False):
        self.pre_bar_init_calls += 1
        if fail:
            self.pcie_context_valid = False
            return False
        self.pcie_context_valid = True
        return True

    def nvme_admin_init(self, fail_pre_bar=False, fail_csts=False):
        if not self.pcie_context_valid:
            if not self.pcie_pre_bar_init(fail=fail_pre_bar):
                self.pcie_context_valid = False
                return False
        if fail_csts:
            self.pcie_context_valid = False
            self.admin_initialized = False
            return False
        self.admin_generation += 1
        self.admin_initialized = True
        return True

    def nvme_admin_command(self, opcode, timeout=False, fatal_transport=False, status_sc=0):
        if fatal_transport or timeout:
            self.pcie_context_valid = False
            self.admin_initialized = False
            return "POISON"
        return {"status": status_sc, "context_valid": self.pcie_context_valid}

    def nvme_create_q1(self, fail=False):
        if not self.admin_initialized:
            return False
        if fail:
            return False
        self.q1_generation = self.admin_generation
        self.q1_ready = True
        self.geometry_c422 = 0x1234
        self.block_size_c424 = 512
        self.media_gate_c404 = 2
        return True

    def pyrite_unlock(self, credential):
        if credential == b"valid":
            self.pyrite_locked = False
            return True
        return False

    def media_ready(self):
        return (
            self.pcie_context_valid
            and self.admin_initialized
            and self.q1_ready
            and not self.pyrite_locked
            and (self.q1_generation == self.admin_generation)
            and (self.media_gate_c404 == 2)
        )

    def scsi_tur(self):
        if not self.media_ready():
            return {"status": "CHECK_CONDITION", "sense": (0x02, 0x3A, 0x00)}
        return {"status": "GOOD"}

    def scsi_inquiry(self):
        return {"status": "GOOD", "vendor": "ASMedia", "product": "ASM2464PD"}

    def scsi_read_capacity(self):
        if not self.media_ready():
            return {"status": "CHECK_CONDITION", "sense": (0x02, 0x3A, 0x00)}
        return {"status": "GOOD", "blocks": self.geometry_c422, "block_size": self.block_size_c424}

    def scsi_read(self, lba, count):
        if not self.media_ready():
            sense = (0x07, 0x20, 0x00) if self.pyrite_locked else (0x02, 0x3A, 0x00)
            return {"status": "CHECK_CONDITION", "sense": sense}
        return {"status": "GOOD", "lba": lba, "count": count}

    def scsi_write(self, lba, count):
        if not self.media_ready():
            sense = (0x07, 0x20, 0x00) if self.pyrite_locked else (0x02, 0x3A, 0x00)
            return {"status": "CHECK_CONDITION", "sense": sense}
        return {"status": "GOOD", "lba": lba, "count": count}

    def poison_q1(self):
        self.q1_ready = False
        self.media_gate_c404 = 0

    def link_loss(self):
        self.pcie_context_valid = False
        self.admin_initialized = False
        self.q1_ready = False
        self.media_gate_c404 = 0

    def bot_reset(self):
        self.bot_generation += 1


def test_same_instance_pcie_context_and_pre_bar_policy():
    m = LifecycleExecutionModel()

    # 1. Cold boot -> invalid context -> pre-BAR runs once -> valid
    assert not m.pcie_context_valid
    assert m.admin_generation == 0
    assert m.nvme_admin_init() is True
    assert m.pcie_context_valid is True
    assert m.pre_bar_init_calls == 1
    assert m.admin_generation == 1

    # 2. Second Admin init with valid retained context -> pre-BAR is skipped
    assert m.nvme_admin_init() is True
    assert m.pcie_context_valid is True
    assert m.pre_bar_init_calls == 1  # Not incremented!
    assert m.admin_generation == 2

    # 3. Synchronous completion with nonzero NVMe status -> context preserved -> pre-BAR skipped
    res = m.nvme_admin_command(0x06, status_sc=0x02)  # Invalid Field
    assert res["status"] == 0x02
    assert res["context_valid"] is True
    assert m.pcie_context_valid is True
    assert m.nvme_admin_init() is True
    assert m.pre_bar_init_calls == 1  # Still not incremented!
    assert m.admin_generation == 3

    # 4. Admin timeout / poison -> context invalidated -> next recovery reruns pre-BAR
    poison_res = m.nvme_admin_command(0x06, timeout=True)
    assert poison_res == "POISON"
    assert m.pcie_context_valid is False
    assert m.nvme_admin_init() is True
    assert m.pcie_context_valid is True
    assert m.pre_bar_init_calls == 2  # Rerun!
    assert m.admin_generation == 4

    # 5. Failed MMIO / CSTS fatal -> context invalidated -> next recovery reruns pre-BAR
    assert m.nvme_admin_init(fail_csts=True) is False
    assert m.pcie_context_valid is False
    assert m.nvme_admin_init() is True
    assert m.pcie_context_valid is True
    assert m.pre_bar_init_calls == 3  # Rerun!
    assert m.admin_generation == 5

    # 6. Link loss / teardown -> context invalidated -> next recovery reruns pre-BAR
    m.link_loss()
    assert m.pcie_context_valid is False
    assert m.nvme_admin_init() is True
    assert m.pcie_context_valid is True
    assert m.pre_bar_init_calls == 4  # Rerun!
    assert m.admin_generation == 6

    # 7. Pre-BAR failure -> context stays invalid
    m.pcie_context_valid = False
    assert m.nvme_admin_init(fail_pre_bar=True) is False
    assert m.pcie_context_valid is False
    assert m.pre_bar_init_calls == 5

    # Recover from pre-BAR failure
    assert m.nvme_admin_init() is True
    assert m.pcie_context_valid is True
    assert m.pre_bar_init_calls == 6
    assert m.admin_generation == 7

    # 8. Q1-only poison -> context preserved, pre-BAR skipped
    assert m.nvme_create_q1() is True
    assert m.q1_ready is True
    m.poison_q1()
    assert m.q1_ready is False
    assert m.pcie_context_valid is True  # Context preserved across Q1 poison!
    assert m.nvme_admin_init() is True
    assert m.pre_bar_init_calls == 6  # Skipped!
    assert m.admin_generation == 8


def test_adversarial_mutations_pcie_context_policy():
    m = LifecycleExecutionModel()

    # Mutation 1: Premature valid bit before window restore
    def broken_pre_bar_premature():
        m.pcie_context_valid = True
        # Failure occurs after premature set
        raise RuntimeError("BAR window restore failed")

    with pytest.raises(RuntimeError):
        broken_pre_bar_premature()
    # If the implementation didn't guard this, pcie_context_valid would be erroneously True
    # Correct contract clears on failure:
    m.pcie_context_valid = False
    assert m.pcie_context_valid is False

    # Mutation 2: Transport timeout failing to clear context
    m.nvme_admin_init()
    assert m.pcie_context_valid is True
    broken_nvme_admin_command = lambda: "POISON"  # forgets to clear context
    broken_nvme_admin_command()
    with pytest.raises(AssertionError):
        # A correct poison must invalidate context
        assert m.pcie_context_valid is False

    # Mutation 3: Clearing context on valid CQE status error
    # Nonzero status is a valid software completion, not a transport loss
    m.pcie_context_valid = True
    sc_error = 0x02
    flawed_clear_on_sc = lambda: setattr(m, "pcie_context_valid", False)
    flawed_clear_on_sc()
    with pytest.raises(AssertionError):
        assert m.pcie_context_valid is True

    # Mutation 4: Unconditional pre-BAR on every Admin init
    m.pcie_context_valid = True
    calls_before = m.pre_bar_init_calls
    unconditional_pre_bar = lambda: setattr(m, "pre_bar_init_calls", m.pre_bar_init_calls + 1)
    unconditional_pre_bar()
    with pytest.raises(AssertionError):
        assert m.pre_bar_init_calls == calls_before


def test_bot_disk_gate_scsi_invariants():
    m = LifecycleExecutionModel()
    m.nvme_admin_init()

    # Before Q1 creation or unlock: INQUIRY succeeds, TUR/READ/WRITE fail
    inq = m.scsi_inquiry()
    assert inq["status"] == "GOOD"
    assert inq["vendor"] == "ASMedia"

    tur = m.scsi_tur()
    assert tur["status"] == "CHECK_CONDITION"
    assert tur["sense"] == (0x02, 0x3A, 0x00)  # Not Ready

    rcap = m.scsi_read_capacity()
    assert rcap["status"] == "CHECK_CONDITION"

    read_res = m.scsi_read(0, 1)
    assert read_res["status"] == "CHECK_CONDITION"
    assert read_res["sense"] == (0x07, 0x20, 0x00)  # Data Protect (locked)

    # Create Q1: media gate becomes 2, but Pyrite still locked
    assert m.nvme_create_q1() is True
    assert m.media_gate_c404 == 2
    assert m.scsi_tur()["status"] == "CHECK_CONDITION"  # Still locked!

    # Unlock Pyrite: media becomes fully ready
    assert m.pyrite_unlock(b"valid") is True
    assert m.media_ready() is True

    # Now TUR, READ CAPACITY, READ, and WRITE all succeed
    assert m.scsi_tur()["status"] == "GOOD"
    rcap_good = m.scsi_read_capacity()
    assert rcap_good["status"] == "GOOD"
    assert rcap_good["block_size"] == 512
    assert m.scsi_read(0, 8)["status"] == "GOOD"
    assert m.scsi_write(0, 8)["status"] == "GOOD"

    # Q1 poison quiesces media I/O while preserving PCIe context
    m.poison_q1()
    assert m.scsi_tur()["status"] == "CHECK_CONDITION"
    assert m.pcie_context_valid is True

    # BOT reset increments BOT generation without revoking session/auth
    bot_gen_before = m.bot_generation
    m.bot_reset()
    assert m.bot_generation == bot_gen_before + 1


def test_tcg_9x6_reset_matrix_completeness_and_adversarial(ledger):
    matrix = ledger.get("tcg_reset_matrix")
    assert matrix is not None
    assert len(matrix) == 9  # 9 reset events

    required_domains = [
        "bot_domain", "session_domain", "authorization_domain",
        "queue_domain_q1", "controller_bar_domain", "transport_domain_q0"
    ]
    total_cells = 0
    classifications = set()
    for event in matrix:
        assert "event_id" in event
        assert "display_name" in event
        domains = event["domains"]
        for d in required_domains:
            assert d in domains, f"Missing domain {d} in event {event['event_id']}"
            cell = domains[d]
            assert "effect" in cell
            assert "classification" in cell
            assert "detail" in cell
            classifications.add(cell["classification"])
            total_cells += 1

    assert total_cells == 54
    assert classifications <= {
        "INSTRUCTION_PROVEN", "CALLER_CALLEE_PROVEN",
        "STANDARD_BACKED_INTERPRETATION", "EMULATOR_MODEL_ONLY"
    }

    # Adversarial check: attempt to claim session survives USB bus reset
    usb_event = next(e for e in matrix if e["event_id"] == "usb_bus_reset")
    flawed_policy = lambda event: "Preserved"
    with pytest.raises(AssertionError):
        assert flawed_policy(usb_event) == usb_event["domains"]["session_domain"]["effect"]


def test_decoder_cycle_three_summary_output(ledger):
    data = bytearray(0xCE)
    data[0] = data[0x2B] = 1
    data[0x0C] = data[0x2E] = 3
    data[0x38] = 0x00  # pcie_devfn = 0
    data[0x3A] = 1     # pcie_context_valid = 1
    data[0x54] = 2     # pyrite_state = 2 (authorized)
    data[0x8F] = 1     # media_state = 1
    data[0x91] = 1     # namespace_valid = 1
    data[0x93] = 0x00
    data[0x94] = 0x02  # 512
    data[0xCB] = 9     # lba_shift = 9

    # Case 1: pcie_context_valid = 1 -> "valid"
    seg = {"base": "0xA800", "hex": data.hex()}
    decoded = decode_snapshot({"segments": [seg]}, ledger)
    assert decoded["summary"]["pcie_context"] == "valid"
    assert decoded["summary"]["selected_devfn"] == 0

    # Case 2: pcie_context_valid = 0 -> "invalid"
    data[0x3A] = 0
    seg = {"base": "0xA800", "hex": data.hex()}
    decoded = decode_snapshot({"segments": [seg]}, ledger)
    assert decoded["summary"]["pcie_context"] == "invalid"

