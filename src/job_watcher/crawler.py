from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from .adapters import BrowserAdapter, BrowserSettings
from .config import load_config
from .database import Database
from .models import Source, SourceResult
from .reporting import generate_reports
from .scoring import score_job


async def run_watcher(
    *,
    config_path: str | Path,
    db_path: str | Path,
    reports_dir: str | Path,
    site_dir: str | Path,
) -> dict:
    sources, raw_settings = load_config(config_path)
    enabled = [source for source in sources if source.enabled]
    if not enabled:
        raise RuntimeError("配置中没有启用的招聘来源")
    database = Database(db_path)
    database.initialize()
    for source in sources:
        database.upsert_source(source)
    run_id = database.begin_run(len(enabled))

    browser_settings = BrowserSettings(
        timeout_ms=int(raw_settings.get("timeout_ms", 45_000)),
        settle_ms=int(raw_settings.get("settle_ms", 1_200)),
        scroll_rounds=int(raw_settings.get("scroll_rounds", 7)),
        max_pages=int(raw_settings.get("max_pages", 4)),
        max_jobs_per_source=int(raw_settings.get("max_jobs_per_source", 200)),
    )
    concurrency = max(1, min(5, int(raw_settings.get("concurrency", 3))))
    semaphore = asyncio.Semaphore(concurrency)
    results: list[SourceResult] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        adapter = BrowserAdapter(browser, browser_settings)

        async def fetch_one(source: Source) -> SourceResult:
            async with semaphore:
                return await adapter.fetch(source)

        results = list(await asyncio.gather(*(fetch_one(source) for source in enabled)))
        await browser.close()

    errors: list[str] = []
    for result in results:
        previous_count = database.active_count_for_source(result.source.key)
        if result.complete and previous_count >= 5 and len(result.jobs) < previous_count * 0.4:
            result.complete = False
            result.status = "suspect"
            result.error = (
                f"本次仅提取 {len(result.jobs)} 个岗位，较历史在招 {previous_count} 个骤降超过60%，"
                "已保护历史岗位并等待复核"
            )
        for job in result.jobs:
            score = score_job(job)
            job.score = score.score
            job.score_reasons = score.reasons
            database.upsert_job(run_id, job)
        if result.is_authoritative_success:
            database.mark_missing(run_id, result.source.key, [job.job_key for job in result.jobs])
        database.record_source_result(run_id, result)
        if result.status != "success":
            errors.append(f"{result.source.company}: {result.error or result.status}")

    database.finish_run(run_id, error_summary="\n".join(errors))
    data = database.report_data(run_id)
    generate_reports(data, reports_dir, site_dir)
    authoritative_successes = sum(1 for result in results if result.is_authoritative_success)
    if authoritative_successes == 0:
        raise RuntimeError(
            f"本次 {len(enabled)} 个来源均未完成可信抓取；已生成诊断报告，保留上一次已发布看板"
        )
    return data
