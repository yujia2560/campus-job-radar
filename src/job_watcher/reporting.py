from __future__ import annotations

import csv
from datetime import datetime
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .scoring import score_band


TRACK_LABELS = {
    "ai_internet": "AI / 互联网",
    "ev_manufacturing": "新能源 / 高端制造",
    "retail_food": "零售 / 食品饮料",
}

EVENT_LABELS = {
    "NEW": "今日新增",
    "UPDATED": "信息更新",
    "REOPENED": "重新开放",
    "SEEN": "持续招聘",
}


def _local_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _prepare(data: dict) -> dict:
    jobs = []
    for raw in data.get("jobs", []):
        item = dict(raw)
        try:
            item["score_reasons"] = json.loads(item.pop("score_reasons_json", "[]"))
        except json.JSONDecodeError:
            item["score_reasons"] = []
        item["score_band"] = score_band(int(item.get("score", 0)))
        item["track_label"] = TRACK_LABELS.get(item.get("track", ""), item.get("track", "其他"))
        item["event_label"] = EVENT_LABELS.get(item.get("latest_event", "SEEN"), "持续招聘")
        jobs.append(item)
    sources = []
    for raw in data.get("sources", []):
        item = dict(raw)
        item["track_label"] = TRACK_LABELS.get(item.get("track", ""), item.get("track", "其他"))
        sources.append(item)
    return {
        "run": dict(data.get("run", {})),
        "jobs": jobs,
        "sources": sources,
        "mode": data.get("mode", "live"),
    }


