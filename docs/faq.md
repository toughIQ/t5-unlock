# ❓ FAQ

## 🔑 I forgot my password. Can this help?

No. The tool only checks the password you give it. It cannot recover, guess or bypass
one, and it will not be extended to do that. Without the password the data cannot be
reached. As far as we know, Samsung documents a factory reset for these drives, and it
**erases the drive**. Contact Samsung support for the current procedure. We have not
tested it.

## 🛡️ Does it change or erase anything on my drive?

The tool sends the password check and the unlock command, then asks the drive to
re-present itself as the full disk. To our knowledge it does not write to your files or
to the data area, and it does not format anything. It also does not change the password.

No software can promise zero risk on hardware it has only been tested on once, so keep
a backup of anything important. Copy your data off once the drive is unlocked.

## 🎯 How many attempts do I get?

One per run, and the tool never retries by itself. The drive's behavior after many wrong
passwords is **unknown** (some drives lock harder or wipe). That is why the tool asks you
to type the password twice, so a typo cannot use up an attempt, and why it stops at the
first problem. If the drive rejects your password, think before you try again.

## 💾 Which drives work?

Verified: one Samsung Portable SSD T5 (USB `04e8:61f6` when locked). Everything else is
untested. The tools check the drive's name, USB ID and protocol version before sending
anything and refuse a drive that does not match. A T3, T5 EVO, T7 or T9 may use a
different protocol, so do not assume it works. If you try one, a
[verification report](https://github.com/toughIQ/t5-unlock/issues/new/choose) helps
everyone.

## 🪟 What about Windows?

Not covered. Samsung's old Windows app may or may not still work for you. We have not
tested it.

## 🍎 Does it work on an Intel Mac or an older macOS?

Untested. It was verified on an Apple Silicon Mac running macOS 27.2. The approach is
not tied to that, so it may work elsewhere, but we cannot say.

## 🧑‍💻 Why does it need `sudo`?

To talk to the drive directly, a program needs administrator rights. On macOS the tool
takes over the drive's USB connection, which needs root. On Linux it uses a disk
interface that needs root. The tool asks `sudo` for **your own login password**. This is
different from the drive's password, which the tool asks for next. See the quick start
for which prompt is which.

## 🔒 Is my password safe?

The password is read directly from the terminal, never printed, never written to a file,
never put in a command-line argument or environment variable, and the tool wipes its own
buffers after use (best effort). The drive never receives the password itself: it gets a
hash built from the password and a random challenge the drive provided. The code is one
short file per platform, so you can read it yourself.

## 📦 Why is there no download to double-click?

A program that runs as root and handles a password is something you should be able to
read and build yourself. The macOS tool is one C file with a short build, and the Linux
tool is one Python file. We do not publish pre-built binaries. If you want a copy that
does not need Homebrew afterwards, `make static` builds one on your own Mac.

## 🩺 The drive vanished after `--probe`. Is it broken?

No. Claiming the drive's USB interface without unlocking leaves it off the USB list until
you unplug it and plug it back in. That is expected.

## ⚖️ Is this legal? Is it affiliated with Samsung?

It is not affiliated with Samsung. It exists so owners can use a drive they own, with
their own password, when the vendor's software no longer runs. It contains no Samsung
code, firmware or keys, and the protocol description comes from observing how the drive
behaves on USB. This is not legal advice. If you have doubts about your situation, ask
someone qualified.

## 🔓 Can I remove the password for good?

Not with this tool. After you copy your data off, the options are to keep unlocking it
this way after each power-up, or to switch the protection off with vendor software.
Samsung's Android app for the T3 and T5 is reported to be able to turn it off. We have
**not** tested that. Reformatting the drive does not remove the protection, because the
password is kept in the drive's firmware.

## 🤝 How can I help?

Open a [verification report](https://github.com/toughIQ/t5-unlock/issues/new/choose)
whether it worked or not. See [CONTRIBUTING.md](../CONTRIBUTING.md) for what is
welcome and what will not be accepted.
