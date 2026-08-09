"""Static MCU cost model for the four engines (Q15/int16 implementations).

Counted per output sample and per 20 ms frame setup, as functions of L.
Two cores:
  P1 "mul": single-cycle 32x32 mul (PY32F003 M0+, CH32V006). mul=1 cycle.
  P2 "no-mul": RV32EC soft mul via __mulsi3 ~= 120 cycles (calibrated by the
     rv003usb repo's own measurement in demo_pikoball_hid/color_utilities.h).
Common cycle prices (both cores): add/sub=1, shift=1, logic=1, load=2,
store=2, taken branch=2 (amortized where noted).  These are coarse RV32/M0+
figures -- the model ranks engines, it does not promise cycle-exact numbers.
All flash numbers are ESTIMATEs for code+tables, decoder-side only.
"""

MUL_SOFT = 120
LOAD = 2
STORE = 2
FRAME_RATE = 50      # 20 ms frames
FS = 8000


def _cycles(c, mul_cost):
    return (c["mul"] * mul_cost + c["add"] + c["shift"] + c["mem"] * LOAD +
            c.get("branch", 0) * 2)


def engine_cost(name, L):
    """Returns dict with per-sample and per-frame op counts + memory."""
    Nt = 2 * L            # cycle-replay table length ~= period ~= 2L samples

    if name == "osc-bank":
        # per harmonic: phase add, index shift+mask, sin LUT load, mul by amp,
        # acc add, amp ramp add
        s = {"mul": L, "add": 3 * L, "shift": 2 * L, "mem": L}
        f = {"mul": 0, "add": 2 * L, "shift": L, "mem": L}
        ram = 6 * L + 16          # u16 phase, amp, damp per harmonic
        flash = 512 + 600         # sin LUT (256 x i16) + code
    elif name == "impulse-iir":
        # excitation: 1 add + wrap check (branch); IIR order 10: 10 mul,
        # 10 add, circular state (10 loads, 1 store); output add
        s = {"mul": 10, "add": 13, "shift": 1, "mem": 11, "branch": 1}
        # setup: LSP->LPC ~100 MAC + gain match ~40 ops (real decoder gets
        # LPC from the bitstream path anyway -- shared with any engine's
        # envelope decode; counted here to be conservative)
        f = {"mul": 140, "add": 140, "shift": 10, "mem": 40}
        ram = 2 * 10 + 2 * 11 + 8   # state + coeffs + misc
        flash = 800
    elif name in ("impulse-iir-csd", "impulse-iir-csd-sos"):
        # each coeff mul -> <=3 shifts + 2 adds (CSD 3 terms); SOS form has
        # the same 10 coefficient-mults, +5 adds of section plumbing
        extra = 5 if name.endswith("sos") else 0
        s = {"mul": 0, "add": 13 + 20 + extra, "shift": 1 + 30, "mem": 11,
             "branch": 1}
        # CSD conversion per frame: ~30 ops/coeff (or 0 if coeffs ship as CSD
        # from a baked codebook -- noted in report); gain match without mul
        # uses log-domain adds ~60; SOS adds a per-frame root pairing UNLESS
        # the bitstream envelope is decoded straight to biquads (LSP pairs
        # ARE conjugate pole pairs -- natural fit, ~0 extra)
        f = {"mul": 0, "add": 300, "shift": 100, "mem": 40}
        ram = 2 * 10 + 3 * 11 + 8
        flash = 900
    elif name in ("meander-sq", "meander-tri"):
        # per basis wave: phase add, sign test (branch ~amortized 1.5),
        # add +-B ; triangle: +1 shift +1 add for the ramp fold
        extra = L if name == "meander-tri" else 0
        s = {"mul": 0, "add": 2 * L + extra, "shift": extra,
             "mem": L, "branch": L}
        # solve: ~0.55*L*ln(L) terms, each = const mul (shift-add ~3 ops)
        import math
        terms = int(0.5 * L * math.log(max(L, 2))) + L
        f = {"mul": 0, "add": 3 * terms, "shift": 2 * terms, "mem": terms}
        ram = 4 * L + 2 * L        # phase u16 + B i16 (+ solve scratch)
        flash = 700
    elif name in ("cycle-replay", "cycle-replay-2x"):
        # per sample: phase add, shift, 2 table loads, linear interp
        # (1 mul + 2 add); crossfade region adds 2 mul + 2 mem for 32 samples
        # per 160 -> amortized 0.4 mul, 0.4 mem
        os_f = 2 if name.endswith("2x") else 1
        Nt = os_f * Nt
        s = {"mul": 1.4, "add": 3.4, "shift": 1, "mem": 2.4}
        # setup: table build = Nt*L MACs via phase-acc + sin LUT
        f = {"mul": Nt * L, "add": 2 * Nt * L, "shift": Nt * L, "mem": Nt * L}
        ram = 2 * 2 * Nt + 16      # double buffer i16
        flash = 512 + 800
    elif name == "cycle-replay-nn":
        s = {"mul": 0.4, "add": 1.4, "shift": 1, "mem": 1.4}
        f = {"mul": Nt * L, "add": 2 * Nt * L, "shift": Nt * L, "mem": Nt * L}
        ram = 2 * 2 * Nt + 16
        flash = 512 + 700
    else:
        raise ValueError(name)

    res = {"per_sample": s, "per_frame": f, "ram_B": ram, "flash_B": flash}
    for core, mc in (("mul", 1), ("nomul", MUL_SOFT)):
        cyc_s = _cycles(s, mc)
        cyc_f = _cycles(f, mc)
        mhz = (cyc_s * FS + cyc_f * FRAME_RATE) / 1e6
        res[f"cycles_per_sample_{core}"] = cyc_s
        res[f"setup_cycles_per_frame_{core}"] = cyc_f
        res[f"mhz_{core}"] = mhz
    return res


ALL = ["osc-bank", "impulse-iir", "impulse-iir-csd", "impulse-iir-csd-sos",
       "meander-sq", "meander-tri", "cycle-replay", "cycle-replay-2x",
       "cycle-replay-nn"]


def cost_table(Ls=(20, 40, 80)):
    rows = []
    for name in ALL:
        for L in Ls:
            c = engine_cost(name, L)
            s = c["per_sample"]
            rows.append({
                "engine": name, "L": L,
                "mul/smp": round(s["mul"], 1), "add/smp": round(s["add"], 1),
                "shift/smp": round(s["shift"], 1), "mem/smp": round(s["mem"], 1),
                "setup_cyc(mul)": int(c["setup_cycles_per_frame_mul"]),
                "MHz@mul": round(c["mhz_mul"], 2),
                "MHz@nomul": round(c["mhz_nomul"], 2),
                "RAM_B": c["ram_B"], "flash_B": c["flash_B"],
            })
    return rows
