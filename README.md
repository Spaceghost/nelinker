# nelinker

nelinker is a static ELF linker written in Nelua for x86-64 Linux. It resolves symbols and relocations in ELF objects and Unix archives and writes fixed-address executables. It supports GNU and BSD archives, archive groups, static GOT entries, and local-exec TLS. PIE, shared libraries, thin archives, LTO, and custom linker scripts are outside its current scope.

## Build

Use `necc` with a staged standalone Nelibc SDK, including TinyCC. Set `NECC_SYSROOT` to that SDK and run these commands from the repository root. [necc SDK guide](https://spacegho.st/nelua/necc/)

```sh
export NECC_SYSROOT=/path/to/sdk
mkdir -p build
(cd src && NECC_NELINKER= necc --release nelinker.nelua -o ../build/nelinker)
```

## Try an archive

With GNU binutils installed, build the included example. The link trace should include `build/libanswer.a(answer.o)`. The program exits with status 42.

```sh
as --64 -o build/start.o examples/start.s
as --64 -o build/answer.o examples/answer.s
ar rcs build/libanswer.a build/answer.o
build/nelinker -t -o build/demo build/start.o build/libanswer.a
build/demo
echo "$?"
```

Run the archive and relocation checks with Python 3, GNU binutils, and the same SDK.

```sh
python3 tests/check_archives.py build/nelinker "$NECC_SYSROOT" build
```
