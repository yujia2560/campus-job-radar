from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import re
from typing import Any


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def stable_hash(*parts: str) -> str:
    raw = "\x1f".join(clean_text(part).lower() for part in parts)
    return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Source:
    key: str
    company: str
    track: str
    ats: str
    url: str
    priority: int = 2
    enabled: bool = True
    cities: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(slots=True)
class Job:
    source_key: str
    company: str
    track: str
    title: str
    url: str
    location: str = ""
    department: str = ""
    description: str = ""
    raw_text: str = ""
    publish_date: str = ""
    deadline: str = ""
    job_type: str = ""
    campus_year: str = ""
    external_id: str = ""
    score: int = 0
    score_reasons: list[str] = field(default_factory=list)

    @property
    def job_key(self) -> str:
        if self.external_id:
            return stable_hash(self.source_key, self.external_id)
        return stable_hash(self.source_key, self.title, self.location, self.url)

    @property
    def content_hash(self) -> str:
        return stable_hash(
            self.title,
            self.location,
            self.department,
            self.description,
            self.publish_date,
            self.deadline,
            self.job_type,
            self.campus_year,
            self.url,
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["job_key"] = self.job_key
        data["content_hash"] = self.content_hash
        return data


@dataclass(slots=True)
class SourceResult:
    source: Source
    status: str
    jobs: list[Job] = field(default_factory=list)
    error: str = ""
    duration_ms: int = 0
    complete: bool = False

    @property
    def is_authoritative_success(self) -> bool:
        return self.status == "success" and self.complete and bool(self.jobs)


def reasons_json(reasons: list[str]) -> str:
    return json.dumps(reasons, ensure_ascii=False, separators=(",", ":"))
