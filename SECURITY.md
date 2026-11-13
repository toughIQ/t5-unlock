# 🔒 Security policy

## 📌 Supported versions

Only the latest commit on `main` is supported. There are no release branches.

## 🚨 Reporting a problem

Please report security problems **privately**, not in a public issue:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include what you saw, the commands you ran, your OS and tool version, and the output of
`--probe` if it is relevant. **Never include your drive password**, and do not attach drive
contents or serial numbers.

This is a small hobby project run by one person, so replies are best effort.

## 🎯 What counts as a security problem here

- 🔒 The T5 password is logged, printed, stored, or passed in an argument or environment
  variable.
- 🛡️ A safety rule is broken: a command is sent after the drive rejected the password,
  a password is retried automatically, or the identity and protocol checks are skipped.
- 🧑‍💻 A memory-safety bug in the macOS tool, or a way for a crafted USB device or reply to
  make either tool misbehave.
- 🔧 A problem in the build or CI setup that could affect users of the tools.

## 🚫 Out of scope

- 🚫 The drive's own cryptographic design or firmware. This repo does not change them.
- 🚫 Forgotten passwords, password recovery, or bypassing the drive's protection. The
  tools do not do this and will not be extended to.
- 🚫 Problems that need an already compromised computer or an administrator account that
  is already controlled by an attacker.
