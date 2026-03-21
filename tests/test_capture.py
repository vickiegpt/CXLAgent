"""Tests for cxlagent.capture — data classes and parsing logic (no hardware)."""

from unittest.mock import patch, MagicMock

from cxlagent.capture import (
    CxlTraceEvent,
    CxlTracer,
    CxlWindow,
    CxlTopology,
)


# ---------------------------------------------------------------------------
# CxlWindow
# ---------------------------------------------------------------------------

class TestCxlWindow:
    def test_basic_fields(self):
        w = CxlWindow(index=0, start=0x4000_0000, end=0x4FFF_FFFF, size=0x1000_0000)
        assert w.index == 0
        assert w.start == 0x4000_0000
        assert w.end == 0x4FFF_FFFF
        assert w.size == 0x1000_0000

    def test_discover_all_no_iomem(self):
        """When /proc/iomem is not accessible, returns empty list."""
        with patch("builtins.open", side_effect=PermissionError):
            windows = CxlWindow.discover_all()
        assert windows == []

    def test_discover_all_parses_iomem(self):
        iomem_content = (
            "00000000-0009ffff : System RAM\n"
            "4000000000-4fffffff : CXL Window 0\n"
            "5000000000-5fffffff : CXL Window 1\n"
            "6000000000-6fffffff : Some other device\n"
        )
        with patch("builtins.open", MagicMock(
            return_value=MagicMock(
                __enter__=MagicMock(return_value=iter(iomem_content.splitlines(keepends=True))),
                __exit__=MagicMock(return_value=False),
            )
        )):
            windows = CxlWindow.discover_all()

        assert len(windows) == 2
        assert windows[0].index == 0
        assert windows[1].index == 1
        assert windows[0].start == 0x4000000000
        assert windows[1].start == 0x5000000000

    def test_discover_all_no_cxl_windows(self):
        iomem_content = "00000000-0009ffff : System RAM\n"
        with patch("builtins.open", MagicMock(
            return_value=MagicMock(
                __enter__=MagicMock(return_value=iter(iomem_content.splitlines(keepends=True))),
                __exit__=MagicMock(return_value=False),
            )
        )):
            windows = CxlWindow.discover_all()
        assert windows == []


# ---------------------------------------------------------------------------
# CxlTopology
# ---------------------------------------------------------------------------

class TestCxlTopology:
    def test_empty_topology(self):
        topo = CxlTopology()
        assert topo.caches == []
        assert topo.mems == []
        assert topo.windows == []

    def test_summary_empty(self):
        topo = CxlTopology()
        summary = topo.summary()
        assert "CXL Topology:" in summary

    def test_summary_with_windows(self):
        topo = CxlTopology()
        topo.windows = [
            CxlWindow(index=0, start=0x4000_0000, end=0x4FFF_FFFF, size=256 * 1024 * 1024),
        ]
        summary = topo.summary()
        assert "Window 0" in summary

    def test_discover_no_sysfs(self):
        """On a system without CXL sysfs, discover() returns an empty topology."""
        with patch("cxlagent.capture.Path.exists", return_value=False), \
             patch("cxlagent.capture.CxlWindow.discover_all", return_value=[]):
            topo = CxlTopology.discover()
        assert topo.caches == []
        assert topo.mems == []
        assert topo.windows == []


# ---------------------------------------------------------------------------
# CxlTracer._parse_line
# ---------------------------------------------------------------------------

class TestCxlTracerParseLine:
    """Tests for the static _parse_line method — no hardware required."""

    SAMPLE_LINE = (
        "  kworker/3:2-1234  [003] ....  1234.567890: cxl_general_media: "
        "memdev=mem0 serial=1234 transaction_type='HostRead' "
        "dpa=0x1000 hpa=0x4000000000 region=region0"
    )

    def test_valid_line_parsed(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event is not None
        assert isinstance(event, CxlTraceEvent)

    def test_event_type_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.event_type == "cxl_general_media"

    def test_pid_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.pid == 1234

    def test_cpu_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.cpu == 3

    def test_timestamp_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert abs(event.timestamp - 1234.567890) < 0.001

    def test_memdev_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.memdev == "mem0"

    def test_transaction_type_stripped_quotes(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.transaction_type == "HostRead"

    def test_dpa_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.dpa == 0x1000

    def test_hpa_extracted(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.hpa == 0x4000000000

    def test_invalid_line_returns_none(self):
        result = CxlTracer._parse_line("this is not a trace line")
        assert result is None

    def test_empty_line_returns_none(self):
        result = CxlTracer._parse_line("")
        assert result is None

    def test_comment_line_returns_none(self):
        result = CxlTracer._parse_line("# tracer: nop")
        assert result is None

    def test_cxl_dram_event(self):
        line = (
            "  proc-5678  [001] ....  9999.000001: cxl_dram: "
            "memdev=mem1 serial=43981 transaction_type='HostWrite' "
            "dpa=0x2000 hpa=0x5000000000"
        )
        event = CxlTracer._parse_line(line)
        assert event is not None
        assert event.event_type == "cxl_dram"
        assert event.transaction_type == "HostWrite"

    def test_raw_field_preserved(self):
        event = CxlTracer._parse_line(self.SAMPLE_LINE)
        assert event.raw == self.SAMPLE_LINE.strip()

    def test_hex_dpa_parsed(self):
        line = (
            "  proc-1  [000] ....  1.0: cxl_general_media: "
            "memdev=mem0 serial=0 transaction_type=Unknown "
            "dpa=0xdeadbeef hpa=0x0"
        )
        event = CxlTracer._parse_line(line)
        assert event is not None
        assert event.dpa == 0xDEADBEEF
