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
def cost(mnem, ops, exec_from, ioport_regs=(), flash_regs=()):
    m = mnem.lower()
    # objdump prints width suffixes (b.n, beq.w); they are not part of the
    # mnemonic for costing purposes.  Missing this scores an unconditional
    # branch as 1 cycle instead of 2-3.
    if m.endswith('.n') or m.endswith('.w'):
        m = m[:-2]
    # Rows are indexed by WHICH MEMORY is touched, not by addressing mode:
    # ports 1, flash 2, RAM 4 for flash-resident code; the flash and RAM
    # columns swap for RAM-resident code (xm_030.md:470-486).
    ram_data   = (2, 2) if exec_from == 'ram' else (4, 4)
    flash_data = (4, 4) if exec_from == 'ram' else (2, 2)
    lit_pool   = flash_data

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
        if mo:
            base = norm(mo.group(1))
            if base in ioport_regs:
                return (1, 1)
            if base in flash_regs:
                return flash_data
        return ram_data
    if m == 'bl':   return (4, 4)
    if m in ('bx', 'blx'): return (3, 3)
    if m == 'b':    return (2, 3)          # unconditional, always taken
    if re.fullmatch(r'b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)', m):
        return (1, 3)                      # 1 not taken, 2-3 taken
    return (1, 1)

ALIAS = {'lr': 'r14', 'sp': 'r13', 'ip': 'r12', 'fp': 'r11',
         'sl': 'r10', 'sb': 'r9', 'pc': 'r15'}

def norm(r):
    """objdump prints lr/ip/sl/fp/sb/sp/pc, not r14/r12/r10/r11/r9/r13/r15.
    Comparing the printed name against a --flashdata/--ioport argument spelled
    the other way silently matches nothing, and every lookup through that
    register is then charged as a RAM access."""
    r = r.lower()
    return ALIAS.get(r, r)

WRITES_NOTHING = {'cmp', 'cmn', 'tst', 'nop', 'push', 'b', 'bl', 'bx', 'blx',
                  'bkpt', 'dmb', 'dsb', 'isb', 'svc', 'wfe', 'wfi', 'yield'}

def propagate(mnem, ops, seeds_flash, seeds_ioport, flash_now, ioport_now):
    """Track which registers currently HOLD a flash-table or GPIO base.

    The encoding does not say what a base register points at, so the caller
    names the registers that hold one - but an engine that keeps its table
    base in a high register loads it into a low one before every lookup
    (`mov r2, r14` / `ldrh r1, [r2, r1]`), and pricing only the named register
    charges every one of those lookups as a RAM access.  On engine16_tx.S that
    reported seven of ten bit cells two cycles over budget that are not.

    So: `mov rD, rS` carries the attribute, and any other write to a register
    takes it away.  Sets are re-seeded at each block, since the named
    registers are pinned by the engines' register contracts.
    """
    m = mnem.lower()
    if m.endswith('.n') or m.endswith('.w'):
        m = m[:-2]
    if m in WRITES_NOTHING or m.startswith('str') or m.startswith('stm') \
       or re.fullmatch(r'b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)', m):
        return flash_now, ioport_now
    if m == 'pop' or m.startswith('ldm'):
        written = [norm(r) for r in re.findall(r'[a-z]+[0-9]*',
                                               ops.split('{')[-1].split('}')[0])]
        return (flash_now - set(written), ioport_now - set(written))
    mo = re.match(r'\s*(\w+)', ops)
    if not mo:
        return flash_now, ioport_now
    dst = norm(mo.group(1))
    if m == 'mov':
        src = re.findall(r'\w+', ops)
        src = norm(src[1]) if len(src) > 1 else None
        f = (flash_now | {dst}) if src in flash_now else (flash_now - {dst})
        i = (ioport_now | {dst}) if src in ioport_now else (ioport_now - {dst})
        return f, i
    return (flash_now - {dst} | (seeds_flash & {dst}),
            ioport_now - {dst} | (seeds_ioport & {dst}))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('obj')
    ap.add_argument('--exec', dest='ex', required=True, choices=['flash', 'ram'],
                    help='where this code executes from - the cost columns swap')
    ap.add_argument('--budget', type=int, default=None,
                    help='flag any block whose max exceeds this (e.g. 16)')
    ap.add_argument('--section', default=None)
    ap.add_argument('--flashdata', default='',
                    help='comma-separated registers pointing at a table in '
                         'FLASH; loads through them cost 2 from flash-resident '
                         'code and 4 from RAM-resident code')
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

    flashregs = tuple(norm(r.strip()) for r in a.flashdata.split(',') if r.strip())
    ioport = tuple(norm(r.strip()) for r in a.ioport.split(',') if r.strip())

    label_re = re.compile(r'^([0-9a-f]+) <([^>]+)>:')
    insn_re  = re.compile(r'^\s+([0-9a-f]+):\s+([0-9a-f ]+?)\s+(\S+)\s*(.*)$')

    blocks, cur = [], None
    propagate.state = (set(flashregs), set(ioport))
    for line in dis.splitlines():
        mo = label_re.match(line)
        if mo:
            cur = {'label': mo.group(2), 'addr': mo.group(1), 'insns': []}
            blocks.append(cur)
            propagate.state = (set(flashregs), set(ioport))
            continue
        mo = insn_re.match(line)
        if mo and cur is not None:
            addr, _, mnem, ops = mo.groups()
            ops = ops.split(';')[0].split('@')[0].strip()
            if mnem.startswith('.'): continue
            fl_now, ip_now = propagate.state
            cur['insns'].append((addr, mnem, ops,
                                 cost(mnem, ops, a.ex, ip_now, fl_now)))
            propagate.state = propagate(mnem, ops, set(flashregs), set(ioport),
                                        fl_now, ip_now)

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
