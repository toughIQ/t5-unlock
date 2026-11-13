#!/usr/bin/env python3
"""Tests for the code around the unlock sequence: drive lookup through a fake sysfs tree,
mount handling, and the prompts, exit codes and messages of unlock() and main().
No drive, no root. Run: python3 linux/test_flow.py
"""

import contextlib
import errno
import importlib.util
import io
import os
import tempfile
import types
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

    def refused(self):
        err, out = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(out):
            t5.find_locked()
        return cm.exception.code, out.getvalue(), err.getvalue()

    def test_already_unlocked_t5_exits_0_with_nothing_to_do(self):
        self.fs.add("sda", pid="61f5", sectors=2_000_000_000)
        code, out, err = self.refused()
        self.assertEqual(code, 0)
        self.assertIn("The T5 is already unlocked (/dev/sda). Nothing to do.", out)
        self.assertEqual(err, "")

    def test_two_locked_drives_are_refused(self):
        self.fs.add("sda")
        self.fs.add("sdb")
        code, _, err = self.refused()
        self.assertEqual(code, 2)
        self.assertIn("Found 2 locked T5 drives. Connect only one. Nothing was sent.", err)

    def test_no_drive_is_refused(self):
        code, _, err = self.refused()
        self.assertEqual(code, 2)
        self.assertIn("No Samsung Portable SSD T5 found. Plug it in and wait a few seconds.",
                      err)

    def test_t5_with_an_unknown_usb_id_is_refused(self):
        self.fs.add("sda", pid="1234")
        code, _, err = self.refused()
        self.assertEqual(code, 2)
        self.assertIn("Nothing was sent.", err)

    def test_locked_id_with_a_big_disk_is_refused(self):
        self.fs.add("sda", sectors=2_000_000_000)
        code, _, err = self.refused()
        self.assertEqual(code, 2)
        self.assertIn("Nothing was sent.", err)

    def test_one_locked_next_to_an_unlocked_t5_is_still_found(self):
        self.fs.add("sda", pid="61f5", sectors=2_000_000_000)
        self.fs.add("sdb")
        self.assertEqual(self.find(), "/dev/sdb")

    def test_half_set_up_devices_are_skipped_not_fatal(self):
        self.fs.add("sda")
        self.fs.add("sdb")
        write(os.path.join(self.fs.block, "sda", "size"), "not a number\n")
        os.remove(os.path.join(self.fs.root, "usb", "sdb", "idProduct"))
        os.makedirs(os.path.join(self.fs.root, "usb", "sdb", "idProduct"))  # unreadable
        self.fs.add("sdc")
        self.assertEqual(self.find(), "/dev/sdc")

    def test_undecodable_sysfs_text_is_skipped(self):
        self.fs.add("sda")
        with open(os.path.join(self.fs.block, "sda", "size"), "wb") as f:
            f.write(b"\xff\xfe\n")
        self.fs.add("sdb")
        self.assertEqual(self.find(), "/dev/sdb")

    def test_unreadable_entries_are_skipped_not_fatal(self):
        self.fs.add("sda", readable=False)
        self.fs.add("sdb")
        self.assertEqual(self.find(), "/dev/sdb")


PW = "Correct-Horse_42"


class FakeTty(io.StringIO):
    def __init__(self, tty=True):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def good_status_block():
    block = bytearray(512)
    block[0x20:0x24] = b"PSSD"
    block[0x24:0x28] = (0x00010103).to_bytes(4, "little")
    return bytes(block)


def fake_open_device(_dev, exclusive=False):
    return os.open(os.devnull, os.O_RDONLY)  # a real fd, closed by the code under test


