# 🍎 Step by step: macOS (no programming experience needed)

This walks through everything from a fresh Mac. Read the [README](../README.md) first to
check that your drive is the right one. It takes about 15 minutes, most of it waiting for
downloads. Every command is typed into the **Terminal** app.

> ⚠️ **Use this only on a drive you own, with your own password.** The tool makes one
> attempt per run and never retries.

## 1️⃣ Open Terminal

Press `Cmd` + `Space`, type `Terminal`, and press Return. A window with a text prompt
opens. You will copy the commands below into it, one at a time, and press Return after
each.

## 2️⃣ Install Apple's command line tools

```sh
xcode-select --install
```

A window asks whether to install the tools. Click **Install** and wait until it
finishes. If Terminal says the tools are already installed, continue.

## 3️⃣ Install Homebrew

Homebrew is the usual way to install small developer tools on a Mac. Follow the
instructions on [brew.sh](https://brew.sh). It shows one command to paste into Terminal.
It asks for **your Mac login password** (nothing is shown while you type) and tells you
at the end if it wants you to run two extra commands. Run those too.

Check that it worked:

```sh
brew --version
```

## 4️⃣ Install the one library the tool needs

```sh
brew install libusb
```

## 5️⃣ Get the tool

The easiest way without extra software: open
<https://github.com/toughIQ/t5-unlock>, click the green **Code** button, choose
**Download ZIP**, and open the downloaded file so it unzips in your Downloads folder.
Then in Terminal:

```sh
cd ~/Downloads/t5-unlock-main/macos
```

If you already use git, `git clone https://github.com/toughIQ/t5-unlock.git` works too.

## 6️⃣ Build it and run the offline test

```sh
make selftest
```

This compiles the tool on your own Mac and checks it without touching any drive. You
should see a line starting with `PASS`. If it says `make: command not found`, repeat
step 2.

## 7️⃣ Plug in the locked drive

Use a **USB 3 port and the original cable**, without a hub or adapter. Wait until the
small `T5_SETUP` volume appears on your Desktop or in Finder. Do not open the Samsung
app on it.

## 8️⃣ Run the unlock

```sh
sudo ./t5-unlock-mac --unlock
```

You will be asked for two different passwords:

- **First, `Password:`** is **your Mac login password**, asked by `sudo`. Nothing is
  shown while you type.
- **Then, `T5 password:` and `Repeat password:`** is the **drive's password**, entered
  twice. If the two entries differ, nothing is sent to the drive.

A normal run ends like this:

```text
Password accepted by the drive.
Unlock sent, then relink sent (a disconnect here is expected).
Unlocked T5 appeared: /dev/disk4, 931.5 GiB.
Volume(s) mounted successfully
```

The disk name and size will differ on your Mac. If macOS asks whether to **allow the accessory**, click **Allow**. Your drive then shows
up in Finder with its files.

## 9️⃣ Copy your data off

Once unlocked, copy anything important to another place right away. The drive locks
again whenever it loses power, for example when you unplug it. To use it again, repeat
steps 7 and 8.

## 🩺 If something goes wrong

- **"Expected exactly one locked T5"**: unplug the drive, wait five seconds, plug it
  back in, wait for `T5_SETUP`, and try again.
- **"Not on a SuperSpeed link"**: use a USB 3 port and the original cable.
- **"Wrong password: the drive rejected it"**: the tool stopped and sent nothing else.
  Do not keep trying. The drive's behavior after many wrong attempts is unknown.
- **Anything else**: see [docs/macos.md](macos.md) and the [FAQ](faq.md), or open a
  [verification report](https://github.com/toughIQ/t5-unlock/issues/new/choose) with the
  tool's output (never your password, and leave out serial numbers).
