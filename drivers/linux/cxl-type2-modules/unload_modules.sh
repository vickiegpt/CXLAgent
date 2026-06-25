#!/bin/bash
# Unload CXL Type 2 kernel modules

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

echo -e "${GREEN}Unloading CXL Type 2 kernel modules...${NC}"

# Unload in reverse order
echo "Unloading cxl_iomem..."
if lsmod | grep -q cxl_iomem; then
    rmmod cxl_iomem 2>/dev/null || true
fi

echo "Unloading cxl_type2_mem..."
if lsmod | grep -q cxl_type2_mem; then
    rmmod cxl_type2_mem 2>/dev/null || true
fi

echo "Unloading cxl_type2_cache..."
if lsmod | grep -q cxl_type2_cache; then
    rmmod cxl_type2_cache 2>/dev/null || true
fi

echo "Unloading cxl_type2_accel..."
if lsmod | grep -q cxl_type2_accel; then
    rmmod cxl_type2_accel 2>/dev/null || true
fi

# Verify
if lsmod | grep -q cxl; then
    echo -e "${YELLOW}Some CXL modules still loaded:${NC}"
    lsmod | grep cxl
else
    echo -e "${GREEN}✓ All CXL modules unloaded${NC}"
fi
