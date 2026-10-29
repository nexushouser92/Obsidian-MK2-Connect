"""MK2 patch for music software on macOS: same idea as patch_mk2.py, for the Mach-O binary.

HardwareInterface::startDeviceSubscriptions() calls the Jam's subscribeToDevice() on the Jam object, which
subscribes to PID 0x1500. This patch makes that one call work on the MK2 object (the Jam object + a fixed
distance) and subscribe to PID 0x1140 instead. Two edits per architecture:

    x86_64:  lea disp32(%reg),%rdi       Jam object -> MK2 object        (call site)
             mov $0x1500,%esi            Jam PID    -> MK2 PID           (inside subscribeToDevice)
    arm64:   add x0,xN,#imm12            Jam object -> MK2 object        (call site)
             mov w1,#0x1500              Jam PID    -> MK2 PID           (inside subscribeToDevice)

Nothing is hard-coded per version: the Jam and MK2 objects are located through the symbol table (the vtables of
controller subscription types and the Jam subscribeToDevice), and the offsets are read from the
constructor that stores those vtables. If anything does not match exactly, the file is reported as unsupported.

Usage:
    python3 patch_mk2_mac.py <input> <output>            apply the patch
    python3 patch_mk2_mac.py <input> <output> --revert   undo it
Never modifies the input file.
"""
import array
import os
import re
import struct
import sys

FAT_MAGIC, FAT_MAGIC_64, MH_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF, 0xFEEDFACF
CPU_X86_64, CPU_ARM64 = 0x01000007, 0x0100000C
CPU_NAMES = {CPU_X86_64: "x86_64", CPU_ARM64: "arm64"}
LC_SEGMENT_64, LC_SYMTAB = 0x19, 0x2

JAM_PID, MK2_PID = 0x1500, 0x1140
FUNC_WINDOW = 0x100          # the PID is set within this many bytes of the start of subscribeToDevice

# mov edx,'NiM3' ; mov ecx,'prmy' ; call
X86_ARGS = bytes.fromhex("BA334D694E" "B9796D7270" "E8")
# mov w2,#0x4d33 ; movk w2,#0x4e69,lsl 16 ; mov w3,#0x6d79 ; movk w3,#0x7072,lsl 16
ARM_ARGS = struct.pack("<4I", 0x5289A662, 0x72A9CD22, 0x528DAF23, 0x72AE0E43)