class UnlockWrapper(unittest.TestCase):
    """unlock(): prompts, exit codes and messages. The drive conversation is faked."""

    def run_unlock(self, passwords, outcome=("unlocked", ""), waited=True, tty=True,
                   real_preflight=False, t5_identified=True, status_block=None):
        res = types.SimpleNamespace(
            sequence=mock.Mock(return_value=outcome), wait=mock.Mock(return_value=waited),
            getpass=mock.Mock(side_effect=passwords), unmount=mock.Mock(),
            preflight=mock.Mock(), open=mock.Mock(side_effect=fake_open_device))
        err, out = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(t5.sys, "stdin", FakeTty(tty)))
            stack.enter_context(mock.patch.object(t5.getpass, "getpass", res.getpass))
            stack.enter_context(mock.patch.object(t5, "unmount_all", res.unmount))
            stack.enter_context(mock.patch.object(t5, "unlock_sequence", res.sequence))
            stack.enter_context(mock.patch.object(t5, "wait_for_unlocked", res.wait))
            stack.enter_context(mock.patch.object(t5, "unlocked_disks", return_value=[]))
            stack.enter_context(mock.patch.object(t5, "open_device", res.open))
            if real_preflight:
                stack.enter_context(
                    mock.patch.object(t5, "inquiry_is_t5", return_value=t5_identified))
                stack.enter_context(mock.patch.object(
                    t5, "read_log",
                    return_value=good_status_block() if status_block is None else status_block))
            else:
                stack.enter_context(mock.patch.object(t5, "preflight", res.preflight))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(contextlib.redirect_stdout(out))
            try:
                t5.unlock("/dev/sda")
                res.code = 0
            except SystemExit as e:
                res.code = e.code
        res.out, res.err = out.getvalue(), err.getvalue()
        return res

    def test_mismatched_passwords_send_nothing(self):
        res = self.run_unlock(["secret-one", "secret-two"])
        self.assertEqual(res.code, 2)
        res.sequence.assert_not_called()
        self.assertIn("do not match", res.err)

    def test_invalid_password_characters_send_nothing(self):
        for bad in ("has space", "x" * 33, "umlaut-\u00e4"):
            with self.subTest(bad=bad):
                res = self.run_unlock([bad, bad])
                self.assertEqual(res.code, 2)
                res.sequence.assert_not_called()
                self.assertIn("Nothing was sent", res.err)

    def test_prompts_name_the_drive_password(self):
        res = self.run_unlock([PW] * 2)
        prompts = [c.args[0] for c in res.getpass.call_args_list]
        self.assertEqual(prompts, ["T5 DRIVE password (not your login or sudo password): ",
                                   "Repeat the T5 DRIVE password: "])

    def test_success_reports_and_waits_for_the_disk(self):
        res = self.run_unlock([PW] * 2)
        self.assertEqual(res.code, 0)
        res.sequence.assert_called_once()
        self.assertEqual(res.sequence.call_args[0][1], PW.encode())
        res.wait.assert_called_once()
        self.assertIn("Password accepted", res.out)

    def test_unlock_unmounts_then_opens_the_device_exclusively(self):
        res = self.run_unlock([PW] * 2)
        res.unmount.assert_called_once_with("/dev/sda")
        res.open.assert_called_once_with("/dev/sda", exclusive=True)

    def test_wrong_password_exits_with_code_2_and_says_what_to_do(self):
        res = self.run_unlock([PW] * 2, outcome=("rejected", ""))
        self.assertEqual(res.code, 4)
        self.assertIn("No unlock command was sent", res.err)
        self.assertIn("Do not try again until you are sure of the password. "
                      "The drive's behavior after several wrong passwords is unknown.", res.err)
        res.wait.assert_not_called()

    def test_every_failure_outcome_has_its_exit_code_and_never_claims_success(self):
        codes = {"not_t5": 3, "bad_status": 3, "no_challenge": 3, "validation_unclear": 3,
                 "no_fresh_challenge": 3, "unlock_error": 3, "rejected": 4}
        for outcome, expected in codes.items():
            with self.subTest(outcome=outcome):
                res = self.run_unlock([PW] * 2, outcome=(outcome, "detail"))
                self.assertEqual(res.code, expected)
                self.assertNotIn("Password accepted", res.out)
                res.wait.assert_not_called()

    def test_unlock_error_does_not_wait_and_says_what_to_do(self):
        res = self.run_unlock([PW] * 2, outcome=("unlock_error", ""))
        self.assertEqual(res.code, 3)
        res.wait.assert_not_called()
        self.assertIn("Unlock did not report success. No relink was sent. "
                      "Unplug and replug the drive, then check with lsblk.", res.err)

    def test_disk_not_appearing_is_an_error(self):
        res = self.run_unlock([PW] * 2, waited=False)
        self.assertEqual(res.code, 5)
        self.assertIn("did not show up", res.err)

    def test_no_terminal_is_refused_before_anything_happens(self):
        res = self.run_unlock([PW] * 2, tty=False)
        self.assertEqual(res.code, 2)
        self.assertIn("A terminal is needed to type the password. Nothing was sent.", res.err)
        res.getpass.assert_not_called()
        res.preflight.assert_not_called()
        res.open.assert_not_called()
        res.sequence.assert_not_called()

    def test_preflight_runs_before_the_password_is_asked(self):
        res_calls = []
        with mock.patch.object(t5, "preflight", side_effect=lambda d: res_calls.append("pre")), \
                mock.patch.object(t5.getpass, "getpass",
                                  side_effect=lambda p: res_calls.append("ask") or PW), \
                mock.patch.object(t5, "unmount_all"), \
                mock.patch.object(t5, "unlocked_disks", return_value=[]), \
                mock.patch.object(t5, "open_device", fake_open_device), \
                mock.patch.object(t5, "unlock_sequence", return_value=("unlocked", "")), \
                mock.patch.object(t5, "wait_for_unlocked", return_value=True), \
                mock.patch.object(t5.sys, "stdin", FakeTty()), \
                contextlib.redirect_stdout(io.StringIO()):
            t5.unlock("/dev/sda")
        self.assertEqual(res_calls, ["pre", "ask", "ask"])

    def test_preflight_failure_means_no_password_prompt(self):
        bad_status = bytearray(good_status_block())
        bad_status[0x20:0x24] = b"XXXX"
        cases = [{"t5_identified": False}, {"status_block": bytes(bad_status)}]
        for case in cases:
            with self.subTest(case=list(case)):
                res = self.run_unlock([PW] * 2, real_preflight=True, **case)
                self.assertEqual(res.code, 3)
                self.assertIn("No password was asked or sent.", res.err)
                res.getpass.assert_not_called()
                res.unmount.assert_not_called()
                res.sequence.assert_not_called()

    def test_preflight_read_failure_means_no_password_prompt(self):
        err = io.StringIO()
        with mock.patch.object(t5, "open_device", fake_open_device), \
                mock.patch.object(t5, "inquiry_is_t5", return_value=True), \
                mock.patch.object(t5, "read_log", return_value=None), \
                mock.patch.object(t5.getpass, "getpass") as ask, \
                mock.patch.object(t5.sys, "stdin", FakeTty()), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            t5.unlock("/dev/sda")
        self.assertEqual(cm.exception.code, 3)
        self.assertIn("could not read the status block", err.getvalue())
        self.assertIn("No password was asked or sent.", err.getvalue())
        ask.assert_not_called()

    def test_preflight_passes_on_a_good_drive(self):
        res = self.run_unlock([PW] * 2, real_preflight=True)
        self.assertEqual(res.code, 0)
        self.assertEqual(res.getpass.call_count, 2)

    def test_password_never_appears_in_output(self):
        outcomes = ["unlocked", "rejected", "unlock_error", "validation_unclear", "not_t5",
                    "bad_status", "no_challenge", "no_fresh_challenge"]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                res = self.run_unlock([PW] * 2, outcome=(outcome, "detail"))
                self.assertNotIn(PW, res.out)
                self.assertNotIn(PW, res.err)
        for passwords in ([PW, "other-One_1"], [PW + " x", PW + " x"]):
            res = self.run_unlock(passwords)
            self.assertNotIn(PW, res.out)
            self.assertNotIn(PW, res.err)


