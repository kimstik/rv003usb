#!/usr/bin/env python3
"""make_listen.py — build out/listen.html: one self-contained listening page.

Everything is inlined: the audio is base64 data-URI RIFF/WAVE (8 kHz mono
s16, the codec's own sample format — no lossy re-encoding), the CSS is a
couple of dozen lines, there is no JavaScript and no external request, so the
file plays from file:// in any browser and can be mailed as one attachment.
"""
import base64
import html
import json
import os
import statistics
import subprocess
import sys

import paths

RES = os.path.join(paths.OUT, "results")
WAVS = os.path.join(paths.OUT, "wavs")


def b64wav(name):
    with open(os.path.join(WAVS, name), "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def audio(name):
    return (f'<audio controls preload="none" src="data:audio/wav;base64,'
            f'{b64wav(name)}"></audio>')


def num(v, fmt="{:.3f}", dash="&mdash;"):
    if v is None:
        return dash
    try:
        if isinstance(v, float) and v != v:
            return "n/a"
    except TypeError:
        pass
    return fmt.format(v)


def sgn(v, fmt="{:+.3f}"):
    if v is None:
        return "&mdash;"
    if isinstance(v, float) and v != v:
        return "n/a"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "zero")
    return f'<span class="{cls}">{fmt.format(v)}</span>'


CSS = """
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#ccc;--head:#f0f0f0;--acc:#e8eef7}
@media (prefers-color-scheme:dark){
 :root{--bg:#14161a;--fg:#e6e6e6;--mut:#9aa0a6;--line:#3a3f46;--head:#1e2229;
       --acc:#1b2430}}
html,body{background:var(--bg);color:var(--fg)}
body{font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
     margin:0;padding:18px 22px;max-width:100%}
h1{font-size:19px;margin:0 0 2px}
h2{font-size:16px;margin:26px 0 4px;border-top:1px solid var(--line);
   padding-top:10px}
h3{font-size:14px;margin:16px 0 4px}
p,li{max-width:none}
.sub{color:var(--mut);margin:0 0 14px;font-size:12.5px}
.meth{font-size:12.5px;line-height:1.5}
.meth dt{font-weight:600;margin-top:5px}
.meth dd{margin:0 0 0 0;padding-left:0;color:var(--fg)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:6px 0 2px}
table{border-collapse:collapse;font-size:12.5px}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:right;
      white-space:nowrap;vertical-align:middle}
th{background:var(--head);font-weight:600;text-align:center;font-size:11.5px}
td.l,th.l{text-align:left}
td.n,th.n{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
          font-variant-numeric:tabular-nums}
tr.anchor td{background:var(--acc)}
tr.pending td{color:var(--mut);font-style:italic}
audio{height:30px;width:230px;vertical-align:middle}
code,kbd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
     font-size:12px}
.pos{color:#137333}.neg{color:#b3261e}.zero{color:var(--mut)}
@media (prefers-color-scheme:dark){.pos{color:#6fcf8b}.neg{color:#ef8b82}}
.small{font-size:11.5px;color:var(--mut)}
.grp{border-left:2px solid var(--line)}
"""


