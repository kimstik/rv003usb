#!/usr/bin/env python3
"""engine16_cyc.py - instruction cost annotator for the 24 MHz / 16-cycle competition.

Checks a claimed cycle ledger against the instruction stream that actually
assembles.  It does NOT resolve control flow: it annotates straight-line blocks
between labels and reports min/max, because a taken branch on this part costs
2-3 cycles and the source says the ambiguity depends on alignment and on the
preceding instruction (CHIP_FACTS_XIAMATSU.md §1).  A tool that printed one
number would be lying about that.

Cost model: doc/py32/ENGINE16_SPEC.md §2, measured at Flash Latency = 0, which
IS the 24 MHz operating point.  Costs depend on where the code executes from and
the columns swap, so --exec is mandatory.

Usage:
  arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c e.S -o e.o
  tools/engine16_cyc.py e.o --exec ram
  tools/engine16_cyc.py e.o --exec flash --budget 16
"""
import argparse, re, subprocess, sys

# (min, max) cycles.  Ranges are real hardware ambiguity, not tool uncertainty.
def cost(mnem, ops, exec_from, ioport_regs=()):
    m = mnem.lower()
    # objdump prints width suffixes (b.n, beq.w); they are not part of the
    # mnemonic for costing purposes.  Missing this scores an unconditional
    # branch as 1 cycle instead of 2-3.
    if m.endswith('.n') or m.endswith('.w'):
        m = m[:-2]
    ram_data  = (2, 2) if exec_from == 'ram' else (4, 4)
    lit_pool  = (4, 4) if exec_from == 'ram' else (2, 2)

    if m in ('push', 'pop'):
        n = len(re.findall(r'[a-z0-9]+', ops.split('{')[-1].split('}')[0])) if '{' in ops else 1
        base = 2 if exec_from == 'ram' else 4
        return (base + max(0, n - 1),) * 2
    if m.startswith('ldm') or m.startswith('stm'):
        n = len(re.findall(r'[a-z0-9]+', ops.split('{')[-1].split('}')[0])) if '{' in ops else 1
        base = 2 if exec_from == 'ram' else 4
        return (base + max(0, n - 1),) * 2
    if m.startswith('ldr') or m.startswith('str'):
        if '[pc' in ops:
            return lit_pool
        # A GPIO access over the IOPORT bus is 1 cycle in both columns.  The
        # encoding does not say what the base register points at, so the caller
        # must name the registers that hold a GPIO base (--ioport).  Getting
        # this wrong is not cosmetic: the IDR read is the most frequent
        # operation in the bit cell.
        mo = re.search(r'\[(\w+)', ops)
        if mo and mo.group(1).lower() in ioport_regs:
            return (1, 1)
        return ram_data
    if m == 'bl':   return (4, 4)
    if m in ('bx', 'blx'): return (3, 3)
    if m == 'b':    return (2, 3)          # unconditional, always taken
    if re.fullmatch(r'b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)', m):
        return (1, 3)                      # 1 not taken, 2-3 taken
    return (1, 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('obj')
    ap.add_argument('--exec', dest='ex', required=True, choices=['flash', 'ram'],
                    help='where this code executes from - the cost columns swap')
    ap.add_argument('--budget', type=int, default=None,
                    help='flag any block whose max exceeds this (e.g. 16)')
    ap.add_argument('--section', default=None)
    ap.add_argument('--ioport', default='',
                    help='comma-separated registers holding a GPIO base, e.g. '
                         '"r3,r9" - loads/stores through them cost 1 cycle')
    a = ap.parse_args()

    cmd = ['arm-none-eabi-objdump', '-d']
    if a.section: cmd += ['-j', a.section]
    cmd.append(a.obj)
    try:
        dis = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        sys.exit(f'objdump failed: {e}')

    ioport = tuple(r.strip().lower() for r in a.ioport.split(',') if r.strip())

    label_re = re.compile(r'^([0-9a-f]+) <([^>]+)>:')
    insn_re  = re.compile(r'^\s+([0-9a-f]+):\s+([0-9a-f ]+?)\s+(\S+)\s*(.*)$')

    blocks, cur = [], None
    for line in dis.splitlines():
        mo = label_re.match(line)
        if mo:
            cur = {'label': mo.group(2), 'addr': mo.group(1), 'insns': []}
            blocks.append(cur); continue
        mo = insn_re.match(line)
        if mo and cur is not None:
            addr, _, mnem, ops = mo.groups()
            ops = ops.split(';')[0].split('@')[0].strip()
            if mnem.startswith('.'): continue
            cur['insns'].append((addr, mnem, ops, cost(mnem, ops, a.ex, ioport)))

    print(f'# cost model: code executing from {a.ex.upper()}  '
          f'(ENGINE16_SPEC.md §2, measured at LAT=0)')
    print(f'# ranges are hardware ambiguity: a taken branch is 2-3 cycles, '
          f'alignment-dependent')
    ip_desc = ','.join(ioport) if ioport else 'NONE GIVEN - GPIO reads are being overcharged as RAM'
    print('# IOPORT base registers (1-cycle access): ' + ip_desc)
    print(f'# a conditional branch is scored 1 (not taken) .. 3 (taken); block '
          f'totals assume fall-through\n')
    over = 0
    for b in blocks:
        if not b['insns']: continue
        lo = sum(i[3][0] for i in b['insns'])
        hi = sum(i[3][1] for i in b['insns'])
        flag = ''
        if a.budget is not None and hi > a.budget:
            flag = f'   <-- OVER BUDGET ({a.budget})'; over += 1
        span = f'{lo}' if lo == hi else f'{lo}..{hi}'
        print(f'{b["label"]}:   {span} cycles{flag}')
        run_lo = run_hi = 0
        for addr, mnem, ops, (cl, ch) in b['insns']:
            run_lo += cl; run_hi += ch
            c = f'{cl}' if cl == ch else f'{cl}-{ch}'
            r = f'{run_lo}' if run_lo == run_hi else f'{run_lo}..{run_hi}'
            note = ''
            if '[pc' in ops:
                note = ('  ! flash literal pool from RAM code = 4 cycles'
                        if a.ex == 'ram' else '  (flash literal pool)')
            print(f'    {addr}  {mnem:<8} {ops:<28} {c:>4}  ={r:<8}{note}')
        print()
    if a.budget is not None:
        print(f'blocks over budget: {over}')
        sys.exit(1 if over else 0)

if __name__ == '__main__':
    main()
