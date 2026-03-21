"""LLM Analysis Agent — uses Claude to interpret CXL memory snapshots.

Feeds captured data, diffs, and detected patterns to Claude for:
- Identifying what application/process owns the memory
- Reconstructing data structures from raw bytes
- Spotting encryption keys, game state, auth tokens
- Building a Recall-style narrative of CXL memory activity
"""

import json
import os
import time
from typing import Optional

import anthropic

from .db import TimelineDB
from .patterns import Pattern, PatternType, analyze_chunk, analyze_diff, shannon_entropy
from .snapshot import DiffRegion, MemoryChunk, Snapshot


SYSTEM_PROMPT = """\
You are CXLAgent, a hardware security research assistant specialized in \
analyzing CXL (Compute Express Link) memory captures.

You receive snapshots of CXL device memory — raw hex dumps, binary diffs \
between snapshots, and pattern detection results (entropy analysis, key \
schedule detection, pointer chains, strings).

Your job:
1. **Identify** what the memory contains (model weights, KV cache, game state, \
   encryption keys, auth tokens, protocol buffers, etc.)
2. **Reconstruct** data structures from raw bytes when possible
3. **Highlight** security-relevant findings (key material, credentials, \
   sensitive data exposed in CXL memory)
4. **Narrate** changes between snapshots — what happened on the CXL bus

Output format:
- Lead with the most important finding
- Use hex addresses and offsets
- Show reconstructed structs in C-like notation
- Rate confidence: HIGH / MEDIUM / LOW
- Be concise but thorough

Context: This system has CXL Type 2 accelerators (Intel FPGA) with 128MB \
cache per device, 64-byte cache lines, connected via PCIe. The CXL memory \
is used for ML inference (MoE expert weights, KV cache) via NVIDIA Dynamo.
"""


def _format_hex_dump(data: bytes, base_offset: int = 0, max_lines: int = 16) -> str:
    """Format bytes as a hex dump string."""
    lines = []
    for i in range(0, min(len(data), max_lines * 16), 16):
        hex_part = " ".join(f"{b:02x}" for b in data[i : i + 16])
        ascii_part = "".join(
            chr(b) if 32 <= b < 127 else "." for b in data[i : i + 16]
        )
        lines.append(f"  {base_offset + i:08x}: {hex_part:<48s}  {ascii_part}")
    if len(data) > max_lines * 16:
        lines.append(f"  ... ({len(data) - max_lines * 16} more bytes)")
    return "\n".join(lines)


def _format_patterns(patterns: list[Pattern]) -> str:
    """Format pattern list for the LLM prompt."""
    if not patterns:
        return "  (no patterns detected)"
    lines = []
    for p in sorted(patterns, key=lambda x: -x.confidence):
        lines.append(f"  [{p.pattern_type.value}] +0x{p.offset:x} ({p.size}B) "
                      f"conf={p.confidence:.0%}: {p.description}")
        if p.metadata:
            for k, v in p.metadata.items():
                if k != "string":
                    lines.append(f"    {k}: {v}")
    return "\n".join(lines)


