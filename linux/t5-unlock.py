#!/usr/bin/env python3
"""Unlock a password-protected Samsung Portable SSD T5 on Linux.

Stdlib only. Talks to the drive with SCSI ATA PASS-THROUGH (16) over SG_IO,
the same SMART READ/WRITE LOG sequence that
github.com/TurboKach/samsung-t5-ssd-unlock-apple-silicon sends over raw UAS:

  read  0xC8  status      ("PSSD", protocol version 0x00010103)
  read  0xD7  challenge
  write 0xD6  validate    (password check, no unlock yet)
  read  0xD7  challenge   (fresh)
  write 0xC6  unlock
  write 0xCA  relink      (drive re-enumerates as the full-capacity disk)

payload = SHA256(SHA256(password padded to 32 || chal[0x30:0x40]) || chal[0x00:0x20])
placed at offset 0x20 of an otherwise zero 512-byte block.

Usage (as root):
  t5-unlock.py --probe     read-only: INQUIRY + status block, no password
  t5-unlock.py --unlock    one password attempt, never retries
"""

import argparse
import ctypes
import fcntl
import getpass
import glob
import hashlib
import os
import pathlib
import subprocess
import sys
import time

SG_IO = 0x2285
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3
DRIVER_SENSE = 0x08

SAMSUNG_VID = "04e8"
T5_LOCKED_PID = "61f6"
T5_UNLOCKED_PID = "61f5"
SYS_BLOCK = "/sys/block"
SETUP_MAX_BYTES = 1 << 30  # the setup disk is tens of MB; anything big is not it

BLOCK = 512
STATUS_LOG, CHALLENGE_LOG = 0xC8, 0xD7
VALIDATE_LOG, UNLOCK_LOG, RELINK_LOG = 0xD6, 0xC6, 0xCA
VERIFIED_VERSION = 0x00010103
PASSWORD_MAX = 32


class SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


class Reply:
    def __init__(self, ok_transport, scsi_status, sense, data, resid):
        self.ok_transport = ok_transport
        self.scsi_status = scsi_status
        self.sense = sense
        self.data = data
        self.resid = resid


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def sg_io(fd, cdb, direction, buf):
    cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(bytes(cdb))
    sense_buf = (ctypes.c_ubyte * 64)()
    data_buf = (ctypes.c_ubyte * len(buf)).from_buffer(buf)
    hdr = SgIoHdr()
    hdr.interface_id = ord("S")
    hdr.dxfer_direction = direction
    hdr.cmd_len = len(cdb)
    hdr.mx_sb_len = len(sense_buf)
    hdr.dxfer_len = len(buf)
    hdr.dxferp = ctypes.addressof(data_buf)
    hdr.cmdp = ctypes.addressof(cdb_buf)
    hdr.sbp = ctypes.addressof(sense_buf)
    hdr.timeout = 10000
    try:
        fcntl.ioctl(fd, SG_IO, hdr)
    except OSError as e:
        print(f"  SG_IO ioctl failed: {e}", file=sys.stderr)
        return Reply(False, None, b"", bytes(buf), None)
    sense = bytes(sense_buf[: hdr.sb_len_wr])
    ok = hdr.host_status == 0 and (hdr.driver_status & ~DRIVER_SENSE) == 0
    if not ok:
        print(f"  transport error: host_status={hdr.host_status:#x} "
              f"driver_status={hdr.driver_status:#x}", file=sys.stderr)
    return Reply(ok, hdr.status, sense, bytes(buf), hdr.resid)


def ata_cdb(log, write):
    # ATA PASS-THROUGH(16), SMART READ LOG (0xD5) / WRITE LOG (0xD6), 1 sector,
    # CK_COND set so the drive returns the ATA status registers.
    return [0x85, 0x0A if write else 0x08, 0x26 if write else 0x2E, 0x00,
            0xD6 if write else 0xD5, 0x00, 0x01, 0x00,
            log, 0x00, 0x4F, 0x00, 0xC2, 0x00, 0xB0, 0x00]


def ata_sense_ok(s):
    """Port of t5_bot_ata_sense_ok: descriptor sense, ASC/ASCQ 00/1D,
    one ATA return descriptor with error=0 and no BSY/DF/DRQ/ERR."""
    if len(s) < 8 or (s[0] & 0x7F) != 0x72 or (s[1] & 0x0F) > 1 or s[2] != 0 or s[3] != 0x1D:
        return False
    end = 8 + s[7]
    if end > len(s):
        return False
    found = False
    i = 8
    while i < end:
        if end - i < 2:
            return False
        size = 2 + s[i + 1]
        if size > end - i:
            return False
        if s[i] == 9:
            if found or size != 14 or s[i + 3] != 0 or (s[i + 13] & 0xA9):
                return False
            found = True
        i += size
    return found


def validation_rejected(s):
    """Exact wrong-password reply seen on the verified T5:
    ABORTED COMMAND, ATA error=ABRT (0x04), status=0x51."""
    return (len(s) == 22 and s[0] == 0x72 and s[1] == 0x0B and s[2] == 0 and s[3] == 0
            and s[7] == 14 and s[8] == 9 and s[9] == 12 and s[11] == 4 and s[21] == 0x51)


