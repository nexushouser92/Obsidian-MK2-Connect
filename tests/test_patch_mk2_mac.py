"""Tests for patch_mk2_mac.py and mk2_patcher_mac.py on small synthetic Mach-O files: no vendor binary needed.

Run with: python -m unittest discover -s tests
"""
import contextlib
import io
import os
import plistlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mk2_patcher_mac
import patch_mk2_mac
from patch_mk2_mac import (ARM_ARGS, CPU_ARM64, CPU_X86_64, JAM_PID, MK2_PID, X86_ARGS, analyze, apply, apply_in_place,
                           check, identify)

TEXT_ADDR, TEXT_OFF, TEXT_SIZE = 0x100001000, 0x1000, 0x300
VT_JAM, VT_MK2 = 0x100003000, 0x100003100
FUNC = TEXT_ADDR + 0x100
JAM_DISP = {CPU_X86_64: 0x360, CPU_ARM64: 0x290}
MK2_DISP = {CPU_X86_64: 0x3C0, CPU_ARM64: 0x2F0}
SYMBOLS = [
    ("__ZTVN2NI9MASCOMMON2HW22ControllerSubscriptionINS_4NHL211MaschineJam12SubscriptionEEE", VT_JAM),
    ("__ZTVN2NI9MASCOMMON2HW22ControllerSubscriptionINS_4NHL211MaschineMK212SubscriptionEEE", VT_MK2),
    ("__ZN2NI9MASCOMMON2HW22ControllerSubscriptionINS_4NHL211MaschineJam12SubscriptionEE17subscribeToDeviceEv", FUNC),
]


def x86_text(patched, calls=1, pid=None):
    t = bytearray(b"\x90" * TEXT_SIZE)
    for at, vt, disp in ((0x000, VT_JAM, JAM_DISP[CPU_X86_64]), (0x020, VT_MK2, MK2_DISP[CPU_X86_64])):
        rel = vt + 16 - (TEXT_ADDR + at + 7)       # lea vtable+16(%rip),%rax ; mov %rax,disp(%rbx)
        t[at:at + 14] = b"\x48\x8D\x05" + struct.pack("<i", rel) + b"\x48\x89\x83" + struct.pack("<i", disp)
    t[0x100:0x104] = b"\x55\x48\x89\xE5"            # push %rbp ; mov %rsp,%rbp
    block = b"\xBE" + struct.pack("<I", pid or (MK2_PID if patched else JAM_PID)) + X86_ARGS + b"\0\0\0\0" + b"\xC3"
    t[0x120:0x120 + len(block)] = block
    for n in range(calls):
        at = 0x200 + 16 * n
        disp = MK2_DISP[CPU_X86_64] if patched else JAM_DISP[CPU_X86_64]
        t[at:at + 12] = b"\x48\x8D\xBB" + struct.pack("<i", disp) + b"\xE8" + struct.pack("<i", FUNC - (TEXT_ADDR + at + 12))
    return bytes(t)