LEA_RIP = re.compile(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", re.S)
SYMBOL_PATTERNS = {
    "vt_jam": rb"__ZTV[^\0]*22ControllerSubscription[^\0]*11MaschineJam[^\0]*\0",
    "vt_mk2": rb"__ZTV[^\0]*22ControllerSubscription[^\0]*11MaschineMK2[^\0]*\0",
    "jam_subscribe": rb"__ZN[^\0]*22ControllerSubscription[^\0]*11MaschineJam[^\0]*17subscribeToDevice[^\0]*\0",
}


def macho_slices(data):
    """Returns [(cputype, file offset, size)] for a fat or thin 64-bit Mach-O, or [] if it is neither."""
    magic = struct.unpack(">I", data[:4])[0]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        out = []
        for i in range(struct.unpack(">I", data[4:8])[0]):
            if magic == FAT_MAGIC:
                cpu, _, off, size, _ = struct.unpack(">iiIII", data[8 + 20 * i:28 + 20 * i])
            else:
                cpu, _, off, size, _, _ = struct.unpack(">iiQQII", data[8 + 32 * i:40 + 32 * i])
            out.append((cpu & 0xFFFFFFFF, off, size))
        return out
    if struct.unpack("<I", data[:4])[0] == MH_MAGIC_64:
        return [(struct.unpack("<i", data[4:8])[0] & 0xFFFFFFFF, 0, len(data))]
    return []


def load_commands(data, base):
    """Returns ((__text address, size, offset within the slice) or None, (symoff, nsyms, stroff, strsize) or None)."""
    text = symtab = None
    p = base + 32
    for _ in range(struct.unpack("<I", data[base + 16:base + 20])[0]):
        cmd, size = struct.unpack("<II", data[p:p + 8])
        if cmd == LC_SEGMENT_64 and data[p + 8:p + 24].rstrip(b"\0") == b"__TEXT":
            s = p + 72
            for _ in range(struct.unpack("<I", data[p + 64:p + 68])[0]):
                if data[s:s + 16].rstrip(b"\0") == b"__text":
                    text = struct.unpack("<QQI", data[s + 32:s + 52])
                s += 80
        elif cmd == LC_SYMTAB:
            symtab = struct.unpack("<IIII", data[p + 8:p + 24])
        p += size
    return text, symtab


def find_symbols(data, base, symtab):
    """Addresses of the Jam/MK2 ControllerSubscription vtables and of the Jam subscribeToDevice; each must be unique."""
    symoff, nsyms, stroff, strsize = symtab
    strtab = bytes(data[base + stroff:base + stroff + strsize])
    wanted = {}
    for key, pattern in SYMBOL_PATTERNS.items():
        hits = [m.start() for m in re.finditer(pattern, strtab)]
        if len(hits) != 1:
            raise ValueError(f"symbol {key}: found {len(hits)} times, expected 1")
        wanted[hits[0]] = key
    found = {}
    nlist = data[base + symoff:base + symoff + 16 * nsyms]
    for strx, _, _, _, value in struct.iter_unpack("<IBBHQ", nlist):
        if strx in wanted and value:
            found[wanted[strx]] = value
    if len(found) != len(wanted):
        raise ValueError("a needed symbol has no address")
    return found


def single(values, what):
    if len(values) != 1:
        raise ValueError(f"{what}: found {sorted(values)}, expected exactly one value")
    return next(iter(values))


def analyze_x86_64(data, base, text, syms):
    """Returns (state, patches) for one x86_64 slice. Raises ValueError if the code is not what we expect."""
    addr, size, off = text
    tb = bytes(data[base + off:base + off + size])
    obj = {}
    for key in ("vt_jam", "vt_mk2"):
        target, disps = syms[key] + 16, set()
        for m in LEA_RIP.finditer(tb):
            i = m.start()
            rel = int.from_bytes(tb[i + 3:i + 7], "little", signed=True)
            if addr + i + 7 + rel != target:
                continue
            reg = ((tb[i + 2] >> 3) & 7) | (8 if tb[i] == 0x4C else 0)
            st = tb[i + 7:i + 14]      # mov %reg,disp32(%base): stores the vtable pointer into the object
            if (len(st) == 7 and st[0] in (0x48, 0x49, 0x4C, 0x4D) and st[1] == 0x89 and st[2] & 0xC0 == 0x80
                    and st[2] & 7 != 4 and ((st[2] >> 3) & 7) | (8 if st[0] & 4 else 0) == reg):
                disps.add(int.from_bytes(st[3:7], "little", signed=True))
        obj[key] = single(disps, f"{key} object offset")

    sites = []
    for pid in (JAM_PID, MK2_PID):
        pattern = b"\xBE" + pid.to_bytes(4, "little") + X86_ARGS
        sites += [(m.start(), pid) for m in re.finditer(re.escape(pattern), tb)]
    if len(sites) != 1:
        raise ValueError(f"PID block found {len(sites)} times, expected 1")
    site, pid = sites[0]
    func = syms["jam_subscribe"]
    if not 0 <= addr + site - func < FUNC_WINDOW:
        raise ValueError("the PID block is not inside the Jam subscribeToDevice")

    calls = []
    for m in re.finditer(b"\xE8", tb):
        i = m.start()
        if addr + i + 5 + int.from_bytes(tb[i + 1:i + 5], "little", signed=True) == func:
            calls.append(i)
    if len(calls) != 1:
        raise ValueError(f"subscribeToDevice is called from {len(calls)} places, expected 1")
    lea = tb[calls[0] - 7:calls[0]]
    if lea[0] not in (0x48, 0x49) or lea[1] != 0x8D or lea[2] & 0xF8 != 0xB8:
        raise ValueError("the call is not preceded by lea disp32(%reg),%rdi")
    disp = int.from_bytes(lea[3:7], "little", signed=True)

    if pid == JAM_PID and disp == obj["vt_jam"]:
        state, direction = "original", (obj["vt_jam"], obj["vt_mk2"])
    elif pid == MK2_PID and disp == obj["vt_mk2"]:
        state, direction = "patched", (obj["vt_jam"], obj["vt_mk2"])
    else:
        raise ValueError(f"unexpected PID {pid:#x} / object offset {disp:#x}")
    d_off = base + off + calls[0] - 4
    p_off = base + off + site + 1
    jam, mk2 = (x.to_bytes(4, "little", signed=True) for x in direction)
    return state, [
        (d_off, jam, mk2, f"lea {direction[0]:#x}(%reg),%rdi (Jam object) -> lea {direction[1]:#x}(%reg),%rdi (MK2 object)"),
        (p_off, JAM_PID.to_bytes(4, "little"), MK2_PID.to_bytes(4, "little"),
         "mov $0x1500,%esi (Jam PID) -> mov $0x1140,%esi (MK2 PID)"),
    ]


def analyze_arm64(data, base, text, syms):
    """Returns (state, patches) for one arm64 slice. Raises ValueError if the code is not what we expect."""
    addr, size, off = text
    tb = bytes(data[base + off:base + off + size - size % 4])
    words = array.array("I")
    words.frombytes(tb)
    if sys.byteorder == "big":
        words.byteswap()
    pages = {}      # both vtables can share a page
    for k in ("vt_jam", "vt_mk2"):
        pages.setdefault((syms[k] + 16) & ~0xFFF, []).append(k)
    low = {k: (syms[k] + 16) & 0xFFF for k in ("vt_jam", "vt_mk2")}
    func, disps, calls = syms["jam_subscribe"], {"vt_jam": set(), "vt_mk2": set()}, []
    # Match only possible ADRP/BL high bytes in C, then decode aligned instructions.
    candidates = re.finditer(rb"[\x90-\x97\xb0-\xb7\xd0-\xd7\xf0-\xf7]", tb)
    for match in candidates:
        pos = match.start()
        if pos % 4 != 3:
            continue
        i = pos // 4
        w = words[i]
        if w & 0x9F000000 == 0x90000000:                       # adrp xD,page
            imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
            imm -= (imm & 0x100000) << 1
            keys = pages.get(((addr + 4 * i) & ~0xFFF) + (imm << 12))
            if keys and i + 2 < len(words):
                rd, w2, w3 = w & 31, words[i + 1], words[i + 2]
                for key in keys:
                    if (w2 & 0xFFC00000 == 0x91000000 and (w2 >> 5) & 31 == rd and w2 & 31 == rd   # add xD,xD,#lo
                            and (w2 >> 10) & 0xFFF == low[key] and w3 & 0xFFC00000 == 0xF9000000      # str xD,[xN,#disp]
                            and w3 & 31 == rd and (w3 >> 10) & 0xFFF):    # offset 0 is the object's own constructor
                        disps[key].add(((w3 >> 10) & 0xFFF) * 8)
        elif w >> 26 == 0x25:                                  # bl
            imm = w & 0x3FFFFFF
            imm -= (imm & 0x2000000) << 1
            if addr + 4 * i + 4 * imm == func:
                calls.append(i)
    obj = {k: single(v, f"{k} object offset") for k, v in disps.items()}

    sites = []
    for pid in (JAM_PID, MK2_PID):
        pattern = struct.pack("<I", 0x52800001 | pid << 5) + ARM_ARGS
        for m in re.finditer(re.escape(pattern), tb):
            i = m.start()
            if i % 4 == 0 and i + 24 <= len(tb) and struct.unpack("<I", tb[i + 20:i + 24])[0] >> 26 == 0x25:
                sites.append((i, pid))
    if len(sites) != 1:
        raise ValueError(f"PID block found {len(sites)} times, expected 1")
    site, pid = sites[0]
    if not 0 <= addr + site - func < FUNC_WINDOW:
        raise ValueError("the PID block is not inside the Jam subscribeToDevice")
    if len(calls) != 1:
        raise ValueError(f"subscribeToDevice is called from {len(calls)} places, expected 1")
    add = words[calls[0] - 1]
    if add & 0xFFC0001F != 0x91000000:
        raise ValueError("the call is not preceded by add x0,xN,#imm")
    disp = (add >> 10) & 0xFFF

    if pid == JAM_PID and disp == obj["vt_jam"]:
        state = "original"
    elif pid == MK2_PID and disp == obj["vt_mk2"]:
        state = "patched"
    else:
        raise ValueError(f"unexpected PID {pid:#x} / object offset {disp:#x}")
    if obj["vt_mk2"] > 0xFFF:
        raise ValueError("the MK2 object offset does not fit in add #imm12")

    def word(w):
        return struct.pack("<I", w)

    jam_add = (add & ~(0xFFF << 10)) | obj["vt_jam"] << 10
    mk2_add = (add & ~(0xFFF << 10)) | obj["vt_mk2"] << 10
    return state, [
        (base + off + 4 * (calls[0] - 1), word(jam_add), word(mk2_add),
         f"add x0,xN,#{obj['vt_jam']:#x} (Jam object) -> add x0,xN,#{obj['vt_mk2']:#x} (MK2 object)"),
        (base + off + site, word(0x52800001 | JAM_PID << 5), word(0x52800001 | MK2_PID << 5),
         "mov w1,#0x1500 (Jam PID) -> mov w1,#0x1140 (MK2 PID)"),
    ]


ANALYZERS = {CPU_X86_64: analyze_x86_64, CPU_ARM64: analyze_arm64}


def analyze(data):
    """Returns (state, architectures, patches, problems). state is "original" | "patched", or None when the file
    cannot be patched safely (problems then says why). Patches are (file offset, original, patched, description)."""
    try:
        slices = macho_slices(data)
    except struct.error:
        slices = []
    slices = [s for s in slices if s[0] in ANALYZERS]
    if not slices:
        return None, [], [], ["not a Mach-O file with an x86_64 or arm64 slice"]
    states, patches, problems = set(), [], []
    for cpu, base, _ in slices:
        name = CPU_NAMES[cpu]
        try:
            text, symtab = load_commands(data, base)
            if not text or not symtab:
                raise ValueError("no __text section or symbol table")
            state, found = ANALYZERS[cpu](data, base, text, find_symbols(data, base, symtab))
        except (ValueError, struct.error) as e:
            problems.append(f"{name}: {e}")
            continue
        states.add(state)
        patches += [(off, orig, new, f"[{name}] {desc}") for off, orig, new, desc in found]
    if problems:
        return None, [CPU_NAMES[s[0]] for s in slices], [], problems
    if len(states) != 1:
        return None, [CPU_NAMES[s[0]] for s in slices], [], ["the architectures are in different states"]
    return states.pop(), [CPU_NAMES[s[0]] for s in slices], patches, []


def identify(data):
    """Returns ("original" | "patched", architectures, patches), or (None, None, None) if it cannot be patched safely."""
    state, archs, patches, _ = analyze(data)
    return (state, archs, patches) if state else (None, None, None)


def check(data, patches, revert):
    """None if every patch site holds the expected bytes, else a message saying where it does not."""
    for off, orig, new, _ in patches:
        expect = new if revert else orig
        found = bytes(data[off:off + len(expect)])
        if found != expect:
            return f"at offset {off:#x}: expected {expect.hex(' ')}, found {found.hex(' ')}"
    return None


def apply(data, patches, revert):
    """Applies (or reverts) the patches on a copy; returns (copy, log), or (None, message) if any byte does not match."""
    error = check(data, patches, revert)
    if error:
        return None, error
    out = bytearray(data)
    log = []
    for off, orig, new, desc in patches:
        before, after = (new, orig) if revert else (orig, new)
        out[off:off + len(after)] = after
        log.append(f"{'reverted' if revert else 'patched'} @ {off:#x}: {before.hex(' ')} -> {after.hex(' ')}  ({desc})")
    return out, log


def apply_in_place(path, patches, revert):
    """Same as apply() but writes only the patched bytes into the file (the music software binary is ~500 MB)."""
    with open(path, "r+b") as f:
        for off, orig, new, _ in patches:
            expect = new if revert else orig
            f.seek(off)
            found = f.read(len(expect))
            if found != expect:
                raise ValueError(f"at offset {off:#x}: expected {expect.hex(' ')}, found {found.hex(' ')}")
        for off, orig, new, _ in patches:
            f.seek(off)
            f.write(orig if revert else new)
        f.flush()
        os.fsync(f.fileno())


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    revert = "--revert" in sys.argv[3:]
    if os.path.exists(dst) and os.path.samefile(src, dst):
        sys.exit("ABORTED: the output cannot be the same file as the input")
    with open(src, "rb") as f:
        data = f.read()

    state, archs, patches, problems = analyze(data)
    if state is None:
        sys.exit("ABORTED: this binary cannot be patched safely:\n  " + "\n  ".join(problems))
    if revert != (state == "patched"):
        sys.exit("ABORTED: the input is " + ("not patched" if revert else "already patched"))
    out, log = apply(data, patches, revert)
    if out is None:
        sys.exit(f"ABORTED {log}")
    print(f"music software ({', '.join(archs)})")
    print("\n".join(log))
    with open(dst, "wb") as f:
        f.write(out)
    print(f"written {dst}")


if __name__ == "__main__":
    main()