def describe_sense(s):
    if len(s) >= 4 and (s[0] & 0x7F) in (0x72, 0x73):
        txt = f"sense key={s[1] & 0xF:#x} ASC={s[2]:#04x} ASCQ={s[3]:#04x}"
        if len(s) >= 22 and s[8] == 9 and s[9] == 12:
            txt += f", ATA error={s[11]:#04x} status={s[21]:#04x}"
        return txt
    return f"sense={s.hex()}" if s else "no sense data"


def read_log(fd, log):
    buf = bytearray(BLOCK)
    r = sg_io(fd, ata_cdb(log, False), SG_DXFER_FROM_DEV, buf)
    if not r.ok_transport or r.resid:
        return None
    if r.scsi_status == 0 or (r.scsi_status == 2 and ata_sense_ok(r.sense)):
        return r.data
    print(f"  read log {log:#04x}: SCSI status={r.scsi_status:#x}, {describe_sense(r.sense)}",
          file=sys.stderr)
    return None


def write_log(fd, log, block):
    """Returns 'ok', 'rejected' or 'error'. Writes require explicit ATA success."""
    nonzero = [i for i, b in enumerate(block) if b]
    if log == RELINK_LOG:
        assert not nonzero, "relink block must be all zero"
    else:
        assert all(32 <= i < 64 for i in nonzero), "payload must sit at 0x20..0x3F only"
    buf = bytearray(block)
    r = sg_io(fd, ata_cdb(log, True), SG_DXFER_TO_DEV, buf)
    for i in range(len(buf)):
        buf[i] = 0
    if r.ok_transport and r.scsi_status == 2 and ata_sense_ok(r.sense):
        return "ok"
    if r.ok_transport and r.scsi_status == 2 and validation_rejected(r.sense):
        return "rejected"
    print(f"  write log {log:#04x}: SCSI status={r.scsi_status}, {describe_sense(r.sense)}",
          file=sys.stderr)
    return "error"


def inquiry_is_t5(fd):
    buf = bytearray(36)
    r = sg_io(fd, [0x12, 0, 0, 0, 36, 0], SG_DXFER_FROM_DEV, buf)
    d = r.data
    return (r.ok_transport and r.scsi_status == 0 and d[0] == 0
            and d[8:16] == b"Samsung " and d[16:32] == b"Portable SSD T5 ")


def check_status(block):
    if block is None:
        return "could not read the status block"
    if block[0x20:0x24] != b"PSSD":
        return "status block has no PSSD signature"
    version = int.from_bytes(block[0x24:0x28], "little")
    if version != VERIFIED_VERSION:
        return (f"protocol version {version:#010x} differs "
                f"from the verified {VERIFIED_VERSION:#010x}")
    if block[0x28] & 1:
        return "drive reports a different hash scheme (flag at 0x28)"
    return None


def check_password(pw):
    if not 1 <= len(pw) <= PASSWORD_MAX:
        return False
    i = 0
    while i < len(pw):
        c = pw[i]
        if 0x21 <= c <= 0x7E:
            i += 1
            continue
        if 0xD0 <= c <= 0xD3 and i + 1 < len(pw) and 0x80 <= pw[i + 1] <= 0xBF:
            i += 2
            continue
        return False
    return True


def make_payload(pw, challenge):
    if not any(challenge[0:32]) and not any(challenge[0x30:0x40]):
        return None
    first = hashlib.sha256(pw.ljust(32, b"\0") + challenge[0x30:0x40]).digest()
    final = hashlib.sha256(first + challenge[0:32]).digest()
    block = bytearray(BLOCK)
    block[0x20:0x40] = final
    return block


def usb_ids(sysdev):
    path = os.path.realpath(sysdev)
    while path != "/":
        vid, pid = os.path.join(path, "idVendor"), os.path.join(path, "idProduct")
        if os.path.exists(vid) and os.path.exists(pid):
            return pathlib.Path(vid).read_text().strip(), pathlib.Path(pid).read_text().strip()
        path = os.path.dirname(path)
    return None, None


def t5_disks():
    found = []
    for blk in sorted(glob.glob(f"{SYS_BLOCK}/sd*")):
        try:
            vendor = pathlib.Path(f"{blk}/device/vendor").read_text().strip()
            model = pathlib.Path(f"{blk}/device/model").read_text().strip()
            size = int(pathlib.Path(f"{blk}/size").read_text()) * 512
        except OSError:
            continue
        if vendor == "Samsung" and model == "Portable SSD T5":
            vid, pid = usb_ids(f"{blk}/device")
            found.append((os.path.basename(blk), vid, pid, size))
    return found


