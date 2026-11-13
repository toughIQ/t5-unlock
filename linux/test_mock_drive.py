#!/usr/bin/env python3
"""Mock-drive tests: the safety rules the README promises, checked against a fake T5.

No drive, no root. A scripted fake sits behind sg_io and records every command the
script sends. A second group runs the real sg_io, read_log and write_log with a fake
ioctl. Run: python3 linux/test_mock_drive.py
"""

import contextlib
import ctypes
import importlib.util
import io
import os
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "t5", os.path.join(os.path.dirname(os.path.abspath(__file__)), "t5-unlock.py"))
t5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t5)

PASSWORD = b"Correct-Horse_42"

OK_SENSE = bytes([0x72, 0x01, 0x00, 0x1D, 0, 0, 0, 14, 0x09, 0x0C,
                  0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x50])
REJECTED_SENSE = bytes([0x72, 0x0B, 0x00, 0x00, 0, 0, 0, 14, 0x09, 0x0C,
                        0, 0x04, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x51])
GARBAGE_SENSE = bytes([0x70, 0x05, 0, 0, 0, 0, 0, 10] + [0] * 10)


def good_status():
    block = bytearray(512)
    block[0x20:0x24] = b"PSSD"
    block[0x24:0x28] = (0x00010103).to_bytes(4, "little")
    return bytes(block)


def good_challenge(n=0):
    return bytes((i * 7 + 3 + n * 13) % 256 for i in range(512))


class FakeDrive:
    """Answers like the verified T5, with one configurable misbehaviour.

    Every challenge read returns a different challenge: challenges[0] is the first one,
    challenges[1] the fresh one, and so on. read_fail is a set of (log, n) pairs: the
    n-th read (1-based) of that log fails at transport level.
    """

    def __init__(self, validate="ok", unlock="ok", relink="ok", status=None,
                 challenge=None, inquiry=b"Samsung Portable SSD T5 ", read_fail=()):
        self.validate, self.unlock, self.relink = validate, unlock, relink
        self.status = good_status() if status is None else status
        self.fixed_challenge = challenge
        self.inquiry = inquiry
        self.read_fail = set(read_fail)
        self.reads = {}
        self.challenges = []  # every challenge handed out, in order
        self.commands = []  # ("inquiry",) / ("read", log) / ("write", log)
        self.written = []   # payload bytes of every write, copied before the script wipes them

    def writes(self):
        return [c[1] for c in self.commands if c[0] == "write"]

    def sg_io(self, _fd, cdb, direction, buf, quiet=False):
        if cdb[0] == 0x12:
            self.commands.append(("inquiry",))
            data = bytearray(36)
            data[8:32] = self.inquiry.ljust(24)[:24]
            return t5.Reply(True, 0, b"", bytes(data), 0)
        log, is_write = cdb[8], cdb[1] == 0x0A
        if not is_write:
            self.commands.append(("read", log))
            n = self.reads[log] = self.reads.get(log, 0) + 1
            if (log, n) in self.read_fail:
                return t5.Reply(False, None, b"", bytes(512), None)
            if log == t5.STATUS_LOG:
                block = self.status
            elif self.fixed_challenge is not None:
                block = self.fixed_challenge
            else:
                block = good_challenge(len(self.challenges))
            if log == t5.CHALLENGE_LOG:
                self.challenges.append(bytes(block))
            return t5.Reply(True, 0, b"", bytes(block), 0)
        self.commands.append(("write", log))
        self.written.append(bytes(buf))
        behaviour = {t5.VALIDATE_LOG: self.validate, t5.UNLOCK_LOG: self.unlock,
                     t5.RELINK_LOG: self.relink}[log]
        if behaviour == "ok":
            return t5.Reply(True, 2, OK_SENSE, bytes(buf), 0)
        if behaviour == "rejected":
            return t5.Reply(True, 2, REJECTED_SENSE, bytes(buf), 0)
        if behaviour == "garbage":
            return t5.Reply(True, 2, GARBAGE_SENSE, bytes(buf), 0)
        if behaviour == "plain_good":  # SCSI GOOD, no sense: not an ATA success
            return t5.Reply(True, 0, b"", bytes(buf), 0)
        return t5.Reply(False, None, b"", bytes(buf), None)  # transport failure


