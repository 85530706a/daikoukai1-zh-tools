"""Minimal 8086 real-mode emulator, enough to run the TINYPROG unpacker stub of MAIN.EXE.

Usage: PSPSEG=0ff0 python3 -I emu86.py MAIN.EXE OUTDIR

Loads the EXE with its PSP at segment PSPSEG, runs the stub until it jumps
back into the load segment (the real entry point), then writes the first
640 KB of memory to OUTDIR/mem.bin and the CPU state to OUTDIR/state.txt.
apply_patch.py runs it twice with different load segments to recover the
relocation table.
"""
import sys, struct, os

MEM = bytearray(0x110000)
R = [0]*8  # AX CX DX BX SP BP SI DI
S = [0]*4  # ES CS SS DS
AX,CX,DX,BX,SP,BP,SI,DI = range(8)
ES,CS,SS,DS = range(4)
ip = 0
F = {'c':0,'p':0,'a':0,'z':0,'s':0,'t':0,'i':1,'d':0,'o':0}
seg_override = None
rep = None
log = []

def lin(seg, off): return ((seg << 4) + (off & 0xffff)) & 0xfffff
def rb(a): return MEM[a & 0xfffff]
def rw(a): return MEM[a & 0xfffff] | (MEM[(a+1) & 0xfffff] << 8)
def wb(a, v): MEM[a & 0xfffff] = v & 0xff
def ww(a, v): MEM[a & 0xfffff] = v & 0xff; MEM[(a+1) & 0xfffff] = (v >> 8) & 0xff

def r8(i): return (R[i] & 0xff) if i < 4 else (R[i-4] >> 8)
def w8(i, v):
    v &= 0xff
    if i < 4: R[i] = (R[i] & 0xff00) | v
    else: R[i-4] = (R[i-4] & 0xff) | (v << 8)

def fetch():
    global ip
    v = MEM[lin(S[CS], ip)]; ip = (ip + 1) & 0xffff; return v
def fetchw():
    lo = fetch(); return lo | (fetch() << 8)
def sx8(v): return v - 0x100 if v & 0x80 else v

def push(v):
    R[SP] = (R[SP] - 2) & 0xffff; ww(lin(S[SS], R[SP]), v)
def pop():
    v = rw(lin(S[SS], R[SP])); R[SP] = (R[SP] + 2) & 0xffff; return v

def flags_word():
    b = [('c',0),('p',2),('a',4),('z',6),('s',7),('t',8),('i',9),('d',10),('o',11)]
    v = 0xf002
    for k, s in b: v |= F[k] << s
    return v
def set_flags_word(v):
    for k, s in [('c',0),('p',2),('a',4),('z',6),('s',7),('t',8),('i',9),('d',10),('o',11)]:
        F[k] = (v >> s) & 1

def parity(v): return 1 if bin(v & 0xff).count('1') % 2 == 0 else 0
def szp(v, w):
    m = 0xffff if w else 0xff
    v &= m
    F['z'] = int(v == 0); F['s'] = (v >> (15 if w else 7)) & 1; F['p'] = parity(v)

def modrm():
    m = fetch()
    mod, reg, rm = m >> 6, (m >> 3) & 7, m & 7
    if mod == 3: return mod, reg, rm, None
    defseg = DS
    if rm == 0: ea = R[BX] + R[SI]
    elif rm == 1: ea = R[BX] + R[DI]
    elif rm == 2: ea = R[BP] + R[SI]; defseg = SS
    elif rm == 3: ea = R[BP] + R[DI]; defseg = SS
    elif rm == 4: ea = R[SI]
    elif rm == 5: ea = R[DI]
    elif rm == 6:
        if mod == 0: ea = fetchw()
        else: ea = R[BP]; defseg = SS
    else: ea = R[BX]
    if mod == 1: ea += sx8(fetch())
    elif mod == 2: ea += fetchw()
    ea &= 0xffff
    seg = seg_override if seg_override is not None else defseg
    return mod, reg, rm, (S[seg], ea)

