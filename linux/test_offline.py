#!/usr/bin/env python3
"""Offline checks for t5-unlock.py against the upstream project's test vectors.
No drive needed. Run: python3 test_offline.py"""

import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "t5", os.path.join(os.path.dirname(os.path.abspath(__file__)), "t5-unlock.py"))
t5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t5)


def challenge_fixture(reverse=False):
    return bytes((255 - (i % 256)) if reverse else (i % 256) for i in range(512))


# Hash vectors from upstream Tests/test_protocol.c (challenge = 0..255 repeating).
vectors = {
    "password": "0300faba3e3fb4124e3a8bd285a8c63bb84bb232276a9ae9984a325009ca381b",
    "A" * 32: "026e0a13661efa6527e9ac9281d7e0135c4f6178e5f6949ea55590e653d7e30f",
    "!": "a31b551259b9c81c5e81281fb26138f92bcd660f5b77d2e3ed1a11bcef6c64ed",
    "Correct-Horse_42": "f8fff8c890e90098cc70cb563e44c258e477d2ee35f28b47e39793b2bbb43ebe",
    "Тест-Only42": "18e24d41263f1f54d9022f0aaf125adc0037a657b1392fb55451a8b8270bf35e",
    "Я" * 16: "5141c096f8bc046ed8a6137b0eec47ba8c3b3d2b6db28de50c58887f282969a0",
}
for pw, hexval in vectors.items():
    p = pw.encode()
    assert t5.check_password(p), pw
    block = t5.make_payload(p, challenge_fixture())
    assert block[0x20:0x40].hex() == hexval, pw
    assert not any(block[:0x20]) and not any(block[0x40:]), pw

# Unlock step uses the reversed challenge upstream.
assert t5.make_payload(b"password", challenge_fixture(True))[0x20:0x40].hex() == \
    "f835d737a17cc8547c4110f5b5488616ea5d6da6b085ab5ea8428a078b7429db"

# Bytes outside the two challenge fields must not matter.
c = bytearray(challenge_fixture())
for i in list(range(0x20, 0x30)) + list(range(0x40, 512)):
    c[i] ^= 0xFF
assert t5.make_payload(b"password", bytes(c))[0x20:0x40].hex() == vectors["password"]

# Empty challenge is refused.
c = bytearray(512)
c[0x20] = 1
assert t5.make_payload(b"password", bytes(c)) is None

# Password guards.
for bad in [b"", b"a" * 33, b"with space", b"tab\t", "Ä".encode()]:
    assert not t5.check_password(bad), bad

# Status block checks.
status = bytearray(512)
status[0x20:0x24] = b"PSSD"
status[0x24:0x28] = (0x00010103).to_bytes(4, "little")
assert t5.check_status(bytes(status)) is None
s2 = bytearray(status); s2[0x28] = 1
assert t5.check_status(bytes(s2))
s3 = bytearray(status); s3[0x24] = 4
assert t5.check_status(bytes(s3))
assert t5.check_status(None)

# CDBs match upstream (read from t5_bot_readonly.c, write per t5_uas_commands.c).
assert bytes(t5.ata_cdb(0xC8, False)).hex() == "85082e00d5000100c8004f00c200b000"
assert bytes(t5.ata_cdb(0xD6, True)).hex() == "850a2600d6000100d6004f00c200b000"

# Sense parsing: ATA success descriptor vs the exact wrong-password reply.
ok_sense = bytes([0x72, 0x01, 0x00, 0x1D, 0, 0, 0, 14,
                  0x09, 0x0C, 0x00, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x50])
assert t5.ata_sense_ok(ok_sense)
rejected = bytes([0x72, 0x0B, 0x00, 0x00, 0, 0, 0, 14,
                  0x09, 0x0C, 0x00, 0x04, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x51])
assert t5.validation_rejected(rejected)
assert not t5.ata_sense_ok(rejected)
assert not t5.validation_rejected(ok_sense)
err = bytearray(ok_sense); err[21] = 0x51
assert not t5.ata_sense_ok(bytes(err))

# SG_IO header layout on 64-bit Linux is 88 bytes.
import ctypes
assert ctypes.sizeof(t5.SgIoHdr) == 88, ctypes.sizeof(t5.SgIoHdr)

print("PASS: hash vectors, challenge fields, guards, status, CDBs, sense parsing, SG_IO layout")
