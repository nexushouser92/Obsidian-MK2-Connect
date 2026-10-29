# Maschine MK2 Works in Maschine 3

<img width="1243" height="634" alt="banner" src="https://github.com/user-attachments/assets/07cacfa8-aee2-42aa-b85f-bce3ba61ccf8" />


Makes your **Maschine MK2** work in the **Maschine 3** software again (Windows and macOS). Download **MK2 Activator** for your platform from [Releases](../../releases). See the macOS instructions below for the latest Mac release.

> [!CAUTION]
> **!!! MASCHINE JAM WILL STOP WORKING IN MASCHINE 3 WHILE THE MK2 IS ENABLED !!!**
>
> Use **Restore original** in the Activator to get the Jam back.

$${\color{red}\textbf{!!! MASCHINE JAM WILL STOP WORKING IN MASCHINE 3 WHILE THE MK2 IS ENABLED !!!}}$$

> [!WARNING]
> This is an unofficial project, not affiliated with or endorsed by Native Instruments. Use it at your own risk. It does not touch licensing or activation: you need your own licensed copy of Maschine 3.

## Requirements

- Windows 10 or 11 (64-bit)
- Maschine 3 installed and activated with Native Access
- Maschine MK2 driver installed (*Maschine Controller MK2 Driver*)

**Supported Maschine 3 versions:** 3.6.0 and 3.7.1 are analyzed and tested. Other 3.x builds are patched only if the Activator finds the exact code it expects; otherwise it refuses and nothing is changed.

## How to use

1. Download `MK2 Activator.exe` from the [Releases](../../releases) page.
2. Close Maschine 3.
3. Double-click `MK2 Activator.exe` and click **Yes** when Windows asks for permission.
4. Click **Enable MK2**.
5. Connect the MK2 and open Maschine 3.

To undo it, open the Activator again and click **Restore original**. Repairing Maschine 3 in Native Access works too.

**After every Maschine 3 update from Native Access, run the Activator again.**

### Windows or the antivirus says the app is dangerous

The Activator is not digitally signed, so Windows SmartScreen shows "Unknown publisher". Click **More info** → **Run anyway**.

A few antivirus engines flag apps made with PyInstaller as false positives, because they share the same launcher as some malware written in Python. Check the release notes for available scan reports.

Windows release executables are built by [GitHub Actions](../../actions). For a release that includes `SHA256.txt`, check the download with:

```powershell
Get-FileHash "MK2 Activator.exe" -Algorithm SHA256
```

## macOS

Same patch as on Windows, applied to the Mach-O binary of `Maschine 3.app` (both the Intel and the Apple Silicon parts).

### Requirements

- macOS 11 or later, with Maschine 3 installed in `/Applications/Native Instruments/Maschine 3/`
- Maschine MK2 driver for Mac installed (*Maschine Controller MK2 Driver*)
- Python 3.8 or later (`python3` from the Xcode Command Line Tools is enough)
- To build the window: the Xcode Command Line Tools (`xcode-select --install`)

### With the window (like on Windows)

1. Download `MK2.Activator.mac.zip` from the [latest macOS release](../../releases), extract it, and open `MK2 Activator.app`.
2. Close Maschine 3 before proceeding.
3. Click **Enable MK2** and type your password when macOS asks for it.
4. Connect the MK2 and open Maschine 3.

To undo it, open the app again and click **Restore original**. The window shows what state Maschine 3 is in, and the check takes a few seconds.

### From Terminal

Needs only `patch_mk2_mac.py` and `mk2_patcher_mac.py`, in the same folder.

1. Close Maschine 3.
2. Check what state Maschine 3 is in (changes nothing):

   ```bash
   python3 mk2_patcher_mac.py status
   ```

3. Enable the MK2. It asks for your password, because it writes inside `/Applications`:

   ```bash
   python3 mk2_patcher_mac.py enable
   ```

4. Connect the MK2 and open Maschine 3.

To undo it, run `python3 mk2_patcher_mac.py restore`. Repairing Maschine 3 in Native Access works too.

**After every Maschine 3 update from Native Access, run `enable` again.**

### What it does to the app

- Before the first change it saves the original binary and its `_CodeSignature` in `/Library/Application Support/MK2 Activator`. `restore` verifies that the backup matches the current installation before restoring it. If no matching backup is available, repair Maschine with Native Access.
- It changes 2 places per architecture (4 in total). Nothing else in the app is touched.
- The changed bytes no longer match the original code signature, so it signs the app ad hoc (`codesign --sign -`). If signing fails, the original is put back.
- The patch is found through the symbols of the binary and checked before writing. If anything is different from what it expects (for example a build with stripped symbols or another code layout), it refuses and changes nothing.

If macOS answers "Operation not permitted", allow Terminal under **System Settings → Privacy & Security → App Management** (or **Full Disk Access**) and run it again.

**Tested:** Maschine 3.3.0 on an Intel Mac with an MK2 connected. The updated activator enabled the device and its screens, pads, and buttons responded. Activation and exact binary restoration were also tested on a copy of the 3.3.0 universal app. Apple Silicon and other Maschine versions have not yet been tested with hardware; the patch refuses builds it cannot identify safely.

---

This repository contains no Native Instruments code or binaries. Released under the [MIT License](LICENSE).
