"""CLI interface for CXLAgent.

Commands:
  topology    — Show CXL device topology
  snapshot    — Take a single snapshot and analyze
  live        — Continuous monitoring with periodic snapshots + LLM analysis
  hunt        — Targeted search for specific patterns (keys, game state, etc.)
  timeline    — Show snapshot/analysis history from the database
  diff        — Compare two snapshots
"""

import argparse
import signal
import sys
import time
from typing import Optional

from .capture import CxlTopology, CxlTracer
from .db import TimelineDB
from .patterns import analyze_chunk, analyze_diff
from .snapshot import SnapshotEngine


def cmd_topology(args):
    """Show CXL device topology."""
    topo = CxlTopology.discover()
    print(topo.summary())
    print(f"\nTotal CXL memory: {sum(w.size for w in topo.windows) / (1024**3):.1f} GB across {len(topo.windows)} windows")
    print(f"Cache devices: {len(topo.caches)} ({sum(c.size for c in topo.caches) / (1024**2):.0f} MB total)")


def cmd_snapshot(args):
    """Take a single CXL memory snapshot."""
    topo = CxlTopology.discover()
    engine = SnapshotEngine(
        topology=topo,
        scan_size=args.scan_size * 1024 * 1024,
        chunk_size=args.chunk_size,
    )
    db = TimelineDB()

    print(f"Taking snapshot (scan {args.scan_size}MB per window, chunk {args.chunk_size}B)...")

    windows = [int(w) for w in args.windows.split(",")] if args.windows else None
    snap = engine.take_snapshot(
        windows=windows,
        trigger_wbinvd=not args.no_wbinvd,
        scan_offset=args.offset,
    )

    print(snap.summary())
    db.save_snapshot(snap)

    # Run pattern detection
    all_patterns = []
    for chunk in snap.non_empty_chunks:
        patterns = analyze_chunk(chunk)
        all_patterns.extend(patterns)
        db.save_patterns(snap.snapshot_id, patterns, base_phys_addr=chunk.phys_addr)

    print(f"\nDetected {len(all_patterns)} patterns:")
    for p in sorted(all_patterns, key=lambda x: -x.confidence)[:20]:
        print(f"  {p.summary()}")

    # LLM analysis if requested
    if args.analyze:
        _run_llm_analysis(snap, all_patterns, db, mode="snapshot")

    db.close()


def cmd_live(args):
    """Continuous CXL memory monitoring."""
    topo = CxlTopology.discover()
    engine = SnapshotEngine(
        topology=topo,
        scan_size=args.scan_size * 1024 * 1024,
        chunk_size=args.chunk_size,
    )
    db = TimelineDB()
    tracer = CxlTracer()

    # Enable tracepoints
    if not args.no_trace:
        tracer.enable()
        print("CXL tracepoints enabled")

    windows = [int(w) for w in args.windows.split(",")] if args.windows else None
    interval = args.interval
    running = True

    def _sigint(sig, frame):
        nonlocal running
        running = False
        print("\nStopping...")

    signal.signal(signal.SIGINT, _sigint)

    print(f"Live monitoring: snapshot every {interval}s, scan {args.scan_size}MB")
    print("Press Ctrl+C to stop\n")

    prev_snap = None
    cycle = 0

    while running:
        cycle += 1
        snap = engine.take_snapshot(
            windows=windows,
            trigger_wbinvd=True,
        )

        # Collect trace events
        if not args.no_trace:
            snap.trace_events = tracer.read_trace()
            tracer.clear_trace()

        db.save_snapshot(snap)

        # Pattern detection
        all_patterns = []
        for chunk in snap.non_empty_chunks:
            patterns = analyze_chunk(chunk)
            all_patterns.extend(patterns)
            db.save_patterns(snap.snapshot_id, patterns, base_phys_addr=chunk.phys_addr)

        # Diff with previous
        diffs = []
        if prev_snap:
            diffs = engine.diff(prev_snap, snap)
            if diffs:
                db.save_diffs(prev_snap.snapshot_id, snap.snapshot_id, diffs)

        # Print status
        n_changes = sum(d.changed_bytes for d in diffs)
        n_events = len(snap.trace_events)
        interesting = [p for p in all_patterns if p.confidence > 0.7]

        print(
            f"[{cycle:04d}] {snap.summary()} | "
            f"diff={n_changes}B changed, {len(diffs)} regions | "
            f"trace={n_events} events | "
            f"{len(interesting)} interesting patterns"
        )

        for p in interesting[:5]:
            print(f"  -> {p.summary()}")

        # LLM analysis on interesting changes
        if args.analyze and (interesting or n_changes > 1024):
            if diffs and prev_snap:
                _run_llm_diff(prev_snap, snap, diffs, db)
            elif interesting:
                _run_llm_analysis(snap, all_patterns, db, mode="live")

        prev_snap = snap

        # Wait for next cycle
        if running:
            time.sleep(interval)

    tracer.disable()
    db.close()
    print("Done.")


