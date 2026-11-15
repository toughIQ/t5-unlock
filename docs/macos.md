# 🍎 macOS notes

Verified on macOS 27.2, Apple Silicon: the current version probed, unlocked and mounted
the drive on 2026-11-15, and the drive came back to macOS by itself after the probe. See
the Status table in the README. Intel Macs and other macOS versions are untested.

## ✅ Requirements

- ✅ Homebrew and the Xcode Command Line Tools (`xcode-select --install`)
- ✅ `brew install libusb`
- ✅ A **USB 3 (SuperSpeed) port and cable.** The tool needs the drive's UAS mode and
  refuses a slower link.
- ✅ `sudo`. The tool needs root to take over the USB interface. No kernel extension,
  no background service and no code-signing identity are involved.

## 🧑‍💻 Build and run

```sh
cd macos
make selftest                    # offline checks, no drive, no root
sudo ./t5-unlock-mac --probe     # read-only, no password
sudo -p "Mac login password: " ./t5-unlock-mac --unlock    # one password attempt
```

`make static` builds `t5-unlock-mac-static`, which has libusb linked in and does not
need Homebrew at run time.

> 📌 **Note:** A copy of the binary moved to another Mac is ad-hoc signed only. If it
> was downloaded, macOS may quarantine it. Clear that yourself if you trust the build,
> or build it on the target Mac.

## 🔑 Which password is which

You are asked for two different passwords, in this order:

1️⃣ **Your Mac login password**, asked by `sudo` as soon as you run the command. This
is the password of the administrator account you are logged in with. macOS has no
separate root password by default. The tool needs these rights to take over the USB
connection. It is the only thing `sudo` is used for.

2️⃣ **The T5 password, twice**, asked by the tool itself (`T5 DRIVE password (not your
Mac password):` and `Repeat the T5 DRIVE password:`). This is the password you set on the drive. The second entry only
guards against typos: if the two entries differ, nothing is sent, so a typo does not
use up an attempt.

Nothing is shown while you type either one. The T5 password is read directly from the
terminal, is never written to disk or printed, and the tool wipes its own buffers after
use (best effort, as with any program).

If you are not an administrator on the Mac, `sudo` will refuse. In that case ask an
administrator, or use another computer.

## 📋 What a normal run looks like

```text
Mac login password:                          <- sudo: your Mac login password
Found the locked T5 (setup disk /dev/disk4).
T5 DRIVE password (not your Mac password):   <- the drive's password
Repeat the T5 DRIVE password:                <- the drive's password again
Unmounting setup disk disk4.
Password accepted by the drive.
Unlock sent, then relink sent (a disconnect here is expected).
Relink gave no clean reply (normal while the drive re-enumerates).
Waiting for the drive to come back as the full-capacity disk ...
Unlocked T5 appeared: /dev/disk4, 931.5 GiB.
The volume is mounted.
```

## 🩺 Troubleshooting

Every message that ends in **"Nothing was sent"** or **"Nothing was asked or sent"**
means the drive did not receive any password data.

- **"No Samsung Portable SSD T5 found"**: plug it in and wait until the `T5_SETUP`
  volume appears.
- **"More than one T5 found"**: connect only one T5 while you use the tool.
- **"Expected exactly one locked T5 on USB"** or **"A T5 was found but it is not in the
  verified locked state"**: the drive is not visible in its locked state. Unplug it, wait
  a few seconds, plug it back in, and wait for `T5_SETUP`.
- **"The T5 is already unlocked"**: nothing to do. If the message adds a
  `diskutil mountDisk` command, run it to mount the volume.
- **"The T5 is not on a SuperSpeed link"**: use a USB-C port directly on the Mac and the
  original cable. Avoid hubs and adapters.
- **"The drive may have dropped off the USB connection"**: after a probe or a failed
  attempt the drive sometimes disconnects itself. Unplug it and plug it back in. After a
  probe the tool waits up to 15 seconds for the drive to return (on the tested drive it
  came back by itself); a slow return can still show this hint.
- **"Wrong password: the drive rejected it"**: the tool stops and sends no unlock
  command. Do not keep trying. The drive's behavior after several wrong passwords is
  unknown.
- **"Password validation failed at transport level"**: the drive gave no clear answer,
  so nothing else was sent. Do not retry blindly. Unplug and replug the drive, then try
  once more only if you are sure of the password.
- **"Unlock did not report success"**: no relink was sent, so the drive stays locked.
  Unplug and replug it before trying again.
- **"The unlocked disk did not show up"**: the drive may still be unlocked. Check Finder or
  Disk Utility before you unplug it, because unplugging locks it again.
- **"Cannot claim the USB interface"** or another message starting with **"Cannot"**: the
  tool could not take over the USB connection. No password was sent. Unplug the drive,
  plug it into a USB-C port directly on the Mac, and try again.
- **"Status check failed"** or **"INQUIRY did not identify a Samsung Portable SSD T5"**:
  the drive answered, but not like the verified drive. No password was sent. Please open a
  [verification report](https://github.com/toughIQ/t5-unlock/issues/new/choose).
- **"Password must be 1-32 bytes"**: the drive password contains a space, an umlaut or
  another character outside plain ASCII (Cyrillic letters are accepted), or is longer
  than 32 characters. Nothing was
  sent.
- **"The drive is unlocked, but no mounted volume was found"**: look in Finder first, an
  APFS volume can mount under another disk name. Otherwise click Allow if macOS asks to
  allow the accessory, or run the `diskutil mountDisk` command the tool prints.
- **"Unlock sent. The relink was not sent"**: usually after Ctrl-C at that moment. The
  full disk will not appear by itself. Unplug and replug the drive (this locks it again)
  and unlock once more.
- **"Interrupted"**: you pressed Ctrl-C. The message says whether anything was sent.
- **Plain bulk-only mode timed out in testing.** The tool uses UAS only.

## 🔢 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, or the drive is already unlocked |
| 2 | No usable drive, or nothing was sent |
| 3 | Transport, identity or unlock problem |
| 4 | Wrong password |
| 5 | The unlocked disk did not appear |
| 64 | Usage error |
| 77 | Not run as root |
| 130 | Interrupted (Ctrl-C) |

`--selftest` returns 1 if an offline check fails.

The Linux script uses the same codes.

## 📦 Installing (optional)

Run `make` first as your normal user, then `sudo make install`. It copies
`t5-unlock-mac` to `/usr/local/bin`, so you can run `sudo t5-unlock-mac --unlock` from
anywhere. Building first matters because Homebrew's files should not be used as root. `sudo make uninstall` removes it.
`make install PREFIX=/some/path` chooses another location.

## ❓ Why not just run the Linux script on a Mac?

macOS does not give user programs raw SCSI access to a USB disk, so the Linux
`SG_IO` approach has no equivalent here. The macOS tool talks to the drive over USB
directly instead. If you would rather not run it, the [Linux route](linux.md) works
from a virtual machine.
