"""
링블(ringble.co.kr) 체험단 목록 수집/파싱.

- 비로그인 홈(https://www.ringble.co.kr/)만 GET. 홈에 캠페인 36건이 SSR로 노출됨.
- category.php / all_search.php(필터 목록) 와 detail.php(상세)는 robots 차단 → 자동 요청 금지.
  detail.php 는 "알림에 넣을 링크"로만 사용한다.
- 구형 PHP SSR HTML 이라 BeautifulSoup 로 카드 파싱한다.
- 결과를 monitor.py 가 쓰는 표준 캠페인 dict 로 변환 (source/key 포함).

특이점:
- 카드: td.store_list_wrap[data-val]. 상세링크: detail.php?number={id}.
- title: 카드 내 두 번째 .list_title (첫 번째 .list_title 은 마감 "22일 남음").
- 채널: 아이콘 이미지(IconBlog.png / IconInsta.png 등)로 판별 → 한글 변환.
- 신청/모집: "신청 534 / 모집 1" 텍스트.
- 지역/포인트는 홈 카드에 없음 → region_field="" (지역필터 통과), point=None.
- 마감이 'N일 남음' 상대표시 → 실행시각(KST) 기준 날짜로 근사 변환.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

SOURCE = "링블"
KST = timezone(timedelta(hours=9))
HOME_URL = "https://www.ringble.co.kr/"
DETAIL_BASE = "https://www.ringble.co.kr/detail.php?number="

# 채널 아이콘 파일명(소문자 비교) → 한글 채널명
CHANNEL_ICONS = {
    "iconblog": "블로그",
    "iconinsta": "인스타그램",
    "iconinstagram": "인스타그램",
    "iconyoutube": "유튜브",
    "iconreels": "릴스",
    "iconshorts": "유튜브 쇼츠",
    "iconclip": "클립",
    "iconplace": "네이버플레이스",
    "iconstore": "네이버스토어",
    "icontiktok": "틱톡",
}


# ──────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────
def collect():
    """링블 홈을 긁어 표준 캠페인 dict 리스트로 반환."""
    results = []
    html = _fetch(HOME_URL)
    for card in parse_campaigns(html):
        std = _to_standard(card)
        if std:
            results.append(std)
    return results


def _fetch(url):
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


# ──────────────────────────────────────────────
# HTML 카드 파싱
# ──────────────────────────────────────────────
def parse_campaigns(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("td.store_list_wrap[data-val]")
    return [parse_card(c) for c in cards]


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_card(card):
    out = {k: None for k in
           ["id", "title", "channel", "applied", "capacity", "deadline"]}

    # id: detail.php?number= 값을 쓴다 (실제 캠페인 고유 id, 상세 URL/신규감지의 키).
    #     data-val 은 홈 카드의 위치 인덱스(매 크롤마다 재사용)라 id 로 부적합 →
    #     number 가 없을 때만 최후 폴백으로 사용.
    link = card.select_one("a[href*='detail.php']")
    if link:
        m = re.search(r"number=(\d+)", link.get("href", ""))
        if m:
            out["id"] = m.group(1)
    if not out["id"]:
        out["id"] = card.get("data-val") or None

    # .list_title 들: [0]=마감("22일 남음"), [1]=제목
    titles = card.select("a.list_title")
    if titles:
        out["deadline"] = _txt(titles[0])
    if len(titles) >= 2:
        out["title"] = _txt(titles[1])

    # 채널: 아이콘 이미지 파일명으로 판별
    out["channel"] = _detect_channel(card)

    # 신청/모집: "신청 534 / 모집 1"
    body = card.get_text(" ", strip=True)
    m = re.search(r"신청\s*([\d,]+)", body)
    if m:
        out["applied"] = m.group(1).replace(",", "")
    m = re.search(r"모집\s*([\d,]+)", body)
    if m:
        out["capacity"] = m.group(1).replace(",", "")

    return out


def _detect_channel(card):
    for img in card.select("img[src]"):
        src = img.get("src", "")
        base = re.sub(r"\.(png|gif|jpg|jpeg|svg|webp)$", "", src.rsplit("/", 1)[-1]).lower()
        if base in CHANNEL_ICONS:
            return CHANNEL_ICONS[base]
        # 파일명에 채널 키워드가 섞여 있는 경우 보조 매칭
        for key, ko in CHANNEL_ICONS.items():
            if key in base:
                return ko
    return ""


# ──────────────────────────────────────────────
# 표준 형식 변환 (monitor.py 가 쓰는 형태)
# ──────────────────────────────────────────────
def _to_standard(card):
    cid = card.get("id")
    if not cid:
        return None
    cid = str(cid)

    deadline, expired = _parse_deadline(card.get("deadline", ""))

    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": card.get("title") or "",
        "region_field": "",       # 홈 카드에 지역 없음 → 지역필터 통과
        "is_delivery": False,
        "type": "",
        "channel": card.get("channel") or "",
        "applied": _int_or_none(card.get("applied")),
        "capacity": _int_or_none(card.get("capacity")),
        "point": None,            # 홈 카드에 포인트 없음
        "offer": "",
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,
        "url": f"{DETAIL_BASE}{cid}",
    }


def _int_or_none(s):
    if s in ("", None):
        return None
    m = re.search(r"\d+", str(s).replace(",", ""))
    return int(m.group()) if m else None


def _parse_deadline(text):
    """링블 마감 표시 → ('M/D', expired).

    - 'N일 남음'  → 오늘(KST)+N → 'M/D', expired=False
    - '오늘'/'오늘 마감'/'마감임박' → 오늘 날짜, expired=False (아직 신청 가능)
    - '마감' (남음/오늘 아님) → 과거로 간주, expired=True
    - 읽을 수 없음 → ('', False)
    """
    t = (text or "").strip()
    if not t:
        return "", False

    today = datetime.now(KST)

    # 'N일 남음' (가장 흔함)
    m = re.search(r"(\d+)\s*일\s*남음", t)
    if m:
        target = today + timedelta(days=int(m.group(1)))
        return f"{target.month}/{target.day}", False

    # '오늘 마감' / '오늘' / '마감임박' → 오늘까지 신청 가능
    if "오늘" in t or "마감임박" in t or "임박" in t:
        return f"{today.month}/{today.day}", False

    # '마감' (위 케이스 제외) → 종료됨
    if "마감" in t or "종료" in t:
        yday = today - timedelta(days=1)
        return f"{yday.month}/{yday.day}", True

    # 그 외 숫자가 있으면 남은 일수로 근사
    m = re.search(r"(\d+)", t)
    if m:
        target = today + timedelta(days=int(m.group(1)))
        return f"{target.month}/{target.day}", False

    return "", False