def cmd_hunt(args):
    """Targeted pattern search in CXL memory."""
    topo = CxlTopology.discover()
    engine = SnapshotEngine(
        topology=topo,
        scan_size=args.scan_size * 1024 * 1024,
        chunk_size=args.chunk_size,
    )
    db = TimelineDB()

    target = args.target
    print(f"Hunting for: '{target}'")
    print(f"Taking snapshot...")

    windows = [int(w) for w in args.windows.split(",")] if args.windows else None
    snap = engine.take_snapshot(windows=windows, trigger_wbinvd=True)
    db.save_snapshot(snap)

    print(snap.summary())

    # Run all pattern detectors
    all_patterns = []
    for chunk in snap.non_empty_chunks:
        patterns = analyze_chunk(chunk)
        all_patterns.extend(patterns)
        db.save_patterns(snap.snapshot_id, patterns, base_phys_addr=chunk.phys_addr)

    # Filter by hunt target
    target_lower = target.lower()
    relevant = []
    for p in all_patterns:
        if (
            target_lower in p.pattern_type.value
            or target_lower in p.description.lower()
            or ("key" in target_lower and "key" in p.pattern_type.value.lower())
            or ("key" in target_lower and "entropy" in p.pattern_type.value.lower())
            or p.confidence > 0.85
        ):
            relevant.append(p)

    print(f"\nFound {len(relevant)} relevant patterns:")
    for p in sorted(relevant, key=lambda x: -x.confidence):
        print(f"  {p.summary()}")

    # LLM analysis
    if args.analyze:
        from .agent import CxlAgent
        agent = CxlAgent(db=db, model=args.model)
        print(f"\nAsking Claude to analyze hunt results...")
        result = agent.hunt(snap, target)
        print(f"\n{'='*60}")
        print(result)
        print(f"{'='*60}")

    db.close()


def cmd_timeline(args):
    """Show snapshot and analysis history."""
    db = TimelineDB()

    if args.analyses:
        analyses = db.get_recent_analyses(limit=args.limit)
        print(f"Recent LLM analyses ({len(analyses)}):\n")
        for a in analyses:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a["timestamp"]))
            print(f"[{ts}] mode={a['mode']} snapshot=#{a['snapshot_id']} tokens={a['tokens_used']}")
            print(f"  {a['response'][:200]}...")
            print()
    elif args.patterns:
        patterns = db.search_patterns(
            pattern_type=args.pattern_type,
            min_confidence=args.min_confidence,
        )
        print(f"Patterns ({len(patterns)} results):\n")
        for p in patterns:
            print(f"  [{p['pattern_type']}] snap=#{p['snapshot_id']} "
                  f"phys=0x{p['phys_addr']:x} conf={p['confidence']:.0%}: "
                  f"{p['description']}")
    else:
        snapshots = db.get_recent_snapshots(limit=args.limit)
        print(f"Recent snapshots ({len(snapshots)}):\n")
        for s in snapshots:
            print(f"  {s['summary']}")

    db.close()


