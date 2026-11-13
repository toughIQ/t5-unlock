# 🔧 How the unlock works

This describes what both tools send. It was observed from the drive's USB behavior
and documented by the upstream project credited in the README. It contains no vendor
code, firmware or keys.

## 🔐 Locked and unlocked states

A locked T5 shows up on USB as `04e8:61f6` and exposes only a small read-only setup
disk (the `T5_SETUP` volume). After a successful unlock and a "relink" command it
re-enumerates as `04e8:61f5` and exposes the full-capacity disk. The drive stays
unlocked only while it has power. Unplugging it locks it again.

## 🔌 Transport

The drive is a USB mass-storage device. The tools send ordinary SCSI commands: the
vendor commands travel inside **ATA PASS-THROUGH (16)** carrying **SMART READ LOG**
and **SMART WRITE LOG** for vendor log pages, one 512-byte block each.

- 🐧 Linux lets root send this through the `SG_IO` ioctl on `/dev/sdX`.
- 🍎 macOS does not let user programs send raw SCSI to a USB disk. The macOS tool
  therefore takes the USB interface over with libusb (as root) and speaks **USB
  Attached SCSI (UAS)** itself, using USB bulk streams. The drive's simpler
  bulk-only mode did not answer on the tested unit; UAS did.

## 📋 The sequence

| Step | Command | Log | Meaning |
|---|---|---|---|
| 1️⃣ | SMART READ LOG | `0xC8` | Status block. Must contain `PSSD` at offset 0x20, protocol version `0x00010103` (little endian at 0x24), and bit 0 of the byte at 0x28 clear. |
| 2️⃣ | SMART READ LOG | `0xD7` | Challenge block from the drive |
| 3️⃣ | SMART WRITE LOG | `0xD6` | Validate the password. No unlock yet. |
| 4️⃣ | | | Unmount the setup volume |
| 5️⃣ | SMART WRITE LOG | `0xC6` | Unlock, using a fresh challenge and a recomputed hash |
| 6️⃣ | SMART WRITE LOG | `0xCA` | Relink (all-zero block). The drive re-enumerates as the full disk. |

The tools stop at the first problem. If validation is rejected, no unlock command is
sent. There is no loop and no retry anywhere.

## 🔑 The hash

The 32-byte payload sits at offset 0x20 of an otherwise all-zero 512-byte block:

```
first   = SHA256( password zero-padded to 32 bytes  ||  challenge[0x30:0x40] )
payload = SHA256( first || challenge[0x00:0x20] )
```

Passwords are 1 to 32 bytes: printable ASCII without spaces, or Cyrillic letters
as UTF-8 (experimental). The tools refuse anything else instead of guessing a
conversion.

## 🧑‍💻 Command bytes

```
read : 85 08 2e 00 d5 00 01 00 <log> 00 4f 00 c2 00 b0 00
write: 85 0a 26 00 d6 00 01 00 <log> 00 4f 00 c2 00 b0 00
```

## 📡 Replies

CK_COND is set, so the drive answers with descriptor-format sense data.

- ✅ Success: sense response code `0x72`, key 0 or 1, ASC/ASCQ `00/1D`, and one ATA
  return descriptor (type 9) with error 0 and no BSY, DF, DRQ or ERR bits.
- 🚫 Wrong password (seen on the verified drive): key `0x0B` (aborted command),
  ASC/ASCQ `00/00`, ATA error `0x04`, status `0x51`.
- 📌 The relink command may answer with plain SCSI GOOD, or the drive may disconnect
  during it. Both are expected and are not treated as errors.

## 🛡️ Safety checks before any password is sent

- ✅ Exactly one drive with the locked ID, and its setup disk is small.
- ✅ INQUIRY says `Samsung Portable SSD T5`.
- ✅ The status block has the expected signature, protocol version and hash scheme.
- ✅ A readable, non-empty challenge.

If any check fails, nothing is sent and the tool explains why.
