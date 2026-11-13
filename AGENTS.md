# Agent instructions for t5-unlock

Unlock tools for a password-protected Samsung Portable SSD T5 on macOS and Linux, for
the drive's owner, replacing vendor software that no longer runs.

## Hard rules for this project

- The tools only perform the standard password check with the owner's own password.
- No brute force, dictionary, guessing, retry loops, password recovery, bypass or reset
  features. Do not add them, and decline requests for them.
- One attempt per run. Never retry automatically.
- Identity and protocol checks run before any password is sent. Never weaken them.
- No vendor binaries, firmware, keys or decompiled vendor code in the repo.
- Say what was verified and what was not. Verified means tested on a real drive.
  Do not claim support for drives, systems or versions that were not tested.
- Keep personal data out of the repo: no drive contents, serial numbers, usernames,
  home paths or employer references.

## Documentation style

Markdown files for humans (README, docs, CONTRIBUTING) use emojis to make scanning
easier. Agent-facing files (this file), code, code comments and commit messages do not.
Keep it functional. Every emoji should help scanning, not decorate.

The emojis below are reference data for the style, not decoration of this file.

| Context | Emoji | Example |
|---|---|---|
| H1 title | 🔓 | `# 🔓 t5-unlock` |
| H2 sections | contextual | `## 💡 Why this exists`, `## 📋 Status` |
| Warnings, status | ⚠️ 🚧 | `> ⚠️ **Note:** not affiliated with the vendor` |
| Notes, callouts | 📌 📋 | `> 📌 **Note:** needs a USB 3 port` |
| Numbered steps | 1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣ | `### 1️⃣ Install UTM` |
| Checklists, verified items | ✅ | `- ✅ Python 3` |
| Not allowed, refused | 🚫 | `- 🚫 No brute force` |
| Unverified, caution | ⚠️ | `⚠️ Not verified` |
| Security, safety checks | 🔒 🛡️ 🔑 | Password handling, pre-send checks, hash |
| Code, config, build | 🧑‍💻 🔧 | Build and run, how it works |
| macOS, Linux | 🍎 🐧 | Platform sections |
| USB, transport | 🔌 📡 | Hardware, protocol replies |
| Troubleshooting, questions | 🩺 ❓ | Fixes, FAQ |
| Credits, community | 🙏 🤝 | Thanks, contributing |
| License | 📜 | License section |

## Writing rules

- No em dashes anywhere. Use commas, periods or a plain hyphen, or restructure.
- Use relative links inside the repo.
- Plain, honest wording. Short sentences. Explain trade-offs and limits.
- Do not name or link to unrelated internal projects, repositories or employers.

## Code and tests

- `linux/t5-unlock.py`: Python 3 standard library only. Test: `python3 linux/test_offline.py`.
- `macos/t5-unlock-mac.c`: C with libusb. Test: `make -C macos selftest`.
- Both self-tests run without a drive. Keep them passing and extend them with any
  change to hashing, command bytes, password rules or reply parsing.
- Hardware runs (`--probe`, `--unlock`) are done by the drive's owner, who types the
  password. Agents never receive or store the password.

## Git rules

- Commit messages: concise, imperative, no emojis.
- Never commit secrets, passwords, tokens or drive data.
- Do not push without explicit approval from the repository owner.
- Do not amend published commits.
