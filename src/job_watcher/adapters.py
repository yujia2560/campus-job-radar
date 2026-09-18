from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
import re
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Locator, Page, TimeoutError as PlaywrightTimeoutError

from .models import Job, Source, SourceResult, clean_text


CITY_ALIASES = {
    "上海": ["上海", "Shanghai"], "深圳": ["深圳", "Shenzhen"], "杭州": ["杭州", "Hangzhou"],
    "广州": ["广州", "Guangzhou"], "苏州": ["苏州", "Suzhou"], "北京": ["北京", "Beijing"],
    "南京": ["南京", "Nanjing"], "成都": ["成都", "Chengdu"], "武汉": ["武汉", "Wuhan"],
    "长沙": ["长沙", "Changsha"], "西安": ["西安", "Xi'an", "Xian"], "宁波": ["宁波", "Ningbo"],
    "常州": ["常州", "Changzhou"], "无锡": ["无锡", "Wuxi"], "合肥": ["合肥", "Hefei"],
    "惠州": ["惠州", "Huizhou"], "厦门": ["厦门", "Xiamen"], "宁德": ["宁德", "Ningde"],
    "佛山": ["佛山", "Foshan"], "郑州": ["郑州", "Zhengzhou"], "天津": ["天津", "Tianjin"],
    "重庆": ["重庆", "Chongqing"], "青岛": ["青岛", "Qingdao"], "东莞": ["东莞", "Dongguan"],
    "珠海": ["珠海", "Zhuhai"], "全国": ["全国", "Nationwide"], "海外": ["海外", "Overseas"],
}

ATS_LINK_SELECTORS = {
    "moka": ["a[href*='job']", "a[href*='position']", "a[href*='#/jobs']"],
    "feishu": ["a[href*='/job/']", "a[href*='/position/']", "a[href*='jobId']"],
    "beisen": ["a[href*='/job']", "a[href*='/position']", "a[href*='jobId']"],
    "dayee": ["a[href*='position']", "a[href*='post']", "a[href*='job']"],
    "custom": ["a[href*='job']", "a[href*='position']", "a[href*='career']", "a[href*='campus']"],
}

JOBISH_TEXT = re.compile(
    r"产品|运营|增长|策略|供应链|采购|计划|物流|项目|销售|商务|渠道|解决方案|售前|管培|培训生|分析|数字化|客户成功|市场|"
    r"product|operations?|growth|strategy|supply\s*chain|procurement|sourcing|logistics|project|sales|"
    r"business|solutions?|pre[- ]?sales|trainee|graduate|analyst|marketing|customer\s*success|account",
    re.IGNORECASE,
)
BLOCKED_TEXT = re.compile(r"验证码|访问过于频繁|安全验证|人机验证|captcha|access denied", re.IGNORECASE)
NAV_TEXT = {
    "首页", "校园招聘", "社会招聘", "职位列表", "搜索", "查看全部", "了解更多", "申请职位",
    "投递简历", "上一页", "下一页", "登录", "注册", "加入我们", "查看更多", "more",
}
TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "session", "token", "from",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_QUERY_KEYS]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), parts.fragment))


