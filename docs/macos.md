# macOS notes

Verified on macOS 27.2, Apple Silicon. Intel Macs and other macOS versions are
untested.

## Requirements

- Homebrew and the Xcode Command Line Tools (`xcode-select --install`)
- `brew install libusb`
- A **USB 3 (SuperSpeed) port and cable.** The tool needs the drive's UAS mode and
  refuses a slower link.
- `sudo`. The tool needs root to take over the USB interface. No kernel extension,
  no background service and no code-signing identity are involved.

## Build and run

```sh
cd macos
make selftest                    # offline checks, no drive, no root
sudo ./t5-unlock-mac --probe     # read-only, no password
sudo ./t5-unlock-mac --unlock    # one password attempt
```

`make static` builds `t5-unlock-mac-static`, which has libusb linked in and does not
need Homebrew at run time. A copy of the binary moved to another Mac is ad-hoc signed
only. If it was downloaded, macOS may quarantine it; clear that yourself if you trust
the build, or build it on the target Mac.

## What a normal run looks like

```
Found the locked T5 (setup disk /dev/disk4).
T5 password:
Repeat password:
Unmounting setup disk disk4.
Password accepted by the drive.
Unlock sent. Sending relink (a disconnect here is expected).
Relink gave no clean reply (normal while the drive re-enumerates).
Waiting for the drive to come back as the full-capacity disk ...
Unlocked T5 appeared: /dev/disk4, 931.5 GiB.
Volume(s) mounted successfully
```

## Troubleshooting

- **"Expected exactly one locked T5"**: the drive is not in the locked state or not
  visible. Unplug it, wait a few seconds, plug it in, and wait for `T5_SETUP`.
- **The drive vanished after `--probe`**: expected. Claiming the USB interface
  without unlocking leaves the drive off the USB list until it is replugged.
- **"Not on a SuperSpeed link"**: use a USB 3 port and the original USB-C cable.
  Avoid hubs and adapters while testing.
- **"Cannot claim the USB interface" or access errors**: run with `sudo`.
- **Wrong password**: the tool stops after the drive rejects it and sends no unlock
  command. Do not keep trying; the drive's lockout behavior is unknown.
- **macOS asks whether to allow the accessory**: click Allow. The volume then mounts.
- **Plain bulk-only mode timed out in testing.** The tool uses UAS only.

## Why not just run the Linux script on a Mac?

macOS does not give user programs raw SCSI access to a USB disk, so the Linux
`SG_IO` approach has no equivalent here. The macOS tool talks to the drive over USB
directly instead. If you would rather not run it, the [Linux route](linux.md) works
from a virtual machine.
