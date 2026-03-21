# CXLAgent

CXL Memory Snooping & Analysis Agent — like Microsoft Recall, but for CXL memory bus traffic.

Captures CXL memory requests via FPGA sysfs APIs, takes coherent snapshots by triggering WBINVD (writeback+invalidate), and uses an LLM agent (Claude) to analyze the captured data for encryption keys, game state, credentials, and data structures.

## Architecture

```
/dev/cxl/cache*  ──►  WBINVD trigger  ──►  Coherent snapshot
/dev/cxl/mem*         (sysfs)              of CXL memory
/dev/mem         ──►  mmap read       ──►  Binary diff engine  ──►  LLM Agent
ftrace           ──►  CXL tracepoints ──►  Pattern detection       (Claude)
                                            - Entropy analysis       │
                                            - AES key schedules      │
                                            - Pointer chains         ▼
                                            - String extraction   Timeline DB
                                            - Counter detection   (SQLite)
```

## Hardware Requirements

- CXL Type 2 accelerators with `cxl_cache` and `cxl_type2_accel` drivers
- FPGA sysfs interface: `init_wbinvd`, `cache_invalid`, `cache_disable`
- Linux 6.x+ with CXL subsystem and tracepoints

## Usage

```bash
pip install -e .

# Show CXL device topology
cxlagent topology

# Take a single snapshot (1MB scan of window 0)
cxlagent snapshot --scan-size 1 --windows 0

# Take snapshot with LLM analysis
cxlagent snapshot --scan-size 16 --analyze

# Continuous monitoring every 5 seconds
cxlagent live --interval 5 --analyze

# Hunt for encryption keys
cxlagent hunt "aes keys" --analyze

# Hunt for game state
cxlagent hunt "game state" --analyze

# Compare two snapshots 2 seconds apart
cxlagent diff --delay 2 --analyze

# View timeline history
cxlagent timeline
cxlagent timeline --analyses
cxlagent timeline --patterns --pattern-type aes_key_schedule
```

## Snapshot Flow

1. **WBINVD** — Write `1` to `/sys/.../cache0/init_wbinvd` to flush all dirty cache lines from the CXL cache to device memory
2. **Read** — mmap `/dev/mem` at CXL window physical addresses to read coherent device memory
3. **Diff** — Binary diff against previous snapshot to detect changed regions
4. **Analyze** — Run pattern detectors (entropy, AES key schedule, pointers, strings) and optionally feed to Claude for deep analysis

## Environment

Set `ANTHROPIC_API_KEY` for LLM analysis features.
