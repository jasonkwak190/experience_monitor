"""
미블(mrblog.net) 체험단 목록 수집/파싱.

- 전체/지역/배송 목록은 로그인 벽(302→/login)이라, 비로그인으로 캠페인이
  보이는 홈(https://www.mrblog.net/)만 GET 한다. 홈 캐러셀에 ~30건이 SSR.
- 개별 캠페인 상세(/campaigns/{id})는 자동 요청 금지 — 알림 링크로만 사용.
- robots.txt 전면 허용(Disallow 빈값). 순수 SSR HTML이라 BeautifulSoup로 파싱.
- 결과를 monitor.py 가 쓰는 표준 캠페인 dict 로 변환 (source/key 포함).

카드 구조 (a.campaign_item):
- href = /campaigns/{id}
- 제목: strong.subject
- 지역: span.area 의 직접 텍스트 (예 "수원 인계동"). "배송"이면 지역 없는 배송형.
- 채널: span.area > span.sns_icon 의 클래스 (blog/insta)
- 유형 배지: span.area 안 span.reels / span.clip
- 신청: div.count .current strong ("25명")
- 모집: div.count 텍스트의 "모집 N명"
- 마감: span.d_day ("D-Day" / "N일 남음" / "마감" 상대표기)
- 미션설명: p.desc
- 포인트: 홈 카드에 없음 → None.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

SOURCE = "미블"
KST = timezone(timedelta(hours=9))
HOME = "https://www.mrblog.net/"
DETAIL_BASE = "https://www.mrblog.net"

# span.sns_icon 클래스 → 채널 한글명
CHANNEL_MAP = {"blog": "블로그", "insta": "인스타그램"}
# span.area 안 유형 배지 클래스 → 유형 한글명
TYPE_BADGES = {"reels": "릴스", "clip": "클립"}


# ──────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────
def collect():
    """미블 홈을 긁어 표준 캠페인 dict 리스트로 반환."""
    results = []
    html = _fetch(HOME)
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
    cards = soup.select("a.campaign_item[href]")
    seen = set()
    out = []
    for c in cards:
        parsed = parse_card(c)
        cid = parsed.get("id")
        if not cid or cid in seen:
            continue  # 홈 캐러셀은 중복 슬라이드가 있을 수 있어 id로 dedup
        seen.add(cid)
        out.append(parsed)
    return out


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_card(card):
    out = {k: "" for k in
           ["id", "title", "region", "channel", "type", "applied",
            "capacity", "offer", "deadline", "detail_url"]}

    # detail_url & id
    href = card.get("href", "")
    out["detail_url"] = href if href.startswith("http") else DETAIL_BASE + href
    m = re.search(r"/campaigns/(\d+)", href)
    if m:
        out["id"] = m.group(1)

    # title
    out["title"] = _txt(card.select_one("strong.subject"))

    # area: 직접 텍스트(자식 span 제외) = 지역 또는 "배송"
    area = card.select_one(".area")
    region = ""
    if area:
        region = "".join(
            t for t in area.find_all(string=True, recursive=False)
        ).strip()
        region = re.sub(r"\s+", " ", region)

    # 채널: .area 안 span.sns_icon 의 두 번째 클래스
    chan = ""
    icon = card.select_one(".sns_icon")
    if icon:
        for cls in icon.get("class", []):
            if cls in CHANNEL_MAP:
                chan = CHANNEL_MAP[cls]
                break
    out["channel"] = chan

    # 유형 배지: .area 안 span.reels / span.clip
    typ = ""
    if area:
        for span in area.find_all("span"):
            for cls in span.get("class", []):
                if cls in TYPE_BADGES:
                    typ = TYPE_BADGES[cls]
                    break
            if typ:
                break
    out["type"] = typ

    # "배송"이면 지역 없는 배송형 → region 비우고 플래그 신호 남김
    if region == "배송":
        out["region"] = ""
        out["_delivery"] = True
    else:
        out["region"] = region
        out["_delivery"] = False

    # 신청/모집
    cur = card.select_one(".count .current strong")
    if cur:
        out["applied"] = _txt(cur)
    cnt = card.select_one(".count")
    if cnt:
        cm = re.search(r"모집\s*([\d,]+)\s*명", _txt(cnt))
        if cm:
            out["capacity"] = cm.group(1)

    # 마감(상대표기)
    out["deadline"] = _txt(card.select_one(".d_day"))

    # 미션설명
    out["offer"] = _txt(card.select_one("p.desc"))

    return out


# ──────────────────────────────────────────────
# 표준 형식 변환 (monitor.py 가 쓰는 형태)
# ──────────────────────────────────────────────
def _to_standard(card):
    cid = card.get("id")
    if not cid:
        return None
    cid = str(cid)

    region = card.get("region") or ""
    is_delivery = bool(card.get("_delivery"))

    deadline, expired = _parse_deadline(card.get("deadline", ""))

    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": card.get("title") or "",
        "region_field": region,
        "is_delivery": is_delivery,
        "type": card.get("type") or "",
        "channel": card.get("channel") or "",
        "applied": _int_or_none(card.get("applied")),
        "capacity": _int_or_none(card.get("capacity")),
        "point": None,  # 홈 카드에 포인트 없음
        "offer": card.get("offer") or "",
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,
        "url": card.get("detail_url") or f"{DETAIL_BASE}/campaigns/{cid}",
    }


def _int_or_none(s):
    if s in ("", None):
        return None
    m = re.search(r"\d+", str(s).replace(",", ""))
    return int(m.group()) if m else None


def _parse_deadline(raw):
    """상대 마감표기를 ('M/D', expired) 로 변환.
    - 'N일 남음' → 오늘(KST)+N → 'M/D', expired False
    - 'D-Day'    → 오늘(KST)        → 'M/D', expired False
    - '마감'      → ('', True)
    - 그 외 못 읽으면 ('', False)
    """
    t = (raw or "").strip()
    if not t:
        return "", False
    if "마감" in t:
        return "", True
    m = re.search(r"(\d+)\s*일\s*남음", t)
    if m:
        target = datetime.now(KST) + timedelta(days=int(m.group(1)))
        return f"{target.month}/{target.day}", False
    if "D-Day" in t or re.search(r"D-?\s*0\b", t):
        today = datetime.now(KST)
        return f"{today.month}/{today.day}", False
    # 'D-N' 형태 대비 (혹시 모를 변형)
    m = re.search(r"D-?\s*(\d+)", t)
    if m:
        target = datetime.now(KST) + timedelta(days=int(m.group(1)))
        return f"{target.month}/{target.day}", False
    return "", False
