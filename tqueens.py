"""
택배의여왕(tqueens.net) 체험단 목록 수집/파싱.

- 디너의여왕(dinnerqueen)의 자매 사이트(배송 전문). 같은 /taste SSR 구조라 골격은 동일,
  포인트·마감·제공내역·도메인 셀렉터만 다르다.
- robots: User-agent * 는 Allow:/ (목록 허용). 상세(/taste/{id})는 자동요청 금지 — 링크로만.
- 전부 배송형(재택) → 지역 무관 통과. 목록에 신청/모집 숫자가 없어 경쟁률 필터는 비활성
  (applied/capacity=None → passes_filter 가 통과시킴).
- 마감 D-day → 실행시각 기준 날짜로 근사. 제공내역(offer)은 디너와 달리 목록에 노출됨.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

SOURCE = "택배의여왕"
KST = timezone(timedelta(hours=9))
DETAIL_BASE = "https://tqueens.net"
CHANNEL_TOKENS = {"릴스", "클립", "블로그", "인스타", "유튜브", "쇼츠"}


def collect():
    """택배의여왕 목록을 긁어 표준 캠페인 dict 리스트로 반환."""
    results = []
    for i, url in enumerate(config.TQUEENS_LIST_URLS):
        if i > 0:
            time.sleep(config.REQUEST_DELAY_SEC)
        html = _fetch(url)
        for card in parse_campaigns(html):
            std = _to_standard(card)
            if std:
                results.append(std)
    return results


def _fetch(url):
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.text


def parse_campaigns(html):
    soup = BeautifulSoup(html, "html.parser")
    return [parse_card(c) for c in soup.select("div.qz-dq-card.qz-button.fluid")]


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_card(card):
    out = {k: "없음" for k in
           ["id", "title", "type", "channel", "point", "offer", "deadline", "url"]}

    # url & id
    link = card.select_one("a.qz-dq-card__link[href]")
    if link:
        href = link["href"]
        out["url"] = href if href.startswith("http") else DETAIL_BASE + href
        m = re.search(r"/taste/(\d+)", href)
        if m:
            out["id"] = m.group(1)

    # title (span 으로 쪼개진 대괄호 정리)
    tp = card.select_one("p.ellipsis.color-title")
    title = re.sub(r"\s+", " ", tp.get_text("", strip=True)).strip() if tp else ""
    title = re.sub(r"\[\s*", "[", title)
    title = re.sub(r"\s*\]", "]", title)
    out["title"] = title or "없음"
    tokens = re.findall(r"\[([^\[\]]+)\]", title)

    # offer(제공내역) — 택배의여왕은 목록에 노출됨
    out["offer"] = _txt(card.select_one("p.color-placeholder")) or "없음"

    # type (배송형/뷰티형/포인트 등)
    out["type"] = _txt(card.select_one("p.qz-body2-kr.color-primary-dq strong")) or "없음"

    # channel — 제목 대괄호 토큰에서
    out["channel"] = next((t.strip() for t in tokens if t.strip() in CHANNEL_TOKENS), "없음")

    # deadline (D-day)
    dd = card.select_one("p.qz-badge.layer-primary strong") or card.select_one("p.qz-badge strong")
    out["deadline"] = _txt(dd) or "없음"

    # point
    ps = card.select_one("span.color-primary-dq--only")
    if ps:
        out["point"] = ps.get_text(strip=True).replace(" ", "") + "P"

    return out


def _to_standard(card):
    cid = card.get("id")
    if not cid or cid == "없음":
        return None
    cid = str(cid)
    deadline, expired = _parse_dday(card.get("deadline", ""))
    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": card.get("title") or "",
        "region_field": "재택/배송",
        "is_delivery": True,            # 택배의여왕은 전부 배송형(재택)
        "type": _clean(card.get("type")),
        "channel": _clean(card.get("channel")),
        "applied": None,                # 목록에 신청/모집 숫자 없음
        "capacity": None,
        "point": None if card.get("point") in ("없음", "", None) else card.get("point"),
        "offer": _clean(card.get("offer")),
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,
        "url": card.get("url") or f"{DETAIL_BASE}/taste/{cid}",
    }


def _clean(v):
    return "" if v in ("없음", None) else v


def _parse_dday(dday):
    """'D-7' → ('M/D', False). 못 읽으면 ('', False)."""
    m = re.search(r"(\d+)", dday or "")
    if not m:
        return "", False
    target = datetime.now(KST) + timedelta(days=int(m.group(1)))
    return f"{target.month}/{target.day}", False
