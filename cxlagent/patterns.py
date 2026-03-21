"""Pattern Detection — identify interesting structures in CXL memory.

Heuristics for:
- Entropy analysis (high entropy = encrypted/compressed/key material)
- AES key schedule detection
- RSA prime candidates
- Pointer chains (64-bit values in plausible virtual/physical address ranges)
- String extraction (ASCII/UTF-8)
- Structure fingerprinting (repeated patterns, alignment)
"""

import math
import re
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .snapshot import DiffRegion, MemoryChunk


class PatternType(str, Enum):
    HIGH_ENTROPY = "high_entropy"
    LOW_ENTROPY = "low_entropy"
    AES_KEY_SCHEDULE = "aes_key_schedule"
    RSA_PRIME_CANDIDATE = "rsa_prime_candidate"
    POINTER_CHAIN = "pointer_chain"
    ASCII_STRING = "ascii_string"
    REPEATED_STRUCT = "repeated_struct"
    ZERO_TRANSITION = "zero_transition"  # boundary between used/unused memory
    COUNTER = "counter"  # incrementing values


@dataclass
class Pattern:
    """A detected pattern in CXL memory."""
    pattern_type: PatternType
    offset: int            # offset within the chunk/region
    size: int
    confidence: float      # 0.0 - 1.0
    description: str
    data_preview: bytes    # first N bytes of the pattern
    metadata: dict         # type-specific metadata

    def summary(self) -> str:
        preview = self.data_preview[:32].hex()
        return (
            f"[{self.pattern_type.value}] @ +0x{self.offset:x} "
            f"({self.size}B, conf={self.confidence:.0%}): "
            f"{self.description} | {preview}..."
        )


# ---------------------------------------------------------------------------
# Entropy
# ---------------------------------------------------------------------------

def shannon_entropy(data: bytes) -> float:
    """Calculate Shannon entropy in bits per byte (0-8)."""
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    n = len(data)
    entropy = 0.0
    for count in freq:
        if count > 0:
            p = count / n
            entropy -= p * math.log2(p)
    return entropy


def entropy_blocks(data: bytes, block_size: int = 64) -> list[tuple[int, float]]:
    """Calculate per-block entropy."""
    results = []
    for i in range(0, len(data), block_size):
        block = data[i : i + block_size]
        if len(block) >= 16:
            results.append((i, shannon_entropy(block)))
    return results


# ---------------------------------------------------------------------------
# AES key schedule detection
# ---------------------------------------------------------------------------

# AES S-box (first 16 bytes for quick validation)
_AES_SBOX = bytes([
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5,
    0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
])

# AES round constants
_AES_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _sub_word(word: int) -> int:
    """AES SubWord operation using full S-box."""
    sbox = [
        0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
        0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
        0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
        0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
        0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
        0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
        0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
        0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
        0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
        0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
        0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
        0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
        0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
        0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
        0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
        0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
    ]
    b = [(word >> (24 - 8 * i)) & 0xFF for i in range(4)]
    s = [sbox[x] for x in b]
    return (s[0] << 24) | (s[1] << 16) | (s[2] << 8) | s[3]


def _rot_word(word: int) -> int:
    return ((word << 8) & 0xFFFFFFFF) | (word >> 24)


def detect_aes_key_schedule(data: bytes) -> list[Pattern]:
    """Detect AES-128/192/256 key schedules in data."""
    patterns = []
    # AES-128: 16-byte key expands to 176 bytes (11 round keys)
    # AES-256: 32-byte key expands to 240 bytes (15 round keys)
    for key_len, num_words, schedule_len in [(16, 4, 176), (24, 6, 208), (32, 8, 240)]:
        for i in range(0, len(data) - schedule_len + 1, 4):
            if _check_aes_schedule(data[i : i + schedule_len], num_words):
                patterns.append(Pattern(
                    pattern_type=PatternType.AES_KEY_SCHEDULE,
                    offset=i,
                    size=schedule_len,
                    confidence=0.95,
                    description=f"AES-{key_len*8} key schedule ({schedule_len}B)",
                    data_preview=data[i : i + min(32, schedule_len)],
                    metadata={"key_size": key_len, "key_hex": data[i : i + key_len].hex()},
                ))
    return patterns