class SafetyRules(unittest.TestCase):
    def run_sequence(self, drive, password=PASSWORD):
        err = io.StringIO()
        with mock.patch.object(t5, "sg_io", drive.sg_io), contextlib.redirect_stderr(err):
            result = t5.unlock_sequence(0, password)
        self.stderr = err.getvalue()
        return result

    def test_happy_path_sends_the_exact_sequence_once(self):
        drive = FakeDrive()
        self.assertEqual(self.run_sequence(drive)[0], "unlocked")
        self.assertEqual(drive.commands, [
            ("inquiry",), ("read", t5.STATUS_LOG), ("read", t5.CHALLENGE_LOG),
            ("write", t5.VALIDATE_LOG), ("read", t5.CHALLENGE_LOG),
            ("write", t5.UNLOCK_LOG), ("write", t5.RELINK_LOG)])
        self.assertEqual(self.stderr, "")

    def test_validate_uses_the_first_challenge_and_unlock_the_fresh_one(self):
        drive = FakeDrive()
        self.run_sequence(drive)
        first, second = drive.challenges
        self.assertNotEqual(first, second)
        self.assertEqual(drive.written[0], bytes(t5.make_payload(PASSWORD, first)))
        self.assertEqual(drive.written[1], bytes(t5.make_payload(PASSWORD, second)))
        self.assertNotEqual(drive.written[0], drive.written[1])

    def test_rejected_password_never_leads_to_unlock_or_relink(self):
        drive = FakeDrive(validate="rejected")
        self.assertEqual(self.run_sequence(drive)[0], "rejected")
        self.assertEqual(drive.writes(), [t5.VALIDATE_LOG])

    def test_exactly_one_validation_attempt_never_a_retry(self):
        for behaviour in ("rejected", "garbage", "transport", "plain_good"):
            with self.subTest(behaviour=behaviour):
                drive = FakeDrive(validate=behaviour)
                self.run_sequence(drive)
                self.assertEqual(drive.writes().count(t5.VALIDATE_LOG), 1)
                self.assertNotIn(t5.UNLOCK_LOG, drive.writes())
                self.assertNotIn(t5.RELINK_LOG, drive.writes())

    def test_unclear_validation_stops_without_unlock(self):
        for behaviour in ("garbage", "transport"):
            with self.subTest(behaviour=behaviour):
                drive = FakeDrive(validate=behaviour)
                self.assertEqual(self.run_sequence(drive)[0], "validation_unclear")
                self.assertEqual(drive.writes(), [t5.VALIDATE_LOG])

    def test_plain_scsi_good_is_not_a_validation_success(self):
        drive = FakeDrive(validate="plain_good")
        self.assertEqual(self.run_sequence(drive)[0], "validation_unclear")
        self.assertEqual(drive.writes(), [t5.VALIDATE_LOG])
        self.assertNotIn(("read", t5.CHALLENGE_LOG), drive.commands[4:])

    def test_unlock_error_sends_no_relink_and_does_not_retry(self):
        for behaviour in ("garbage", "rejected", "transport", "plain_good"):
            with self.subTest(behaviour=behaviour):
                drive = FakeDrive(unlock=behaviour)
                self.assertEqual(self.run_sequence(drive)[0], "unlock_error")
                self.assertEqual(drive.writes(), [t5.VALIDATE_LOG, t5.UNLOCK_LOG])

    def test_relink_failure_is_not_fatal_not_retried_and_silent(self):
        for behaviour in ("transport", "plain_good", "garbage"):
            with self.subTest(behaviour=behaviour):
                drive = FakeDrive(relink=behaviour)
                self.assertEqual(self.run_sequence(drive)[0], "unlocked")
                self.assertEqual(drive.writes().count(t5.RELINK_LOG), 1)
                self.assertEqual(self.stderr, "")

    def test_wrong_drive_receives_only_an_inquiry(self):
        drive = FakeDrive(inquiry=b"Other Vendor Disk      ")
        self.assertEqual(self.run_sequence(drive)[0], "not_t5")
        self.assertEqual(drive.commands, [("inquiry",)])

    def test_unverified_protocol_version_sends_no_password_data(self):
        status = bytearray(good_status())
        status[0x24:0x28] = (0x00020000).to_bytes(4, "little")
        drive = FakeDrive(status=bytes(status))
        outcome, detail = self.run_sequence(drive)
        self.assertEqual(outcome, "bad_status")
        self.assertIn("differs", detail)
        self.assertEqual(drive.writes(), [])
        self.assertNotIn(("read", t5.CHALLENGE_LOG), drive.commands)

    def test_missing_signature_and_other_hash_scheme_are_refused(self):
        no_signature = bytearray(good_status())
        no_signature[0x20:0x24] = b"XXXX"
        other_scheme = bytearray(good_status())
        other_scheme[0x28] = 1
        for status in (bytes(no_signature), bytes(other_scheme)):
            drive = FakeDrive(status=status)
            self.assertEqual(self.run_sequence(drive)[0], "bad_status")
            self.assertEqual(drive.writes(), [])

    def test_empty_challenge_sends_nothing(self):
        drive = FakeDrive(challenge=bytes(512))
        self.assertEqual(self.run_sequence(drive)[0], "no_challenge")
        self.assertEqual(drive.writes(), [])

    def test_status_read_failure_stops_before_any_challenge(self):
        drive = FakeDrive(read_fail={(t5.STATUS_LOG, 1)})
        outcome, detail = self.run_sequence(drive)
        self.assertEqual(outcome, "bad_status")
        self.assertIn("could not read", detail)
        self.assertEqual(drive.commands, [("inquiry",), ("read", t5.STATUS_LOG)])

    def test_first_challenge_read_failure_sends_no_write(self):
        drive = FakeDrive(read_fail={(t5.CHALLENGE_LOG, 1)})
        self.assertEqual(self.run_sequence(drive)[0], "no_challenge")
        self.assertEqual(drive.commands, [
            ("inquiry",), ("read", t5.STATUS_LOG), ("read", t5.CHALLENGE_LOG)])

    def test_fresh_challenge_read_failure_sends_no_unlock(self):
        drive = FakeDrive(read_fail={(t5.CHALLENGE_LOG, 2)})
        self.assertEqual(self.run_sequence(drive)[0], "no_fresh_challenge")
        self.assertEqual(drive.commands, [
            ("inquiry",), ("read", t5.STATUS_LOG), ("read", t5.CHALLENGE_LOG),
            ("write", t5.VALIDATE_LOG), ("read", t5.CHALLENGE_LOG)])

    def test_password_never_appears_in_clear_in_anything_sent(self):
        drive = FakeDrive()
        self.run_sequence(drive)
        for payload in drive.written:
            self.assertNotIn(PASSWORD, payload)
            self.assertEqual(payload[:0x20], bytes(0x20))
            self.assertEqual(payload[0x40:], bytes(len(payload) - 0x40))

    def test_only_the_six_known_commands_are_ever_sent(self):
        allowed = {("inquiry",), ("read", t5.STATUS_LOG), ("read", t5.CHALLENGE_LOG),
                   ("write", t5.VALIDATE_LOG), ("write", t5.UNLOCK_LOG),
                   ("write", t5.RELINK_LOG)}
        for validate in ("ok", "rejected", "garbage"):
            drive = FakeDrive(validate=validate)
            self.run_sequence(drive)
            self.assertTrue(set(drive.commands) <= allowed)

    def test_sequence_has_no_reachable_loop_around_writes(self):
        # Even with a drive that never succeeds, the number of commands is bounded.
        drive = FakeDrive(validate="transport")
        self.run_sequence(drive)
        self.assertLessEqual(len(drive.commands), 7)


