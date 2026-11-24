#!/bin/sh
# Relink the Nelibc startup, TLS and kernel-thread fixture with Nelinker.
set -eu
linker=$(realpath "${1:?usage: check_aarch64_nelibc.sh NELINKER NELIBC}")
libc=$(realpath "${2:?supply the Nelibc checkout}")
tmp=$(mktemp -d)
trap 'rm -r "$tmp"' EXIT
cd "$tmp"

make -C "$libc" OUT="$tmp" smoke-freestanding-aarch64-gcc
gcc -O2 -ffreestanding -fno-pie -fno-builtin -fno-stack-protector \
    -ftls-model=local-exec -DNELIBC_STANDALONE -DNELIBC_ARM_TEST_TLS \
    -I"$libc/include" -c "$libc/tools/freestanding_aarch64_smoke.c" -o smoke.o
"$linker" --aarch64 -o program smoke.o start-linux-aarch64.o boot-linux-aarch64.o \
    clone-linux-aarch64.o linux-aarch64-raw-syscall.o linux-aarch64-ops.o \
    stat-linux-aarch64.o
readelf -lW program > headers
grep -q ' TLS ' headers
ln -s . link
NELIBC_ARM_SMOKE=present ./program a
echo 'Nelinker AArch64 TLS, constructors and kernel-thread smoke passed'
