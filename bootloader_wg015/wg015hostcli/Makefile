# Host CLI for the WG015 bootloader.  Needs hidapi (Debian/Ubuntu:
# apt install libhidapi-dev) and the cross toolchain for the blobs.

PREFIX ?= riscv64-unknown-elf-

HIDAPI_FLAGS := $(shell pkg-config --cflags --libs hidapi-hidraw 2>/dev/null || echo -lhidapi-hidraw)

all : wg015bflash

../blobs/blobs.h :
	$(MAKE) -C ../blobs PREFIX=$(PREFIX)

wg015bflash : wg015bflash.c ../blobs/blobs.h
	gcc -O2 -Wall -o $@ $< $(HIDAPI_FLAGS)

# Compile-only check for environments without hidapi installed (the source
# falls back to a stub via __has_include and refuses to run).
syntax-check : ../blobs/blobs.h
	gcc -fsyntax-only -Wall wg015bflash.c
	@echo "syntax OK"

clean :
	rm -f wg015bflash

.PHONY : all syntax-check clean