def arm_text(patched, calls=1, pid=None):
    words = [0xD503201F] * (TEXT_SIZE // 4)         # nop

    def adrp(rd, pc, target):
        pages = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
        return 0x90000000 | (pages & 3) << 29 | ((pages >> 2) & 0x7FFFF) << 5 | rd

    for at, vt, disp in ((0x00, VT_JAM, JAM_DISP[CPU_ARM64]), (0x20, VT_MK2, MK2_DISP[CPU_ARM64])):
        pc = TEXT_ADDR + at
        words[at // 4:at // 4 + 3] = [adrp(8, pc, vt + 16), 0x91000000 | ((vt + 16) & 0xFFF) << 10 | 8 << 5 | 8,
                                      0xF9000000 | disp // 8 << 10 | 19 << 5 | 8]
    words[0x100 // 4] = 0xD10183FF                  # sub sp,sp,#0x60
    pid = pid or (MK2_PID if patched else JAM_PID)
    words[0x110 // 4:0x110 // 4 + 6] = [0x52800001 | pid << 5, *struct.unpack("<4I", ARM_ARGS), 0x94000000]
    words[0x128 // 4] = 0xD65F03C0                  # ret
    for n in range(calls):
        at = 0x200 + 8 * n
        disp = MK2_DISP[CPU_ARM64] if patched else JAM_DISP[CPU_ARM64]
        words[at // 4] = 0x91000000 | disp << 10 | 19 << 5
        words[at // 4 + 1] = 0x94000000 | ((FUNC - (TEXT_ADDR + at + 4)) // 4) & 0x3FFFFFF
    return struct.pack(f"<{len(words)}I", *words)


def macho(cpu, text, symbols=SYMBOLS):
    """Thin 64-bit Mach-O with a __TEXT,__text section at TEXT_OFF and a symbol table."""
    strtab = b"\0"
    nlist = b""
    for name, value in symbols:
        nlist += struct.pack("<IBBHQ", len(strtab), 0x0F, 1, 0, value)
        strtab += name.encode() + b"\0"
    symoff = TEXT_OFF + len(text)
    stroff = symoff + len(nlist)
    segment = (struct.pack("<II16sQQQQiiII", 0x19, 152, b"__TEXT", TEXT_ADDR - TEXT_OFF, 0x10000, 0, len(text) + TEXT_OFF,
                           5, 5, 1, 0)
               + struct.pack("<16s16sQQIIIIIIII", b"__text", b"__TEXT", TEXT_ADDR, len(text), TEXT_OFF, 4, 0, 0, 0, 0, 0, 0))
    symtab = struct.pack("<IIIIII", 0x2, 24, symoff, len(symbols), stroff, len(strtab))
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu - (1 << 32) if cpu >= 1 << 31 else cpu, 0, 2, 2,
                         len(segment) + len(symtab), 0, 0)
    body = header + segment + symtab
    return body + b"\0" * (TEXT_OFF - len(body)) + text + nlist + strtab


def thin_x86(patched=False, **kw):
    return macho(CPU_X86_64, x86_text(patched, **kw))


def thin_arm(patched=False, **kw):
    return macho(CPU_ARM64, arm_text(patched, **kw))


def fat(*slices):
    """Fat file with the given thin slices, each one aligned to 0x10000."""
    out, entries, off = b"", b"", 8 + 20 * len(slices)
    off = (off + 0xFFFF) & ~0xFFFF
    for s in slices:
        cpu = struct.unpack("<i", s[4:8])[0]
        entries += struct.pack(">iiIII", cpu, 0, off, len(s), 16)
        out += b"\0" * (off - 8 - 20 * len(slices) - len(out)) + s
        off = (off + len(s) + 0xFFFF) & ~0xFFFF
    return struct.pack(">II", 0xCAFEBABE, len(slices)) + entries + out


class AnalyzeTests(unittest.TestCase):
    def test_x86_64_original_and_offsets(self):
        data = thin_x86()
        state, archs, patches = identify(data)
        self.assertEqual((state, archs), ("original", ["x86_64"]))
        self.assertEqual([p[0] for p in patches], [TEXT_OFF + 0x200 + 3, TEXT_OFF + 0x120 + 1])
        self.assertEqual(patches[0][1:3], (struct.pack("<i", 0x360), struct.pack("<i", 0x3C0)))
        self.assertEqual(patches[1][1:3], (struct.pack("<I", JAM_PID), struct.pack("<I", MK2_PID)))

    def test_arm64_original_and_offsets(self):
        state, archs, patches = identify(thin_arm())
        self.assertEqual((state, archs), ("original", ["arm64"]))
        self.assertEqual([p[0] for p in patches], [TEXT_OFF + 0x200, TEXT_OFF + 0x110])

    def test_apply_gives_the_patched_file_and_revert_the_original(self):
        for build in (thin_x86, thin_arm, lambda **kw: fat(thin_x86(**kw), thin_arm(**kw))):
            with self.subTest(build=build):
                original = build()
                _, _, patches = identify(original)
                out, _ = apply(original, patches, revert=False)
                self.assertEqual(bytes(out), build(patched=True))
                state, _, patches2 = identify(bytes(out))
                self.assertEqual(state, "patched")
                back, _ = apply(bytes(out), patches2, revert=True)
                self.assertEqual(bytes(back), original)

    def test_fat_file_patches_every_architecture(self):
        state, archs, patches = identify(fat(thin_x86(), thin_arm()))
        self.assertEqual((state, archs, len(patches)), ("original", ["x86_64", "arm64"], 4))
        self.assertEqual([p[3][:8] for p in patches], ["[x86_64]"] * 2 + ["[arm64] "] * 2)

    def test_does_not_modify_the_input(self):
        original = thin_x86()
        _, _, patches = identify(original)
        apply(original, patches, revert=False)
        self.assertEqual(original, thin_x86())


class RefusalTests(unittest.TestCase):
    def assertRefused(self, data, text):
        state, archs, patches, problems = analyze(data)
        self.assertIsNone(state)
        self.assertEqual(patches, [])
        self.assertIn(text, " ".join(problems))
        self.assertEqual(identify(data), (None, None, None))

    def test_not_macho(self):
        self.assertRefused(b"MZ" + b"\0" * 100, "not a Mach-O")
        self.assertRefused(b"", "not a Mach-O")

    def test_missing_symbol(self):
        for cpu, text in ((CPU_X86_64, x86_text(False)), (CPU_ARM64, arm_text(False))):
            self.assertRefused(macho(cpu, text, SYMBOLS[:2]), "jam_subscribe")
            self.assertRefused(macho(cpu, text, SYMBOLS[:1] + SYMBOLS[2:]), "vt_mk2")

    def test_two_call_sites(self):
        self.assertRefused(thin_x86(calls=2), "called from 2 places")
        self.assertRefused(thin_arm(calls=2), "called from 2 places")

    def test_no_call_site(self):
        self.assertRefused(thin_x86(calls=0), "called from 0 places")

    def test_unknown_pid(self):
        self.assertRefused(thin_x86(pid=0x1600), "PID block found 0 times")
        self.assertRefused(thin_arm(pid=0x1600), "PID block found 0 times")

    def test_half_patched(self):
        # PID already changed but the call still goes to the Jam object (or the other way round)
        self.assertRefused(macho(CPU_X86_64, x86_text(False, pid=MK2_PID)), "unexpected PID")
        self.assertRefused(macho(CPU_ARM64, arm_text(False, pid=MK2_PID)), "unexpected PID")

    def test_architectures_in_different_states(self):
        self.assertRefused(fat(thin_x86(patched=True), thin_arm(patched=False)), "different states")

    def test_one_bad_architecture_refuses_the_whole_file(self):
        self.assertRefused(fat(thin_x86(), thin_arm(calls=2)), "arm64")


class ApplyTests(unittest.TestCase):
    def test_check_reports_the_offset(self):
        data = bytearray(thin_x86())
        _, _, patches = identify(bytes(data))
        data[patches[1][0]] ^= 0xFF
        self.assertIn(f"{patches[1][0]:#x}", check(bytes(data), patches, revert=False))
        out, message = apply(bytes(data), patches, revert=False)
        self.assertIsNone(out)

    def test_in_place_writes_only_the_patched_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Maschine 3")
            original = fat(thin_x86(), thin_arm())
            with open(path, "wb") as f:
                f.write(original)
            _, _, patches = identify(original)
            apply_in_place(path, patches, revert=False)
            with open(path, "rb") as f:
                changed = f.read()
            self.assertEqual(changed, fat(thin_x86(patched=True), thin_arm(patched=True)))
            apply_in_place(path, patches, revert=True)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), original)

    def test_in_place_refuses_and_changes_nothing_on_unexpected_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Maschine 3")
            original = thin_x86()
            _, _, patches = identify(original)
            broken = bytearray(original)
            broken[patches[1][0]] ^= 0xFF           # second site differs: the first one must not be written either
            with open(path, "wb") as f:
                f.write(broken)
            with self.assertRaises(ValueError):
                apply_in_place(path, patches, revert=False)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), bytes(broken))


class ActivatorTests(unittest.TestCase):
    """enable/restore of mk2_patcher_mac on a fake application (no codesign, no real process check)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.app = os.path.join(self.dir.name, "Maschine 3.app")
        self.backup = os.path.join(self.dir.name, "backup")
        os.makedirs(os.path.join(self.app, "Contents", "MacOS"))
        os.makedirs(os.path.join(self.app, "Contents", "_CodeSignature"))
        with open(os.path.join(self.app, "Contents", "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleShortVersionString": "3.3.0"}, f)
        with open(os.path.join(self.app, "Contents", "_CodeSignature", "CodeResources"), "w") as f:
            f.write("original signature")
        self.original = fat(thin_x86(), thin_arm())
        self.write(self.original)
        self.signed = []

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        with open(mk2_patcher_mac.exe_path(self.app), "wb") as f:
            f.write(data)

    def read(self):
        with open(mk2_patcher_mac.exe_path(self.app), "rb") as f:
            return f.read()

    def sign(self, app):
        self.signed.append(app)
        with open(os.path.join(app, "Contents", "_CodeSignature", "CodeResources"), "w") as f:
            f.write("ad hoc signature")

    def signature(self):
        with open(os.path.join(self.app, "Contents", "_CodeSignature", "CodeResources")) as f:
            return f.read()

    def enable(self, **kw):
        return mk2_patcher_mac.enable(self.app, self.backup, running=lambda: False, sign_app=self.sign, **kw)

    def restore(self):
        return mk2_patcher_mac.restore(self.app, self.backup, running=lambda: False, sign_app=self.sign)

    def test_describe(self):
        self.assertIn("3.3.0 (x86_64, arm64) original", mk2_patcher_mac.describe(self.app)[1])

    def test_enable_patches_signs_and_keeps_a_backup(self):
        self.enable()
        self.assertEqual(self.read(), fat(thin_x86(patched=True), thin_arm(patched=True)))
        self.assertEqual(self.signed, [self.app])
        self.assertEqual(mk2_patcher_mac.describe(self.app)[0], "patched")
        with open(os.path.join(self.backup, "Maschine 3"), "rb") as f:
            self.assertEqual(f.read(), self.original)
        with open(os.path.join(self.backup, "_CodeSignature", "CodeResources")) as f:
            self.assertEqual(f.read(), "original signature")

    def test_restore_puts_back_the_exact_original_and_its_signature(self):
        self.enable()
        self.assertEqual(self.restore(), "backup")
        self.assertEqual(self.read(), self.original)
        self.assertEqual(self.signature(), "original signature")
        self.assertFalse(os.path.exists(mk2_patcher_mac.exe_path(self.app) + ".mk2tmp"))

    def test_restore_without_a_backup_refuses_without_modification(self):
        self.enable()
        os.remove(os.path.join(self.backup, "Maschine 3"))
        patched = self.read()
        with self.assertRaises(mk2_patcher_mac.Abort):
            self.restore()
        self.assertEqual(self.read(), patched)

    def test_restore_rejects_mismatched_backup(self):
        self.enable()
        with open(os.path.join(self.backup, "Maschine 3"), "ab") as f:
            f.write(b"changed")
        patched = self.read()
        with self.assertRaises(mk2_patcher_mac.Abort):
            self.restore()
        self.assertEqual(self.read(), patched)

    def test_signing_failure_puts_the_original_back(self):
        def broken(app):
            raise mk2_patcher_mac.Abort("codesign failed: boom")
        with self.assertRaises(mk2_patcher_mac.Abort) as ctx:
            mk2_patcher_mac.enable(self.app, self.backup, running=lambda: False, sign_app=broken)
        self.assertIn("Nothing was changed", str(ctx.exception))
        self.assertEqual(self.read(), self.original)
        self.assertEqual(self.signature(), "original signature")

    def test_refuses_while_maschine_is_running(self):
        with self.assertRaises(mk2_patcher_mac.Abort):
            mk2_patcher_mac.enable(self.app, self.backup, running=lambda: True, sign_app=self.sign)
        self.assertEqual(self.read(), self.original)
        self.assertFalse(os.path.exists(self.backup))

    def test_refuses_unsupported_and_already_patched(self):
        self.enable()
        with self.assertRaises(mk2_patcher_mac.Abort):
            self.enable()
        self.write(fat(thin_x86(calls=2), thin_arm()))
        with self.assertRaises(mk2_patcher_mac.Abort) as ctx:
            self.enable()
        self.assertIn("cannot be patched safely", str(ctx.exception))

    def test_restore_refuses_when_not_patched(self):
        with self.assertRaises(mk2_patcher_mac.Abort):
            self.restore()

    def test_missing_app(self):
        with self.assertRaises(mk2_patcher_mac.Abort):
            mk2_patcher_mac.describe(os.path.join(self.dir.name, "nope.app"))

    def status_output(self, *flags):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mk2_patcher_mac.main(["status", "--app", self.app, *flags])
        return out.getvalue().splitlines()

    def test_status_porcelain_gives_the_state_first_for_the_app_window(self):
        self.assertEqual(self.status_output("--porcelain")[0], "original")
        self.assertIn("original", self.status_output()[0])
        self.enable()
        self.assertEqual(self.status_output("--porcelain")[0], "patched")
        self.write(fat(thin_x86(calls=2), thin_arm()))
        lines = self.status_output("--porcelain")
        self.assertEqual(lines[0], "unsupported")
        self.assertIn("cannot be patched safely", lines[1])


if __name__ == "__main__":
    unittest.main()