def methods_html(bench, man, neural, versions):
    enc = bench["encoder_note"]
    cc = man.get("crosscheck")
    ccs = ""
    if cc:
        ccs = (f' Provenance cross-check: the web copy '
               f'<code>{html.escape(os.path.basename(cc["url"]))}</code> and the '
               f'repo file <code>{html.escape(cc["repo_file"])}</code> are '
               f'<b>{"byte-identical" if cc["identical"] else "DIFFERENT"}</b> '
               f'(sha256[:16] {cc["sha256_16"]}).')
    miss = man.get("missing") or []
    misss = ""
    if miss:
        misss = (" Not reachable, therefore absent from the corpus: "
                 + ", ".join(html.escape(m["source"]) for m in miss) + ".")

    nn = []
    if neural.get("nisqa"):
        nn.append("NISQA v2 (mos/noi/col/dis/loud)")
    if neural.get("dnsmos"):
        nn.append("DNSMOS P.835 (SIG/BAK/OVRL/P.808)")
    neural_line = (", ".join(nn) if nn else
                   "none available in this run") + "."
    for n in neural.get("notes", []):
        neural_line += f" Note: {html.escape(n)}"

    return f"""
<h2>Methods</h2>
<div class="meth">
<p><b>What this page is.</b> A listening bench for the codec2&rarr;tiny-MCU
port. Every clip below is produced by the same pinned toolchain and scored by
the same metric conventions as the project's research reports, so ears and
numbers can be checked against each other on the same page. It is not a
subjective test: there is no panel, no randomisation and no hidden reference,
so the numbers are objective proxies and the audio is the actual evidence.</p>

<dl>
<dt>Codec / oracle version</dt>
<dd>codec2 <code>{versions['codec2_commit']}</code> (pinned, shallow clone,
built Release on host) &mdash; <code>c2enc</code>, <code>c2dec</code>.
Decoder under test: <code>{versions['proto_ref']}</code>, host build
<code>cc -O2 -std=c99</code>. Mode <b>1300 bit/s</b> throughout: 52 bits per
40&nbsp;ms frame, 7 bytes/frame.</dd>

<dt>Corpus</dt>
<dd>{versions['corpus_line']}{ccs}{misss} All items are 8&nbsp;kHz mono
16-bit already at the source, so no resampling was applied; each is trimmed to
a whole number of 40&nbsp;ms frames and capped at
{man['max_s']:.0f}&nbsp;s.</dd>

<dt>Conditions</dt>
<dd>All conditions of one utterance consume the <b>same</b> <code>.c2</code>
bitstream produced by stock <code>c2enc 1300</code>, so B/C/E differ only in
the decoder &mdash; no encoder variance enters the comparison.
<b>{enc}</b></dd>

<dt>ESTOI (vs A, higher is better)</dt>
<dd><code>pystoi</code> extended STOI against the original. Aligned by a single
constant lag chosen to <i>maximise ESTOI</i> over |lag|&nbsp;&le;&nbsp;256
samples (best-case single-delay alignment, the tube-ladder convention applied
to the metric being reported); the lag used is printed per row. The
correlation-peak lag of <code>proto/decoder/validate.py</code> is deliberately
not used here &mdash; it is phase-sensitive and mis-locked on the noisy
utterance (mmt1&nbsp;C: +144 instead of ~&minus;100, depressing ESTOI 0.328
&rarr; 0.186). Absolute values therefore run slightly higher than the same
names in <code>proto/decoder/REPORT.md</code>; rankings are unchanged.</dd>

<dt>LSD (vs B, lower is better)</dt>
<dd>Per-frame log-spectral distance, 160-sample Hann window on the 10&nbsp;ms
grid, RMS over 100&ndash;3700&nbsp;Hz, frames gated 40&nbsp;dB below the
reference's utterance RMS (<code>experiments/tube-ladder/metrics_ladder.py</code>).
Median and p90 shown. The lag is the one minimising mean LSD.</dd>

<dt>segSNR (vs B, higher is better) &mdash; weak by construction</dt>
<dd>20&nbsp;ms frame / 10&nbsp;ms hop, per-frame clamp [&minus;10,&nbsp;+35]&nbsp;dB,
40&nbsp;dB silence gate (<code>experiments/oracle/metrics_signal.py</code>), at
the LSD-optimal lag. <b>c2dec synthesises with phase0 IFFT+OLA while c2tube is
a free-running IIR tube</b>: the two carry different phase tracks, so this
column measures waveform disagreement that is largely inaudible. It is
reported for completeness and must not be used to rank conditions.</dd>

<dt>Neural MOS (reference-free)</dt>
<dd>{neural_line} Both judges were trained on wideband natural speech; on
8&nbsp;kHz vocoded speech their absolute values are depressed and are
<b>not</b> MOS. The metrics-adequacy stand established they are usable as
per-utterance deltas and rankings only &mdash; read the &Delta; columns.</dd>

<dt>Level</dt>
<dd><code>RMS</code> is the active-speech RMS in dBFS (frames above a
40&nbsp;dB-below-utterance gate). It is a sanity column: a decoder that wins a
metric by being quieter is visible here.</dd>
</dl>
</div>
"""