def _extract_external_id(url: str) -> str:
    patterns = [
        r"(?:job|position|post)(?:id)?[=/_-]([A-Za-z0-9_-]{4,})",
        r"/(?:jobs?|positions?|posts?)/([A-Za-z0-9_-]{4,})",
        r"[?&](?:jobId|positionId|postId|id)=([A-Za-z0-9_-]{4,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _parent_context(anchor) -> str:
    node = anchor
    best = clean_text(anchor.get_text(" ", strip=True))
    for _ in range(4):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = clean_text(node.get_text(" ", strip=True))
        if len(text) > len(best) and len(text) <= 650:
            best = text
        if getattr(node, "name", "") in {"li", "article", "tr"}:
            break
    return best[:650]


def _guess_title(anchor_text: str, context: str) -> str:
    anchor_text = clean_text(anchor_text)
    if 2 <= len(anchor_text) <= 100 and anchor_text not in NAV_TEXT and JOBISH_TEXT.search(anchor_text):
        return anchor_text
    chunks = [clean_text(part) for part in re.split(r"[|｜·•\n]", context)]
    for chunk in chunks:
        if 2 <= len(chunk) <= 80 and JOBISH_TEXT.search(chunk) and chunk not in NAV_TEXT:
            return chunk
    return ""


def _guess_location(text: str) -> str:
    cities = [
        city
        for city, aliases in CITY_ALIASES.items()
        if any(re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", text, re.IGNORECASE) for alias in aliases)
    ]
    return " / ".join(OrderedDict.fromkeys(cities))


def _guess_date(text: str, label: str) -> str:
    if label == "deadline":
        patterns = [r"(?:截止|截止时间|申请截止)[：:]?\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)"]
    else:
        patterns = [r"(?:发布|发布时间)[：:]?\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def extract_jobs_from_html(html: str, base_url: str, source: Source, max_jobs: int = 200) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = ATS_LINK_SELECTORS.get(source.ats, ATS_LINK_SELECTORS["custom"])
    anchors = []
    for selector in selectors:
        anchors.extend(soup.select(selector))
    if not anchors:
        anchors = soup.select("a[href]")

    jobs: OrderedDict[str, Job] = OrderedDict()
    for anchor in anchors:
        href = clean_text(anchor.get("href", ""))
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        url = canonicalize_url(urljoin(base_url, href))
        context = _parent_context(anchor)
        anchor_text = clean_text(anchor.get_text(" ", strip=True))
        if "{{" in anchor_text or "}}" in anchor_text or "{{" in context:
            continue
        title = _guess_title(anchor_text, context)
        if not title:
            continue
        if not JOBISH_TEXT.search(context) and not re.search(r"job|position|post|campus", url, re.IGNORECASE):
            continue

        external_id = _extract_external_id(url)
        dedupe = external_id or f"{title.lower()}|{_guess_location(context)}|{url}"
        if dedupe in jobs:
            continue
        jobs[dedupe] = Job(
            source_key=source.key,
            company=source.company,
            track=source.track,
            title=title,
            url=url,
            location=_guess_location(context),
            description=context,
            raw_text=context,
            publish_date=_guess_date(context, "publish"),
            deadline=_guess_date(context, "deadline"),
            job_type="校园招聘" if re.search(r"校招|校园招聘|应届|graduate", context, re.IGNORECASE) else "",
            campus_year="2027" if re.search(r"2027|27届", context) else "",
            external_id=external_id,
        )
        if len(jobs) >= max_jobs:
            break
    return list(jobs.values())


@dataclass(slots=True)
class BrowserSettings:
    timeout_ms: int = 45_000
    settle_ms: int = 1_200
    scroll_rounds: int = 7
    max_pages: int = 4
    max_jobs_per_source: int = 200


class BrowserAdapter:
    def __init__(self, browser: Browser, settings: BrowserSettings):
        self.browser = browser
        self.settings = settings

    async def _settle_and_scroll(self, page: Page, source: Source) -> tuple[dict[str, Job], bool]:
        found: OrderedDict[str, Job] = OrderedDict()
        stable_rounds = 0
        last_count = -1
        for _ in range(self.settings.scroll_rounds):
            html = await page.content()
            for job in extract_jobs_from_html(html, page.url, source, self.settings.max_jobs_per_source):
                found[job.job_key] = job
            if len(found) == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_count = len(found)
            if stable_rounds >= 2 or len(found) >= self.settings.max_jobs_per_source:
                break
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(self.settings.settle_ms)
        return found, stable_rounds >= 2

    async def _visible_next_control(self, page: Page) -> Locator | None:
        candidates = [
            page.get_by_text(
                re.compile(r"^(下一页|下页|加载更多|更多职位|更多岗位|next|›|»)$", re.IGNORECASE)
            ),
            page.locator(
                "a[rel='next'],button[aria-label*='next' i],a[aria-label*='next' i],"
                "button[aria-label*='下一页'],a[aria-label*='下一页']"
            ),
        ]
        for locator in candidates:
            for index in range(min(await locator.count(), 6)):
                candidate = locator.nth(index)
                if await candidate.is_visible():
                    return candidate
        return None

    async def _has_pagination_shell(self, page: Page) -> bool:
        locator = page.locator(
            "[class*='pagination'],[class*='Pagination'],[class*='pager'],"
            "nav[aria-label*='pagination' i]"
        )
        return await locator.count() > 0

    async def fetch(self, source: Source) -> SourceResult:
        started = time.perf_counter()
        page = await self.browser.new_page(
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CampusJobRadar/0.1"
            ),
        )
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font"}
            else route.continue_(),
        )
        try:
            await page.goto(source.url, wait_until="domcontentloaded", timeout=self.settings.timeout_ms)
            await page.wait_for_timeout(self.settings.settle_ms)
            text = clean_text(await page.locator("body").inner_text(timeout=8_000))
            if BLOCKED_TEXT.search(text):
                return SourceResult(
                    source=source,
                    status="blocked",
                    error="页面触发安全验证，需要人工复核",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    complete=False,
                )

            aggregate, stable = await self._settle_and_scroll(page, source)
            pagination_complete = True
            clicked_any_page = False
            pages_seen = 1
            while pages_seen < self.settings.max_pages:
                candidate = await self._visible_next_control(page)
                if candidate is None:
                    if not clicked_any_page and await self._has_pagination_shell(page):
                        pagination_complete = False
                    break
                disabled = await candidate.get_attribute("disabled")
                aria_disabled = await candidate.get_attribute("aria-disabled")
                if disabled is not None or aria_disabled == "true":
                    break
                before_url = page.url
                before_count = len(aggregate)
                await candidate.click(timeout=5_000)
                clicked_any_page = True
                pages_seen += 1
                with suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state("domcontentloaded", timeout=8_000)
                await page.wait_for_timeout(self.settings.settle_ms)
                page_jobs, page_stable = await self._settle_and_scroll(page, source)
                aggregate.update(page_jobs)
                stable = stable and page_stable
                if len(aggregate) == before_count and page.url == before_url:
                    pagination_complete = False
                    break

            if pages_seen >= self.settings.max_pages:
                remaining = await self._visible_next_control(page)
                if remaining is not None:
                    disabled = await remaining.get_attribute("disabled")
                    aria_disabled = await remaining.get_attribute("aria-disabled")
                    if disabled is None and aria_disabled != "true":
                        pagination_complete = False

            jobs = list(aggregate.values())[: self.settings.max_jobs_per_source]
            complete = bool(jobs and stable and pagination_complete)
            status = "success" if complete else "partial" if jobs else "empty"
            if complete:
                error = ""
            elif jobs:
                error = "已提取部分岗位，但无法确认滚动或分页已完整结束；本次不据此判定岗位下架"
            else:
                error = "页面可访问，但未提取到岗位；可能需要更新适配规则"
            return SourceResult(
                source=source,
                status=status,
                jobs=jobs,
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
                complete=complete,
            )
        except PlaywrightTimeoutError:
            return SourceResult(
                source=source,
                status="timeout",
                error=f"页面加载超过 {self.settings.timeout_ms // 1000} 秒",
                duration_ms=int((time.perf_counter() - started) * 1000),
                complete=False,
            )
        except Exception as exc:  # source failures should not stop the full daily run
            return SourceResult(
                source=source,
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:1000],
                duration_ms=int((time.perf_counter() - started) * 1000),
                complete=False,
            )
        finally:
            await page.close()
            await asyncio.sleep(0.25)
