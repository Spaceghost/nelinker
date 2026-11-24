# nelinker

nelinker is a static ELF linker written in Nelua for x86-64 and AArch64 Linux. It resolves symbols and relocations in ELF objects and Unix archives and writes fixed-address Linux executables or x86-64 relocatable PE32+ UEFI applications. It supports GNU and BSD archives, archive groups, static GOT entries, and x86-64 local-exec TLS. PIE, shared libraries, thin archives, LTO, and custom linker scripts are outside its current scope.

## AArch64 static ELF

Build with the Spaceghost Nelua compiler and a Linux C toolchain:

```sh
mkdir -p build
nelua -r -D NELINKER_HOSTED=true -L src -o build/nelinker src/nelinker.nelua
sh tests/check_aarch64.sh build/nelinker
```

Run the smoke on Linux AArch64, including the
[Fedora Incus environment](https://nelua.tech/compiler/#linux-arm64).
It assembles and runs a static executable. The linker can run on either
Linux host architecture with the hosted build above.

Use `nelinker --aarch64 -o program start.o libprogram.a` for little-endian
AArch64 ELF objects with standard RELA addends. To create an archive with
the same object format, use `nelinker --archive --aarch64 -o libprogram.a
member.o`. The mode supports direct calls and jumps, PC-relative and
absolute addresses, page-relative instruction pairs and static GOT
references. AArch64 TLS and PE/EFI output are outside this mode.


## Build

Use `necc` with a staged standalone Nelibc SDK, including TinyCC. Set `NECC_SYSROOT` to that SDK and run these commands from the repository root. [necc SDK guide](https://spacegho.st/nelua/necc/)

```sh
export NECC_SYSROOT=/path/to/sdk
mkdir -p build
(cd src && NECC_NELINKER= necc --release nelinker.nelua -o ../build/nelinker)
```

The native C backend is also supported for building from this standalone
repository. Run the public `necc` driver from `src` so its module path stays
inside this checkout; the output uses a separate name from the seed build:

```sh
mkdir -p build
(cd src && NECC_NELINKER= necc --native --release -L . nelinker.nelua -o ../build/nelinker-native)
```

Run the archive and firmware checks against the native output. The archive
check also uses the staged SDK to verify real Nelibc archive extraction:

```sh
python3 tests/check_archives.py build/nelinker-native "$NECC_SYSROOT" build
python3 tests/check_efi.py build/nelinker-native
```

## Create and link an archive

Assemble the two example inputs, then use nelinker to create the static library and link its caller. The link trace should include `build/libanswer.a(answer.o)`. The program exits with status 42.

```sh
as --64 -o build/start.o examples/start.s
as --64 -o build/answer.o examples/answer.s
build/nelinker --archive -o build/libanswer.a build/answer.o
build/nelinker -t -o build/demo build/start.o build/libanswer.a
build/demo
echo "$?"
```

Archive mode writes a fresh GNU-format library with a symbol index. Pass every
object in the desired member order. Repeating the command with the same object
bytes and member names produces identical archive bytes, even if the input
timestamps change. Give each member a unique basename. Long names and spaces
are supported. An empty member list creates an empty archive. The writer uses
a 32-bit GNU symbol index and supports archives smaller than 2 GiB. Input
objects must use ordinary ELF section indexes.

Run the archive and relocation checks with Python 3, GNU binutils, and the same SDK.

```sh
python3 tests/check_archives.py build/nelinker "$NECC_SYSROOT" build
```


## Link an x86-64 UEFI application

Use freestanding ELF64 objects with a Microsoft x64 ABI firmware entry point.
The default entry symbol is `efi_main`. Use `-e NAME` for another symbol.
The default preferred image base is `0x400000`. Set `--image-base ADDRESS`
to a 64 KiB aligned address in decimal or hexadecimal. Firmware can relocate
the image when that address is unavailable.
The linker accepts the same ordered objects and static archives as the Linux
mode. Each firmware entry or callback must use the calling convention expected
by its caller.

```sh
build/nelinker --efi-app -o BOOTX64.EFI loader.o support.a
python3 tests/check_efi.py build/nelinker
```

The image separates executable code, read-only data, and writable data.
RIP-relative references retain their displacements when firmware moves the
image. Absolute 64-bit image pointers and populated GOT slots have PE DIR64
base relocations. An undefined weak symbol used as an absolute pointer stays
zero. For x86-64 code that embeds addresses, use RIP-relative instructions
or full-width pointers. EFI mode rejects 32-bit absolute address fields,
local-exec TLS, and PC-relative references to undefined weak symbols.

The test validates the headers, maps the image at another address, applies
its relocation table, and calls the relocated entry on x86-64 Linux.
