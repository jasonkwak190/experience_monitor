"""
디너의여왕(dinnerqueen.net) 체험단 목록 수집/파싱.

- 목록 페이지(/taste?order=...)만 GET (robots.txt: User-agent * 는 Allow: /).
- 개별 캠페인 상세(/taste/{id})는 자동 요청 금지 — 알림 링크로만 사용.
- 순수 SSR HTML이라 BeautifulSoup로 카드 파싱한다.
- 결과를 monitor.py 가 쓰는 표준 캠페인 dict 로 변환 (source/key 포함).

특이점:
- 마감이 'D-7' 같은 상대표시 → 실행시각 기준 날짜로 근사 변환.
- 제공내역(offer)은 목록에 없음(상세 전용) → 빈값, 링크로 확인.
- '100% 선정' 배지 없음 → 경쟁률(applied/capacity)로만 판단.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

SOURCE = "디너의여왕"
KST = timezone(timedelta(hours=9))
DETAIL_BASE = "https://dinnerqueen.net"

# 타이틀 대괄호 토큰 중 '지역'으로 인정할 시/도 키워드
SIDO = ["서울", "경기", "인천", "부산", "대구", "대전", "광주", "울산", "세종",
        "강원", "충북", "충남", "충청", "전북", "전남", "전라", "경북", "경남",
        "제주", "전국"]
CHANNEL_TOKENS = {"릴스", "클립", "블로그", "인스타", "유튜브", "쇼츠"}

# 카드 뱃지 아이콘 클래스 → 의미
ICON_TYPE = {
    "qz_b_plate": "맛집(방문형)",
    "qz_b_map": "지역(방문형)",
    "qz_b_pandora_box": "배송형",
    "qz_b_delevery": "배달",
    "qz_b_payback": "페이백",
    "qz_b_dice": "랜덤픽",
    "qz_b_lipstic": "뷰티",
    "qz_b_umbrella": "여가",
    "qz_b_document": "기자단",
}
ICON_CHANNEL = {"qz_b_reels": "릴스", "qz_b_clip": "클립"}


# ──────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────
def collect():
    """디너의여왕 목록을 긁어 표준 캠페인 dict 리스트로 반환."""
    results = []
    for i, url in enumerate(config.DINNERQUEEN_LIST_URLS):
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
    cards = soup.select("div.qz-dq-card.qz-button.fluid")
    return [parse_card(c) for c in cards]


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_card(card):
    out = {k: "없음" for k in
           ["id", "title", "region", "channel", "type", "applied",
            "capacity", "point", "deadline", "detail_url"]}

    # detail_url & id
    link = card.select_one("a.qz-dq-card__link[href]")
    if link:
        href = link["href"]
        out["detail_url"] = href if href.startswith("http") else DETAIL_BASE + href
        m = re.search(r"/taste/(\d+)", href)
        if m:
            out["id"] = m.group(1)

    # title (내부 span 들이 '[' 'X' ']' 로 쪼개져 있어 텍스트만 합침)
    title_p = card.select_one("p.qz-body2-kr--line.ellipsis.color-title")
    raw_title = ""
    if title_p:
        raw_title = re.sub(r"\s+", " ", title_p.get_text("", strip=True)).strip()
    elif link and link.get("title"):
        raw_title = re.sub(r"\s*신청하기\s*$", "", link["title"]).strip()
    out["title"] = raw_title or "없음"

    tokens = re.findall(r"\[([^\[\]]+)\]", raw_title)

    # region: 대괄호 토큰 중 시/도로 시작하는 것
    region = None
    for t in tokens:
        t = t.strip()
        if any(t.startswith(s) for s in SIDO):
            region = t
            break

    # 뱃지 아이콘으로 type/channel 보조 판별
    badge_classes = " ".join(
        c for d in card.select(".qz-dq-card__text .qz-ico") for c in d.get("class", [])
    )
    icon_type = next((v for k, v in ICON_TYPE.items() if k in badge_classes), None)
    icon_channel = next((v for k, v in ICON_CHANNEL.items() if k in badge_classes), None)

    badge_labels = [_txt(s) for s in card.select(".qz-dq-card__text .qz-wrap strong")]
    badge_labels = [b for b in badge_labels if b and not re.match(r"^D-?\d", b)]

    # 배송/랜덤픽 등 재택형이면 region 을 '재택/배송'으로
    if region is None and (icon_type in ("배송형", "랜덤픽") or
                           any(t.strip() in ("배송", "랜덤픽") for t in tokens)):
        region = "재택/배송"

    # channel
    chan = icon_channel
    if not chan:
        chan = next((t.strip() for t in tokens if t.strip() in CHANNEL_TOKENS), None)
    if not chan:
        chan = next((b for b in badge_labels if b in CHANNEL_TOKENS), None)
    out["channel"] = chan or "없음"

    # type (페이백/랜덤픽/기자단 토큰을 아이콘 유형에 덧붙임)
    typ = icon_type
    for t in tokens:
        t = t.strip()
        if t in ("페이백", "랜덤픽", "기자단"):
            typ = f"{typ} / {t}" if typ and t not in typ else (typ or t)
    out["type"] = typ or (badge_labels[0] if badge_labels else "없음")
    out["region"] = region or "없음"

    # deadline (D-day)
    dd = card.select_one(".layer-primary p.qz-caption-kr--line strong")
    if dd:
        out["deadline"] = _txt(dd)

    # applied / capacity
    ab = card.select_one("p.apply_badge")
    if ab:
        t = _txt(ab)
        m = re.search(r"신청\s*([\d,]+)", t)
        if m:
            out["applied"] = m.group(1).replace(",", "")
        m = re.search(r"모집\s*([\d,]+)", t)
        if m:
            out["capacity"] = m.group(1).replace(",", "")

    # point
    pb = card.select_one("p.point_badge")
    if pb:
        ptxt = _txt(pb)
        m = re.search(r"([+\-]?[\d,]+)\s*P", ptxt)
        out["point"] = (m.group(1).replace(" ", "") + "P") if m else (ptxt or "없음")

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

    # 재택/배송 여부 (지역 무관 통과 판정용)
    is_delivery = ("재택" in region) or ("배송" in ctype) or ("랜덤픽" in ctype)

    deadline, expired = _parse_dday(card.get("deadline", ""))

    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": card.get("title") or "",
        "region_field": region,
        "is_delivery": is_delivery,
        "type": ctype,
        "channel": channel,
        "applied": _int_or_none(card.get("applied")),
        "capacity": _int_or_none(card.get("capacity")),
        # 포인트는 "+27,900P" 같은 문자열 → format_message 가 _to_int 로 안전 처리
        "point": None if card.get("point") in ("없음", "", None) else card.get("point"),
        "offer": "",  # 목록에 제공내역 없음 (상세 전용)
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,  # 100% 선정 배지 없음 → 경쟁률로 판단
        "url": card.get("detail_url") or f"{DETAIL_BASE}/taste/{cid}",
    }


def _clean(v):
    return "" if v in ("없음", None) else v


def _int_or_none(s):
    if s in ("없음", "", None):
        return None
    m = re.search(r"\d+", str(s).replace(",", ""))
    return int(m.group()) if m else None


def _parse_dday(dday):
    """'D-7' → ('M/D', False). D-day는 남은 일수라 목록에 있으면 미래(expired False).
    숫자를 못 읽으면 ('', False)."""
    m = re.search(r"(\d+)", dday or "")
    if not m:
        return "", False
    target = datetime.now(KST) + timedelta(days=int(m.group(1)))
    return f"{target.month}/{target.day}", False