def write_csv(data: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(data)
    fields = [
        "匹配分", "推荐等级", "变化", "赛道", "公司", "岗位", "城市", "部门",
        "届别", "招聘类型", "发布时间", "截止时间", "首次发现", "最后发现", "投递链接", "匹配理由",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for job in prepared["jobs"]:
            writer.writerow(
                {
                    "匹配分": job["score"],
                    "推荐等级": job["score_band"],
                    "变化": job["event_label"],
                    "赛道": job["track_label"],
                    "公司": job["company"],
                    "岗位": job["title"],
                    "城市": job["location"],
                    "部门": job["department"],
                    "届别": job["campus_year"],
                    "招聘类型": job["job_type"],
                    "发布时间": job["publish_date"],
                    "截止时间": job["deadline"],
                    "首次发现": _local_time(job["first_seen_at"]),
                    "最后发现": _local_time(job["last_seen_at"]),
                    "投递链接": job["url"],
                    "匹配理由": "；".join(job["score_reasons"]),
                }
            )


def write_markdown(data: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(data)
    run = prepared["run"]
    jobs = prepared["jobs"]
    high = [job for job in jobs if int(job["score"]) >= 70]
    new_jobs = [job for job in jobs if job["latest_event"] == "NEW"]
    ok_sources = sum(
        1
        for source in prepared["sources"]
        if source.get("run_status") == "success" and int(source.get("complete") or 0) == 1
    )
    lines = [
        "# 秋招岗位雷达日报",
        "",
        f"> 更新时间：{_local_time(run.get('finished_at') or run.get('started_at'))}",
        "",
        f"**在招岗位 {len(jobs)} 个｜今日新增 {len(new_jobs)} 个｜70分以上 {len(high)} 个｜正常来源 {ok_sources}/{len(prepared['sources'])}**",
        "",
        "## 优先关注",
        "",
        "| 匹配度 | 公司 | 岗位 | 城市 | 变化 | 推荐理由 |",
        "|---:|---|---|---|---|---|",
    ]
    if prepared.get("mode") == "demo":
        lines[3:3] = ["> 当前为离线演示数据，首次正式运行后会由官方招聘来源替换。", ""]
    for job in jobs[:30]:
        title = str(job["title"]).replace("|", "\\|")
        company = str(job["company"]).replace("|", "\\|")
        location = str(job["location"] or "待确认").replace("|", "\\|")
        reasons = "；".join(job["score_reasons"][:3]).replace("|", "\\|")
        lines.append(
            f"| {job['score']} | {company} | [{title}]({job['url']}) | {location} | {job['event_label']} | {reasons} |"
        )
    if not jobs:
        lines.append("| — | — | 暂未抓取到岗位 | — | — | 请查看来源健康状态 |")

    lines.extend(["", "## 来源健康状态", "", "| 来源 | 系统 | 状态 | 岗位数 | 备注 |", "|---|---|---|---:|---|"])
    for source in prepared["sources"]:
        status = source.get("run_status") or "未运行"
        note = (source.get("run_error") or "").replace("|", "\\|")
        lines.append(f"| {source['company']} | {source['ats']} | {status} | {source.get('job_count') or 0} | {note} |")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 只监控企业官网或官方授权招聘系统的公开岗位，不登录、不绕过验证码。",
            "- 岗位连续两次在完整抓取中消失后才判定下架；来源失败不会导致岗位误下架。",
            "- 匹配分用于排序，不代替你对岗位资格、工作内容和截止时间的人工确认。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _dashboard_html(payload: dict) -> str:
    run = payload["run"]
    jobs = payload["jobs"]
    sources = payload["sources"]
    updated = _local_time(run.get("finished_at") or run.get("started_at"))
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    source_ok = sum(
        1
        for source in sources
        if source.get("run_status") == "success" and int(source.get("complete") or 0) == 1
    )
    demo_banner = (
        '<div class="demo">当前展示离线演示数据；上传至 GitHub 并手动运行一次工作流后，将替换为官方来源的实时结果。</div>'
        if payload.get("mode") == "demo"
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="每日更新的2027届秋招岗位监控看板">
  <title>秋招岗位雷达</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%230c2f4a'/%3E%3Cpath d='M15 37a17 17 0 1 1 12 12' fill='none' stroke='%2356d7bc' stroke-width='5' stroke-linecap='round'/%3E%3Ccircle cx='32' cy='32' r='5' fill='%23fff'/%3E%3Cpath d='m34 30 14-11-9 17z' fill='%23ffb84d'/%3E%3C/svg%3E">
  <style>
    :root{{--ink:#102432;--muted:#647783;--line:#dce6e8;--paper:#f4f7f5;--panel:#fff;--navy:#0c2f4a;--teal:#158a7c;--mint:#dff4ee;--amber:#f2a93b;--red:#c84d45;--shadow:0 14px 38px rgba(20,51,61,.08)}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;font-size:16px;line-height:1.55}}
    button,input,select{{font:inherit}} a{{color:inherit}} .shell{{max-width:1380px;margin:auto;padding:26px 26px 64px}}
    header{{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:20px}} .brand small{{display:block;color:var(--teal);font-weight:750;letter-spacing:.14em}}
    h1{{font-size:clamp(1.8rem,3vw,3rem);line-height:1.08;margin:.25rem 0}} .stamp{{color:var(--muted);font-size:.9rem;text-align:right}}
    .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin-bottom:18px}} .metric{{background:var(--panel);border:1px solid var(--line);padding:17px 18px;border-radius:14px;box-shadow:var(--shadow)}}
    .metric span{{display:block;color:var(--muted);font-size:.85rem}} .metric strong{{font-size:1.75rem;letter-spacing:-.04em}}
    .workspace{{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;align-items:start}} .main,.rail{{background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}}
    .toolbar{{padding:16px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:minmax(220px,1fr) 190px 160px;gap:10px}} input,select{{width:100%;height:44px;border:1px solid #cfdadd;border-radius:10px;background:#fff;color:var(--ink);padding:0 12px}}
    .tabs{{display:flex;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line);overflow:auto}} .tab{{border:0;border-radius:999px;padding:8px 13px;background:#edf2f2;color:#536771;white-space:nowrap;cursor:pointer}}
    .tab.active{{background:var(--navy);color:#fff}} .list{{padding:4px 16px 16px}} .job{{display:grid;grid-template-columns:76px minmax(0,1fr) auto;gap:16px;padding:17px 0;border-bottom:1px solid var(--line)}} .job:last-child{{border-bottom:0}}
    .score{{width:68px;height:68px;border-radius:16px;background:var(--mint);color:var(--teal);display:grid;place-items:center;font-weight:850;font-size:1.5rem}} .score.low{{background:#f2f2f2;color:#718087}}
    .job h2{{font-size:1.05rem;line-height:1.35;margin:0 0 4px}} .company{{font-weight:750;color:var(--navy)}} .meta{{display:flex;gap:7px;flex-wrap:wrap;color:var(--muted);font-size:.82rem;margin:5px 0}}
    .pill{{background:#eef3f4;border-radius:999px;padding:3px 8px}} .pill.new{{background:#fff0d8;color:#885500}} .reasons{{font-size:.88rem;color:#50646e;margin:6px 0 0}} .apply{{align-self:center;text-decoration:none;background:var(--navy);color:#fff;border-radius:10px;padding:9px 13px;font-weight:700;white-space:nowrap}}
    .rail{{padding:17px;position:sticky;top:16px}} .rail h2{{font-size:1rem;margin:0 0 12px}} .source{{display:grid;grid-template-columns:10px 1fr auto;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line);font-size:.85rem}} .source:last-child{{border:0}}
    .dot{{width:9px;height:9px;border-radius:50%;background:#93a4aa}} .dot.ok{{background:#21a67a}} .dot.warn{{background:var(--amber)}} .dot.bad{{background:var(--red)}} .source small{{color:var(--muted)}}
    .demo{{margin:-4px 0 18px;padding:11px 14px;border:1px solid #efcf96;background:#fff5df;color:#765015;border-radius:12px;font-size:.9rem}} .empty{{padding:52px 20px;text-align:center;color:var(--muted)}} .footnote{{margin-top:18px;color:var(--muted);font-size:.8rem}}
    @media(max-width:900px){{.workspace{{grid-template-columns:1fr}}.rail{{position:static}}.metrics{{grid-template-columns:repeat(2,1fr)}}}}
    @media(max-width:620px){{.shell{{padding:18px 12px 42px}}header{{align-items:flex-start;flex-direction:column}}.stamp{{text-align:left}}.toolbar{{grid-template-columns:1fr}}.job{{grid-template-columns:58px minmax(0,1fr)}}.score{{width:54px;height:54px;font-size:1.2rem}}.apply{{grid-column:2;justify-self:start}}}}
  </style>
</head>
<body>
  <main class="shell">
    <header><div class="brand"><small>CAMPUS JOB RADAR</small><h1>秋招岗位雷达</h1><div>只看官方来源，把时间留给真正值得投的岗位。</div></div><div class="stamp">最近更新<br><strong>{html.escape(updated)}</strong></div></header>
    <section class="metrics" aria-label="今日概览">
      <div class="metric"><span>当前在招</span><strong id="m-active">{len(jobs)}</strong></div>
      <div class="metric"><span>今日新增</span><strong>{sum(1 for j in jobs if j.get('latest_event') == 'NEW')}</strong></div>
      <div class="metric"><span>信息更新</span><strong>{int(run.get('updated_count') or 0)}</strong></div>
      <div class="metric"><span>确认下架</span><strong>{int(run.get('closed_count') or 0)}</strong></div>
      <div class="metric"><span>推荐及以上</span><strong>{sum(1 for j in jobs if int(j.get('score', 0)) >= 65)}</strong></div>
      <div class="metric"><span>正常来源</span><strong>{source_ok}/{len(sources)}</strong></div>
    </section>
    {demo_banner}
    <section class="workspace">
      <div class="main">
        <div class="toolbar"><input id="q" type="search" placeholder="搜索公司、岗位、城市或关键词" aria-label="搜索岗位"><select id="city" aria-label="筛选城市"><option value="">全部城市</option></select><select id="sort" aria-label="排序"><option value="score">按匹配度</option><option value="new">今日新增优先</option><option value="company">按公司</option></select></div>
        <div class="tabs" role="tablist"><button class="tab active" data-track="">全部赛道</button><button class="tab" data-track="ai_internet">AI / 互联网</button><button class="tab" data-track="ev_manufacturing">新能源 / 制造</button><button class="tab" data-track="retail_food">零售 / 食品饮料</button></div>
        <div id="jobs" class="list" aria-live="polite"></div>
      </div>
      <aside class="rail"><h2>来源健康</h2><div id="sources"></div><p class="footnote">黄色或红色表示页面空壳、超时或触发验证。系统会保留历史岗位，不会因单次失败误判下架。</p></aside>
    </section>
  </main>
  <script type="application/json" id="payload">{embedded}</script>
  <script>
    const data=JSON.parse(document.getElementById('payload').textContent); let track='';
    const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}}[c]));
    const jobsEl=document.getElementById('jobs'), q=document.getElementById('q'), city=document.getElementById('city'), sort=document.getElementById('sort');
    const cities=[...new Set(data.jobs.flatMap(j=>(j.location||'').split(/\\s*\\/\\s*/)).filter(Boolean))].sort(); cities.forEach(c=>city.insertAdjacentHTML('beforeend',`<option>${{esc(c)}}</option>`));
    function render(){{let rows=data.jobs.filter(j=>(!track||j.track===track)&&(!city.value||(j.location||'').includes(city.value))&&(!q.value||[j.company,j.title,j.location,j.description].join(' ').toLowerCase().includes(q.value.toLowerCase()))); rows.sort((a,b)=>sort.value==='company'?a.company.localeCompare(b.company,'zh-CN'):sort.value==='new'?(b.latest_event==='NEW')-(a.latest_event==='NEW')||b.score-a.score:b.score-a.score); document.getElementById('m-active').textContent=rows.length; jobsEl.innerHTML=rows.length?rows.map(j=>`<article class="job"><div class="score ${{j.score<50?'low':''}}">${{j.score}}</div><div><h2><span class="company">${{esc(j.company)}}</span> · ${{esc(j.title)}}</h2><div class="meta"><span class="pill">${{esc(j.track_label)}}</span><span class="pill">${{esc(j.location||'地点待确认')}}</span><span class="pill ${{j.latest_event==='NEW'?'new':''}}">${{esc(j.event_label)}}</span><span class="pill">${{esc(j.score_band)}}</span></div><p class="reasons">${{esc((j.score_reasons||[]).slice(0,3).join(' · ')||'职位信息有限，建议打开详情复核')}}</p></div><a class="apply" href="${{esc(j.url)}}" target="_blank" rel="noopener">查看岗位</a></article>`).join(''):'<div class="empty"><strong>没有符合当前筛选的岗位</strong><br>可以放宽城市或关键词后再看。</div>'}}
    function renderSources(){{document.getElementById('sources').innerHTML=data.sources.map(s=>{{const complete=Number(s.complete||0)===1;const cls=s.run_status==='success'&&complete?'ok':['empty','timeout','partial','suspect'].includes(s.run_status)?'warn':'bad';return `<div class="source" title="${{esc(s.run_error||'')}}"><i class="dot ${{cls}}"></i><span>${{esc(s.company)}}<br><small>${{esc(s.ats)}}</small></span><b>${{s.job_count||0}}</b></div>`}}).join('')}}
    document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{{document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));btn.classList.add('active');track=btn.dataset.track;render()}})); [q,city,sort].forEach(el=>el.addEventListener(el===q?'input':'change',render)); renderSources();render();
  </script>
</body></html>"""


def write_site(data: dict, directory: str | Path) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(data)
    (directory / "index.html").write_text(_dashboard_html(prepared), encoding="utf-8")
    (directory / "data.json").write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / ".nojekyll").write_text("", encoding="utf-8")


def generate_reports(data: dict, reports_dir: str | Path, site_dir: str | Path) -> None:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_markdown(data, reports_dir / "latest.md")
    write_csv(data, reports_dir / "jobs.csv")
    write_site(data, site_dir)
