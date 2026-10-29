"""MK2 Activator for music software on macOS: a command-line front end over patch_mk2_mac.py.

Finds compatible application, tells whether it is original or patched, and enables the MK2 or restores the original.
Only touches the binary where identify() in patch_mk2_mac.py finds the Jam block. Before the first change it keeps a copy
of the original binary and its _CodeSignature in /Library/Application Support/MK2 Activator, and after patching it
signs the app ad hoc (the original signature no longer matches the changed bytes).

Usage:
    python3 mk2_patcher_mac.py status                  say what state music software is in (no changes)
    sudo python3 mk2_patcher_mac.py enable             enable the MK2
    sudo python3 mk2_patcher_mac.py restore            go back to the original
Options: --app PATH (choose the application), --yes (skip the question).
"""
import mmap
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys

from patch_mk2_mac import analyze, apply_in_place

DEFAULT_APP = "/Applications/Native Instruments/Maschine 3/Maschine 3.app"
BACKUP_DIR = "/Library/Application Support/MK2 Activator"
PROCESS_NAME = "Maschine 3"
JAM_WARNING = "!!! WARNING: JAM CONTROLLER WILL STOP WORKING IN THE MUSIC SOFTWARE WHILE THE MK2 IS ENABLED !!!"


class Abort(Exception):
    """A problem to show to the user; nothing was left half-done."""


def exe_path(app):
    return os.path.join(app, "Contents", "MacOS", PROCESS_NAME)


def bundle_version(app):
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString", "?")
    except (OSError, plistlib.InvalidFileException):
        return "?"


def software_running():
    return subprocess.run(["pgrep", "-x", PROCESS_NAME], capture_output=True).returncode == 0


def read_state(app):
    """(state, architectures, patches, problems) for the app's binary; raises Abort if it cannot be read."""
    path = exe_path(app)
    if not os.path.isfile(path):
        raise Abort(f"The music software was not found at {app}. Use --app to point to the compatible application.")
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return analyze(data)
    except OSError as e:
        raise Abort(f"Could not read music software: {e.strerror}")


