#!/usr/bin/env python3
"""Verify native archive selection, x86-64 relocation modes and distro runtime use."""
import argparse
import hashlib
import os
import struct
import subprocess
import tempfile
from pathlib import Path

def invoke(cmd, *, expect=0, env=None, limit=15):
    p = subprocess.run(list(map(str, cmd)), capture_output=True, env=env, timeout=limit)
    if p.returncode != expect:
        raise RuntimeError(f"{cmd}\nexpected {expect}, got {p.returncode}\n{p.stdout.decode(errors='replace')}\n{p.stderr.decode(errors='replace')}")
    return p

def assemble(path, text, relax=None):
    # Keep the assembler's optional ISA usage note out of these freestanding fixtures.
    flags = ["--64", "-mx86-used-note=no"]
    if relax is not None:
        flags += ["-mrelax-relocations=" + ("yes" if relax else "no")]
    s = path.with_suffix(".s")
    s.write_text(text+'\n.section .note.GNU-stack,"",@progbits\n')
    invoke(["as", *flags, s, "-o", path])
    return path

def make_archive(path, *members, indexed=True):
    invoke(["ar", "rcsD" if indexed else "rcSD", path, *members])
    return path

def header(name, size):
    return (f"{name:<16}{0:<12}{0:<6}{0:<6}{'644':<8}{size:<10}".encode()+b"\x60\n")

def special_archive(path, member, exported, flavor):
    contents=member.read_bytes()
    if flavor=="gnu64":
        names=exported.encode()+b"\0"
        count=16+len(names)
        off=8+60+count+count%2
        idx=struct.pack(">QQ",1,off)+names
        raw=header("/SYM64/",len(idx))+idx+b"\n"*(len(idx)%2)
        label="provider.o/"
    else:
        name=b"a BSD member with spaces.o\0\0"
        contents=name+contents
        label="#1/"+str(len(name))
        raw=b""
    path.write_bytes(b"!<arch>\n"+raw+header(label,len(contents))+contents+b"\n"*(len(contents)%2))
    return path

def basic_fixtures(d, sdk):
    start=assemble(d/"start.o", ".text\n.globl _start\n_start:\n call work\n mov %eax,%edi\n mov $60,%eax\n syscall")
    sys=assemble(d/"system-call.o", """.text
.globl work
work:
 sub $8,%rsp
 mov $39,%edi
 xor %esi,%esi
 xor %edx,%edx
 xor %ecx,%ecx
 call nelibc_raw_syscall3
 add $8,%rsp
 test %rax,%rax
 jle 1f
 mov $42,%eax
 ret
1: xor %eax,%eax
 ret""")
    def fn(file, name, code, weak=False):
        return assemble(d/(file+".o"), ".text\n"+(".weak " if weak else ".globl ")+name+"\n"+name+":\n "+code+"\n ret")
    work=fn("work","work","call middle\n add $1,%eax")
    mid=fn("a long archive member with spaces","middle","call base\n add $1,%eax")
    base=fn("base","base","mov $40,%eax")
    unused=fn("unused","unused","call should_never_be_pulled")
    simple=fn("standalone","work","mov $42,%eax")
    return start,sys,work,mid,base,unused,simple

