#!/bin/sh
set -eu
linker=$(realpath "${1:?usage: check_aarch64.sh /path/to/nelinker}")
tmp=$(mktemp -d)
trap 'rm -r "$tmp"' EXIT
cd "$tmp"

cat > start.s <<'ASM'
.text
.global _start
.type _start,%function
_start:
    bl answer
    adr x1, _start
    adrp x2, saved
    add x2, x2, :lo12:saved
    ldr x2, [x2]
    cmp x1, x2
    b.ne .Lfail
    adrp x1, value
    ldr w1, [x1, :lo12:value]
    add w0, w0, w1
    b exit
.Lfail:
    mov w0, #1
    b exit

.data
.p2align 3
saved:
    .xword _start
ASM
cat > answer.c <<'C'
extern int value;
int answer(void) { return (value + 1) * 2; }
C
cat > value.s <<'ASM'
.data
.global value
.p2align 2
value:
    .word 13

.text
.global exit
.type exit,%function
exit:
    mov x8, #93
    svc #0
ASM
gcc -c start.s value.s
gcc -fPIC -ffreestanding -c answer.c
"$linker" --archive --aarch64 -o libfixture.a answer.o value.o
"$linker" --aarch64 -o program start.o libfixture.a
readelf -h program | grep -q AArch64
if ./program; then exit 1; else test "$?" -eq 41; fi
if "$linker" -o bad start.o 2>error; then exit 1; fi
grep -q 'x86-64 ELF relocatable' error
test ! -e bad
echo 'AArch64 static ELF, archive extraction, branches, GOT and data relocations passed'
