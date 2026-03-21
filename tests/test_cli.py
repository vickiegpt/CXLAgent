"""Tests for cxlagent.cli — argument parser and command structure."""

import pytest

from cxlagent.cli import build_parser


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_returns_parser(self):
        parser = build_parser()
        assert parser is not None

    def test_prog_name(self):
        parser = build_parser()
        assert parser.prog == "cxlagent"

    def test_no_command_requires_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None  # no subcommand → command is None

    # -----------------------------------------------------------------------
    # topology
    # -----------------------------------------------------------------------

    def test_topology_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["topology"])
        assert args.command == "topology"
        assert callable(args.func)

    # -----------------------------------------------------------------------
    # snapshot
    # -----------------------------------------------------------------------

    def test_snapshot_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["snapshot"])
        assert args.command == "snapshot"
        assert args.scan_size == 16
        assert args.chunk_size == 4096
        assert args.windows is None
        assert args.analyze is False
        assert args.no_wbinvd is False
        assert args.offset == 0

    def test_snapshot_custom_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "snapshot",
            "--scan-size", "32",
            "--chunk-size", "8192",
            "--windows", "0,1",
            "--offset", "4096",
            "--no-wbinvd",
            "--analyze",
        ])
        assert args.scan_size == 32
        assert args.chunk_size == 8192
        assert args.windows == "0,1"
        assert args.offset == 4096
        assert args.no_wbinvd is True
        assert args.analyze is True

    # -----------------------------------------------------------------------
    # live
    # -----------------------------------------------------------------------

    def test_live_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["live"])
        assert args.command == "live"
        assert args.interval == 5.0
        assert args.no_trace is False
        assert args.analyze is False

    def test_live_custom_interval(self):
        parser = build_parser()
        args = parser.parse_args(["live", "--interval", "10.0", "--no-trace"])
        assert args.interval == 10.0
        assert args.no_trace is True

    # -----------------------------------------------------------------------
    # hunt
    # -----------------------------------------------------------------------

    def test_hunt_requires_target(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["hunt"])

    def test_hunt_with_target(self):
        parser = build_parser()
        args = parser.parse_args(["hunt", "aes keys"])
        assert args.command == "hunt"
        assert args.target == "aes keys"

    def test_hunt_with_model(self):
        parser = build_parser()
        args = parser.parse_args([
            "hunt", "credentials",
            "--model", "claude-3-haiku-20240307",
        ])
        assert args.model == "claude-3-haiku-20240307"

    # -----------------------------------------------------------------------
    # timeline
    # -----------------------------------------------------------------------

    def test_timeline_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["timeline"])
        assert args.command == "timeline"
        assert args.limit == 20
        assert args.analyses is False
        assert args.patterns is False
        assert args.pattern_type is None
        assert args.min_confidence == 0.5

    def test_timeline_analyses_flag(self):
        parser = build_parser()
        args = parser.parse_args(["timeline", "--analyses", "--limit", "5"])
        assert args.analyses is True
        assert args.limit == 5

    def test_timeline_patterns_flag(self):
        parser = build_parser()
        args = parser.parse_args([
            "timeline", "--patterns",
            "--pattern-type", "aes_key_schedule",
            "--min-confidence", "0.8",
        ])
        assert args.patterns is True
        assert args.pattern_type == "aes_key_schedule"
        assert args.min_confidence == 0.8

    # -----------------------------------------------------------------------
    # diff
    # -----------------------------------------------------------------------

    def test_diff_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["diff"])
        assert args.command == "diff"
        assert args.delay == 2.0

    def test_diff_custom_delay(self):
        parser = build_parser()
        args = parser.parse_args(["diff", "--delay", "5.0"])
        assert args.delay == 5.0

    # -----------------------------------------------------------------------
    # common args shared across subcommands
    # -----------------------------------------------------------------------

    def test_model_default(self):
        parser = build_parser()
        for sub in ["snapshot", "live", "hunt all", "diff"]:
            args = parser.parse_args(sub.split())
            assert hasattr(args, "model")
            assert args.model == "claude-sonnet-4-20250514"

    def test_unknown_subcommand_exits(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["nonexistent-command"])