class CxlAgent:
    """LLM-powered CXL memory analysis agent."""

    def __init__(self, db: Optional[TimelineDB] = None,
                 model: str = "claude-sonnet-4-20250514"):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Set ANTHROPIC_API_KEY environment variable to use the LLM agent"
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.db = db

    def analyze_snapshot(self, snap: Snapshot,
                         patterns: Optional[list[Pattern]] = None) -> str:
        """Analyze a single snapshot with the LLM."""
        # Build prompt
        sections = [f"# CXL Memory Snapshot #{snap.snapshot_id}"]
        sections.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snap.timestamp))}")
        sections.append(f"Capture: {snap.capture_duration_ms:.1f}ms, {snap.total_bytes} bytes total")
        sections.append(f"WBINVD triggered on: {snap.wbinvd_triggered}")
        sections.append(f"Cache states: {json.dumps(snap.cache_states, indent=2)}")

        # Include non-empty chunks with hex dumps
        non_empty = snap.non_empty_chunks
        sections.append(f"\n## Memory Regions ({len(non_empty)} non-empty chunks)")
        for chunk in non_empty[:20]:  # limit to 20 chunks for context
            ent = shannon_entropy(chunk.data)
            sections.append(
                f"\n### Window {chunk.window_index} +0x{chunk.offset:x} "
                f"(phys 0x{chunk.phys_addr:x}, {chunk.size}B, entropy={ent:.2f})"
            )
            sections.append(_format_hex_dump(chunk.data, chunk.offset))

            # Run pattern detection if not provided
            chunk_patterns = patterns if patterns else analyze_chunk(chunk)
            if chunk_patterns:
                sections.append("Patterns:")
                sections.append(_format_patterns(chunk_patterns))

        # Include trace events
        if snap.trace_events:
            sections.append(f"\n## CXL Trace Events ({len(snap.trace_events)})")
            for ev in snap.trace_events[:50]:
                sections.append(
                    f"  [{ev.event_type}] {ev.transaction_type} "
                    f"DPA=0x{ev.dpa:x} HPA=0x{ev.hpa:x} dev={ev.memdev}"
                )

        prompt = "\n".join(sections)
        prompt += "\n\nAnalyze this CXL memory snapshot. What does the memory contain? Any security-relevant findings?"

        return self._query(prompt, snap.snapshot_id, "snapshot")

    def analyze_diff(self, old_snap: Snapshot, new_snap: Snapshot,
                     diffs: list[DiffRegion]) -> str:
        """Analyze changes between two snapshots."""
        sections = [
            f"# CXL Memory Diff: Snapshot #{old_snap.snapshot_id} → #{new_snap.snapshot_id}",
            f"Time delta: {new_snap.timestamp - old_snap.timestamp:.3f}s",
            f"Changed regions: {len(diffs)}",
            f"Total bytes changed: {sum(d.changed_bytes for d in diffs)}",
        ]

        for diff in diffs[:15]:  # limit for context
            sections.append(
                f"\n## Window {diff.window_index} +0x{diff.offset:x} "
                f"(phys 0x{diff.phys_addr:x}, {diff.changed_bytes}/{diff.size}B changed, "
                f"{diff.change_ratio:.0%})"
            )
            sections.append("Before:")
            sections.append(_format_hex_dump(diff.old_data, diff.offset, max_lines=8))
            sections.append("After:")
            sections.append(_format_hex_dump(diff.new_data, diff.offset, max_lines=8))

            diff_patterns = analyze_diff(diff)
            if diff_patterns:
                sections.append("New patterns:")
                sections.append(_format_patterns(diff_patterns))

        prompt = "\n".join(sections)
        prompt += "\n\nAnalyze these CXL memory changes. What activity caused them? Any security-relevant changes?"

        return self._query(prompt, new_snap.snapshot_id, "diff")

    def hunt(self, snap: Snapshot, target: str) -> str:
        """Targeted hunt for specific patterns in a snapshot."""
        sections = [
            f"# CXL Memory Hunt: '{target}'",
            f"Snapshot #{snap.snapshot_id}, {snap.total_bytes} bytes",
        ]

        # Run all pattern detectors
        all_patterns = []
        for chunk in snap.non_empty_chunks:
            chunk_patterns = analyze_chunk(chunk)
            for p in chunk_patterns:
                all_patterns.append((chunk, p))

        # Filter by relevance to target
        target_lower = target.lower()
        relevant = []
        for chunk, p in all_patterns:
            score = 0
            if target_lower in p.pattern_type.value:
                score += 2
            if target_lower in p.description.lower():
                score += 2
            # Always include high-confidence patterns
            if p.confidence > 0.8:
                score += 1
            # Key-related hunts
            if "key" in target_lower and p.pattern_type in (
                PatternType.AES_KEY_SCHEDULE,
                PatternType.HIGH_ENTROPY,
                PatternType.RSA_PRIME_CANDIDATE,
            ):
                score += 3
            # Game-related hunts
            if "game" in target_lower and p.pattern_type in (
                PatternType.COUNTER,
                PatternType.POINTER_CHAIN,
                PatternType.ASCII_STRING,
            ):
                score += 2
            if score > 0:
                relevant.append((score, chunk, p))

        relevant.sort(key=lambda x: -x[0])

        sections.append(f"\n## Relevant Findings ({len(relevant)} matches)")
        for score, chunk, p in relevant[:20]:
            sections.append(
                f"\n### {p.pattern_type.value} @ Window {chunk.window_index} "
                f"+0x{chunk.offset + p.offset:x} (phys 0x{chunk.phys_addr + p.offset:x})"
            )
            sections.append(f"Confidence: {p.confidence:.0%}")
            sections.append(f"Description: {p.description}")
            sections.append("Hex dump:")
            sections.append(_format_hex_dump(
                chunk.data[p.offset : p.offset + p.size],
                chunk.offset + p.offset,
                max_lines=8,
            ))

        prompt = "\n".join(sections)
        prompt += f"\n\nHunt target: '{target}'. Analyze these CXL memory regions for {target}. Reconstruct any found data structures."

        return self._query(prompt, snap.snapshot_id, f"hunt:{target}")

    def _query(self, prompt: str, snapshot_id: Optional[int],
               mode: str) -> str:
        """Send prompt to Claude and return response."""
        t0 = time.monotonic()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens
        elapsed = (time.monotonic() - t0) * 1000

        # Save to DB if available
        if self.db:
            self.db.save_analysis(
                snapshot_id=snapshot_id,
                mode=mode,
                prompt_summary=prompt[:500],
                response=text,
                tokens_used=tokens,
            )

        return text