def get_rm(mr, w):
    mod, reg, rm, a = mr
    if a is None: return R[rm] if w else r8(rm)
    return rw(lin(*a)) if w else rb(lin(*a))
def set_rm(mr, w, v):
    mod, reg, rm, a = mr
    if a is None:
        if w: R[rm] = v & 0xffff
        else: w8(rm, v)
    else:
        if w: ww(lin(*a), v)
        else: wb(lin(*a), v)

def alu(op, a, b, w):
    m = 0xffff if w else 0xff; sb = 15 if w else 7
    c = F['c']
    if op == 0 or op == 2:  # add adc
        cin = c if op == 2 else 0
        r = a + b + cin
        F['c'] = int(r > m); F['a'] = ((a ^ b ^ r) >> 4) & 1
        F['o'] = (((a ^ r) & (b ^ r)) >> sb) & 1
    elif op == 5 or op == 3 or op == 7:  # sub sbb cmp
        cin = c if op == 3 else 0
        r = a - b - cin
        F['c'] = int(r < 0); F['a'] = ((a ^ b ^ r) >> 4) & 1
        F['o'] = (((a ^ b) & (a ^ r)) >> sb) & 1
    else:
        r = {1: a | b, 4: a & b, 6: a ^ b}[op]
        F['c'] = 0; F['o'] = 0
    r &= m; szp(r, w)
    return r

def shift(op, v, n, w):
    m = 0xffff if w else 0xff; bits = 16 if w else 8; sb = bits - 1
    n &= 0x1f
    if n == 0: return v
    for _ in range(n):
        if op == 0:  # rol
            c = (v >> sb) & 1; v = ((v << 1) | c) & m; F['c'] = c
        elif op == 1:  # ror
            c = v & 1; v = (v >> 1) | (c << sb); F['c'] = c
        elif op == 2:  # rcl
            c = (v >> sb) & 1; v = ((v << 1) | F['c']) & m; F['c'] = c
        elif op == 3:  # rcr
            c = v & 1; v = (v >> 1) | (F['c'] << sb); F['c'] = c
        elif op in (4, 6):  # shl
            F['c'] = (v >> sb) & 1; v = (v << 1) & m
        elif op == 5:  # shr
            F['c'] = v & 1; v >>= 1
        elif op == 7:  # sar
            F['c'] = v & 1; v = (v >> 1) | (v & (1 << sb))
    if op >= 4: szp(v, w)
    F['o'] = ((v >> sb) & 1) ^ F['c'] if op != 1 and op != 3 else ((v >> sb) ^ (v >> (sb-1))) & 1
    return v

# ---- DOS memory model ----
mem_top = 0xa000
alloc_next = None
blocks = {}

def dos_int21():
    global alloc_next, ip
    ah = R[AX] >> 8
    if ah == 0x30: R[AX] = 0x0005; R[BX] = 0; R[CX] = 0; F['c'] = 0
    elif ah == 0x4a:
        F['c'] = 0
    elif ah == 0x48:
        need = R[BX]
        seg = alloc_next; alloc_next += need + 1
        if alloc_next > mem_top: F['c'] = 1; R[AX] = 8; R[BX] = 0
        else: R[AX] = seg; F['c'] = 0
    elif ah == 0x49: F['c'] = 0
    elif ah == 0x35: R[BX] = 0; S[ES] = 0
    elif ah == 0x25: pass
    elif ah == 0x62 or ah == 0x51: R[BX] = PSP
    elif ah == 0x09 or ah == 0x40 or ah == 0x4c:
        raise SystemExit('DOS exit/print ah=%02x' % ah)
    else:
        raise RuntimeError('unhandled int21 ah=%02x at %04x:%04x' % (ah, S[CS], ip))

