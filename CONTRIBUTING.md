# 🤝 Contributing

Thanks for helping. This project exists so owners of old password-locked drives can
get to their own data when the vendor's tool no longer runs. Keep it that way.

## 🚫 What will not be accepted

- 🚫 Brute force, dictionary or guessing modes, or any password recovery
- 🚫 Anything that bypasses, resets or defeats the drive's protection, or reads the key
- 🚫 Retry loops. One attempt per run is a deliberate safety rule.
- 🚫 Vendor binaries, firmware, keys or decompiled vendor code

## ✅ What is welcome

- ✅ **Verification reports** for other T5 units or firmware, macOS versions, Linux
  distributions and USB setups. Open an issue with: drive model, the output of
  `--probe` (it contains no password or personal data), host OS and version, and what
  happened. A probe that stops with "protocol version differs" is a useful report too.
- ✅ Bug fixes and clearer docs.
- ✅ **Support for other drives**, only with evidence from a real drive of that model:
  the exact identity strings and USB IDs, the protocol version it reports, and a
  successful unlock by its owner. Add the model to the Status table in the README
  only after that, and keep the identity checks strict.

## 🔧 Rules for changes

- 🛡️ Do not weaken the checks that run before a password is sent. The mock-drive tests
  (`linux/test_mock_drive.py` and the `mock:` checks in the C self-test) enforce the
  safety rules. If you change the unlock flow, extend them; never loosen them to make
  a change pass.
- ✅ Keep the checks passing. None of them needs a drive:
  - `ruff check linux`, `python3 linux/test_offline.py` and `python3 linux/test_mock_drive.py`
  - `make -C macos check` (self-test including the mock-drive safety tests, strict
    warnings as errors, AddressSanitizer and UndefinedBehaviorSanitizer, cppcheck).
    Needs `brew install libusb cppcheck`.
  - `markdownlint-cli2` for the docs
  - GitHub Actions runs all of these, plus CodeQL, on every push and pull request.
- 🔒 Do not include drive contents, serial numbers or personal data in issues or commits.
- 📝 Use plain, honest wording in docs. Say what was verified and what was not.
- 📝 Follow the documentation style in [AGENTS.md](AGENTS.md): emojis in human-facing
  docs for scanning, none in code, commit messages or agent-facing files, and no
  em dashes.