def main():
    p=argparse.ArgumentParser()
    p.add_argument("linker",type=Path);p.add_argument("sdk",type=Path);p.add_argument("scratch",type=Path)
    a=p.parse_args(); link=a.linker.resolve();sdk=a.sdk.resolve()
    d=Path(tempfile.mkdtemp(prefix="archive-check-",dir=a.scratch.resolve()))
    os.chdir(d)
    empty_path={**os.environ,"PATH":"/nonexistent"}
    def binary(name,cmd,code=42):
        out=d/name
        r=invoke([link,"-t","-o",out,*cmd],env=empty_path)
        invoke([out],expect=code,env=empty_path)
        return r.stdout.decode().splitlines()
    def reject(cmd,reason):
        out=d/"protected-output";out.write_bytes(b"previous valid output")
        p=invoke([link,"-o",out,*cmd],expect=1,env=empty_path)
        assert reason in p.stderr.decode(),p.stderr
        assert out.read_bytes()==b"previous valid output"
    start,sys,work,mid,base,unused,simple=basic_fixtures(d,sdk)
    lib=sdk/"libnelibc.a";builtins=sdk/"tcc/libtcc1.a"
    member=invoke(["ar","p",lib,"syscall-asm.o"]).stdout
    (d/"extracted.o").write_bytes(member)
    trace=binary("direct",[start,sys,"--tcc-relocs","-L",sdk,"-lnelibc"])
    assert [x for x in trace if x.startswith(str(lib)+"(")]==[str(lib)+"(syscall-asm.o)"],trace
    binary("explicit",[start,sys,"--tcc-relocs",d/"extracted.o"])
    assert (d/"direct").read_bytes()==(d/"explicit").read_bytes()
    print("Real libnelibc.a selected syscall-asm.o alone, ran getpid and matched explicit loading.")
    chain=make_archive(d/"libchain.a",unused,base,mid,work)
    trace=binary("reverse",[start,"-l:libchain.a","-L"+str(d)])
    assert [x.split("(")[-1] for x in trace[1:]]==["work.o)",mid.name+")","base.o)"]
    reject([chain,start],"undefined symbol work")
    binary("repeat",[chain,start,chain])
    liba=make_archive(d/"liba.a",work,base); libb=make_archive(d/"libb.a",mid)
    reject([start,liba,libb],"undefined symbol base")
    binary("group",[start,"--start-group",liba,libb,"--end-group"])
    binary("nested-group",[start,"--start-group",liba,"--start-group",libb,"--end-group","--end-group"])
    binary("archive-again",[start,liba,libb,liba])
    reject([start,"--start-group",liba],"unmatched --start-group")
    wave=assemble(d/"weak-ref.o",".text\n.globl work\n.weak optional\nwork:\n movptr:\n movabs $optional,%rax\n test %rax,%rax\n jne 1f\n mov $42,%eax\n ret\n1: mov (%rax),%eax\n ret")
    opt=assemble(d/"option.o",".data\n.globl optional\noptional: .long 17")
    libopt=make_archive(d/"libopt.a",opt)
    assert len(binary("weak",[start,wave,libopt]))==2
    binary("forced",[start,wave,"-u","optional",libopt],17)
    binary("whole-reset",[start,wave,"--whole-archive",libopt,"--no-whole-archive",chain],17)
    reject([start,simple,"--whole-archive",chain],"duplicate definition")
    entry=assemble(d/"entry.o",".text\n.globl first\nfirst:\n mov $42,%edi\n mov $60,%eax\n syscall")
    binary("entry-only",["-e","first",make_archive(d/"entry.a",entry)])
    alt=assemble(d/"alt.o",".text\n.globl work\nwork: mov $17,%eax\n ret")
    weak=assemble(d/"weak.o",".text\n.weak work\nwork: mov $17,%eax\n ret")
    binary("first-definition",[start,make_archive(d/"dups.a",simple,alt)])
    binary("weak-provider",[start,make_archive(d/"providers.a",weak,simple)],17)
    binary("strong-after-weak",[start,make_archive(d/"first-weak.a",weak),simple])
    host_source=d/"host.c";host_source.write_text("int work(void) { return 42; }\n")
    host_obj=d/"host.o"
    invoke(["gcc","-O2","-fcf-protection=none","-c",host_source,"-o",host_obj])
    binary("host-archive",[start,make_archive(d/"libhost.a",host_obj)])
    required=assemble(d/"required-note.o",'.section .note.gnu.property,"a",@note\n.balign 8\n.long 4,16,5\n.asciz "GNU"\n.long 0xc0000002,4,1,0')
    reject([start,simple,required],"unsupported GNU object property")
    print("Ordered search, reverse dependencies, nested groups, first providers, -u, -e and weak/strong extraction passed.")
    empty=make_archive(d/"empty.a")
    noidx=make_archive(d/"noindex.a",base,mid,work,indexed=False)
    binary("noindex",[start,empty,noidx])
    binary("gnu64",[start,special_archive(d/"wide-index.a",simple,"work","gnu64")])
    binary("bsd-inline",[start,special_archive(d/"bsd.a",simple,"work","bsd")])
    short_archive = d/"inline-names.a"
    payload = simple.read_bytes()
    for field, display in [("Ab1.o/", "Ab1.o"), ("7no-slash", "7no-slash"), ("1234567890abcdef", "1234567890abcdef")]:
        short_archive.write_bytes(b"!<arch>\n" + header(field, len(payload)) + payload + b"\n"*(len(payload)%2))
        lines = binary("inline-name", [start, short_archive])
        assert f"{short_archive}({display})" in lines, lines
    print("Inline archive names with padding, a slash, or all 16 bytes retain their exact trace spelling.")
    blob=bytearray(chain.read_bytes());bad=d/"broken.a"
    malformed=[(b"!<thin>\n","thin archives"),(b"!<arch>\nlo","member header")]
    wrong=blob.copy();wrong[72:76]=struct.pack(">I",9);malformed.append((wrong,"outside a member header"))
    wrong=blob.copy();wrong[68:72]=b"\xff"*4;malformed.append((wrong,"exceeds bounds"))
    idxbytes=b"\x00\x00\x00\x01"+struct.pack(">I",80)+b"xxxx"
    malformed.append((b"!<arch>\n"+header("/",len(idxbytes))+idxbytes+header("a.o/",0),"invalid symbol name"))
    malformed.append((b"!<arch>\n"+header("odd/",1)+b"x","missing archive member padding"))
    malformed.append((b"!<arch>\n"+header("/99",0),"missing archive long-name table"))
    for raw,reason in malformed:
        bad.write_bytes(raw);reject([start,bad],reason)
    alias=d/"alias.a";os.link(chain,alias)
    before=hashlib.sha256(chain.read_bytes()).digest()
    p=invoke([link,"-o",alias,start,chain],expect=1)
    assert b"output is also an input" in p.stderr and before==hashlib.sha256(chain.read_bytes()).digest()
    print("GNU 32/64-bit indexes, GNU and BSD long names, unindexed and empty archives, corrupt records and output preservation passed.")
    absolute=assemble(d/"absolute.o",".text\n.globl work\nwork: movabs $values,%rax\n mov (%rax),%eax\n ret\n.data\nvalues: .long 42,0,17")
    data=bytearray(absolute.read_bytes())
    shoff=struct.unpack_from("<Q",data,40)[0];num=struct.unpack_from("<H",data,60)[0]
    sections=[struct.unpack_from("<IIQQQQIIQQ",data,shoff+64*i) for i in range(num)]
    rela=next(s for s in sections if s[1]==4)
    rel_off,r_info,_=struct.unpack_from("<QQq",data,rela[4]);assert r_info&0xffffffff==1
    struct.pack_into("<Q",data,sections[rela[7]][4]+rel_off,8);absolute.write_bytes(data)
    binary("standard-field",[start,absolute]);binary("tinycc-field",[start,"--tcc-relocs",absolute],17)
    binary("switch-back",["--tcc-relocs",start,"--elf-relocs",absolute])
    print("Standard RELA ignores the in-place field, and the scoped TinyCC mode honors it.")
    for relax in (False,True):
        obj=assemble(d/("got-%s.o"%relax),""".text
.globl work
.weak absent
work:
 mov value@GOTPCREL(%rip),%rax
 mov (%rax),%edx
 mov absent@GOTPCREL(%rip),%rax
 test %rax,%rax
 jne 1f
 call *helper@GOTPCREL(%rip)
 ret
1: xor %eax,%eax
 ret
.globl helper
helper: mov %edx,%eax
 ret
.data
.globl value
value: .long 42""",relax)
        binary("got-%s"%relax,[start,obj])
    print("Static and relaxable GOT references resolved local calls, exported data and an undefined weak target.")
    tls=assemble(d/"tls.o",""".section .tdata,"awT",@progbits
.balign 64
.globl t_initial
.type t_initial,@tls_object
t_initial: .long 19
.section .tbss,"awT",@nobits
.balign 32
.type t_zero,@tls_object
t_zero: .zero 4
.text
.globl tls_state
tls_state:
 mov %fs:0,%rax
 add $t_initial@tpoff,%rax
 test $63,%eax
 jne 1f
 mov %fs:t_initial@tpoff,%eax
 add %fs:t_zero@tpoff,%eax
 mov %edi,%fs:t_zero@tpoff
 ret
1: mov $-1,%eax
 ret""")
    c=d/"runtime.c";c.write_text("""#include <pthread.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
long tls_state(int);
static void *worker(void *p) {
    if (tls_state(7) != 19 || tls_state(7) != 26) return (void*)1;
    errno = 27;
    return p;
}
int main(void) {
    pthread_t tid;
    void *answer = 0;
    int *a = malloc(sizeof *a), *b = malloc(sizeof *b);
    if (!a || !b || a == b) return 1;
    *a = 19; *b = 23;
    if (tls_state(8) != 19 || tls_state(8) != 27) return 2;
    errno = 37;
    if (pthread_create(&tid,0,worker,a) || pthread_join(tid,&answer)) return 3;
    if (answer != a || tls_state(8) != 27 || errno != 37) return 4;
    printf("%d\\\\n",*a+*b);
    free(a); free(b);
    return 42;
}
""".replace("\\\\n","\\n"))
    cobj=d/"runtime.o"
    invoke([sdk/"tcc/tcc","-B"+str(sdk/"tcc"),"-nostdinc","-DNELIBC_STANDALONE","-I"+str(sdk/"include"),"-I"+str(sdk/"tcc/include"),"-I"+str(sdk/"uapi"),"-c",c,"-o",cobj])
    for whole in (False, True):
        inputs=[tls,"--tcc-relocs",cobj]
        inputs+=["--whole-archive",lib,"--no-whole-archive"] if whole else [lib]
        out="runtime-"+str(whole);binary(out,[*inputs,builtins])
        assert invoke([d/out],expect=42).stdout==b"42\n"
        ph=invoke(["readelf","-lW",d/out]).stdout.decode().splitlines()
        assert sum(x.split()[:1]==["TLS"] for x in ph)==1
        assert any(x.split()[:1]==["TLS"] and x.endswith("0x40") for x in ph)
        assert not any(x.split()[:1]==["INTERP"] or "RWE " in x for x in ph)
    print("Whole and lazy SDK programs both passed allocator, stdio, errno, 64-byte aligned initialized/zero TLS and thread isolation.")
    print("PASS", d)
if __name__=="__main__": main()

