# 🔓 t5-unlock

[![tests](https://github.com/toughIQ/t5-unlock/actions/workflows/ci.yml/badge.svg)](https://github.com/toughIQ/t5-unlock/actions/workflows/ci.yml)
[![codeql](https://github.com/toughIQ/t5-unlock/actions/workflows/codeql.yml/badge.svg)](https://github.com/toughIQ/t5-unlock/actions/workflows/codeql.yml)

Unlock your own password-protected **Samsung Portable SSD T5** on current macOS
(Apple Silicon) and on Linux, now that the vendor's old unlock software no longer runs.

> ⚠️ **Not affiliated with, endorsed by, or sponsored by Samsung.** "Samsung" and
> "Portable SSD T5" are trademarks of their owners. They are used here only to
> say which drive this tool works with.

## 🎯 What this is, and what it is not

- ✅ It performs the same standard password check the vendor's own unlock app performed:
  you give it **your** password, it sends the check, and the drive unlocks if it
  accepts the password.
- ✅ It makes **one attempt per run**. It never retries and never guesses.
- 🚫 It has no brute-force, dictionary or recovery mode. Contributions that add any of
  that will not be accepted.
- 🚫 It does not crack, bypass, reset or recover anything. It cannot read the drive's
  key or your data, and it cannot set, change or remove the password.
- 🚫 It contains no Samsung code, firmware or keys.
- 🛡️ It refuses to talk to any drive it was not verified against (see Status).

> 📌 **Note:** If you forgot the password, this tool cannot help you. If you do not own
> the drive, do not use it.

## 💡 Why this exists

The original T5 shipped with a small read-only setup volume (`T5_SETUP`) holding
Samsung's unlock app. On a current Mac that app no longer works: the 2017 installer
is Intel-only and depends on a kernel extension, which Apple Silicon Macs do not
load by default. A Linux version never existed. To our knowledge there is no updated
official tool for the original T5. If Samsung publishes one, use it instead.

So a locked T5 on a modern computer shows only the tiny setup volume and the data
stays out of reach, even for its legitimate owner. This repo is for those owners.

## 📋 Status

Verified on **one** setup. Treat everything else as unverified.

| Part | Verified on |
|---|---|
| ✅ Drive | One Samsung Portable SSD T5, USB `04e8:61f6` when locked and `04e8:61f5` when unlocked, protocol version `0x00010103`, hash scheme 0 |
| ✅ macOS tool | macOS 27.2, Apple Silicon (M5 Pro), libusb 1.0.30, drive on a USB 3 port |
| ✅ Linux tool | Fedora 44 (aarch64, in a UTM virtual machine with USB passthrough), Python 3 standard library only |
| ⚠️ Not verified | Other T5 units or firmware, T3, T7, T9 and other drives, Intel Macs, other macOS versions, other Linux distributions, USB 2 links |

The tools check the drive's identity and protocol version first and stop if anything
differs. Offline self-tests (no drive needed) check the hash against published test
vectors, the exact command bytes, password rules and reply parsing.

## 🍎 Quick start: macOS (Apple Silicon)

You need Homebrew, the Xcode Command Line Tools and a USB 3 port and cable.

```sh
brew install libusb
git clone https://github.com/toughIQ/t5-unlock.git
cd t5-unlock/macos
make selftest
sudo ./t5-unlock-mac --unlock
```

Plug in the locked T5 first and wait for the small `T5_SETUP` volume. The tool finds
the drive, unmounts the setup volume, asks for the drive password twice, sends the
check and the unlock, then waits for the full-size disk and mounts it. macOS may ask
you to allow the accessory.

> 🔑 **You will be asked for two different passwords:**
>
> 1️⃣ **`sudo` asks first for your Mac login password**, the one you use to log in to
> the computer. It is not a separate "root password"; macOS has none by default. The
> tool needs administrator rights to take over the USB connection.
>
> 2️⃣ **The tool then asks for the T5's own password, twice** (`T5 password:` and
> `Repeat password:`). The second entry only guards against typos, so a mistyped
> password does not use up an attempt. If the two entries differ, nothing is sent.
>
> Nothing is shown while you type either password.

`sudo ./t5-unlock-mac --probe` is a read-only check that sends no password. After a
probe the drive drops off the USB list until you unplug and replug it.

`make static` builds a self-contained binary that does not need Homebrew at run time.

More detail and troubleshooting: [docs/macos.md](docs/macos.md).

## 🐧 Quick start: Linux

```sh
sudo python3 linux/t5-unlock.py --probe     # read-only, no password
sudo python3 linux/t5-unlock.py --unlock    # one attempt
```

Needs Python 3 and root (it uses the `SG_IO` interface).

> 🔑 **Two different passwords here as well:** `sudo` first asks for **your own user
> password** (the account that is allowed to use `sudo`). The script then asks for
> the **T5 password twice**. Nothing is shown while you type.

Details, and a recipe for macOS users who prefer a Linux virtual machine:
[docs/linux.md](docs/linux.md).

## 🔧 How it works

The unlock is a short vendor-specific exchange carried in standard ATA pass-through
commands. The password never leaves your computer in clear text: it is hashed with a
challenge the drive provides. Full description, command bytes and reply formats:
[docs/how-it-works.md](docs/how-it-works.md).

## 🔌 Other drives and vendors

This was only tested on one T5. Other Samsung models may use a related protocol, but
that is unproven here, and other vendors differ. If you want to extend it, read
[CONTRIBUTING.md](CONTRIBUTING.md). Verification reports for other drives are welcome.

## 🔒 Security

Found a problem that could expose a password or break a safety rule? Please report it
privately, see [SECURITY.md](SECURITY.md).

## 🙏 Credits

The protocol was first documented from USB observation by
[TurboKach/samsung-t5-ssd-unlock-apple-silicon](https://github.com/TurboKach/samsung-t5-ssd-unlock-apple-silicon)
(MIT). That project is a signed macOS menu-bar app; this repo is an independent,
smaller implementation for Linux and macOS, checked against its published test
vectors. Thanks to its author.

## ⚠️ Disclaimer

This software is provided as is, without warranty. Use it only on drives you own and
with your own password, and keep a backup of your data. It was tested on one drive and
may behave differently on yours. The drive's behavior after many wrong passwords is
unknown, which is why the tools never retry. See [LICENSE](LICENSE).

## 📜 License

[MIT](LICENSE)