def interrupt(n):
    global ip
    if n == 0x21: dos_int21()
    elif n == 0x1a: R[CX]=0; R[DX]=0
    else: raise RuntimeError('int %02x' % n)

def far_jump(seg, off, how):
    global ip
    log.append((how, S[CS], ip, seg, off))
    S[CS] = seg; ip = off
    check_oep()

def check_oep():
    if S[CS] == LOAD and len(log) > 1:
        log.append(('stop',0,0,0,0))

def step():
    global ip, seg_override, rep
    seg_override = None; rep = None
    start_ip = ip
    while True:
        op = fetch()
        if op in (0x26, 0x2e, 0x36, 0x3e): seg_override = (op >> 3) & 3; continue
        if op in (0xf2, 0xf3): rep = op; continue
        if op == 0xf0: continue
        break
    if op < 0x40 and (op & 7) < 6:
        aop = op >> 3; t = op & 7; w = t & 1
        if t < 4:
            mr = modrm(); reg = mr[1]
            rv = R[reg] if w else r8(reg); mv = get_rm(mr, w)
            if t & 2: a, b = rv, mv
            else: a, b = mv, rv
            r = alu(aop, a, b, w)
            if aop != 7:
                if t & 2:
                    if w: R[reg] = r
                    else: w8(reg, r)
                else: set_rm(mr, w, r)
        else:
            b = fetchw() if w else fetch()
            a = R[AX] if w else r8(0)
            r = alu(aop, a, b, w)
            if aop != 7:
                if w: R[AX] = r
                else: w8(0, r)
        return
    if op in (0x06, 0x0e, 0x16, 0x1e): push(S[op >> 3]); return
    if op in (0x07, 0x17, 0x1f): S[op >> 3] = pop(); return
    if 0x40 <= op < 0x48:
        c = F['c']; R[op-0x40] = alu(0, R[op-0x40], 1, 1); F['c'] = c; return
    if 0x48 <= op < 0x50:
        c = F['c']; R[op-0x48] = alu(5, R[op-0x48], 1, 1); F['c'] = c; return
    if 0x50 <= op < 0x58:
        v = R[op-0x50]
        if op == 0x54: v = (v - 2) & 0xffff
        push(v); return
    if 0x58 <= op < 0x60: R[op-0x58] = pop(); return
    if op == 0x60:
        t = R[SP]
        for i in range(8): push(t if i == SP else R[i])
        return
    if op == 0x61:
        for i in reversed(range(8)):
            v = pop()
            if i != SP: R[i] = v
        return
    if op == 0x68: push(fetchw()); return
    if op == 0x6a: push(sx8(fetch()) & 0xffff); return
    if 0x70 <= op < 0x80:
        d = sx8(fetch()); cc = op & 0xf
        c = [F['o'], not F['o'], F['c'], not F['c'], F['z'], not F['z'],
             F['c'] or F['z'], not (F['c'] or F['z']), F['s'], not F['s'],
             F['p'], not F['p'], F['s'] != F['o'], F['s'] == F['o'],
             F['z'] or (F['s'] != F['o']), (not F['z']) and F['s'] == F['o']][cc]
        if c: ip = (ip + d) & 0xffff
        return
    if op in (0x80, 0x81, 0x82, 0x83):
        w = op & 1; mr = modrm()
        if op == 0x81: b = fetchw()
        elif op == 0x83: b = sx8(fetch()) & 0xffff
        else: b = fetch()
        r = alu(mr[1], get_rm(mr, w), b, w)
        if mr[1] != 7: set_rm(mr, w, r)
        return
    if op in (0x84, 0x85):
        w = op & 1; mr = modrm()
        alu(4, get_rm(mr, w), R[mr[1]] if w else r8(mr[1]), w); return
    if op in (0x86, 0x87):
        w = op & 1; mr = modrm(); a = get_rm(mr, w)
        set_rm(mr, w, R[mr[1]] if w else r8(mr[1]))
        if w: R[mr[1]] = a
        else: w8(mr[1], a)
        return
    if 0x88 <= op <= 0x8b:
        w = op & 1; mr = modrm()
        if op & 2:
            v = get_rm(mr, w)
            if w: R[mr[1]] = v
            else: w8(mr[1], v)
        else: set_rm(mr, w, R[mr[1]] if w else r8(mr[1]))
        return
    if op == 0x8c: mr = modrm(); set_rm(mr, 1, S[mr[1] & 3]); return
    if op == 0x8d: mr = modrm(); R[mr[1]] = mr[3][1]; return
    if op == 0x8e: mr = modrm(); S[mr[1] & 3] = get_rm(mr, 1); return
    if op == 0x8f: mr = modrm(); set_rm(mr, 1, pop()); return
    if op == 0x90: return
    if 0x91 <= op < 0x98:
        i = op - 0x90; R[AX], R[i] = R[i], R[AX]; return
    if op == 0x98: R[AX] = (R[AX] & 0xff) | (0xff00 if R[AX] & 0x80 else 0); return
    if op == 0x99: R[DX] = 0xffff if R[AX] & 0x8000 else 0; return
    if op == 0x9a:
        off = fetchw(); seg = fetchw(); push(S[CS]); push(ip); far_jump(seg, off, 'callf'); return
    if op == 0x9c: push(flags_word()); return
    if op == 0x9d: set_flags_word(pop()); return
    if op == 0x9e: v = R[AX] >> 8; F['c']=v&1; F['p']=(v>>2)&1; F['a']=(v>>4)&1; F['z']=(v>>6)&1; F['s']=(v>>7)&1; return
    if op == 0x9f: w8(4, flags_word() & 0xff); return
    if 0xa0 <= op <= 0xa3:
        off = fetchw(); seg = seg_override if seg_override is not None else DS
        a = lin(S[seg], off); w = op & 1
        if op < 0xa2:
            if w: R[AX] = rw(a)
            else: w8(0, rb(a))
        else:
            if w: ww(a, R[AX])
            else: wb(a, R[AX])
        return
    if 0xa4 <= op <= 0xaf and op not in (0xa8, 0xa9):
        w = op & 1; sz = 2 if w else 1
        d = -sz if F['d'] else sz
        srcseg = S[seg_override] if seg_override is not None else S[DS]
        kind = op & 0xfe
        while True:
            if rep is not None:
                if R[CX] == 0: break
            if kind == 0xa4:
                v = rw(lin(srcseg, R[SI])) if w else rb(lin(srcseg, R[SI]))
                (ww if w else wb)(lin(S[ES], R[DI]), v)
                R[SI] = (R[SI] + d) & 0xffff; R[DI] = (R[DI] + d) & 0xffff
            elif kind == 0xaa:
                (ww if w else wb)(lin(S[ES], R[DI]), R[AX])
                R[DI] = (R[DI] + d) & 0xffff
            elif kind == 0xac:
                v = rw(lin(srcseg, R[SI])) if w else rb(lin(srcseg, R[SI]))
                if w: R[AX] = v
                else: w8(0, v)
                R[SI] = (R[SI] + d) & 0xffff
            elif kind == 0xa6:
                a = rw(lin(srcseg, R[SI])) if w else rb(lin(srcseg, R[SI]))
                b = rw(lin(S[ES], R[DI])) if w else rb(lin(S[ES], R[DI]))
                alu(7, a, b, w)
                R[SI] = (R[SI] + d) & 0xffff; R[DI] = (R[DI] + d) & 0xffff
            elif kind == 0xae:
                b = rw(lin(S[ES], R[DI])) if w else rb(lin(S[ES], R[DI]))
                alu(7, R[AX] if w else r8(0), b, w)
                R[DI] = (R[DI] + d) & 0xffff
            if rep is None: break
            R[CX] = (R[CX] - 1) & 0xffff
            if kind in (0xa6, 0xae):
                if rep == 0xf3 and not F['z']: break
                if rep == 0xf2 and F['z']: break
        return
    if op in (0xa8, 0xa9):
        w = op & 1; b = fetchw() if w else fetch()
        alu(4, R[AX] if w else r8(0), b, w); return
    if 0xb0 <= op < 0xb8: w8(op - 0xb0, fetch()); return
    if 0xb8 <= op < 0xc0: R[op - 0xb8] = fetchw(); return
    if op in (0xc0, 0xc1, 0xd0, 0xd1, 0xd2, 0xd3):
        w = op & 1; mr = modrm()
        if op in (0xc0, 0xc1): n = fetch()
        elif op in (0xd0, 0xd1): n = 1
        else: n = R[CX] & 0xff
        set_rm(mr, w, shift(mr[1], get_rm(mr, w), n, w)); return
    if op == 0xc2: n = fetchw(); ip = pop(); R[SP] = (R[SP] + n) & 0xffff; return
    if op == 0xc3: ip = pop(); return
    if op in (0xc4, 0xc5):
        mr = modrm(); a = lin(*mr[3])
        R[mr[1]] = rw(a); S[ES if op == 0xc4 else DS] = rw(a + 2); return
    if op in (0xc6, 0xc7):
        w = op & 1; mr = modrm(); set_rm(mr, w, fetchw() if w else fetch()); return
    if op in (0xca, 0xcb):
        n = fetchw() if op == 0xca else 0
        off = pop(); seg = pop(); R[SP] = (R[SP] + n) & 0xffff
        far_jump(seg, off, 'retf'); return
    if op == 0xcd: interrupt(fetch()); return
    if op == 0xcf:
        off = pop(); seg = pop(); set_flags_word(pop()); far_jump(seg, off, 'iret'); return
    if op == 0xe2:
        d = sx8(fetch()); R[CX] = (R[CX] - 1) & 0xffff
        if R[CX]: ip = (ip + d) & 0xffff
        return
    if op in (0xe0, 0xe1):
        d = sx8(fetch()); R[CX] = (R[CX] - 1) & 0xffff
        if R[CX] and (F['z'] == (op == 0xe1)): ip = (ip + d) & 0xffff
        return
    if op == 0xe3:
        d = sx8(fetch())
        if R[CX] == 0: ip = (ip + d) & 0xffff
        return
    if op == 0xe8: d = fetchw(); push(ip); ip = (ip + d) & 0xffff; return
    if op == 0xe9: d = fetchw(); ip = (ip + d) & 0xffff; return
    if op == 0xea: off = fetchw(); seg = fetchw(); far_jump(seg, off, 'jmpf'); return
    if op == 0xeb: d = sx8(fetch()); ip = (ip + d) & 0xffff; return
    if op == 0xf5: F['c'] ^= 1; return
    if op in (0xf6, 0xf7):
        w = op & 1; mr = modrm(); sub = mr[1]; v = get_rm(mr, w)
        if sub in (0, 1): alu(4, v, fetchw() if w else fetch(), w); return
        if sub == 2: set_rm(mr, w, ~v); return
        if sub == 3: set_rm(mr, w, alu(5, 0, v, w)); F['c'] = int(v != 0); return
        if sub == 4:
            if w: r = R[AX] * v; R[AX] = r & 0xffff; R[DX] = r >> 16; F['c'] = F['o'] = int(R[DX] != 0)
            else: r = r8(0) * v; R[AX] = r & 0xffff; F['c'] = F['o'] = int(r >> 8 != 0)
            return
        if sub == 6:
            if w:
                n = (R[DX] << 16) | R[AX]; q, rr = divmod(n, v); R[AX] = q & 0xffff; R[DX] = rr
            else:
                q, rr = divmod(R[AX], v); R[AX] = (rr << 8) | (q & 0xff)
            return
        raise RuntimeError('f6/f7 sub %d' % sub)
    if op == 0xf8: F['c'] = 0; return
    if op == 0xf9: F['c'] = 1; return
    if op == 0xfa: F['i'] = 0; return
    if op == 0xfb: F['i'] = 1; return
    if op == 0xfc: F['d'] = 0; return
    if op == 0xfd: F['d'] = 1; return
    if op == 0xfe:
        mr = modrm(); c = F['c']
        set_rm(mr, 0, alu(0 if mr[1] == 0 else 5, get_rm(mr, 0), 1, 0)); F['c'] = c; return
    if op == 0xff:
        mr = modrm(); sub = mr[1]
        if sub in (0, 1):
            c = F['c']; set_rm(mr, 1, alu(0 if sub == 0 else 5, get_rm(mr, 1), 1, 1)); F['c'] = c; return
        if sub == 2: t = get_rm(mr, 1); push(ip); ip = t; return
        if sub == 3:
            a = lin(*mr[3]); push(S[CS]); push(ip); far_jump(rw(a + 2), rw(a), 'callf'); return
        if sub == 4: ip = get_rm(mr, 1); return
        if sub == 5: a = lin(*mr[3]); far_jump(rw(a + 2), rw(a), 'jmpf'); return
        if sub == 6: push(get_rm(mr, 1)); return
    raise RuntimeError('unhandled opcode %02x at %04x:%04x' % (op, S[CS], start_ip))

