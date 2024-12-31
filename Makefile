
PROJECTS:=bootloader demo_composite_hid demo_hidapi demo_gamepad demo_pikokey_hid testing/cdc_exp testing/demo_midi testing/sandbox testing/test_ethernet
PROJECTS_PY32:=demo_gamepad

all : build build_py32

build :
	for dir in $(PROJECTS); do make -C $$dir build; done

build_py32 :
	for dir in $(PROJECTS_PY32); do make -C $$dir -f ../Makefile.py32; done

clean : $(PROJECTS)
	for dir in $(PROJECTS); do make -C $$dir clean; done
	for dir in $(PROJECTS_PY32); do make -C $$dir -f ../Makefile.py32 clean; done

.PHONY : $(PROJECTS)

