# SPDX-License-Identifier: GPL-2.0
#
# CXL Type 2 Accelerator Drivers - Out-of-Tree Build System
#
# Build:
#   make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules
#
# Clean:
#   make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean
#
# Install:
#   sudo make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules_install
#   sudo depmod -a

# Module names (each as a separate loadable module)
obj-m := cxl_type2_accel.o cxl_type2_cache.o cxl_type2_mem.o cxl_iomem.o

# Multi-part module for accel driver (includes tracepoints)
cxl_type2_accel-objs := cxl_accel_main.o cxl_tracepoints.o

# Enable tracepoints
ccflags-y := -DCXL_TRACEPOINTS

# Include path for local headers
ccflags-y += -I$(src)

# Check if kernel build directory exists
KERNELDIR ?= /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

all: modules

modules:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) clean

install: modules
	$(MAKE) -C $(KERNELDIR) M=$(PWD) modules_install
	depmod -a

unload:
	-modprobe cxl_type2_accel
	-modprobe cxl_type2_cache
	-modprobe cxl_type2_mem

load:
	modprobe cxl_type2_accel
	modprobe cxl_type2_cache
	modprobe cxl_type2_mem

reload: unload load

.PHONY: all modules clean install unload load reload
