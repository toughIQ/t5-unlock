#!/usr/bin/env python3
"""Mock-drive tests: the safety rules the README promises, checked against a fake T5.

No drive, no root. A scripted fake sits behind sg_io and records every command the
script sends. Run: python3 linux/test_mock_drive.py
"""

import contextlib
import importlib.util
import io
import os
import unittest

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


def good_challenge():
    return bytes((i * 7 + 3) % 256 for i in range(512))


class FakeDrive:
    """Answers like the verified T5, with one configurable misbehaviour."""

    def __init__(self, validate="ok", unlock="ok", relink="ok", status=None,
                 challenge=None, inquiry=b"Samsung Portable SSD T5 "):
        self.validate, self.unlock, self.relink = validate, unlock, relink
        self.status = good_status() if status is None else status
        self.challenge = good_challenge() if challenge is None else challenge
        self.inquiry = inquiry
        self.commands = []  # ("inquiry",) / ("read", log) / ("write", log)
        self.written = []   # payload bytes of every write, copied before the script wipes them

    def writes(self):
        return [c[1] for c in self.commands if c[0] == "write"]

    def sg_io(self, _fd, cdb, direction, buf):
        if cdb[0] == 0x12:
            self.commands.append(("inquiry",))
            data = bytearray(36)
            data[8:32] = self.inquiry.ljust(24)[:24]
            return t5.Reply(True, 0, b"", bytes(data), 0)
        log, is_write = cdb[8], cdb[1] == 0x0A
        if not is_write:
            self.commands.append(("read", log))
            block = self.status if log == t5.STATUS_LOG else self.challenge
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
        return t5.Reply(False, None, b"", bytes(buf), None)  # transport failure


class SafetyRules(unittest.TestCase):
    def run_sequence(self, drive, password=PASSWORD):
        original = t5.sg_io
        t5.sg_io = drive.sg_io
        try:
            with contextlib.redirect_stderr(io.StringIO()):  # the script logs write errors
                return t5.unlock_sequence(0, password)
        finally:
            t5.sg_io = original

    def test_happy_path_sends_the_exact_sequence_once(self):
        drive = FakeDrive()
        self.assertEqual(self.run_sequence(drive)[0], "unlocked")
        self.assertEqual(drive.commands, [
            ("inquiry",), ("read", t5.STATUS_LOG), ("read", t5.CHALLENGE_LOG),
            ("write", t5.VALIDATE_LOG), ("read", t5.CHALLENGE_LOG),
            ("write", t5.UNLOCK_LOG), ("write", t5.RELINK_LOG)])

    def test_rejected_password_never_leads_to_unlock_or_relink(self):
        drive = FakeDrive(validate="rejected")
        self.assertEqual(self.run_sequence(drive)[0], "rejected")
        self.assertEqual(drive.writes(), [t5.VALIDATE_LOG])

    def test_exactly_one_validation_attempt_never_a_retry(self):
        for behaviour in ("rejected", "garbage", "transport"):
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

    def test_unlock_error_sends_no_relink_and_does_not_retry(self):
        drive = FakeDrive(unlock="garbage")
        self.assertEqual(self.run_sequence(drive)[0], "unlock_error")
        self.assertEqual(drive.writes(), [t5.VALIDATE_LOG, t5.UNLOCK_LOG])

    def test_relink_failure_is_not_fatal_and_not_retried(self):
        drive = FakeDrive(relink="transport")
        self.assertEqual(self.run_sequence(drive)[0], "unlocked")
        self.assertEqual(drive.writes().count(t5.RELINK_LOG), 1)

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