def cond_table(bench, u, neural):
    conds = {c["id"]: c for c in bench["conditions"]}
    nis = neural.get("nisqa") or {}
    dns = neural.get("dnsmos") or {}
    rows = u["rows"]
    byc = {r["cond"]: r for r in rows}
    B = byc.get("B")

    def nv(r, table, key):
        d = table.get(r.get("wav", ""), {})
        return d.get(key)

    have_nis = bool(nis)
    have_dns = bool(dns)

    h = []
    h.append('<div class="scroll"><table>')
    head1 = ('<tr><th class="l" rowspan="2">cond</th>'
             '<th class="l" rowspan="2">what it is</th>'
             '<th rowspan="2">listen</th>'
             '<th rowspan="2">RMS<br>dBFS</th>'
             '<th colspan="4" class="grp">vs A = original</th>'
             '<th colspan="4" class="grp">vs B = codec ceiling</th>')
    n_extra = (5 if have_nis else 0) + (2 if have_dns else 0)
    if n_extra:
        head1 += f'<th colspan="{n_extra}" class="grp">reference-free judges</th>'
    head1 += '</tr>'
    head2 = ('<tr><th class="grp">ESTOI</th><th>&Delta;ESTOI<br>vs&nbsp;B</th>'
             '<th>rel&nbsp;%<br>vs&nbsp;B</th><th>lag<br>smp</th>'
             '<th class="grp">LSD med<br>dB</th><th>LSD p90<br>dB</th>'
             '<th>segSNR med<br>dB (weak)</th><th>lag<br>smp</th>')
    if have_nis:
        head2 += ('<th class="grp">NISQA<br>MOS</th><th>&Delta;MOS<br>vs&nbsp;B</th>'
                  '<th>col</th><th>dis</th><th>noi</th>')
    if have_dns:
        head2 += ('<th class="grp">DNS<br>OVRL</th>'
                  '<th>&Delta;OVRL<br>vs&nbsp;B</th>')
    head2 += '</tr>'
    h.append(head1 + head2)

    for r in rows:
        cid = r["cond"]
        c = conds.get(cid, {"label": "?", "detail": "", "role": ""})
        if r.get("pending"):
            h.append(f'<tr class="pending"><td class="l n">{cid}</td>'
                     f'<td class="l" colspan="20">{html.escape(c["label"])} '
                     f'&mdash; {html.escape(bench["encoder_note"])}</td></tr>')
            continue
        anchor = ' class="anchor"' if cid in ("A", "B") else ""
        cells = [f'<td class="l n">{cid}</td>',
                 f'<td class="l">{html.escape(c["label"])}'
                 f'<br><span class="small">{html.escape(c["detail"])} '
                 f'&middot; {html.escape(c["role"])}</span></td>',
                 f'<td>{audio(r["wav"])}</td>',
                 f'<td class="n">{num(r["active_rms_dbfs"], "{:.1f}")}</td>',
                 f'<td class="n grp">{num(r["estoi_vs_A"])}</td>',
                 f'<td class="n">{sgn(r.get("d_estoi_vs_B"))}</td>',
                 f'<td class="n">{sgn(r.get("rel_estoi_vs_B_pct"), "{:+.1f}")}</td>',
                 f'<td class="n">{num(r.get("lag_A"), "{:d}")}</td>',
                 f'<td class="n grp">{num(r.get("lsd_vs_B_median"), "{:.2f}")}</td>',
                 f'<td class="n">{num(r.get("lsd_vs_B_p90"), "{:.2f}")}</td>',
                 f'<td class="n">{num(r.get("segsnr_vs_B_median"), "{:.2f}")}</td>',
                 f'<td class="n">{num(r.get("lag_B"), "{:d}")}</td>']
        if have_nis:
            m = nv(r, nis, "nisqa_mos")
            mb = nv(B, nis, "nisqa_mos") if B else None
            dm = (m - mb) if (m is not None and mb is not None) else None
            cells += [f'<td class="n grp">{num(m, "{:.2f}")}</td>',
                      f'<td class="n">{sgn(dm, "{:+.2f}")}</td>',
                      f'<td class="n">{num(nv(r, nis, "nisqa_col"), "{:.2f}")}</td>',
                      f'<td class="n">{num(nv(r, nis, "nisqa_dis"), "{:.2f}")}</td>',
                      f'<td class="n">{num(nv(r, nis, "nisqa_noi"), "{:.2f}")}</td>']
        if have_dns:
            o = nv(r, dns, "dns_ovrl")
            ob = nv(B, dns, "dns_ovrl") if B else None
            do = (o - ob) if (o is not None and ob is not None) else None
            cells += [f'<td class="n grp">{num(o, "{:.2f}")}</td>',
                      f'<td class="n">{sgn(do, "{:+.2f}")}</td>']
        h.append(f"<tr{anchor}>" + "".join(cells) + "</tr>")
    h.append("</table></div>")
    return "\n".join(h)