def cmd_diff(args):
    """Compare two snapshots."""
    topo = CxlTopology.discover()
    engine = SnapshotEngine(topology=topo, scan_size=args.scan_size * 1024 * 1024)
    db = TimelineDB()

    print(f"Taking snapshot A...")
    snap_a = engine.take_snapshot(trigger_wbinvd=True)
    db.save_snapshot(snap_a)

    print(f"Waiting {args.delay}s before snapshot B...")
    time.sleep(args.delay)

    print(f"Taking snapshot B...")
    snap_b = engine.take_snapshot(trigger_wbinvd=True)
    db.save_snapshot(snap_b)

    diffs = engine.diff(snap_a, snap_b)
    if diffs:
        db.save_diffs(snap_a.snapshot_id, snap_b.snapshot_id, diffs)

    print(f"\nDiff: {len(diffs)} changed regions, {sum(d.changed_bytes for d in diffs)} bytes changed\n")
    for d in diffs[:20]:
        print(f"  Window {d.window_index} +0x{d.offset:x} (phys 0x{d.phys_addr:x}): "
              f"{d.changed_bytes}/{d.size}B ({d.change_ratio:.0%})")
        # Show diff patterns
        patterns = analyze_diff(d)
        for p in patterns:
            print(f"    -> {p.summary()}")

    if args.analyze and diffs:
        _run_llm_diff(snap_a, snap_b, diffs, db)

    db.close()


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _run_llm_analysis(snap, patterns, db, mode="snapshot"):
    """Run LLM analysis on a snapshot."""
    try:
        from .agent import CxlAgent
        agent = CxlAgent(db=db)
        print(f"\nClaude is analyzing snapshot #{snap.snapshot_id}...")
        result = agent.analyze_snapshot(snap, patterns)
        print(f"\n{'='*60}")
        print(result)
        print(f"{'='*60}")
    except Exception as e:
        print(f"\nLLM analysis failed: {e}")


def _run_llm_diff(old_snap, new_snap, diffs, db):
    """Run LLM analysis on a diff."""
    try:
        from .agent import CxlAgent
        agent = CxlAgent(db=db)
        print(f"\nClaude is analyzing diff #{old_snap.snapshot_id}→#{new_snap.snapshot_id}...")
        result = agent.analyze_diff(old_snap, new_snap, diffs)
        print(f"\n{'='*60}")
        print(result)
        print(f"{'='*60}")
    except Exception as e:
        print(f"\nLLM analysis failed: {e}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cxlagent",
        description="CXLAgent — CXL Memory Snooping & Analysis Agent",
    )
    sub = parser.add_subparsers(dest="command")

    # Common args
    def add_common(p):
        p.add_argument("--scan-size", type=int, default=16,
                        help="MB to scan per CXL window (default: 16)")
        p.add_argument("--chunk-size", type=int, default=4096,
                        help="Chunk granularity in bytes (default: 4096)")
        p.add_argument("--windows", type=str, default=None,
                        help="Comma-separated CXL window indices (default: all)")
        p.add_argument("--analyze", action="store_true",
                        help="Enable LLM analysis (requires ANTHROPIC_API_KEY)")
        p.add_argument("--model", type=str, default="claude-sonnet-4-20250514",
                        help="Claude model to use")

    # topology
    p = sub.add_parser("topology", help="Show CXL device topology")
    p.set_defaults(func=cmd_topology)

    # snapshot
    p = sub.add_parser("snapshot", help="Take a single CXL memory snapshot")
    add_common(p)
    p.add_argument("--no-wbinvd", action="store_true",
                    help="Skip cache writeback+invalidate")
    p.add_argument("--offset", type=int, default=0,
                    help="Starting offset within each window")
    p.set_defaults(func=cmd_snapshot)

    # live
    p = sub.add_parser("live", help="Continuous CXL memory monitoring")
    add_common(p)
    p.add_argument("--interval", type=float, default=5.0,
                    help="Seconds between snapshots (default: 5)")
    p.add_argument("--no-trace", action="store_true",
                    help="Disable kernel tracepoints")
    p.set_defaults(func=cmd_live)

    # hunt
    p = sub.add_parser("hunt", help="Hunt for specific patterns in CXL memory")
    add_common(p)
    p.add_argument("target", help="What to hunt for (e.g. 'aes keys', 'game state', 'credentials')")
    p.set_defaults(func=cmd_hunt)

    # timeline
    p = sub.add_parser("timeline", help="Show snapshot/analysis history")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--analyses", action="store_true", help="Show LLM analyses")
    p.add_argument("--patterns", action="store_true", help="Show detected patterns")
    p.add_argument("--pattern-type", type=str, default=None)
    p.add_argument("--min-confidence", type=float, default=0.5)
    p.set_defaults(func=cmd_timeline)

    # diff
    p = sub.add_parser("diff", help="Compare two snapshots taken N seconds apart")
    add_common(p)
    p.add_argument("--delay", type=float, default=2.0,
                    help="Seconds between the two snapshots (default: 2)")
    p.set_defaults(func=cmd_diff)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
