#!/bin/bash
# Load CXL Type 2 kernel modules

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULES_DIR="$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root${NC}"
   exit 1
fi

echo -e "${GREEN}Loading CXL Type 2 kernel modules...${NC}"

# Check kernel headers
if [[ ! -d "/lib/modules/$(uname -r)/build" ]]; then
    echo -e "${RED}Kernel headers not found. Install: linux-headers-$(uname -r)${NC}"
    exit 1
fi

# Build modules if not already built
if [[ ! -f "${MODULES_DIR}/cxl_type2_cache.ko" ]]; then
    echo -e "${YELLOW}Building modules first...${NC}"
    cd "${MODULES_DIR}"
    make
fi

cd "${MODULES_DIR}"

# Load modules in order
echo "Loading cxl_type2_accel..."
insmod cxl_type2_accel.ko || echo -e "${YELLOW}cxl_type2_accel load failed (may already be loaded)${NC}"

echo "Loading cxl_type2_cache..."
insmod cxl_type2_cache.ko || echo -e "${YELLOW}cxl_type2_cache load failed (may already be loaded)${NC}"

echo "Loading cxl_type2_mem..."
insmod cxl_type2_mem.ko || echo -e "${YELLOW}cxl_type2_mem load failed (may already be loaded)${NC}"

echo "Loading cxl_iomem..."
insmod cxl_iomem.ko || echo -e "${YELLOW}cxl_iomem load failed (may already be loaded)${NC}"

# Verify
echo -e "${GREEN}Verifying module loading...${NC}"
if lsmod | grep -q cxl_type2; then
    echo -e "${GREEN}✓ CXL modules loaded successfully${NC}"
    lsmod | grep cxl_type2
else
    echo -e "${RED}✗ CXL modules failed to load${NC}"
    dmesg | tail -20
    exit 1
fi

# Check sysfs
if [[ -d "/sys/bus/cxl/devices" ]]; then
    echo -e "${GREEN}✓ Sysfs interface available${NC}"
    ls -la /sys/bus/cxl/devices/ 2>/dev/null || echo "  (No devices yet - hardware required)"
else
    echo -e "${YELLOW}⚠ Sysfs not fully populated (may require hardware)${NC}"
fi

# Check tracepoints
if [[ -d "/sys/kernel/tracing/events/cxl" ]]; then
    echo -e "${GREEN}✓ Tracepoints available${NC}"
    ls -la /sys/kernel/tracing/events/cxl/
else
    echo -e "${YELLOW}⚠ Tracepoints not registered (check kernel config)${NC}"
fi

echo -e "${GREEN}CXL Type 2 driver setup complete!${NC}"
