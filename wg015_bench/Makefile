# wg015_bench/Makefile — P1 calibration bench set for the WG015 port
# (PLAN.md §4 P1).  Wraps ../rv003usb/wg015/Makefile.wg015 with TARGET=bench.
#
# Why SOURCES is overridden on the sub-make command line instead of only
# ADDITIONAL_C_FILES: Makefile.wg015's SOURCES ':=' hardwires rv003usb.c/.S,
# which do not compile for WG015 until the P2+ port tasks land
# (Makefile.wg015:11-12).  A command-line variable beats the ':=', so the
# bench links exactly: startup + stdio stub + menu + benches, nothing else.
#
#   make [PREFIX=riscv64-unknown-elf-] [all|flash|clean]

TARGET    := bench
WG015_DIR := ../rv003usb/wg015
PREFIX    ?= riscv-none-elf-

ADDITIONAL_C_FILES := \
	bench_common.c \
	bench_kernels.S \
	bench1_gpio.c \
	bench2_branch.c \
	bench3_irq.c \
	bench4_flash.c \
	bench5_slot.c \
	bench6_micc.c

BASE_SOURCES := main.c \
	$(WG015_DIR)/startup_wg015.S \
	$(WG015_DIR)/wg015_stdio_stub.c

all size flash clean :
	$(MAKE) -f $(WG015_DIR)/Makefile.wg015 TARGET=$(TARGET) PREFIX=$(PREFIX) \
		SOURCES="$(BASE_SOURCES) $(ADDITIONAL_C_FILES)" $@

.PHONY : all size flash clean
