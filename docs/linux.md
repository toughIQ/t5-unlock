# 🐧 Linux notes

Verified on Fedora 44 (aarch64) in a UTM virtual machine with the T5 passed through.
Other distributions and bare-metal hosts use the same kernel interface but are
untested here.

## ✅ Requirements

- ✅ Python 3 (standard library only, no extra packages)
- ✅ Root, because the script opens the disk device and uses the `SG_IO` ioctl
- ✅ The locked T5 shows up as a small disk (about 44 to 88 MB) with vendor `Samsung`
  and model `Portable SSD T5`

## 🧑‍💻 Run

```sh
python3 linux/test_offline.py            # offline checks, no drive
sudo python3 linux/t5-unlock.py --probe  # read-only, no password
sudo python3 linux/t5-unlock.py --unlock # one attempt, asks for the password twice
```

The script unmounts the setup volume, validates the password, unlocks, sends the
relink and waits for the full-size disk to appear, then shows it with `lsblk`.
Mount it as usual afterwards.

### 🔑 Which password is which

1️⃣ **`sudo` asks for your own user password** (or run the script from a root shell).
This gives the script the rights to talk to the disk device.

2️⃣ **The script then asks for the T5 password twice.** The second entry only guards
against typos: if the two differ, nothing is sent. Nothing is shown while you type.

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
- The relink may report a harmless status. It is logged and not treated as a failure.
