from job_watcher.database import Database
from job_watcher.models import Job, Source, SourceResult


def make_job(title="供应链管理培训生", external_id="scm"):
    return Job(
        source_key="demo",
        company="示例企业",
        track="ev_manufacturing",
        title=title,
        url="https://example.com/job/scm",
        external_id=external_id,
        campus_year="2027",
    )


def test_missing_job_closes_only_after_two_complete_runs(tmp_path):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    source = Source("demo", "示例企业", "ev_manufacturing", "custom", "https://example.com")
    db.upsert_source(source)

    first = db.begin_run(1)
    job = make_job()
    assert db.upsert_job(first, job) == "NEW"
    db.record_source_result(first, SourceResult(source, "success", [job], complete=True))
    db.finish_run(first)

    second = db.begin_run(1)
    assert db.mark_missing(second, source.key, []) == 0
    other = make_job("采购管理培训生", "procurement")
    db.upsert_job(second, other)
    db.record_source_result(second, SourceResult(source, "success", [other], complete=True))
    db.finish_run(second)

    third = db.begin_run(1)
    assert db.mark_missing(third, source.key, [other.job_key]) == 1


def test_source_failure_does_not_close_jobs(tmp_path):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    source = Source("demo", "示例企业", "ev_manufacturing", "custom", "https://example.com")
    db.upsert_source(source)
    run = db.begin_run(1)
    job = make_job()
    db.upsert_job(run, job)
    db.record_source_result(run, SourceResult(source, "error", [], complete=False))
    db.finish_run(run)
    report = db.report_data(run)
    assert report["jobs"][0]["status"] == "active"
    assert report["sources"][0]["last_status"] == "error"


def test_incomplete_success_is_not_counted_as_healthy(tmp_path):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    source = Source("demo", "示例企业", "ai_internet", "custom", "https://example.com")
    db.upsert_source(source)
    run = db.begin_run(1)
    job = make_job()
    db.upsert_job(run, job)
    db.record_source_result(run, SourceResult(source, "success", [job], complete=False))
    db.finish_run(run)

    report = db.report_data(run)
    assert report["run"]["status"] == "failed"
    assert report["run"]["ok_sources"] == 0


def test_disabled_source_jobs_are_hidden_from_reports(tmp_path):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    source = Source("demo", "示例企业", "ev_manufacturing", "custom", "https://example.com")
    db.upsert_source(source)
    run = db.begin_run(1)
    job = make_job()
    db.upsert_job(run, job)
    db.record_source_result(run, SourceResult(source, "success", [job], complete=True))
    db.finish_run(run)

    source.enabled = False
    db.upsert_source(source)
    report = db.report_data(run)
    assert report["jobs"] == []
    assert report["sources"] == []
