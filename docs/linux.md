# 🐧 Linux notes

Verified on Fedora 44 (aarch64) in a UTM virtual machine with the T5 passed through,
using an earlier version of the script. The current version was reworked after that run
(safer unmounting, clearer messages, more tests). Its offline and mock-drive tests pass,
but it has not been run on a real drive yet. See the Status table in the README. Other
distributions and bare-metal hosts use the same kernel interface but are untested here.

## ✅ Requirements

- ✅ Python 3 (standard library only, no extra packages)
- ✅ Root, because the script opens the disk device and uses the `SG_IO` ioctl
- ✅ The locked T5 shows up as a small disk (about 40 to 90 MB) with vendor `Samsung`
  and model `Portable SSD T5`

## 🧑‍💻 Run

```sh
python3 linux/test_offline.py            # offline checks, no drive
sudo python3 linux/t5-unlock.py --probe  # read-only, no password
sudo python3 linux/t5-unlock.py --unlock # one attempt, asks for the password twice
python3 linux/t5-unlock.py --help        # options and exit codes
```

The script first checks the drive without asking for anything (identity and protocol
version). Only then does it ask for the password. It unmounts the setup volume, validates
the password, unlocks, sends the relink and waits for the full-size disk to appear, then
shows it with `lsblk`. Mount it as usual afterwards.

### 🔑 Which password is which

1️⃣ **`sudo` asks for your own user password** (or run the script from a root shell).
This gives the script the rights to talk to the disk device.

2️⃣ **The script then asks for the T5 DRIVE password twice**: `T5 DRIVE password (not
your login or sudo password):` and `Repeat the T5 DRIVE password:`. The second entry
only guards against typos: if the two differ, nothing is sent. Nothing is shown while
you type.

## 🩺 Messages

Every message that ends in **"Nothing was sent"** or **"No password was asked or sent"**
means the drive did not receive any password data.

- **"No Samsung Portable SSD T5 found"**: plug it in and wait a few seconds.
- **"The T5 is already unlocked"**: nothing to do, mount it as usual.
- **"Found N locked T5 drives"**: connect only one.
- **"Cannot open /dev/sdX: the setup volume is in use or was mounted again"**: close
  file manager windows showing `T5_SETUP`, or turn off automatic mounting for a moment,
  then try again.
- **"Wrong password: the drive rejected it"**: no unlock command was sent. Do not try
  again until you are sure of the password.
- **"Unlock did not report success"**: no relink was sent, the drive stays locked.
  Unplug and replug it before trying again.

The exit codes are the same as on macOS, see [docs/macos.md](macos.md#-exit-codes).

## 🍎 macOS users: the virtual machine route

If you prefer not to run the native macOS tool, a Linux virtual machine works. This
is what was used first, before the macOS tool existed.

### 1️⃣ Install UTM

`brew install --cask utm`

### 2️⃣ Create the VM

Create a Linux VM with the **QEMU backend** (not "Apple Virtualization"). USB
passthrough needs QEMU. Enable USB 3.0 (XHCI) and "share host USB devices".

### 3️⃣ Prepare the VM

Inside the VM install Python 3, copy the two scripts in, and run the offline test.

### 4️⃣ Pass the drive through

Eject the `T5_SETUP` volume on the Mac and pass the T5 to the VM from the UTM
toolbar, or with `utmctl usb list` and `utmctl usb connect <vm> <vid:pid>`.

### 5️⃣ Unlock

Run `--probe`, then `--unlock` in the VM. After the relink the drive may
re-enumerate and go back to the Mac instead of the VM. That is fine: it stays
unlocked while powered, and it mounts on the Mac.

## 📌 Notes

- After the relink the script waits up to 60 seconds for the unlocked disk. In a VM
  that wait can time out because the disk went to the host. This is expected.
- The relink reply is not checked. The drive usually disconnects while it switches to the
  full disk, which is expected.