class UnmountAll(unittest.TestCase):
    MOUNTS_TEXT = (
        "/dev/sda1 /mnt/a ext4 rw 0 0\n"
        "/dev/sda /mnt/b vfat rw 0 0\n"
        "/dev/sdab1 /mnt/c ext4 rw 0 0\n"
        "/dev/sdab /mnt/d ext4 rw 0 0\n"
        "/dev/sda10 /mnt/e ext4 rw 0 0\n"
        "/dev/sdb1 /mnt/f ext4 rw 0 0\n"
        "/dev/sda1p /mnt/g ext4 rw 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mounts = os.path.join(self.tmp.name, "mounts")
        write(self.mounts, self.MOUNTS_TEXT)
        patcher = mock.patch.object(t5, "MOUNTS", self.mounts)
        patcher.start()
        self.addCleanup(patcher.stop)

    def unmount(self, run):
        err = io.StringIO()
        with mock.patch.object(t5.subprocess, "run", run), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                t5.unmount_all("/dev/sda")
                code = 0
            except SystemExit as e:
                code = e.code
        return code, err.getvalue()

    def test_only_the_exact_device_and_its_partitions_are_unmounted(self):
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=0))
        self.assertEqual(self.unmount(run)[0], 0)
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["umount", "/dev/sda1"], ["umount", "/dev/sda"],
                          ["umount", "/dev/sda10"]])

    def test_nothing_mounted_runs_no_command(self):
        write(self.mounts, "/dev/sdab1 /mnt/c ext4 rw 0 0\n")
        run = mock.Mock()
        self.assertEqual(self.unmount(run)[0], 0)
        run.assert_not_called()

    def test_umount_failure_dies_and_stops(self):
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=32))
        code, err = self.unmount(run)
        self.assertEqual(code, 2)
        self.assertIn("Could not unmount /dev/sda1. Nothing was sent.", err)
        self.assertEqual(run.call_count, 1)

    def test_missing_umount_dies_with_a_clear_message(self):
        run = mock.Mock(side_effect=FileNotFoundError(errno.ENOENT, "No such file or directory"))
        code, err = self.unmount(run)
        self.assertEqual(code, 2)
        self.assertIn("Cannot run umount", err)
        self.assertIn("Nothing was sent.", err)

    def test_unreadable_mounts_file_dies(self):
        os.remove(self.mounts)
        code, err = self.unmount(mock.Mock())
        self.assertEqual(code, 2)
        self.assertIn("Nothing was sent.", err)

    def test_umount_failure_in_unlock_stops_before_open_and_any_drive_command(self):
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=1))
        sequence, opener = mock.Mock(), mock.Mock(side_effect=fake_open_device)
        with mock.patch.object(t5.subprocess, "run", run), \
                mock.patch.object(t5, "preflight"), \
                mock.patch.object(t5, "unlocked_disks", return_value=[]), \
                mock.patch.object(t5.getpass, "getpass", side_effect=[PW, PW]), \
                mock.patch.object(t5, "open_device", opener), \
                mock.patch.object(t5, "unlock_sequence", sequence), \
                mock.patch.object(t5.sys, "stdin", FakeTty()), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as cm:
            t5.unlock("/dev/sda")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("Nothing was sent.", err.getvalue())
        opener.assert_not_called()
        sequence.assert_not_called()


