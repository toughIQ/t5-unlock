# 🔓 t5-unlock

[![tests](https://github.com/toughIQ/t5-unlock/actions/workflows/ci.yml/badge.svg)](https://github.com/toughIQ/t5-unlock/actions/workflows/ci.yml)
[![codeql](https://github.com/toughIQ/t5-unlock/actions/workflows/codeql.yml/badge.svg)](https://github.com/toughIQ/t5-unlock/actions/workflows/codeql.yml)

Unlock your own password-protected **Samsung Portable SSD T5** on current macOS
(Apple Silicon) and on Linux, now that the vendor's old unlock software no longer runs.

> ⚠️ **Not affiliated with, endorsed by, or sponsored by Samsung.** "Samsung" and
> "Portable SSD T5" are trademarks of their owners. They are used here only to
> say which drive this tool works with.

## 🩺 Is this for you?

You are probably in the right place if all of these fit:

- ✅ You own a **Samsung Portable SSD T5** and you set a password on it years ago
- ✅ You **know the password**
- ✅ On your computer the drive shows only a tiny volume called **`T5_SETUP`** (about
  40 to 90 MB), and your files are not visible
- ✅ The Samsung unlock app that sits on that volume does not start, is "damaged",
  asks for a driver you cannot allow, or simply does nothing on your new computer

That tiny volume is how the drive looks while it is locked. Your data is still there.

You can check that it is the same drive. While the T5 is plugged in and locked:

```sh
# macOS
system_profiler SPUSBHostDataType | grep -E "Portable SSD|Vendor ID|Product ID|Link Speed"

# Linux
lsusb | grep 04e8
```

A locked original T5 reports USB vendor `0x04e8` and product **`0x61f6`**. After a
successful unlock it reports `0x61f5`. Anything else is a different drive or state, and
the tools will refuse it. Do not post the serial number if you share this output.

> 📌 **Not a fit?** If you forgot the password, this tool cannot help (see the
> [FAQ](docs/faq.md)). If your drive is another model, such as a T3, T7, T9 or a T5
> EVO, it may behave differently and has not been tested. Windows is not covered.

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
load by default. A Linux version never existed. As of November 2026 we found no
official Samsung tool for the original T5 that runs on current macOS or Linux. If you
find one, use it instead.

So a locked T5 on a modern computer shows only the tiny setup volume and the data
stays out of reach, even for its legitimate owner. This repo is for those owners.

## 📋 Status

Verified on **one** setup. Treat everything else as unverified.

| Part | Verified on |
|---|---|
| ✅ Drive | One Samsung Portable SSD T5, USB `04e8:61f6` when locked and `04e8:61f5` when unlocked, protocol version `0x00010103`, hash scheme 0 |
| ✅ macOS tool | macOS 27.2, Apple Silicon (M5 Pro), libusb 1.0.30, drive on a USB 3 port |
| ✅ Linux tool | Fedora 44 (aarch64, in a UTM virtual machine with USB passthrough), Python 3 standard library only |
| ✅ Safety rules | Mock-drive tests in CI check that a rejected password never leads to an unlock, nothing is retried, and unverified drives get no password data. They run without a drive. |
| ✅ Linux lookup and prompts | Tests run the drive lookup against a fake sysfs tree and check the prompts, exit codes and messages of the unlock command. No drive needed. |
| ⚠️ Pending | Both tools were restructured after their real-drive runs (to make the mock tests possible). Behaviour is meant to be identical, and the offline and mock tests pass, but a fresh run on a real drive has not been done yet. |
| ⚠️ Not verified | Other T5 units or firmware, T3, T7, T9 and other drives, Windows, Intel Macs, other macOS versions, other Linux distributions, USB 2 links |

The tools check the drive's identity and protocol version first and stop if anything
differs. Offline self-tests check the hash against published test vectors, the exact
command bytes, password rules and reply parsing.

## 🍎 Quick start: macOS (Apple Silicon)

New to Terminal? Follow the [step-by-step guide](docs/step-by-step-macos.md).

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

## ❓ Questions

Forgot the password? Different drive? Windows? Is it safe? How many attempts do I
get? See the [FAQ](docs/faq.md).

## 💬 Did it work for you?

Reports from other setups are the most useful thing you can give. Whether it worked,
stopped with a message, or refused your drive, please open a
[verification report](https://github.com/toughIQ/t5-unlock/issues/new/choose). The
`--probe` output is safe to share (it contains no password), but leave out any serial
number.

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
