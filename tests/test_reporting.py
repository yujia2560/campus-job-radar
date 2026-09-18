from pathlib import Path

from job_watcher.__main__ import build_parser, run_demo


def test_demo_generates_all_outputs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    args = build_parser().parse_args(
        [
            "demo",
            "--fixture", str(root / "fixtures" / "sample_jobs.json"),
            "--db", str(tmp_path / "demo.db"),
            "--reports", str(tmp_path / "reports"),
            "--site", str(tmp_path / "site"),
        ]
    )
    data = run_demo(args)
    assert len(data["jobs"]) == 9
    assert (tmp_path / "reports" / "latest.md").exists()
    assert (tmp_path / "reports" / "jobs.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "秋招岗位雷达" in html
    assert "AI产品经理培训生" in html
    assert "当前展示离线演示数据" in html
