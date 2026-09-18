from __future__ import annotations

from dataclasses import dataclass
import re

from .models import Job, clean_text


PREFERRED_CITIES = {
    "上海": 12,
    "深圳": 12,
    "杭州": 8,
    "广州": 8,
    "苏州": 8,
}

TRACK_KEYWORDS = {
    "ai_internet": {
        "core": ["ai产品", "人工智能产品", "大模型产品", "产品经理", "用户增长", "增长运营", "产品运营", "解决方案", "售前"],
        "support": ["用户运营", "商业化", "市场运营", "销售运营", "客户成功", "渠道", "商务拓展", "数据分析", "agent", "aigc", "大模型"],
    },
    "ev_manufacturing": {
        "core": ["供应链", "采购", "寻源", "供应商管理", "需求计划", "生产计划", "物流规划", "项目管理", "解决方案"],
        "support": ["运营", "流程", "数字化", "成本", "交付", "销售运营", "海外销售", "商务", "项目运营", "s&op", "pmc"],
    },
    "retail_food": {
        "core": ["连锁运营", "渠道策略", "经营分析", "商业分析", "用户增长", "会员运营", "产品运营", "供应链", "采购", "管培生"],
        "support": ["门店运营", "新零售", "品类运营", "商品运营", "电商运营", "销售运营", "消费者洞察", "数字化", "市场策略", "项目管理"],
    },
}

CAMPUS_TERMS = ["2027", "27届", "校园招聘", "校招", "应届", "毕业生", "管培生", "培训生", "graduate", "trainee"]
HARD_EXCLUDES = ["社会招聘", "社招", "外包", "劳务派遣", "兼职", "普工", "操作工", "焊工", "叉车", "后厨", "服务员", "骑手"]
SENIOR_TERMS = ["总监", "负责人", "资深专家", "高级经理", "5年以上", "五年以上", "3年以上", "三年以上"]
ROLE_DOWNRANK = ["食品研发", "配方研发", "临床营养", "化验员", "咖啡师", "茶饮师", "店员", "设备维修", "材料研发", "电芯研发"]

EXPERIENCE_EVIDENCE = [
    (
        ["投放", "获客", "拉新", "注册", "留存", "付费", "转化", "用户增长", "海外运营"],
        "对应经历：海外C端投放与安装—注册—留存—付费漏斗",
    ),
    (
        ["用户调研", "需求分析", "prd", "原型", "mvp", "产品规划", "上线", "产品迭代"],
        "对应经历：独立完成需求梳理、PRD与可运行原型",
    ),
    (
        ["数据分析", "sql", "python", "指标体系", "看板", "数字化", "ai", "人工智能"],
        "对应经历：Python/SQL数据分析与AI工具应用",
    ),
    (
        ["一线", "巡店", "巡检", "现场运营", "问题闭环", "整改", "标准化"],
        "对应经历：7个项目一线检查与整改闭环",
    ),
    (
        ["采购", "寻源", "供应商", "招投标", "成本", "预算", "比价"],
        "对应经历：10余项招投标、预算复核与成本节约",
    ),
    (
        ["跨部门", "协同", "项目推进", "资源协调", "客户沟通", "项目管理"],
        "对应经历：多方沟通和跨部门项目推进",
    ),
]


@dataclass(slots=True)
class ScoreResult:
    score: int
    reasons: list[str]


def _hits(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def score_job(job: Job) -> ScoreResult:
    title = clean_text(job.title)
    text = " ".join(
        [title, job.location, job.department, job.description, job.raw_text, job.job_type, job.campus_year]
    ).lower()
    score = 8
    reasons: list[str] = []
    risk_reasons: list[str] = []

    terms = TRACK_KEYWORDS.get(job.track, TRACK_KEYWORDS["ai_internet"])
    core_hits = _hits(title, terms["core"])
    support_hits = _hits(text, terms["support"])
    if core_hits:
        gain = min(36, 18 + 9 * (len(core_hits) - 1))
        score += gain
        reasons.append(f"岗位核心词：{'、'.join(core_hits[:3])}")

    evidence_reasons: list[str] = []
    for evidence_terms, evidence_reason in EXPERIENCE_EVIDENCE:
        if _hits(text, evidence_terms):
            score += 4
            evidence_reasons.append(evidence_reason)
            if len(evidence_reasons) >= 4:
                break
    reasons.extend(evidence_reasons)

    if support_hits:
        gain = min(18, 6 * len(support_hits))
        score += gain
        reasons.append(f"相关能力词：{'、'.join(support_hits[:3])}")

    campus_hits = _hits(text, CAMPUS_TERMS)
    if campus_hits:
        score += 18
        reasons.append("符合2027届/校园招聘身份")
    else:
        score -= 8
        reasons.append("未明确标注2027届，需复核")

    matched_city = next((city for city in PREFERRED_CITIES if city in text), "")
    if matched_city:
        score += PREFERRED_CITIES[matched_city]
        reasons.append(f"意向城市：{matched_city}")

    if job.track == "retail_food" and any(term in text for term in ("轮岗", "一线", "门店", "经营分析")):
        score += 10
        reasons.append("包含一线轮岗/经营实践")
    if any(term in text for term in ("数据分析", "python", "sql", "ai", "人工智能", "数字化")):
        score += 6
        reasons.append("可发挥数据与AI应用能力")
    if any(term in text for term in ("跨部门", "协同", "项目管理", "推进", "沟通")):
        score += 5
        reasons.append("需要协同推进能力")

    hard_hits = _hits(text, HARD_EXCLUDES)
    if hard_hits:
        score -= 65
        risk_reasons.append(f"排除风险：{'、'.join(hard_hits[:2])}")
    senior_hits = _hits(text, SENIOR_TERMS)
    if senior_hits:
        score -= 32
        risk_reasons.append("经验/职级可能不适配")
    down_hits = _hits(text, ROLE_DOWNRANK)
    if down_hits:
        score -= 25
        risk_reasons.append(f"方向偏离：{'、'.join(down_hits[:2])}")

    if re.search(r"博士|ph\.?d", text, flags=re.IGNORECASE):
        score -= 35
        risk_reasons.append("学历要求可能不适配")

    visible_reasons = reasons[: max(0, 8 - len(risk_reasons))] + risk_reasons[:8]
    return ScoreResult(max(0, min(100, score)), visible_reasons)


def score_band(score: int) -> str:
    if score >= 80:
        return "强匹配"
    if score >= 65:
        return "推荐"
    if score >= 50:
        return "可关注"
    return "低匹配"
