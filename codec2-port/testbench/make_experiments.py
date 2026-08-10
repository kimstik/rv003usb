#!/usr/bin/env python3
"""make_experiments.py — build out/experiments.html: the whole research tree
as one offline file.

For every stand under codec2-port/experiments/* and codec2-port/proto/*:
its REPORT.md (or README.md) rendered by mdlite, every PNG in plots/ inlined
as a base64 data-URI, and the raw-results file list so the reader knows what
backs each number.  Links inside the reports are flattened (see mdlite) — the
page is one file, there is nowhere for a relative href to point.

PNGs are downscaled to MAX_W px wide and re-encoded (PNG, optimised) before
embedding; the original resolution is printed under each figure so it is
obvious the file on disk is the authority, not the thumbnail.
"""
import base64
import html
import io
import os
import subprocess
import sys

import mdlite
import paths

MAX_W = 1300

# (directory relative to codec2-port, display title, one-line verdict)
ORDER = [
    ("experiments/oracle", "oracle — golden reference + metric harness",
     "The pinned float codec2 @310777b is installed as the golden oracle and "
     "the two-level automated comparison (parameter domain, then signal "
     "domain) is built; every later stand measures against it, no humans in "
     "the loop."),
    ("experiments/synth-bakeoff", "synth-bakeoff — decoder synthesis engines",
     "Four synthesis engines measured head-to-head against a sum-of-sinusoids "
     "reference: the impulse-train -> LPC-IIR 'tube' wins P1/P2, osc-bank "
     "wins P3, meander dies of aliasing, cycle-replay stays a contender."),
    ("experiments/synth-redteam", "synth-redteam — adversarial re-run",
     "The winner survives WITH PRESCRIPTIONS (naive int16 filter state "
     "limit-cycles; guard bits are mandatory), the amortisation myth of "
     "cycle-replay is closed by measurement, and the G8 two-allpass LSP "
     "decomposition becomes the new P2 recommendation — its bitstream -> "
     "coefficient conversion is free."),
    ("experiments/tube-ladder", "tube-ladder — MELP excitation rungs",
     "Each rung priced on real decoded speech: the P2 knee is L0+L2+L4 "
     "(mixed excitation + folded postfilter); the surprise is that bare L0 "
     "already matches the reference on magnitude metrics — the audible gap "
     "lives in temporal texture, which is why crest factor and WARP-Q, not "
     "LSD, ranked the rungs."),
    ("experiments/voicing", "voicing — FFT-free voicing decision (kill-test)",
     "KILL as stated: the best MCU-trivial rule disagrees with the MBE "
     "reference on 9.93% of frames and the non-parametric ceiling over all "
     "FFT-free features is 9.87% — the deficit is the feature set, not the "
     "classifier; but 85% of the flips sit within +-2 frames of a V/UV "
     "boundary."),
    ("experiments/voicing-regate", "voicing-regate — perceptual re-gate of B2",
     "CONFIRMED: those disagreements are perceptually free end-to-end — on "
     "all six files the swapped bitstream is closer to the stock decode than "
     "a rate-matched random control, and the shift against the original is "
     "zero within metric noise. The FFT-free encoder track lives."),
    ("experiments/error-injector", "error-injector — measured stage budgets",
     "The README's 'derive budgets instead of assuming them' turned into "
     "numbers: calibrated errors injected at eight stage boundaries of the "
     "chosen architecture, transfer curves measured, knees read off into the "
     "first adaptive budget table (results/budgets.yaml)."),
    ("experiments/metrics-adequacy",
     "metrics-adequacy — do our judges have jurisdiction?",
     "On LSD-matched 'accurate-but-buzzy vs noisy-but-smooth' pairs the "
     "classical judges mis-rank the H1 question; the per-axis neural judges "
     "(NISQA/DNSMOS) do not — which is what licences the neural columns on "
     "the listening page, and which settles the deferred G3+noise verdict."),
    ("experiments/pareto", "pareto — the whole trade space in one dataset",
     "All measured configurations collated into one quality x MHz x RAM x "
     "flash x stability x latency dataset and projected onto Pareto fronts "
     "per real chip; nothing is re-measured or invented, and the empty cells "
     "are literally the round-4 plan."),
    ("proto/decoder", "proto/decoder — the integer c2tube decoder (round 3)",
     "Research becomes code: an all-integer 1300 decoder (G8 tube + L0/L2/L4 "
     "ladder), bit-exact against its python golden model on 400/400 frames, "
     "6.7 KB flash and ~1.5 KB RAM, statically estimated at ~4.6 MHz on a "
     "CH32V003-class core. This is the decoder condition C of the listening "
     "page runs."),
]

