import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from simulation.asm2464.adapter.stock import full_spi, execute_code
from simulation.asm2464.adapter.cpu import CpuBackend
from simulation.board.model import Board

class StockInputTests(unittest.TestCase):
    def test_missing_restricted_input_is_blocked(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(full_spi()['status'],'BLOCKED_STOCK_FULL_SPI_IMAGE_UNAVAILABLE')
    def test_wrong_sizes_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test-only.bin'
            for n in (0,98006,0x7ffff,0x80001):
                p.write_bytes(b'\xff'*n)
                with self.assertRaisesRegex(ValueError,'exactly 524288'):full_spi(p)
    def test_synthetic_input_exercises_interface_without_disclosure(self):
        # TEST_ONLY_FAULT_INJECTION: this generated input is not a stock image.
        # Only the interface's size/hash/readback/redaction contracts are tested.
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test-only.bin';marker=b'PRIVATE_SENTINEL'
            p.write_bytes(marker+b'\xff'*(0x80000-len(marker)))
            output=io.StringIO()
            with contextlib.redirect_stdout(output):report=full_spi(p)
            text=json.dumps(report)+output.getvalue()
            self.assertNotIn(marker.hex(),text);self.assertNotIn(marker.decode(),text)
            self.assertEqual(report['status'],'STOCK_FULL_SPI_BOOT_BLOCKED_ON_BOOT_ROM_MODEL')
            self.assertFalse(report['raw_image_copied'])
            self.assertEqual(report['image_size'],524288)
            for key in ('image_sha256','source_class','expected_flash_profile','simulator_commit','upstream_emulator_commit','physical_baseline_commit'):
                self.assertIn(key,report)
            self.assertEqual(list(Path(tmp).iterdir()),[p])
    def test_wrong_code_artifact_cannot_become_stock(self):
        with self.assertRaisesRegex(ValueError,'hash/size'):execute_code(bytes(98006))
    def test_restricted_image_cannot_use_generic_boot_metadata(self):
        b=Board(flash_profile='stock_physical',restricted=True)
        with self.assertRaisesRegex(ValueError,'BOOT_BLOCKED'):CpuBackend(b)