def summary(bench, neural):
    """Corpus-level medians of the per-utterance deltas vs B."""
    nis = neural.get("nisqa") or {}
    conds = [c["id"] for c in bench["conditions"] if c["id"] != "D"]
    acc = {c: {"estoi": [], "d": [], "rel": [], "lsd": [], "seg": [],
               "mos": [], "dmos": []} for c in conds}
    for u in bench["utterances"]:
        byc = {r["cond"]: r for r in u["rows"] if not r.get("pending")}
        B = byc.get("B")
        for cid, r in byc.items():
            a = acc[cid]
            a["estoi"].append(r["estoi_vs_A"])
            a["d"].append(r["d_estoi_vs_B"])
            a["rel"].append(r["rel_estoi_vs_B_pct"])
            if cid != "B":
                a["lsd"].append(r["lsd_vs_B_median"])
                a["seg"].append(r["segsnr_vs_B_median"])
            m = (nis.get(r["wav"]) or {}).get("nisqa_mos")
            mb = (nis.get(B["wav"]) or {}).get("nisqa_mos") if B else None
            if m is not None:
                a["mos"].append(m)
                if mb is not None:
                    a["dmos"].append(m - mb)

    def med(v):
        return statistics.median(v) if v else None

    labels = {c["id"]: c["label"] for c in bench["conditions"]}
    h = ['<div class="scroll"><table>',
         '<tr><th class="l">cond</th><th class="l">what it is</th>'
         '<th>ESTOI<br>median</th><th>&Delta;ESTOI vs B<br>median</th>'
         '<th>rel % vs B<br>median</th><th>LSD med vs B<br>median dB</th>'
         '<th>segSNR vs B<br>median dB</th>'
         + ('<th>NISQA MOS<br>median</th><th>&Delta;MOS vs B<br>median</th>'
            if nis else '') + '</tr>']
    for cid in conds:
        a = acc[cid]
        row = [f'<td class="l n">{cid}</td>',
               f'<td class="l">{html.escape(labels.get(cid, ""))}</td>',
               f'<td class="n">{num(med(a["estoi"]))}</td>',
               f'<td class="n">{sgn(med(a["d"]))}</td>',
               f'<td class="n">{sgn(med(a["rel"]), "{:+.1f}")}</td>',
               f'<td class="n">{num(med(a["lsd"]), "{:.2f}")}</td>',
               f'<td class="n">{num(med(a["seg"]), "{:.2f}")}</td>']
        if nis:
            row += [f'<td class="n">{num(med(a["mos"]), "{:.2f}")}</td>',
                    f'<td class="n">{sgn(med(a["dmos"]), "{:+.2f}")}</td>']
        cls = ' class="anchor"' if cid in ("A", "B") else ""
        h.append(f"<tr{cls}>" + "".join(row) + "</tr>")
    h.append("</table></div>")
    return "\n".join(h)


def corpus_table(man):
    h = ['<div class="scroll"><table>',
         '<tr><th class="l">utterance</th><th class="l">voice / content</th>'
         '<th class="l">origin</th><th>s</th><th>40 ms<br>frames</th>'
         '<th>peak</th><th>RMS<br>dBFS</th><th class="l">sha256[:16]</th></tr>']
    for i in man["items"]:
        src = i["source"]
        h.append(
            f'<tr><td class="l n">{html.escape(i["utt"])}</td>'
            f'<td class="l">{html.escape(i["who"])}</td>'
            f'<td class="l"><code>{html.escape(src)}</code></td>'
            f'<td class="n">{i["seconds"]:.2f}</td>'
            f'<td class="n">{i["frames_40ms"]}</td>'
            f'<td class="n">{i["peak"]}</td>'
            f'<td class="n">{i["rms_dbfs"]:.1f}</td>'
            f'<td class="l n">{i["sha256_16"]}</td></tr>')
    h.append("</table></div>")
    return "\n".join(h)


