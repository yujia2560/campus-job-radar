from job_watcher.models import Job
from job_watcher.scoring import score_job


def test_high_fit_retail_operations_job_scores_high():
    job = Job(
        source_key="guming",
        company="古茗",
        track="retail_food",
        title="连锁运营管培生（2027届）",
        url="https://example.com/job/1",
        location="杭州 / 全国",
        description="下沉一线门店轮岗，开展经营分析、项目推进和跨部门协同。",
    )
    result = score_job(job)
    assert result.score >= 75
    assert any("校园招聘" in reason for reason in result.reasons)


def test_irrelevant_senior_role_is_downranked():
    job = Job(
        source_key="x",
        company="示例公司",
        track="retail_food",
        title="食品配方研发总监",
        url="https://example.com/job/2",
        description="要求5年以上经验，负责食品研发与实验室管理。",
    )
    result = score_job(job)
    assert result.score < 30
    assert "经验/职级可能不适配" in result.reasons
    assert any(reason.startswith("方向偏离") for reason in result.reasons)
