# 📝 Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

First public version, 0.1.0, prepared.

### ✅ Added

- 🍎 macOS tool (`macos/t5-unlock-mac.c`): unlocks the drive over USB Attached SCSI with
  libusb, no kernel extension, no background service, no signing identity. `--probe`
  (read-only), `--unlock` (one attempt), `--selftest`, `--help`, `--version`.
  `make static` builds a copy that does not need Homebrew at run time,
  `make install` and `make uninstall` are optional.
- 🐧 Linux script (`linux/t5-unlock.py`): Python 3 standard library only, uses `SG_IO`.
  `--probe`, `--unlock`, `--version`.
- 🛡️ Safety rules enforced by mock-drive tests in both tools: one attempt per run, no
  retry, nothing sent after a rejected or unclear validation, no password data for an
  unverified drive. The tests are checked by breaking the rules on purpose.
- 🔑 Clear password prompts: `sudo` asks for the computer login password, the tool asks
  for the T5 DRIVE password twice (typo guard).
- 🔍 Checks that need no password run before the password prompt, so a wrong drive,
  port or cable fails before you type anything.
- 🔢 The same exit codes on both platforms (0, 2, 3, 4, 5, 64, 77, 130).
- 📚 Docs: README with an "Is this for you?" check, FAQ, a step-by-step guide for macOS
  users without programming experience, protocol description, Linux virtual machine
  route, contributing guide, security policy, issue forms for verification reports.
- 🧑‍💻 CI: Python 3.9, 3.12 and 3.13, ruff, strict C warnings as errors, AddressSanitizer
  and UndefinedBehaviorSanitizer, cppcheck, markdownlint, link check, actionlint, CodeQL,
  Dependabot.

### 🔧 Fixed during preparation

- macOS: no false "failed to mount" when macOS mounts the disk by itself, mount state is
  read from the system instead of trusting `diskutil`'s exit status.
- macOS: no 60 second wait and no misleading replug advice after an unlock error.
- macOS: tries to hand the drive back to macOS after a probe, and says so if it did not
  come back.
- Linux: unmounts only the exact device and its partitions (a name prefix could match an
  unrelated disk), refuses a setup volume that was mounted again, no Python tracebacks on
  Ctrl-C or open errors, no misleading error line for the relink reply.
- Linux: waits only for a newly unlocked disk, not one that was already there.

### ⚠️ Known limits

- Verified on one drive (Samsung Portable SSD T5, USB `04e8:61f6`, protocol version
  `0x00010103`), on macOS 27.2 Apple Silicon and Fedora 44 aarch64 in a virtual machine.
  See the Status table in the README for what has been re-run after the latest changes.
- No Windows support.