def main():
    root = paths.c2port_root()
    with open(os.path.join(RES, "bench.json")) as fh:
        bench = json.load(fh)
    with open(os.path.join(paths.OUT, "corpus", "manifest.json")) as fh:
        man = json.load(fh)
    neural = {}
    npath = os.path.join(RES, "neural.json")
    if os.path.exists(npath):
        with open(npath) as fh:
            neural = json.load(fh)

    try:
        rev = subprocess.run(["git", "-C", root, "rev-parse", "--short",
                              "HEAD"], capture_output=True, text=True
                             ).stdout.strip() or "unknown"
    except Exception:
        rev = "unknown"

    n_repo = sum(1 for i in man["items"] if i["kind"] == "repo")
    n_ext = sum(1 for i in man["items"] if i["kind"] == "ext")
    corpus_line = (
        f'{len(man["items"])} utterances, '
        f'{sum(i["seconds"] for i in man["items"]):.0f}&nbsp;s total: '
        f'{n_repo} from the pinned codec2 <code>raw/</code> directory and '
        f'{n_ext} downloaded as ORIGINAL (uncoded) audio from David Rowe\'s '
        f'codec2 pages &mdash; every URL is in the table below. The per-mode '
        f'A/B files those pages also host are already vocoded and are '
        f'therefore unusable as bench input.')
    versions = {"codec2_commit": man["codec2_commit"],
                "proto_ref": f"codec2-port/proto/decoder @ {rev}",
                "corpus_line": corpus_line}

    parts = [
        '<!doctype html>',
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>codec2 &rarr; tiny-MCU: listening bench (mode 1300)</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>codec2 &rarr; tiny-MCU &mdash; listening bench, mode 1300</h1>",
        f'<p class="sub">Integer &laquo;tube&raquo; decoder '
        f'(<code>c2tube</code>) against the pinned float codec and against its '
        f'own bottom ladder rung. {len(man["items"])} utterances &times; '
        f'{len(bench["conditions"])} conditions '
        f'({sum(1 for u in bench["utterances"][:1] for r in u["rows"] if r.get("pending"))} '
        f'of them pending), audio embedded, no external requests. Generated by '
        f'<code>codec2-port/testbench/run_all.sh</code>.</p>',
        methods_html(bench, man, neural, versions),
        "<h2>Corpus and provenance</h2>",
        corpus_table(man),
        "<h2>Corpus summary (medians over utterances)</h2>",
        '<p class="small">Per-condition medians of the per-utterance values '
        'from the tables below. &Delta; columns are against condition B, the '
        'codec\'s own float ceiling on the same bitstream.</p>',
        summary(bench, neural),
    ]
    for u in bench["utterances"]:
        m = u["meta"]
        parts.append(f'<h2>{html.escape(u["utt"])} '
                     f'<span class="small">&mdash; {html.escape(m["who"])}, '
                     f'{m["seconds"]:.2f}&nbsp;s, {u["frames"]} frames, '
                     f'{u["c2_bytes"]}&nbsp;B of bitstream '
                     f'({u["bitrate_bps"]:.0f}&nbsp;bit/s incl. 7&nbsp;B '
                     f'header)</span></h2>')
        parts.append(cond_table(bench, u, neural))

    # C vs E: the tier gap, as the two families of judge see it
    nis = (neural.get("nisqa") or {})
    dce, dcm = [], []
    for u in bench["utterances"]:
        byc = {r["cond"]: r for r in u["rows"] if not r.get("pending")}
        if "C" in byc and "E" in byc:
            dce.append(byc["C"]["estoi_vs_A"] - byc["E"]["estoi_vs_A"])
            mc = (nis.get(byc["C"]["wav"]) or {}).get("nisqa_mos")
            me = (nis.get(byc["E"]["wav"]) or {}).get("nisqa_mos")
            if mc is not None and me is not None:
                dcm.append(mc - me)
    tier = (f'Measured on this corpus, the L2+L4 rungs move ESTOI by a median '
            f'{statistics.median(dce):+.3f} (range {min(dce):+.3f} to '
            f'{max(dce):+.3f}) '
            + (f'while moving NISQA MOS by a median '
               f'{statistics.median(dcm):+.2f} (range {min(dcm):+.2f} to '
               f'{max(dcm):+.2f}). ' if dcm else '. ')
            + 'The two judges disagree about the size of the rung by an order '
              'of magnitude, and tube-ladder predicted exactly this: its '
              '"surprise 1" records that a bare L0 tube is already '
              'indistinguishable from the reference on magnitude metrics '
              'because the LPC-10e buzz lives in temporal texture, not in the '
              'envelope.')

    parts.append('<h2>Reading the numbers</h2><div class="meth">'
                 f'<p><b>The tier gap, C vs E.</b> {tier}</p>'
                 '<p>ESTOI is an envelope-correlation intelligibility proxy and '
                 'is close to blind to excitation texture; NISQA is trained on '
                 'perceptual quality and is not. Where the two disagree on the '
                 'same pair of clips, the disagreement is the finding, not an '
                 'error &mdash; play the two clips and decide. The whole point '
                 'of this page is that the audio is on it.</p>'
                 '<p>Condition E exists to make the ladder audible: it is the '
                 'same fixed-point decoder with the L2 mixed-excitation '
                 'crossover and the L4 postfilter/tilt/AGC compiled out '
                 '(<code>-DC2TUBE_L0_ONLY</code>), i.e. a bare impulse train / '
                 'LFSR noise into the same G8 tube. The build script proves the '
                 'guards are inert without the define by comparing the guarded '
                 'binary against a pristine <code>proto/decoder</code> build '
                 'byte-for-byte on a real bitstream.</p></div>')
    parts.append("</body></html>")

    out_path = os.path.join(paths.OUT, "listen.html")
    with open(out_path, "w") as fh:
        fh.write("\n".join(parts) + "\n")
    print(f"wrote {out_path} ({os.path.getsize(out_path)/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