def _check_aes_schedule(data: bytes, nk: int) -> bool:
    """Verify if data matches AES key expansion for Nk-word key."""
    if len(data) < (nk + 1) * 4:
        return False

    words = []
    for j in range(len(data) // 4):
        words.append(struct.unpack(">I", data[j * 4 : j * 4 + 4])[0])

    # Verify key schedule relationships
    matches = 0
    checks = 0
    for j in range(nk, min(len(words), 4 * (nk + 7))):
        checks += 1
        if j % nk == 0:
            rcon_idx = (j // nk) - 1
            if rcon_idx >= len(_AES_RCON):
                break
            expected = words[j - nk] ^ _sub_word(_rot_word(words[j - 1])) ^ (_AES_RCON[rcon_idx] << 24)
            if words[j] == expected:
                matches += 1
        elif nk == 8 and j % nk == 4:
            expected = words[j - nk] ^ _sub_word(words[j - 1])
            if words[j] == expected:
                matches += 1
        else:
            expected = words[j - nk] ^ words[j - 1]
            if words[j] == expected:
                matches += 1

    return checks > 0 and matches / checks > 0.8


# ---------------------------------------------------------------------------
# Pointer chain detection
# ---------------------------------------------------------------------------

# Plausible address ranges
_KERNEL_VA_START = 0xFFFF800000000000
_KERNEL_VA_END = 0xFFFFFFFFFFFFFFFF
_USER_VA_START = 0x0000000000001000
_USER_VA_END = 0x00007FFFFFFFFFFF
_CXL_PA_START = 0x0000004000000000  # typical CXL physical address range
_CXL_PA_END = 0x0000200000000000


def _is_plausible_ptr(val: int) -> bool:
    """Check if a 64-bit value looks like a plausible pointer."""
    if val == 0 or val == 0xFFFFFFFFFFFFFFFF:
        return False
    # Check alignment (most pointers are at least 8-byte aligned)
    if val & 0x7:
        return False
    return (
        (_USER_VA_START <= val <= _USER_VA_END)
        or (_KERNEL_VA_START <= val <= _KERNEL_VA_END)
        or (_CXL_PA_START <= val <= _CXL_PA_END)
    )


def detect_pointer_chains(data: bytes, base_addr: int = 0) -> list[Pattern]:
    """Find runs of plausible 64-bit pointers."""
    patterns = []
    if len(data) < 8:
        return patterns

    consecutive = 0
    chain_start = 0

    for i in range(0, len(data) - 7, 8):
        val = struct.unpack("<Q", data[i : i + 8])[0]
        if _is_plausible_ptr(val):
            if consecutive == 0:
                chain_start = i
            consecutive += 1
        else:
            if consecutive >= 3:
                chain_size = consecutive * 8
                patterns.append(Pattern(
                    pattern_type=PatternType.POINTER_CHAIN,
                    offset=chain_start,
                    size=chain_size,
                    confidence=min(0.5 + consecutive * 0.1, 0.95),
                    description=f"Pointer chain ({consecutive} ptrs)",
                    data_preview=data[chain_start : chain_start + min(32, chain_size)],
                    metadata={
                        "count": consecutive,
                        "first_ptr": hex(struct.unpack("<Q", data[chain_start:chain_start+8])[0]),
                    },
                ))
            consecutive = 0

    # Handle chain at end of data
    if consecutive >= 3:
        chain_size = consecutive * 8
        patterns.append(Pattern(
            pattern_type=PatternType.POINTER_CHAIN,
            offset=chain_start,
            size=chain_size,
            confidence=min(0.5 + consecutive * 0.1, 0.95),
            description=f"Pointer chain ({consecutive} ptrs)",
            data_preview=data[chain_start : chain_start + min(32, chain_size)],
            metadata={"count": consecutive},
        ))

    return patterns


# ---------------------------------------------------------------------------
# String extraction
# ---------------------------------------------------------------------------

def extract_strings(data: bytes, min_length: int = 6) -> list[Pattern]:
    """Extract printable ASCII strings."""
    patterns = []
    current = bytearray()
    start = 0

    for i, b in enumerate(data):
        if 0x20 <= b < 0x7F or b in (0x09, 0x0A, 0x0D):
            if not current:
                start = i
            current.append(b)
        else:
            if len(current) >= min_length:
                s = current.decode("ascii", errors="replace")
                patterns.append(Pattern(
                    pattern_type=PatternType.ASCII_STRING,
                    offset=start,
                    size=len(current),
                    confidence=0.9,
                    description=f'String: "{s[:60]}"',
                    data_preview=bytes(current[:32]),
                    metadata={"string": s},
                ))
            current = bytearray()

    if len(current) >= min_length:
        s = current.decode("ascii", errors="replace")
        patterns.append(Pattern(
            pattern_type=PatternType.ASCII_STRING,
            offset=start,
            size=len(current),
            confidence=0.9,
            description=f'String: "{s[:60]}"',
            data_preview=bytes(current[:32]),
            metadata={"string": s},
        ))

    return patterns


# ---------------------------------------------------------------------------
# Counter / incrementing value detection
# ---------------------------------------------------------------------------

def detect_counters(data: bytes) -> list[Pattern]:
    """Detect incrementing 32-bit or 64-bit counter values."""
    patterns = []

    # Check 64-bit incrementing sequences
    if len(data) >= 24:
        for i in range(0, len(data) - 23, 8):
            vals = struct.unpack("<QQQ", data[i : i + 24])
            if vals[1] == vals[0] + 1 and vals[2] == vals[1] + 1:
                # Found incrementing sequence, extend
                count = 3
                for j in range(i + 24, len(data) - 7, 8):
                    v = struct.unpack("<Q", data[j : j + 8])[0]
                    if v == vals[0] + count:
                        count += 1
                    else:
                        break
                if count >= 3:
                    patterns.append(Pattern(
                        pattern_type=PatternType.COUNTER,
                        offset=i,
                        size=count * 8,
                        confidence=0.85,
                        description=f"64-bit counter: {vals[0]}..{vals[0]+count-1} ({count} values)",
                        data_preview=data[i : i + min(32, count * 8)],
                        metadata={"width": 64, "start_val": vals[0], "count": count},
                    ))

    return patterns


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

def analyze_chunk(chunk: MemoryChunk) -> list[Pattern]:
    """Run all pattern detectors on a memory chunk."""
    if chunk.is_empty:
        return []

    patterns = []
    data = chunk.data

    # Entropy analysis
    ent = shannon_entropy(data)
    if ent > 7.5:
        patterns.append(Pattern(
            pattern_type=PatternType.HIGH_ENTROPY,
            offset=0,
            size=len(data),
            confidence=min((ent - 7.0) / 1.0, 1.0),
            description=f"High entropy region ({ent:.2f} bits/byte) — possible key/encrypted data",
            data_preview=data[:32],
            metadata={"entropy": ent},
        ))
    elif 0.1 < ent < 2.0 and len(data) >= 64:
        patterns.append(Pattern(
            pattern_type=PatternType.LOW_ENTROPY,
            offset=0,
            size=len(data),
            confidence=0.6,
            description=f"Low entropy region ({ent:.2f} bits/byte) — structured/repetitive data",
            data_preview=data[:32],
            metadata={"entropy": ent},
        ))

    # AES key schedule
    patterns.extend(detect_aes_key_schedule(data))

    # Pointer chains
    patterns.extend(detect_pointer_chains(data, base_addr=chunk.phys_addr))

    # Strings
    patterns.extend(extract_strings(data))

    # Counters
    patterns.extend(detect_counters(data))

    return patterns


def analyze_diff(diff: DiffRegion) -> list[Pattern]:
    """Run pattern detectors on a diff region (new data)."""
    chunk = MemoryChunk(
        window_index=diff.window_index,
        offset=diff.offset,
        size=diff.size,
        data=diff.new_data,
        phys_addr=diff.phys_addr,
    )
    return analyze_chunk(chunk)