CSS = """
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#ccc;--head:#f0f0f0;--acc:#e8eef7;
      --code:#f6f6f6}
@media (prefers-color-scheme:dark){
 :root{--bg:#14161a;--fg:#e6e6e6;--mut:#9aa0a6;--line:#3a3f46;--head:#1e2229;
       --acc:#1b2430;--code:#1b1e24}}
html,body{background:var(--bg);color:var(--fg)}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
     margin:0;padding:18px 22px}
h1{font-size:20px;margin:0 0 2px}
h2{font-size:17px;margin:30px 0 6px;border-top:2px solid var(--line);
   padding-top:12px}
h3{font-size:15px;margin:18px 0 4px}
h4,h5,h6{font-size:13.5px;margin:14px 0 4px}
.sub{color:var(--mut);font-size:12.5px;margin:0 0 16px}
.scroll{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;font-size:12px}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:left;
      vertical-align:top}
th{background:var(--head);font-weight:600}
pre{background:var(--code);border:1px solid var(--line);padding:8px 10px;
    overflow-x:auto;font-size:12px;line-height:1.35}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
     font-size:12px;background:var(--code);padding:0 3px;border-radius:2px}
pre code{background:none;padding:0}
blockquote{border-left:3px solid var(--line);margin:8px 0;padding:2px 10px;
           color:var(--mut)}
.lnk{color:var(--mut);font-size:11px}
.verdict{background:var(--acc);border-left:3px solid #5b7fb5;padding:7px 10px;
         margin:6px 0 10px;font-size:13px}
.meta{color:var(--mut);font-size:11.5px;margin:2px 0 8px}
figure{margin:12px 0}
figure img{max-width:100%;height:auto;border:1px solid var(--line);
           background:#fff}
figcaption{color:var(--mut);font-size:11.5px;margin-top:3px}
ol.toc{font-size:13px;padding-left:22px}
ol.toc li{margin-bottom:6px}
ol.toc a{color:inherit}
ol.toc .v{color:var(--mut);display:block;font-size:12px}
"""


def inline_png(path):
    """-> (base64, orig_w, orig_h, embedded_bytes, embedded_w).

    matplotlib writes tightly-packed palette PNGs; naively re-encoding them at
    RGB can TRIPLE the file (measured: 396 KiB -> 1380 KiB on
    real_hts1a_spectrograms.png).  So the original bytes always compete, and
    the smallest of {original, resized to 1300, resized to 1000} is embedded.
    """
    from PIL import Image
    im = Image.open(path)
    w0, h0 = im.size
    with open(path, "rb") as fh:
        best, best_w = fh.read(), w0
    for w in (MAX_W, 1000):
        if w >= w0:
            continue
        buf = io.BytesIO()
        im.convert("RGB").resize((w, max(1, round(h0 * w / w0))),
                                 Image.LANCZOS).save(buf, format="PNG",
                                                     optimize=True)
        if len(buf.getvalue()) < len(best):
            best, best_w = buf.getvalue(), w
    return (base64.b64encode(best).decode("ascii"), w0, h0, len(best), best_w)


def anchor(rel):
    return rel.replace("/", "-")