class OpenDevice(unittest.TestCase):
    def open(self, side_effect, **kwargs):
        err = io.StringIO()
        with mock.patch.object(t5.os, "open", side_effect=side_effect) as opener, \
                contextlib.redirect_stderr(err):
            try:
                result = t5.open_device("/dev/sda", **kwargs)
                code = None
            except SystemExit as e:
                result, code = None, e.code
        return opener, result, code, err.getvalue()

    def test_plain_open_flags(self):
        opener, result, _, _ = self.open([7])
        self.assertEqual(result, 7)
        opener.assert_called_once_with("/dev/sda", os.O_RDWR | os.O_NONBLOCK)

    def test_exclusive_open_flags(self):
        opener, _, _, _ = self.open([7], exclusive=True)
        opener.assert_called_once_with("/dev/sda", os.O_RDWR | os.O_NONBLOCK | os.O_EXCL)

    def test_open_failure_dies_with_the_reason(self):
        _, _, code, err = self.open(OSError(errno.EACCES, "Permission denied"))
        self.assertEqual(code, 2)
        self.assertIn("Cannot open /dev/sda: Permission denied. Nothing was sent.", err)

    def test_busy_device_says_the_setup_volume_is_in_use(self):
        _, _, code, err = self.open(OSError(errno.EBUSY, "Device or resource busy"),
                                    exclusive=True)
        self.assertEqual(code, 2)
        self.assertIn("the setup volume is in use or was mounted again", err)
        self.assertIn("Close file windows and try again. Nothing was sent.", err)


