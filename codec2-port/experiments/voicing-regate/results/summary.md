## Per-file: swap (rule voicing) vs stock decode

| file | flip% | segSNR mean dB | ESTOI(vs stock) | LSD dB | NMR dB | ESTOI(orig,stock) | dESTOI(orig) | SD>3dB frames near flips±2 | near flips|trans±2 |
|---|---|---|---|---|---|---|---|---|---|
| hts1 | 9.33 | 2.40 | 0.9403 | 5.63 | -7.5 | 0.5917 | -0.0095 | 38% | 46% |
| hts1a | 9.33 | 7.07 | 0.9325 | 4.82 | -8.1 | 0.5637 | -0.0130 | 39% | 48% |
| hts2a | 8.00 | 3.54 | 0.9252 | 5.63 | -8.9 | 0.5815 | +0.0158 | 37% | 46% |
| kristoff | 4.60 | -0.35 | 0.8979 | 6.23 | -7.5 | 0.3802 | -0.0051 | 15% | 21% |
| testframes_700d | 35.00 | -2.75 | 0.1869 | 7.17 | -4.1 | -0.0205 | -0.0732 | 87% | 94% |
| ve9qrp | 10.02 | -2.78 | 0.8375 | 6.43 | -6.9 | 0.5150 | +0.0027 | 35% | 50% |
| ve9qrp_10s | 10.60 | -2.46 | 0.8296 | 6.37 | -6.6 | 0.5160 | -0.0032 | 37% | 53% |

## Per-file: random control (matched flip rate) vs stock decode
(each cell: mean over seeds 1,2,3)

| file | segSNR mean dB | ESTOI(vs stock) | LSD dB | NMR dB | dESTOI(orig) |
|---|---|---|---|---|---|
| hts1 | -1.92 | 0.8918 | 6.52 | -6.7 | -0.0235 |
| hts1a | -1.50 | 0.8899 | 6.41 | -6.9 | -0.0217 |
| hts2a | -0.69 | 0.9015 | 6.69 | -7.7 | -0.0167 |
| kristoff | -0.27 | 0.8973 | 6.38 | -7.0 | +0.0022 |
| testframes_700d | 1.10 | 0.1942 | 6.46 | -4.8 | -0.0255 |
| ve9qrp | -2.76 | 0.8038 | 6.70 | -6.5 | -0.0154 |
| ve9qrp_10s | -2.57 | 0.8093 | 6.62 | -6.3 | -0.0110 |

## Aggregate (mean over files)

| version | segSNR mean dB | ESTOI(vs stock) | LSD dB | NMR dB | dESTOI(orig) |
|---|---|---|---|---|---|
| swap (rule) | 0.67 | 0.7928 | 6.04 | -7.1 | -0.0122 |
| random ctrl | -1.23 | 0.7697 | 6.54 | -6.6 | -0.0159 |
| swap, speech only (no testframes_700d) | 1.23 | 0.8938 | 5.85 | -7.6 | -0.0020 |
| random, speech only | -1.62 | 0.8656 | 6.55 | -6.9 | -0.0143 |

## Float noise floor (-O2 vs -O3 full chain, hts1a)

segSNR mean 35.00 dB (clamp), ESTOI 1.0000, LSD 0.00 dB, NMR identical (-inf) dB

## WARP-Q (raw score = DTW distance, lower = closer)

| file | stock vs swap | stock vs rand (mean) | orig vs stock | orig vs swap | d(orig) |
|---|---|---|---|---|---|
| hts1 | 1.224 | 1.398 | 1.954 | 1.906 | -0.048 |
| hts1a | 1.128 | 1.381 | 1.916 | 1.853 | -0.063 |
| hts2a | 1.184 | 1.487 | 2.235 | 2.209 | -0.026 |
| kristoff | 1.468 | 1.533 | 2.496 | 2.455 | -0.041 |
| testframes_700d | 2.846 | 2.705 | 3.158 | 3.148 | -0.010 |
| ve9qrp | 1.433 | 1.611 | 2.164 | 2.153 | -0.011 |
| ve9qrp_10s | 1.474 | 1.590 | 2.141 | 2.153 | +0.012 |
| **mean** | 1.537 | 1.672 | 2.295 | 2.268 | -0.027 |

WARP-Q noise floor (hts1a, stock -O3 decode vs -O2 full chain): 0.515