def main():
    root = paths.c2port_root()
    try:
        rev = subprocess.run(["git", "-C", root, "rev-parse", "--short",
                              "HEAD"], capture_output=True,
                             text=True).stdout.strip() or "unknown"
    except Exception:
        rev = "unknown"

    body, toc = [], []
    for rel, title, verdict in ORDER:
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            print(f"  skip {rel} (absent)")
            continue
        rep = None
        for cand in ("REPORT.md", "README.md"):
            if os.path.exists(os.path.join(d, cand)):
                rep = cand
                break
        a = anchor(rel)
        toc.append(f'<li><a href="#{a}"><b>{html.escape(rel)}</b> &mdash; '
                   f'{html.escape(title.split(" — ", 1)[-1])}</a>'
                   f'<span class="v">{html.escape(verdict)}</span></li>')

        body.append(f'<h2 id="{a}">{html.escape(title)}</h2>')
        body.append(f'<div class="verdict"><b>Verdict.</b> '
                    f'{html.escape(verdict)}</div>')

        ver = os.path.join(d, "VERSION")
        vtxt = ""
        if os.path.exists(ver):
            with open(ver) as fh:
                for ln in fh:
                    if ln.startswith("CODEC2_COMMIT="):
                        vtxt = " &middot; pinned oracle " + \
                               html.escape(ln.split("=", 1)[1].strip()[:12])
        results = []
        for sub in ("results", "wavs"):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                results += [f"{sub}/{f}" for f in sorted(os.listdir(p))]
        body.append(f'<div class="meta">source: <code>codec2-port/{rel}/'
                    f'{rep or "(no report)"}</code>{vtxt}'
                    + (f' &middot; raw results committed alongside: '
                       f'<code>{html.escape(", ".join(results))}</code>'
                       if results else "") + '</div>')

        if rep:
            with open(os.path.join(d, rep), encoding="utf-8") as fh:
                body.append(mdlite.render(fh.read(), hshift=2))
        else:
            body.append("<p><i>no REPORT.md / README.md in this "
                        "directory</i></p>")

        plots = os.path.join(d, "plots")
        if os.path.isdir(plots):
            pngs = sorted(f for f in os.listdir(plots) if f.endswith(".png"))
            if pngs:
                body.append(f"<h3>figures ({len(pngs)})</h3>")
            for f in pngs:
                b64, w0, h0, nb, wq = inline_png(os.path.join(plots, f))
                how = ("original bytes" if wq == w0
                       else f"downscaled to {wq} px wide")
                body.append(
                    f'<figure><img alt="{html.escape(f)}" '
                    f'src="data:image/png;base64,{b64}">'
                    f'<figcaption><code>{html.escape(rel)}/plots/'
                    f'{html.escape(f)}</code> &mdash; {w0}&times;{h0} px on '
                    f'disk, embedded as {how} ({nb/1024:.0f}&nbsp;KiB)'
                    f'</figcaption></figure>')
        print(f"  {rel}: {rep}, "
              f"{len(os.listdir(plots)) if os.path.isdir(plots) else 0} plots")

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>codec2 &rarr; tiny-MCU: experiment index</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>codec2 &rarr; tiny-MCU &mdash; experiment index</h1>",
        f'<p class="sub">Every stand in <code>codec2-port/</code> in logical '
        f'order, each one\'s report rendered in full with its figures inlined. '
        f'Single offline file &mdash; no external requests, so links inside '
        f'the reports are flattened to plain text with their target shown. '
        f'Tree at <code>{html.escape(rev)}</code>; oracle pinned at codec2 '
        f'<code>310777b</code>. Companion page: '
        f'<code>testbench/out/listen.html</code> (audio + metrics). Reports '
        f'are in the language they were written in (mostly Russian); the '
        f'one-line verdicts below are editorial summaries added by this '
        f'index.</p>',
        "<h2 style='border:0;margin-top:8px'>Contents</h2>",
        '<ol class="toc">' + "".join(toc) + "</ol>",
    ]
    parts += body
    parts.append("</body></html>")

    out_path = os.path.join(paths.OUT, "experiments.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")
    print(f"wrote {out_path} ({os.path.getsize(out_path)/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