class FakeIoctl:
    """Stands in for fcntl.ioctl: records the request and fills the SG_IO header."""

    def __init__(self, status=0, sense=b"", data=b"", resid=0, host_status=0,
                 driver_status=0, error=None):
        self.status, self.sense, self.data, self.resid = status, sense, data, resid
        self.host_status, self.driver_status, self.error = host_status, driver_status, error
        self.calls = []

    def __call__(self, _fd, request, hdr):
        if self.error:
            raise self.error
        self.calls.append({
            "request": request,
            "cdb": ctypes.string_at(hdr.cmdp, hdr.cmd_len),
            "direction": hdr.dxfer_direction,
            "len": hdr.dxfer_len,
            "sent": ctypes.string_at(hdr.dxferp, hdr.dxfer_len),
        })
        if self.data:
            ctypes.memmove(hdr.dxferp, self.data, len(self.data))
        if self.sense:
            ctypes.memmove(hdr.sbp, self.sense, len(self.sense))
        hdr.status = self.status
        hdr.sb_len_wr = len(self.sense)
        hdr.resid = self.resid
        hdr.host_status = self.host_status
        hdr.driver_status = self.driver_status
        return 0


class RealSgIo(unittest.TestCase):
    """The real sg_io, read_log and write_log, with only the ioctl faked."""

    def call(self, func, fake, *args, **kwargs):
        err = io.StringIO()
        with mock.patch.object(t5.fcntl, "ioctl", fake), contextlib.redirect_stderr(err):
            result = func(3, *args, **kwargs)
        return result, err.getvalue()

    def test_read_log_command_bytes_and_direction(self):
        block = bytes(range(256)) * 2
        fake = FakeIoctl(status=0, data=block)
        result, _ = self.call(t5.read_log, fake, t5.STATUS_LOG)
        self.assertEqual(result, block)
        (call,) = fake.calls
        self.assertEqual(call["request"], t5.SG_IO)
        self.assertEqual(call["cdb"], bytes(t5.ata_cdb(t5.STATUS_LOG, False)))
        self.assertEqual(call["cdb"].hex(), "85082e00d5000100c8004f00c200b000")
        self.assertEqual(call["direction"], t5.SG_DXFER_FROM_DEV)
        self.assertEqual(call["len"], 512)

    def test_read_log_accepts_ata_success_status_2(self):
        fake = FakeIoctl(status=2, sense=OK_SENSE, data=b"\x01" * 512, driver_status=0x08)
        result, _ = self.call(t5.read_log, fake, t5.CHALLENGE_LOG)
        self.assertEqual(result, b"\x01" * 512)

    def test_read_log_refuses_status_2_without_ata_success(self):
        for sense in (REJECTED_SENSE, GARBAGE_SENSE, b""):
            fake = FakeIoctl(status=2, sense=sense, data=b"\x01" * 512)
            result, _ = self.call(t5.read_log, fake, t5.STATUS_LOG)
            self.assertIsNone(result)

    def test_read_log_with_residual_count_is_a_failure(self):
        fake = FakeIoctl(status=0, data=b"\x01" * 512, resid=16)
        result, _ = self.call(t5.read_log, fake, t5.STATUS_LOG)
        self.assertIsNone(result)

    def test_read_log_ioctl_error_is_a_failure(self):
        fake = FakeIoctl(error=OSError(5, "Input/output error"))
        result, err = self.call(t5.read_log, fake, t5.STATUS_LOG)
        self.assertIsNone(result)
        self.assertIn("SG_IO ioctl failed", err)

    def test_read_log_transport_errors_are_failures(self):
        for kwargs in ({"host_status": 7}, {"driver_status": 0x06}):
            fake = FakeIoctl(status=0, data=b"\x01" * 512, **kwargs)
            result, _ = self.call(t5.read_log, fake, t5.STATUS_LOG)
            self.assertIsNone(result)

    def test_write_log_command_bytes_direction_and_payload(self):
        block = bytearray(512)
        block[0x20:0x40] = bytes(range(1, 33))
        fake = FakeIoctl(status=2, sense=OK_SENSE, driver_status=0x08)
        result, err = self.call(t5.write_log, fake, t5.VALIDATE_LOG, block)
        self.assertEqual(result, "ok")
        self.assertEqual(err, "")
        (call,) = fake.calls
        self.assertEqual(call["cdb"].hex(), "850a2600d6000100d6004f00c200b000")
        self.assertEqual(call["direction"], t5.SG_DXFER_TO_DEV)
        self.assertEqual(call["sent"], bytes(block))
        self.assertEqual(bytes(block[0x20:0x40]), bytes(range(1, 33)))  # caller copy untouched

    def test_write_log_uses_the_right_log_number_for_each_command(self):
        for log in (t5.VALIDATE_LOG, t5.UNLOCK_LOG, t5.RELINK_LOG):
            fake = FakeIoctl(status=2, sense=OK_SENSE, driver_status=0x08)
            self.call(t5.write_log, fake, log, bytearray(512))
            self.assertEqual(fake.calls[0]["cdb"], bytes(t5.ata_cdb(log, True)))

    def test_write_log_rejected_reply(self):
        fake = FakeIoctl(status=2, sense=REJECTED_SENSE, driver_status=0x08)
        result, _ = self.call(t5.write_log, fake, t5.VALIDATE_LOG, bytearray(512))
        self.assertEqual(result, "rejected")

    def test_write_log_plain_good_is_an_error_not_a_success(self):
        fake = FakeIoctl(status=0)
        for log in (t5.VALIDATE_LOG, t5.UNLOCK_LOG):
            result, err = self.call(t5.write_log, fake, log, bytearray(512))
            self.assertEqual(result, "error")
            self.assertIn("write log", err)

    def test_write_log_ioctl_error_and_transport_error_are_errors(self):
        result, _ = self.call(t5.write_log, FakeIoctl(error=OSError(19, "No such device")),
                              t5.UNLOCK_LOG, bytearray(512))
        self.assertEqual(result, "error")
        result, _ = self.call(t5.write_log, FakeIoctl(status=2, sense=OK_SENSE, host_status=3),
                              t5.UNLOCK_LOG, bytearray(512))
        self.assertEqual(result, "error")

    def test_quiet_relink_prints_nothing_for_every_kind_of_reply(self):
        replies = [FakeIoctl(status=0), FakeIoctl(error=OSError(19, "No such device")),
                   FakeIoctl(status=2, sense=GARBAGE_SENSE, driver_status=0x08),
                   FakeIoctl(status=0, host_status=7)]
        for fake in replies:
            result, err = self.call(t5.write_log, fake, t5.RELINK_LOG, bytearray(512),
                                    quiet=True)
            self.assertEqual(result, "error")
            self.assertEqual(err, "")

    def test_not_quiet_still_reports_errors(self):
        result, err = self.call(t5.write_log, FakeIoctl(status=0), t5.UNLOCK_LOG,
                                bytearray(512))
        self.assertEqual(result, "error")
        self.assertNotEqual(err, "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
