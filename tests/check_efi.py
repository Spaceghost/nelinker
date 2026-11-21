"""Check PE32+ EFI output on Linux x86-64 with Python 3 and GNU as."""
import ctypes
import mmap
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

LINKER = Path(sys.argv[1]).resolve()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def obj(name, asm):
    run("as", "--64", "-o", name, "-", input=asm.encode())


def read_image(path):
    image = path.read_bytes()
    assert image[:2] == b"MZ"
    header, = struct.unpack_from("<I", image, 0x3c)
    assert image[header:header + 4] == b"PE\0\0"
    machine, count, stamp, _, _, optlen, flags = struct.unpack_from("<HHIIIHH", image, header + 4)
    assert machine == 0x8664 and stamp == 0 and flags & 0x22 == 0x22 and flags & 1 == 0
    opt = header + 24
    assert struct.unpack_from("<H", image, opt)[0] == 0x20b
    entry, = struct.unpack_from("<I", image, opt + 16)
    preferred, = struct.unpack_from("<Q", image, opt + 24)
    va_align, raw_align = struct.unpack_from("<II", image, opt + 32)
    size, header_size = struct.unpack_from("<II", image, opt + 56)
    assert size % va_align == 0 and header_size % raw_align == 0
    assert struct.unpack_from("<H", image, opt + 68)[0] == 10
    assert struct.unpack_from("<I", image, opt + 108)[0] >= 6
    table, length = struct.unpack_from("<II", image, opt + 152)
    # Map at the address the OS chooses, apply DIR64 entries as firmware does,
    # and call the entry. This fixture uses no parameters or ABI-specific calls.
    loaded = mmap.mmap(-1, size, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    base = ctypes.addressof(ctypes.c_char.from_buffer(loaded))
    assert base != preferred
    loaded[:header_size] = image[:header_size]
    previous_rva = header_size
    previous_raw = header_size
    sections = {}
    for index in range(count):
        where = opt + optlen + index * 40
        name = image[where:where + 8].rstrip(b"\0").decode()
        vsize, rva, rawsize, raw = struct.unpack_from("<IIII", image, where + 8)
        flag, = struct.unpack_from("<I", image, where + 36)
        assert rva >= previous_rva and rva % va_align == 0 and rva + vsize <= size
        assert rawsize % raw_align == 0
        if rawsize:
            assert raw >= previous_raw and raw % raw_align == 0 and raw + rawsize <= len(image)
            loaded[rva:rva + rawsize] = image[raw:raw + rawsize]
            previous_raw = raw + rawsize
        previous_rva = rva + vsize
        assert flag & 0xa0000000 != 0xa0000000  # no section is writable and executable
        sections[name] = (rva, vsize, rawsize, flag)
    assert sections[".text"][0] <= entry < sum(sections[".text"][:2])
    assert sections[".reloc"][:2] == (table, length)
    fixups = set()
    pos = table
    while pos < table + length:
        page, block_size = struct.unpack_from("<II", loaded, pos)
        assert not page % 4096 and block_size >= 8 and not block_size % 4
        assert pos + block_size <= table + length
        for off in range(pos + 8, pos + block_size, 2):
            tag, = struct.unpack_from("<H", loaded, off)
            if tag == 0:
                continue
            assert tag >> 12 == 10
            place = page + (tag & 4095)
            assert place not in fixups
            pointer, = struct.unpack_from("<Q", loaded, place)
            assert preferred <= pointer < preferred + size
            struct.pack_into("<Q", loaded, place, base + (pointer - preferred))
            fixups.add(place)
        pos += block_size
    assert pos == table + length
    result = ctypes.CFUNCTYPE(ctypes.c_int)(base + entry)()
    loaded.close()
    return result, sections, fixups


def reject(output, asm, error):
    obj("bad.o", asm)
    saved = output.read_bytes()
    result = subprocess.run([LINKER, "--efi-app", "-o", output, "bad.o"], capture_output=True)
    assert result.returncode != 0 and error in result.stderr, result.stderr
    assert output.read_bytes() == saved


def check():
    # The first two references share a single GOT slot. The absolute data
    # pointers live on different pages. A large BSS tail must stay off disk.
    obj("main.o", """
        .text
        .globl efi_main
        efi_main:
            mov value@GOTPCREL(%rip), %rax
            mov value@GOTPCREL(%rip), %rdx
            cmp %rdx, %rax
            jne broken
            mov pointer_a(%rip), %rdx
            cmp %rdx, %rax
            jne broken
            mov pointer_b(%rip), %rdx
            sub $4, %rdx
            cmp %rdx, %rax
            jne broken
            cmpq $0, missing_ptr(%rip)
            jne broken
            cmpq $0, scratch(%rip)
            jne broken
            mov (%rax), %eax
            ret
        broken: mov $-1, %eax
            ret
        .data
        pointer_a: .quad value
        .weak missing
        missing_ptr: .quad missing
        .balign 4096
        pointer_b: .quad value+4
        .bss
        scratch: .skip 65536
        """)
    obj("value.o", ".section .rodata\n.globl value\nvalue: .long 42, 17\n")
    run(LINKER, "--archive", "-o", "helpers.a", "value.o")
    output = Path("app.efi")
    run(LINKER, "--efi-app", "-o", output, "main.o", "helpers.a")
    first = output.read_bytes()
    result, sections, fixups = read_image(output)
    assert result == 42
    assert len(fixups) == 3 and len({address // 4096 for address in fixups}) == 3
    assert sections[".data"][1] > sections[".data"][2] + 32768
    assert len(first) < 65536
    run(LINKER, "--efi-app", "-o", output, "main.o", "helpers.a")
    assert first == output.read_bytes()

    obj("pic.o", ".text\n.globl custom\ncustom: mov $7, %eax\nret\n")
    run(LINKER, "-e", "custom", "--efi-app", "-o", output, "pic.o")
    assert read_image(output)[::2] == (7, set())
    entry = ".text\n.globl efi_main\nefi_main:\n"
    reject(output, entry + "mov $efi_main, %eax\nret\n", b"unsupported EFI x86-64 relocation")
    reject(output, entry + "ret\n.section .tdata,\"awT\",@progbits\n.long 1\n", b"TLS")
    reject(output, entry + "leaq missing(%rip), %rax\nret\n.weak missing\n", b"undefined weak")
    print("PE32+ EFI entry, permissions, lazy archive, GOT, two data pages, BSS, weak zero,")
    print("DIR64 rebasing and execution at a different base, PIC-only image and rejected fixups passed")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        check()