class Probe(unittest.TestCase):
    def probe(self, identified=True, status=None):
        err = io.StringIO()
        with mock.patch.object(t5, "open_device", fake_open_device), \
                mock.patch.object(t5, "inquiry_is_t5", return_value=identified), \
                mock.patch.object(t5, "read_log",
                                  return_value=good_status_block() if status is None else status), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                t5.probe("/dev/sda")
                return 0, err.getvalue()
            except SystemExit as e:
                return e.code, err.getvalue()

    def test_good_drive_exits_0(self):
        self.assertEqual(self.probe()[0], 0)

    def test_identity_and_status_problems_exit_3(self):
        self.assertEqual(self.probe(identified=False)[0], 3)
        bad = bytearray(good_status_block())
        bad[0x20:0x24] = b"XXXX"
        self.assertEqual(self.probe(status=bytes(bad))[0], 3)

    def test_unreadable_status_exits_3(self):
        with mock.patch.object(t5, "open_device", fake_open_device), \
                mock.patch.object(t5, "inquiry_is_t5", return_value=True), \
                mock.patch.object(t5, "read_log", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            t5.probe("/dev/sda")
        self.assertEqual(cm.exception.code, 3)


class WaitForUnlocked(unittest.TestCase):
    BIG = 2_000_000_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fs = FakeSysfs(self.tmp.name)
        for patcher in (mock.patch.object(t5, "SYS_BLOCK", self.fs.block),
                        mock.patch.object(t5.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def wait(self, before, run=None):
        run = run or mock.Mock()
        with mock.patch.object(t5.subprocess, "run", run), \
                contextlib.redirect_stdout(io.StringIO()):
            return t5.wait_for_unlocked(before, timeout=1), run

    def test_an_unlocked_disk_that_existed_before_is_not_accepted(self):
        self.fs.add("sdb", pid="61f5", sectors=self.BIG)
        before = {name for name, _ in t5.unlocked_disks()}
        self.assertEqual(before, {"sdb"})
        self.assertFalse(self.wait(before)[0])

    def test_a_new_unlocked_disk_is_accepted(self):
        self.fs.add("sdb", pid="61f5", sectors=self.BIG)
        before = {name for name, _ in t5.unlocked_disks()}
        self.fs.add("sdc", pid="61f5", sectors=self.BIG)
        found, run = self.wait(before)
        self.assertTrue(found)
        self.assertEqual(run.call_args[0][0][-1], "/dev/sdc")

    def test_locked_or_small_disks_are_not_accepted(self):
        self.fs.add("sda")
        self.fs.add("sdb", pid="61f5", sectors=100)
        self.assertFalse(self.wait(set())[0])

    def test_missing_lsblk_is_skipped(self):
        self.fs.add("sda", pid="61f5", sectors=self.BIG)
        found, _ = self.wait(set(), mock.Mock(side_effect=FileNotFoundError("lsblk")))
        self.assertTrue(found)


class Main(unittest.TestCase):
    def run_main(self, argv, root=True, **patches):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(t5.sys, "argv", ["t5-unlock.py", *argv]))
            stack.enter_context(mock.patch.object(t5.os, "geteuid",
                                                  return_value=0 if root else 1000))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(t5, name, value))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(contextlib.redirect_stdout(out))
            try:
                t5.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_version(self):
        code, out, _ = self.run_main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "t5-unlock 0.1.0\n")

    def test_help_lists_the_exit_codes(self):
        code, out, _ = self.run_main(["--help"])
        self.assertEqual(code, 0)
        for text in ("0    success, or the drive is already unlocked",
                     "2    no usable drive, or nothing was sent",
                     "3    transport, identity or unlock problem", "4    wrong password",
                     "5    the unlocked disk did not appear", "64   usage error",
                     "77   not run as root", "130  interrupted"):
            self.assertIn(text, out)

    def test_non_root_is_refused_before_looking_for_a_drive(self):
        finder = mock.Mock()
        code, _, err = self.run_main(["--unlock"], root=False, find_locked=finder)
        self.assertEqual(code, 77)
        self.assertIn("Run as root", err)
        finder.assert_not_called()

    def test_ctrl_c_aborts_with_130_and_no_traceback(self):
        code, _, err = self.run_main(["--unlock"], find_locked=mock.Mock(return_value="/dev/sda"),
                                     unlock=mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(code, 130)
        self.assertIn("Aborted.", err)
        self.assertNotIn("Traceback", err)

    def test_end_of_input_aborts_with_130_and_no_traceback(self):
        code, _, err = self.run_main(["--unlock"], find_locked=mock.Mock(return_value="/dev/sda"),
                                     unlock=mock.Mock(side_effect=EOFError))
        self.assertEqual(code, 130)
        self.assertIn("Aborted.", err)
        self.assertNotIn("Traceback", err)

    def test_ctrl_c_and_eof_at_the_password_prompt(self):
        for exc in (KeyboardInterrupt, EOFError):
            with self.subTest(exc=exc.__name__), \
                    mock.patch.object(t5.getpass, "getpass", side_effect=exc), \
                    mock.patch.object(t5.sys, "stdin", FakeTty()):
                sequence = mock.Mock()
                code, _, err = self.run_main(
                    ["--unlock"], find_locked=mock.Mock(return_value="/dev/sda"),
                    preflight=mock.Mock(), unlock_sequence=sequence)
                self.assertEqual(code, 130)
                self.assertNotIn("Traceback", err)
                sequence.assert_not_called()

    def test_usage_errors_exit_64_with_usage_on_stderr(self):
        for argv in ([], ["--probe", "--unlock"], ["--bogus"], ["--unlock", "extra"]):
            with self.subTest(argv=argv):
                finder = mock.Mock()
                code, out, err = self.run_main(argv, find_locked=finder)
                self.assertEqual(code, 64)
                self.assertEqual(out, "")
                self.assertIn("usage:", err)
                self.assertIn("error:", err)
                finder.assert_not_called()

    def test_usage_error_comes_before_the_root_check(self):
        code, _, _ = self.run_main(["--bogus"], root=False)
        self.assertEqual(code, 64)

    def test_exit_code_constants_match_the_documented_scheme(self):
        self.assertEqual(
            (t5.EXIT_NOTHING_SENT, t5.EXIT_PROBLEM, t5.EXIT_WRONG_PASSWORD, t5.EXIT_NO_DISK,
             t5.EXIT_USAGE, t5.EXIT_NOT_ROOT, t5.EXIT_INTERRUPTED),
            (2, 3, 4, 5, 64, 77, 130))

    def test_probe_and_unlock_are_dispatched(self):
        probe, unlock = mock.Mock(), mock.Mock()
        self.run_main(["--probe"], find_locked=mock.Mock(return_value="/dev/sda"),
                      probe=probe, unlock=unlock)
        probe.assert_called_once_with("/dev/sda")
        unlock.assert_not_called()
        probe.reset_mock()
        self.run_main(["--unlock"], find_locked=mock.Mock(return_value="/dev/sda"),
                      probe=probe, unlock=unlock)
        unlock.assert_called_once_with("/dev/sda")
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=1)
