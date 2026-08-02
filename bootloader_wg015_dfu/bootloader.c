// Thin translation unit: the portable DFU core compiled against THIS
// target's dfu_port.h (single-TU build keeps Makefile.wg015's TARGET.c
// convention and lets the port inlines fold into the core at -Os).
// All logic lives in ../rv003usb/dfu_core.c; all chip code in dfu_port.h.
#include "../rv003usb/dfu_core.c"
