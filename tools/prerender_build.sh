#!/bin/sh
# Two-pass build for the pre-rendered descriptor table.
#
# The table is generated from a linked image and then linked into the next
# one, so the build is inherently two passes.  The hazard is not that a stale
# table corrupts a packet - a key that names nothing simply misses, and a miss
# renders at run time, which is the behaviour before the table existed.  The
# hazard is that it costs the cycles the table exists to save, silently, and
# that the ONE line of integration (`#include "usb_prerender.inc"` inside
# usb_config.h's INSTANCE_DESCRIPTORS block) is easy to forget: the three
# symbols are weak, so a build without it links them at zero and quietly takes
# the dynamic path for every packet.
#
# So the verify step is part of the build, not a thing to remember.  It fails
# when the table was not linked, when it is stale, when the keys are no longer
# ascending, and when any record disagrees with the descriptors as they exist
# in the FINAL image.
#
# Usage:  tools/prerender_build.sh <demo-dir> [make args...]
#   e.g.  tools/prerender_build.sh demo_gamepad MCU_TYPE=PY32F003x4
set -e

TOOLS=$(cd "$(dirname "$0")" && pwd)
DEMO=${1:?usage: prerender_build.sh <demo-dir> [make args...]}
shift
cd "$DEMO"
NAME=$(basename "$(pwd)")
ELF=Build/$NAME.elf

if ! grep -q '#include "usb_prerender.inc"' usb_config.h; then
    echo "prerender_build: usb_config.h does not include usb_prerender.inc." >&2
    echo "  Add it inside the INSTANCE_DESCRIPTORS block, after the" >&2
    echo "  descriptor_list definition - that is the only place the" >&2
    echo "  descriptor symbols are in scope." >&2
    exit 1
fi

# Pass 1 needs the include to resolve, so seed an empty table if there is
# none.  An empty table is the weak-symbol behaviour, made explicit.
if [ ! -f usb_prerender.inc ]; then
    echo "prerender_build: no usb_prerender.inc yet - seeding an empty one"
    cat > usb_prerender.inc <<'SEED'
/* seeded empty by tools/prerender_build.sh for pass 1; pass 2 overwrites it */
SEED
fi

echo "prerender_build: pass 1"
find .. -name '*.o' -delete
make -f ../Makefile.py32 "$@"

echo "prerender_build: generating the table from pass 1's image"
python3 "$TOOLS/prerender_gen.py" "$ELF" -o usb_prerender.inc

echo "prerender_build: pass 2"
find .. -name '*.o' -delete
make -f ../Makefile.py32 "$@"

echo "prerender_build: verifying the linked table against the final image"
python3 "$TOOLS/prerender_gen.py" "$ELF" --verify
