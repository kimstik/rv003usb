#!/usr/bin/env python3
"""
selftest.py -- end-to-end selftest for wg015vcd.py.

Generates synthetic VCD captures with make_test_vcd.py (known injected values),
runs wg015vcd.py over them and asserts the reported numbers match the injected
ones within tolerance. Prints an injected-vs-measured table. Exit 0 = all pass.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', 'wg015vcd.py')
GEN = os.path.join(HERE, 'make_test_vcd.py')
PY = sys.executable or 'python3'


def run(cmd, ok_codes=(0,)):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True)
    if r.returncode not in ok_codes:
        sys.stderr.write("command failed (rc=%d): %s\n%s\n%s\n"
                         % (r.returncode, ' '.join(cmd), r.stdout[-3000:],
                            r.stderr[-2000:]))
        sys.exit(1)
    return r


def run_json(mode, vcd, extra=(), ok_codes=(0,)):
    r = run([PY, TOOL, mode, vcd, '--json'] + list(extra), ok_codes)
    try:
        return json.loads(r.stdout), r.returncode
    except json.JSONDecodeError:
        sys.stderr.write("non-JSON output from %s %s:\n%s\n" % (mode, vcd, r.stdout))
        sys.exit(1)


CASES = [
    # name, generator args, expectations
    ('nominal', {'--ppm': 0, '--entry': 50, '--drift': 0.0},
     {'entry': (50, 2.0), 'slope': (0.0, 0.02), 'txcell': (32.0, 0.15),
      'turn': (4.0, 0.6), 'gate_rc': 0}),
    ('ppm+5000', {'--ppm': 5000, '--entry': 50},
     {'entry': (50.25, 2.0), 'slope': (0.16, 0.03), 'txcell': (32.16, 0.15),
      'turn': (4.0, 0.6), 'gate_rc': 0}),
    ('entry40', {'--entry': 40},
     {'entry': (40, 2.0), 'slope': (0.0, 0.02), 'gate_rc': 0}),
    ('entry70', {'--entry': 70},
     {'entry': (70, 2.0), 'gate_rc': 1}),                 # violates --gate-entry 55
    ('drift+0.05', {'--drift': 0.05},
     {'entry': (50, 2.0), 'slope': (0.05, 0.02), 'gate_rc': 0}),
]

N_TXN = 4
N_KA = 3


def main():
    outdir = tempfile.mkdtemp(prefix='wg015vcd_selftest_')
    keep = '--keep' in sys.argv
    rows = []
    ok_all = True

    def check(case, metric, injected, measured, tol):
        nonlocal ok_all
        if measured is None:
            ok = False
        elif tol is None:                       # exact (integers / rc)
            ok = (measured == injected)
        else:
            ok = abs(measured - injected) <= tol
        ok_all &= ok
        rows.append((case, metric, injected, measured, tol, ok))

    for name, gargs, exp in CASES:
        vcd = os.path.join(outdir, name + '.vcd')
        cmd = [PY, GEN, '--out', vcd, '--truth', vcd + '.truth.json',
               '--transactions', str(N_TXN), '--keepalives', str(N_KA), '--seed', '7']
        for k, v in gargs.items():
            cmd += [k, str(v)]
        run(cmd)
        truth = json.load(open(vcd + '.truth.json'))

        # decode: traffic must be fully recognized, CRC clean
        dec, _ = run_json('decode', vcd)
        pids = [p['pid'] for p in dec['packets']]
        check(name, 'decode.setup_count', N_TXN, pids.count('SETUP'), None)
        check(name, 'decode.data0_count', N_TXN, pids.count('DATA0'), None)
        check(name, 'decode.ack_count', N_TXN, pids.count('ACK'), None)
        check(name, 'decode.keepalives', N_KA, dec['keepalives']['n'], None)
        check(name, 'decode.crc_errors', 0, dec['crc_errors'], None)
        check(name, 'decode.invalid', 0, dec['invalid_packets'], None)

        # rx: entry latency, drift slope, gate behaviour
        rx, rc = run_json('rx', vcd, ok_codes=(0, 1))
        entry = rx['rx']['entry_cyc']
        slope = rx['rx']['slope_cyc_per_bit']
        if 'entry' in exp:
            inj, tol = exp['entry']
            check(name, 'rx.entry_med_cyc', inj,
                  entry and entry['med'], tol)
        if 'slope' in exp:
            inj, tol = exp['slope']
            check(name, 'rx.slope_med_c/bit', inj,
                  slope and slope['med'], tol)
        if 'gate_rc' in exp:
            check(name, 'rx.gate55_exitcode', exp['gate_rc'], rc, None)
        # excursion sanity: must be >= |slope| * (bits-1) rough for DATA0
        if 'slope' in exp and exp['slope'][0] != 0:
            worst = rx['rx']['excursion_cyc']['max']
            expected = abs(exp['slope'][0]) * 90       # DATA0 ~97 cells
            check(name, 'rx.worst_excursion', expected, worst, expected * 0.35 + 1.0)

        # tx: cell period + turnaround
        if 'txcell' in exp or 'turn' in exp:
            tx, _ = run_json('tx', vcd)
            if 'txcell' in exp:
                inj, tol = exp['txcell']
                cc = tx['tx']['cell_cyc']
                check(name, 'tx.cell_avg_cyc', inj, cc and cc['mean'], tol)
            if 'turn' in exp:
                inj, tol = exp['turn']
                ta = tx['tx']['turnaround_bt']
                check(name, 'tx.turnaround_bt', inj, ta and ta['med'], tol)
            eop = tx['tx']['eop_se0_cyc']
            check(name, 'tx.eop_se0_cyc', 64.0 * (1 + truth['ppm'] / 1e6),
                  eop and eop['med'], 1.0)

    # excursion gate must fire when set below the drifted excursion
    vcd = os.path.join(outdir, 'ppm+5000.vcd')
    _, rc = run_json('rx', vcd, extra=['--gate-excursion', '5'], ok_codes=(0, 1))
    check('ppm+5000', 'rx.gate_excursion5_rc', 1, rc, None)

    # 100 MS/s LA simulation: quantize nominal case timestamps to 10 ns
    q = os.path.join(outdir, 'nominal_q10ns.vcd')
    with open(os.path.join(outdir, 'nominal.vcd')) as fi, open(q, 'w') as fo:
        for line in fi:
            if line.startswith('#'):
                fo.write('#%d\n' % (round(int(line[1:]) / 10000) * 10000))
            else:
                fo.write(line)
    dec, _ = run_json('decode', q)
    check('quant10ns', 'decode.crc_errors', 0, dec['crc_errors'], None)
    check('quant10ns', 'decode.invalid', 0, dec['invalid_packets'], None)
    rxq, rcq = run_json('rx', q, ok_codes=(0, 1))
    check('quant10ns', 'rx.entry_med_cyc', 50.0, rxq['rx']['entry_cyc']['med'], 2.5)
    check('quant10ns', 'rx.gate55_exitcode', 0, rcq, None)

    # bench mode runs and reports sane grid intervals (~32 cyc dominant)
    be, _ = run_json('bench', os.path.join(outdir, 'nominal.vcd'))
    med = be['bench']['interval_cyc']['med']
    check('nominal', 'bench.interval_med', 32.0, med, 3.0)

    # malformed VCD -> clear failure, exit 2
    bad = os.path.join(outdir, 'garbage.vcd')
    with open(bad, 'w') as f:
        f.write("this is not a vcd file\x00\xff garbage\n" * 10)
    r = subprocess.run([PY, TOOL, 'decode', bad], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    check('malformed', 'exit_code_2', 2, r.returncode, None)
    check('malformed', 'error_msg', True,
          'error' in r.stderr.lower(), None)

    # ---- report table
    print("%-11s %-24s %12s %12s %8s  %s"
          % ("case", "metric", "injected", "measured", "tol", "result"))
    print("-" * 78)
    for case, metric, inj, meas, tol, ok in rows:
        f = lambda v: ("%12.3f" % v) if isinstance(v, float) else ("%12s" % v)
        print("%-11s %-24s %s %s %8s  %s"
              % (case, metric, f(inj), f(meas),
                 ('%g' % tol) if tol is not None else 'exact',
                 'PASS' if ok else 'FAIL'))
    n_fail = sum(1 for r in rows if not r[5])
    print("-" * 78)
    print("%d checks, %d failed" % (len(rows), n_fail))
    if keep:
        print("artifacts kept in %s" % outdir)
    else:
        shutil.rmtree(outdir, ignore_errors=True)
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
