"""JD 获取：手动导入 + 官网抓取（带合规约束）。

合规边界（硬编码，不做开关）：
- 只抓公开页面，不登录、不绕验证码
- 每次请求前检查 robots.txt，Disallow 则直接拒绝
- 串行请求 + 1.5s 间隔，不并发
- 不落库 Cookie / 不模拟浏览器指纹

JS 渲染的官网 httpx 取不到正文，此时抛 FetchBlocked，引导用户改用手动粘贴。
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..models import Job, JobKind, JobSource

UA = "ResumeAssistantBot/0.1 (+local personal tool; contact: owner)"
TIMEOUT = 20.0
_last_request = 0.0
MIN_INTERVAL = 1.5


class FetchBlocked(RuntimeError):
    pass


def _respect_robots(url: str) -> None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        if not rp.can_fetch(UA, url):
            raise FetchBlocked(f"robots.txt 禁止抓取该路径：{url}")
    except FetchBlocked:
        raise
    except Exception:  # noqa: BLE001
        # robots.txt 不存在或不可读时，按「允许抓公开页」处理
        pass


def _throttle() -> None:
    global _last_request
    gap = time.time() - _last_request
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_request = time.time()


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    for sel in ("article", "main", ".content", "#content", ".job-detail", ".job-description"):
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 200:
            soup = node
            break
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_jd_text(url: str) -> str:
    _respect_robots(url)
    _throttle()
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        ) as client:
            resp = client.get(url)
    except Exception as exc:  # noqa: BLE001
        raise FetchBlocked(f"请求失败：{exc}") from exc

    if resp.status_code != 200:
        raise FetchBlocked(f"HTTP {resp.status_code}，可能需要登录或已被限制")
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        raise FetchBlocked(f"返回内容不是网页（{ctype}）")

    text = _html_to_text(resp.text)
    if len(text) < 200:
        raise FetchBlocked("页面正文过短，可能是 JS 动态渲染，请改用手动粘贴")
    return text


def make_job(
    *,
    kind: JobKind,
    title: str,
    raw_text: str,
    org: str = "",
    mentor: str = "",
    url: str = "",
    location: str = "",
    deadline: str = "",
    source: JobSource = JobSource.MANUAL,
    notes: str = "",
) -> Job:
    from ..models import slugify

    jid = f"job_{slugify(org or mentor or title, 20)}_{int(time.time()) % 100000}"
    return Job(
        id=jid,
        kind=kind,
        source=source,
        title=title,
        org=org,
        mentor=mentor,
        url=url,
        location=location,
        deadline=deadline,
        raw_text=raw_text,
        notes=notes,
    )
