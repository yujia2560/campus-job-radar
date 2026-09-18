from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .database import Database
from .models import Job, Source, SourceResult
from .reporting import generate_reports
from .scoring import score_job
from .crawler import run_watcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-radar", description="2027届秋招岗位监控与匹配看板")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="抓取官方招聘来源并更新看板")
    run.add_argument("--config", default="config/sources.json")
    run.add_argument("--db", default="data/jobs.db")
    run.add_argument("--reports", default="reports")
    run.add_argument("--site", default="site")

    demo = subparsers.add_parser("demo", help="用离线示例数据生成看板")
    demo.add_argument("--fixture", default="fixtures/sample_jobs.json")
    demo.add_argument("--db", default="data/demo.db")
    demo.add_argument("--reports", default="reports")
    demo.add_argument("--site", default="site")
    return parser


def run_demo(args: argparse.Namespace) -> dict:
    fixture_path = Path(args.fixture)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    database = Database(db_path)
    database.initialize()
    sources_by_key: dict[str, Source] = {}
    for raw in fixture["sources"]:
        source = Source(**raw)
        sources_by_key[source.key] = source
        database.upsert_source(source)
    run_id = database.begin_run(len(sources_by_key))
    grouped: dict[str, list[Job]] = {key: [] for key in sources_by_key}
    for raw in fixture["jobs"]:
        job = Job(**raw)
        score = score_job(job)
        job.score = score.score
        job.score_reasons = score.reasons
        grouped[job.source_key].append(job)
        database.upsert_job(run_id, job)
    for key, source in sources_by_key.items():
        result = SourceResult(source=source, status="success", jobs=grouped[key], complete=True, duration_ms=680)
        database.record_source_result(run_id, result)
    database.finish_run(run_id)
    data = database.report_data(run_id)
    data["mode"] = "demo"
    generate_reports(data, args.reports, args.site)
    return data


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "demo":
            data = run_demo(args)
        else:
            data = asyncio.run(
                run_watcher(
                    config_path=args.config,
                    db_path=args.db,
                    reports_dir=args.reports,
                    site_dir=args.site,
                )
            )
        run = data.get("run", {})
        print(
            json.dumps(
                {
                    "run_id": run.get("run_id"),
                    "status": run.get("status"),
                    "active_jobs": len(data.get("jobs", [])),
                    "sources": len(data.get("sources", [])),
                },
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        print(f"job-radar failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
