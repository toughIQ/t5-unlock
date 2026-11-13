#!/usr/bin/env python3
"""Tests for the code around the unlock sequence: drive lookup through a fake sysfs tree,
and the prompts, exit codes and messages of unlock(). No drive, no root.
Run: python3 linux/test_flow.py
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "t5", os.path.join(os.path.dirname(os.path.abspath(__file__)), "t5-unlock.py"))
t5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t5)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class FakeSysfs:
    """Builds /sys/block-like trees: block/<name>/{size,device -> usb device dir}."""

    def __init__(self, root):
        self.root = root
        self.block = os.path.join(root, "block")
        os.makedirs(self.block)

    def add(self, name, vendor="Samsung", model="Portable SSD T5", vid="04e8", pid="61f6",
            sectors=172032, readable=True):
        usb = os.path.join(self.root, "usb", name)
        dev = os.path.join(usb, "host0", "target", "dev")
        write(os.path.join(usb, "idVendor"), vid + "\n")
        write(os.path.join(usb, "idProduct"), pid + "\n")
        if readable:
            write(os.path.join(dev, "vendor"), vendor + "  \n")
            write(os.path.join(dev, "model"), model + "\n")
        else:
            os.makedirs(dev)
        write(os.path.join(self.block, name, "size"), f"{sectors}\n")
        os.symlink(dev, os.path.join(self.block, name, "device"))


class DriveLookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fs = FakeSysfs(self.tmp.name)
        patcher = mock.patch.object(t5, "SYS_BLOCK", self.fs.block)
        patcher.start()
        self.addCleanup(patcher.stop)

    def find(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return t5.find_locked()

    def test_finds_exactly_one_locked_t5(self):
        self.fs.add("sda")
        self.assertEqual(self.find(), "/dev/sda")

    def test_usb_ids_come_from_the_parent_usb_device(self):
        self.fs.add("sda", vid="04e8", pid="61f6")
        self.assertEqual(t5.t5_disks(), [("sda", "04e8", "61f6", 172032 * 512)])

    def test_ignores_other_vendors_and_models(self):
        self.fs.add("sda", vendor="Other")
        self.fs.add("sdb", model="Portable SSD T7")
        self.fs.add("sdc")
        self.assertEqual(self.find(), "/dev/sdc")

    def test_unlocked_t5_is_not_a_locked_candidate(self):
        self.fs.add("sda", pid="61f5", sectors=2_000_000_000)
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            self.find()
        self.assertEqual(cm.exception.code, 1)

    def test_two_locked_drives_are_refused(self):
        self.fs.add("sda")
        self.fs.add("sdb")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.find()

    def test_no_drive_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.find()

    def test_locked_id_with_a_big_disk_is_refused(self):
        self.fs.add("sda", sectors=2_000_000_000)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.find()

    def test_unreadable_entries_are_skipped_not_fatal(self):
        self.fs.add("sda", readable=False)
        self.fs.add("sdb")
        self.assertEqual(self.find(), "/dev/sdb")


class UnlockWrapper(unittest.TestCase):
    """unlock(): prompts, exit codes and messages. The drive conversation is faked."""

    def run_unlock(self, passwords, outcome=("unlocked", ""), waited=True):
        sequence = mock.Mock(return_value=outcome)
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(t5.getpass, "getpass", side_effect=passwords), \
                mock.patch.object(t5, "unmount_all"), \
                mock.patch.object(t5, "unlock_sequence", sequence), \
                mock.patch.object(t5, "wait_for_unlocked", return_value=waited), \
                mock.patch.object(t5.os, "open", return_value=99), \
                mock.patch.object(t5.os, "close"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                t5.unlock("/dev/sda")
                code = 0
            except SystemExit as e:
                code = e.code
        return code, sequence, out.getvalue(), err.getvalue()

    def test_mismatched_passwords_send_nothing(self):
        code, sequence, _, err = self.run_unlock(["secret-one", "secret-two"])
        self.assertEqual(code, 1)
        sequence.assert_not_called()
        self.assertIn("do not match", err)

    def test_invalid_password_characters_send_nothing(self):
        for bad in ("has space", "x" * 33, "umlaut-\u00e4"):
            with self.subTest(bad=bad):
                code, sequence, _, err = self.run_unlock([bad, bad])
                self.assertEqual(code, 1)
                sequence.assert_not_called()
                self.assertIn("Nothing was sent", err)

    def test_success_reports_and_waits_for_the_disk(self):
        code, sequence, out, _ = self.run_unlock(["Correct-Horse_42"] * 2)
        self.assertEqual(code, 0)
        sequence.assert_called_once()
        self.assertEqual(sequence.call_args[0][1], b"Correct-Horse_42")
        self.assertIn("Password accepted", out)

    def test_wrong_password_exits_with_code_2_and_says_no_unlock_sent(self):
        code, _, _, err = self.run_unlock(["Correct-Horse_42"] * 2, outcome=("rejected", ""))
        self.assertEqual(code, 2)
        self.assertIn("No unlock command was sent", err)

    def test_every_failure_outcome_exits_nonzero_and_never_claims_success(self):
        for outcome in ("not_t5", "bad_status", "no_challenge", "validation_unclear",
                        "no_fresh_challenge"):
            with self.subTest(outcome=outcome):
                code, _, out, _ = self.run_unlock(["Correct-Horse_42"] * 2,
                                                  outcome=(outcome, "detail"))
                self.assertNotEqual(code, 0)
                self.assertNotIn("Password accepted", out)

    def test_disk_not_appearing_is_an_error(self):
        code, _, _, err = self.run_unlock(["Correct-Horse_42"] * 2, waited=False)
        self.assertNotEqual(code, 0)
        self.assertIn("did not show up", err)


if __name__ == "__main__":
    unittest.main(verbosity=1)