def find_locked():
    disks = t5_disks()
    locked = [d for d in disks if d[1] == SAMSUNG_VID and d[2] == T5_LOCKED_PID]
    for name, vid, pid, size in disks:
        print(f"Found /dev/{name}: USB {vid}:{pid}, {size} bytes")
    if len(locked) != 1:
        die("Expected exactly one locked T5 (USB 04e8:61f6). Nothing was sent.")
    name, _, _, size = locked[0]
    if size > SETUP_MAX_BYTES:
        die(f"/dev/{name} is {size} bytes, too big for a setup disk. Nothing was sent.")
    return f"/dev/{name}"


def unmount_all(dev):
    with open("/proc/mounts") as mounts:
        mounted = [line.split()[0] for line in mounts if line.startswith(dev)]
    for part in mounted:
        print(f"Unmounting {part}")
        if subprocess.run(["umount", part]).returncode != 0:
            die(f"Could not unmount {part}. Nothing was sent.")


def wait_for_unlocked(timeout=60):
    print("Waiting for the drive to come back as the full-capacity disk ...")
    for _ in range(timeout * 2):
        for name, vid, pid, size in t5_disks():
            if vid == SAMSUNG_VID and pid == T5_UNLOCKED_PID and size > SETUP_MAX_BYTES:
                print(f"Unlocked T5 appeared: /dev/{name}, {size / 2**30:.1f} GiB")
                subprocess.run(["lsblk", "-o", "NAME,SIZE,FSTYPE,LABEL", f"/dev/{name}"])
                return True
        time.sleep(0.5)
    return False


def probe(dev):
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        if not inquiry_is_t5(fd):
            die("INQUIRY did not identify a Samsung Portable SSD T5.")
        print("INQUIRY: Samsung Portable SSD T5")
        status = read_log(fd, STATUS_LOG)
        problem = check_status(status)
        if problem:
            die(f"Status check failed: {problem}.")
        print(f"Status OK: PSSD, protocol version {VERIFIED_VERSION:#010x}, hash scheme 0")
        print("Probe passed. The drive speaks the verified protocol. No password was sent.")
    finally:
        os.close(fd)


def unlock_sequence(fd, pw):
    """The whole drive conversation, with no prompts, unmounting or waiting.

    Returns (outcome, detail). It never loops and never retries: every command is
    sent at most once, and nothing is sent after the first problem.
    """
    if not inquiry_is_t5(fd):
        return "not_t5", ""
    problem = check_status(read_log(fd, STATUS_LOG))
    if problem:
        return "bad_status", problem

    challenge = read_log(fd, CHALLENGE_LOG)
    payload = make_payload(pw, challenge) if challenge else None
    if payload is None:
        return "no_challenge", ""
    result = write_log(fd, VALIDATE_LOG, payload)
    if result == "rejected":
        return "rejected", ""
    if result != "ok":
        return "validation_unclear", ""

    challenge = read_log(fd, CHALLENGE_LOG)
    payload = make_payload(pw, challenge) if challenge else None
    if payload is None:
        return "no_fresh_challenge", ""
    if write_log(fd, UNLOCK_LOG, payload) != "ok":
        return "unlock_error", ""
    write_log(fd, RELINK_LOG, bytearray(BLOCK))
    return "unlocked", ""


def unlock(dev):
    pw = getpass.getpass("T5 password: ").encode("utf-8")
    again = getpass.getpass("Repeat password: ").encode("utf-8")
    if pw != again:
        die("Passwords do not match. Nothing was sent.")
    del again
    if not check_password(pw):
        die("Password must be 1-32 bytes of printable ASCII (no spaces) or Cyrillic. "
            "Nothing was sent.")

    unmount_all(dev)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        outcome, detail = unlock_sequence(fd, pw)
    finally:
        os.close(fd)
        del pw

    if outcome == "not_t5":
        die("INQUIRY did not identify a Samsung Portable SSD T5. Nothing was sent.")
    if outcome == "bad_status":
        die(f"Status check failed: {detail}. No password was sent.")
    if outcome == "no_challenge":
        die("Could not read a usable challenge. No password was sent.")
    if outcome == "rejected":
        die("Wrong password: the drive rejected it. No unlock command was sent.", 2)
    if outcome == "validation_unclear":
        die("Password validation failed at transport level (outcome unclear). "
            "No unlock command was sent. Do not retry blindly.")
    if outcome == "no_fresh_challenge":
        die("Could not read a fresh challenge for the unlock step. No unlock command was sent.")
    if outcome == "unlock_error":
        print("Unlock returned an error; checking for the data disk anyway, no relink sent.",
              file=sys.stderr)
    else:
        print("Password accepted by the drive.")
        print("Unlock sent, then relink sent (a disconnect here is expected).")

    if not wait_for_unlocked():
        die("The unlocked disk did not show up within 60 s. Replug the drive and check lsblk; "
            "it stays unlocked only while powered.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="read-only check, no password")
    mode.add_argument("--unlock", action="store_true", help="one unlock attempt")
    args = ap.parse_args()
    if os.geteuid() != 0:
        die("Run as root (sudo).")
    dev = find_locked()
    probe(dev) if args.probe else unlock(dev)


if __name__ == "__main__":
    main()
