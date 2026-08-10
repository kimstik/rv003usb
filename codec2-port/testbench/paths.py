"""paths.py — locate the codec2-port tree and the oracle binaries.

The testbench lives at <c2port>/testbench/ and reads (never writes) the
merged research tree at <c2port>/{proto,experiments}.  Override with
C2PORT_ROOT when running from a worktree that does not carry those dirs.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
BUILD = os.path.join(HERE, "build")


def c2port_root():
    r = os.environ.get("C2PORT_ROOT")
    if r:
        r = os.path.abspath(r)
    else:
        r = os.path.dirname(HERE)
    if not os.path.isdir(os.path.join(r, "experiments")):
        sys.exit(f"ERROR: no experiments/ under {r}; set C2PORT_ROOT to the "
                 f"codec2-port tree that carries proto/ and experiments/")
    return r


def oracle_bin(name):
    p = os.path.join(BUILD, "codec2", "build_host", "src", name)
    if not os.access(p, os.X_OK):
        sys.exit(f"ERROR: {p} missing — run ./build_oracle.sh first")
    return p


def codec2_src():
    return os.path.join(BUILD, "codec2")
