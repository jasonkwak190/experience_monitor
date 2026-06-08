"""
뷰티의여왕(bqueens.net) 체험단 목록 수집/파싱.

- 디너의여왕(dinnerqueen)·택배의여왕(tqueens)의 자매 사이트(뷰티 전문)지만 DOM 구조가 다르다.
  목록: https://bqueens.net/taste?order=new (순수 SSR HTML, 로그인 불필요).
- robots: User-agent * 는 Allow:/ (목록 허용). 상세(/taste/{id})는 자동요청 금지 — 알림 링크로만.
- 카드는 div.item, 내부 aside 안에 제목/마감/포인트가 있다.
  · 링크/id : a.item-content[href]  → /taste/{id}
  · 제목    : aside h5.ellipsis
  · 마감    : aside h5 b  ("D - 7" 같은 상대표시 → 실행시각 기준 날짜로 근사)
  · 포인트  : aside b.color-purple (목록엔 일부 카드만 노출)
  · 지역/채널/유형 : 제목 대괄호 토큰에서 추출 (지역은 시/도로 시작하는 토큰).
- 신청/모집 숫자가 목록에 없어 경쟁률 필터는 비활성(applied/capacity=None → passes_filter 통과).
- 지역 토큰이 없으면 배송형(재택)으로 간주 → 지역 무관 통과.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

SOURCE = "뷰티의여왕"
KST = timezone(timedelta(hours=9))
DETAIL_BASE = "https://bqueens.net"

# 목록 URL (config 를 건드리지 않기 위해 모듈 상수로 둔다)
LIST_URLS = [
    "https://bqueens.net/taste?order=new",
]

# 타이틀 대괄호 토큰 중 '지역'으로 인정할 시/도 키워드
SIDO = ["서울", "경기", "인천", "부산", "대구", "대전", "광주", "울산", "세종",
        "강원", "충북", "충남", "충청", "전북", "전남", "전라", "경북", "경남",
        "제주", "전국"]
CHANNEL_TOKENS = {"릴스", "클립", "블로그", "인스타", "유튜브", "쇼츠"}


# ──────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────
def collect():
    """뷰티의여왕 목록을 긁어 표준 캠페인 dict 리스트로 반환."""
    results = []
    for i, url in enumerate(LIST_URLS):
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


# ──────────────────────────────────────────────
# HTML 카드 파싱
# ──────────────────────────────────────────────
def parse_campaigns(html):
    soup = BeautifulSoup(html, "html.parser")
    return [parse_card(c) for c in soup.select("div.item")]


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_card(card):
    out = {k: "없음" for k in
           ["id", "title", "region", "channel", "type", "point", "deadline", "url"]}

    # url & id
    link = card.select_one("a.item-content[href]")
    if link:
        href = link["href"]
        out["url"] = href if href.startswith("http") else DETAIL_BASE + href
        m = re.search(r"/taste/(\d+)", href)
        if m:
            out["id"] = m.group(1)

    # title (aside h5.ellipsis — 마감 D-day는 별도 h5/b 라 여기 안 섞임)
    title_h5 = card.select_one("aside h5.ellipsis")
    title = re.sub(r"\s+", " ", _txt(title_h5)).strip() if title_h5 else ""
    out["title"] = title or "없음"
    tokens = [t.strip() for t in re.findall(r"\[([^\[\]]+)\]", title)]

    # region: 대괄호 토큰 중 시/도로 시작하는 것 (예: "서울 강서")
    region = next((t for t in tokens if any(t.startswith(s) for s in SIDO)), None)
    out["region"] = region or "없음"

    # channel: 대괄호 토큰 중 채널 키워드
    out["channel"] = next((t for t in tokens if t in CHANNEL_TOKENS), "없음")

    # type: 지역/채널이 아닌 나머지 토큰(있으면 첫 토큰) — 예 "기자단"
    typ = next((t for t in tokens
                if t != region and t not in CHANNEL_TOKENS), None)
    out["type"] = typ or "없음"

    # deadline (D-day) — "D - 7" 처럼 공백이 끼어 있어도 숫자만 뽑는다
    dd = card.select_one("aside h5 b")
    out["deadline"] = _txt(dd) or "없음"

    # point — 목록엔 일부 카드만 노출
    pt = card.select_one("aside b.color-purple")
    if pt:
        ptxt = _txt(pt)
        m = re.search(r"([\d,]+)\s*P", ptxt)
        out["point"] = (m.group(1) + "P") if m else (ptxt or "없음")

    return out


# ──────────────────────────────────────────────
# 표준 형식 변환 (monitor.py 가 쓰는 형태)
# ──────────────────────────────────────────────
def _to_standard(card):
    cid = card.get("id")
    if not cid or cid == "없음":
        return None
    cid = str(cid)

    region = _clean(card.get("region"))
    ctype = _clean(card.get("type"))
    channel = _clean(card.get("channel"))

    # 지역 토큰이 없으면 배송형(재택) → 지역 무관 통과
    is_delivery = region == ""
    region_field = region if region else "재택/배송"

    deadline, expired = _parse_dday(card.get("deadline", ""))

    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": card.get("title") or "",
        "region_field": region_field,
        "is_delivery": is_delivery,
        "type": ctype,
        "channel": channel,
        "applied": None,                # 목록에 신청/모집 숫자 없음
        "capacity": None,
        # 포인트는 "5,000P" 문자열 → format_message 가 _to_int 로 안전 처리
        "point": None if card.get("point") in ("없음", "", None) else card.get("point"),
        "offer": "",                    # 목록에 제공내역 없음 (상세 전용)
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,            # 100% 선정 배지 없음 → 경쟁률로 판단
        "url": card.get("url") or f"{DETAIL_BASE}/taste/{cid}",
    }


def _clean(v):
    return "" if v in ("없음", None) else v


def _parse_dday(dday):
    """'D-7' / 'D - 7' → ('M/D', False). 못 읽으면 ('', False)."""
    m = re.search(r"(\d+)", dday or "")
    if not m:
        return "", False
    target = datetime.now(KST) + timedelta(days=int(m.group(1)))
    return f"{target.month}/{target.day}", False
