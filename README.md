# nelinker

nelinker is a static ELF linker written in Nelua for x86-64 Linux. It resolves symbols and relocations in ELF objects and Unix archives and writes fixed-address executables. It supports GNU and BSD archives, archive groups, static GOT entries, and local-exec TLS. PIE, shared libraries, thin archives, LTO, and custom linker scripts are outside its current scope.

## Build

Use `necc` with a staged standalone Nelibc SDK, including TinyCC. Set `NECC_SYSROOT` to that SDK and run these commands from the repository root. [necc SDK guide](https://spacegho.st/nelua/necc/)

```sh
export NECC_SYSROOT=/path/to/sdk
mkdir -p build
(cd src && NECC_NELINKER= necc --release nelinker.nelua -o ../build/nelinker)
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
