from job_watcher.adapters import canonicalize_url, extract_jobs_from_html
from job_watcher.models import Source


def test_extract_jobs_from_static_html():
    source = Source("demo", "示例企业", "ai_internet", "custom", "https://jobs.example.com/campus")
    html = """
    <main><article><a href="/job/abc123?utm_source=test">AI产品经理（2027届）</a>
    <span>上海｜校园招聘｜用户调研、PRD、数据分析</span></article></main>
    """
    jobs = extract_jobs_from_html(html, source.url, source)
    assert len(jobs) == 1
    assert jobs[0].title == "AI产品经理（2027届）"
    assert jobs[0].location == "上海"
    assert "utm_source" not in jobs[0].url


def test_canonicalize_keeps_job_id_and_drops_tracking():
    url = canonicalize_url("HTTPS://EXAMPLE.COM/job/123/?id=123&utm_campaign=x")
    assert url == "https://example.com/job/123?id=123"


def test_extracts_english_role_and_normalizes_city():
    source = Source("demo", "Example", "ai_internet", "custom", "https://jobs.example.com/campus")
    html = """
    <article><a href="/positions/growth2027">Graduate Growth Operations</a>
    <span>Shanghai · Campus recruitment · User acquisition and retention</span></article>
    """
    jobs = extract_jobs_from_html(html, source.url, source)
    assert len(jobs) == 1
    assert jobs[0].location == "上海"