# ---- loader ----
data = open(sys.argv[1], 'rb').read()
outdir = sys.argv[2]
h = struct.unpack('<14H', data[:28])
last, pages, nrel, hdr, minalloc, maxalloc, ss0, sp0, csum, ip0, cs0, relo = h[1:13]
size = pages * 512 - (512 - last if last else 0)
image = data[hdr*16:size]
PSP = int(os.environ.get('PSPSEG','0ff0'),16)
LOAD = PSP + 0x10
MEM[LOAD*16:LOAD*16+len(image)] = image
for i in range(nrel):
    o, s = struct.unpack('<HH', data[relo + i*4: relo + i*4 + 4])
    a = (LOAD + s) * 16 + o
    ww(a, (rw(a) + LOAD) & 0xffff)
# PSP
ww(PSP*16, 0x20cd); ww(PSP*16 + 2, mem_top)
img_end_para = LOAD + (len(image) + 15) // 16
alloc_next = max(img_end_para + minalloc, (ss0 + LOAD) + (sp0 + 15)//16) + 16
S[CS] = (cs0 + LOAD) & 0xffff; ip = ip0
S[SS] = (ss0 + LOAD) & 0xffff; R[SP] = sp0
S[DS] = S[ES] = PSP
R[AX] = 0
print('image %d bytes, load seg %04x, entry %04x:%04x, ss:sp %04x:%04x, minalloc %x'
      % (len(image), LOAD, S[CS], ip, S[SS], R[SP], minalloc))

maxsteps = int(sys.argv[3]) if len(sys.argv) > 3 else 200_000_000
n = 0
try:
    while n < maxsteps:
        step(); n += 1
        if len(log) and log[-1] and log[-1][0] == 'stop': break
except SystemExit as e:
    print('stopped:', e)
except Exception as e:
    print('error:', e)
print('steps', n, 'at %04x:%04x' % (S[CS], ip))
for l in log[:-1] if log and log[-1][0]=='stop' else log: print('%s from %04x:%04x -> %04x:%04x' % l)
print('regs', ['%04x' % v for v in R], 'segs', ['%04x' % v for v in S])
os.makedirs(outdir, exist_ok=True)
open(os.path.join(outdir, 'mem.bin'), 'wb').write(MEM[:0xa0000])
open(os.path.join(outdir, 'state.txt'), 'w').write(repr({'R': R, 'S': S, 'ip': ip, 'LOAD': LOAD, 'PSP': PSP, 'log': log}))