def sign(app):
    """Ad-hoc signature: the bytes changed, so the original one is no longer valid (arm64 refuses to run without one)."""
    r = subprocess.run(["codesign", "--force", "--sign", "-", "--preserve-metadata=entitlements", app],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Abort(f"codesign failed: {r.stderr.strip()}")


def save_backup(app, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(exe_path(app), os.path.join(backup_dir, PROCESS_NAME))
    signature = os.path.join(backup_dir, "_CodeSignature")
    shutil.rmtree(signature, ignore_errors=True)
    shutil.copytree(os.path.join(app, "Contents", "_CodeSignature"), signature, symlinks=True)
    with open(os.path.join(backup_dir, "metadata.json"), "w") as f:
        json.dump({"app": os.path.realpath(app), "version": bundle_version(app), "sha256": file_hash(exe_path(app))}, f)


def load_backup(app, backup_dir):
    """Stage both components before replacing either; roll back on a failed swap."""
    import tempfile
    contents = os.path.join(app, "Contents")
    dst = exe_path(app)
    with tempfile.TemporaryDirectory(prefix=".mk2-restore-", dir=contents) as staging:
        binary = os.path.join(staging, "original")
        signature = os.path.join(staging, "signature")
        old_signature = os.path.join(staging, "previous-signature")
        shutil.copy2(os.path.join(backup_dir, PROCESS_NAME), binary)
        shutil.copytree(os.path.join(backup_dir, "_CodeSignature"), signature, symlinks=True)
        target = os.path.join(contents, "_CodeSignature")
        os.replace(target, old_signature)
        try:
            os.replace(signature, target)
            try:
                os.replace(binary, dst)
            except BaseException:
                shutil.rmtree(target)
                os.replace(old_signature, target)
                raise
        except BaseException:
            if not os.path.exists(target) and os.path.exists(old_signature):
                os.replace(old_signature, target)
            raise


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def has_backup(backup_dir):
    return os.path.isfile(os.path.join(backup_dir, PROCESS_NAME))


def describe(app):
    state, archs, _, problems = read_state(app)
    version = bundle_version(app)
    if state == "original":
        return state, f"Music software {version} ({', '.join(archs)}) original. The MK2 is not enabled yet."
    if state == "patched":
        return state, f"Music software {version} ({', '.join(archs)}) with the MK2 enabled."
    return None, ("This music software cannot be patched safely: the expected code was not found. Nothing was changed.\n  "
                  + "\n  ".join(problems))


def enable(app, backup_dir=BACKUP_DIR, running=software_running, sign_app=sign):
    state, archs, patches, problems = read_state(app)
    if state != "original":
        raise Abort("The music software is already patched." if state == "patched" else
                    "This music software cannot be patched safely. Nothing was changed.\n  " + "\n  ".join(problems))
    if running():
        raise Abort("The music software is open. Close it and run this again.")
    save_backup(app, backup_dir)
    try:
        apply_in_place(exe_path(app), patches, revert=False)
        sign_app(app)
        if read_state(app)[0] != "patched":
            raise Abort("The patched file did not pass the final check.")
        metadata_path = os.path.join(backup_dir, "metadata.json")
        with open(metadata_path) as f:
            meta = json.load(f)
        meta["patched_sha256"] = file_hash(exe_path(app))
        with open(metadata_path, "w") as f:
            json.dump(meta, f)
    except (Abort, OSError, ValueError) as e:
        load_backup(app, backup_dir)
        raise Abort(f"{e}\nThe original music software was put back. Nothing was changed.")
    return patches


def restore(app, backup_dir=BACKUP_DIR, running=software_running, sign_app=sign):
    state, archs, patches, problems = read_state(app)
    if state != "patched":
        raise Abort("The music software is not patched." if state == "original" else
                    "This music software is not in a known state. Nothing was changed.\n  " + "\n  ".join(problems))
    if running():
        raise Abort("The music software is open. Close it and run this again.")
    if has_backup(backup_dir):
        try:
            with open(os.path.join(backup_dir, "metadata.json")) as f:
                meta = json.load(f)
            if meta["app"] != os.path.realpath(app) or meta["version"] != bundle_version(app) or meta["sha256"] != file_hash(os.path.join(backup_dir, PROCESS_NAME)):
                raise Abort("Backup does not match this installation; restore it using your software provider’s installer.")
            # Codesign changes embedded signature bytes, so compare the signed patched binary.
            if file_hash(exe_path(app)) != meta["patched_sha256"]:
                raise Abort("Backup does not match the current executable; restore it using your software provider’s installer.")
        except (OSError, ValueError, KeyError) as e:
            raise Abort(f"Backup cannot be verified: {e}. Restore using your software provider’s installer.")
        load_backup(app, backup_dir)
        return "backup"
    # A missing backup cannot safely restore the original signature.
    raise Abort("No verified backup is available. Restore music software using your software provider’s installer.")


def elevate():
    """Writing to /Applications needs root: ask for the password in this terminal, then run again as root."""
    os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])


def main(argv):
    args = [a for a in argv if a not in ("--yes", "--porcelain")]
    assume_yes = "--yes" in argv
    porcelain = "--porcelain" in argv      # for the app window: first line is the state, then the message
    app = DEFAULT_APP
    if "--app" in args:
        i = args.index("--app")
        app = os.path.normpath(args[i + 1]) if i + 1 < len(args) else sys.exit(__doc__)
        del args[i:i + 2]
    if len(args) != 1 or args[0] not in ("status", "enable", "restore"):
        sys.exit(__doc__)
    command = args[0]

    try:
        if command == "status":
            state, text = describe(app)
            print(f"{state or 'unsupported'}\n{text}" if porcelain else text)
            return
        if os.geteuid() != 0:
            elevate()
        if command == "enable":
            if not assume_yes:
                print(f"\n{JAM_WARNING}\n")
                if input("Do you want to enable the MK2? [y/N] ").strip().lower() not in ("y", "yes"):
                    return
            enable(app)
            print("MK2 enabled. Connect the MK2 and open the music software.\n"
                  "If your software provider’s installer updates music software, you will need to enable it again.")
        else:
            how = restore(app)
            print("The music software is back to its factory state. The Jam controller works again."
                  + (" (restored from the saved copy)" if how == "backup" else ""))
    except Abort as e:
        sys.exit(f"ERROR: {e}")
    except PermissionError as e:
        sys.exit(f"ERROR: {e.strerror}. If macOS blocks it, allow Terminal under System Settings > "
                 "Privacy & Security > App Management (or Full Disk Access) and try again.")


if __name__ == "__main__":
    main(sys.argv[1:])
