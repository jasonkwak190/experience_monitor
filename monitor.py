"""
리뷰노트 체험단 목록 모니터링.

동작:
  1) 목록 페이지(LIST_URLS) GET  ← 상세는 robots.txt가 막아서 안 건드림
  2) 캠페인 파싱 (NEXT_DATA JSON 우선, 실패 시 HTML 파싱)
  3) 내 조건 필터 (지역/카테고리/경쟁률)
  4) 이전에 본 것과 비교 → 신규만
  5) 텔레그램 전송

사용법:
  python monitor.py            # 정상 실행 (신규만 알림)
  python monitor.py --debug    # 받은 HTML을 debug_page.html로 저장 (파싱 셀렉터 맞출 때)
  python monitor.py --test     # 신규 판정 무시하고 첫 캠페인 1건 강제 전송 (텔레그램 연결 테스트)
"""

import json
import re
import sys
import time
import os
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

import config
from telegram_notify import send_telegram


# 한국 시간대 (마감일 변환용)
KST = timezone(timedelta(hours=9))

# 리뷰노트가 JSON에 쓰는 영문 enum → 한글 표시값
# (실제 페이지 __NEXT_DATA__ 구조 확인해서 맞춤)
SORT_KO = {
    "DELIVERY": "배송형",   # 집으로 배송 → 재택
    "VISIT": "방문형",      # 매장 방문 필요
    "PAYBACK": "페이백",    # 구매 후 페이백 (대개 재택)
    "TAKEOUT": "포장형",    # 데이터상 재택으로 분류돼 옴
    "REPORTER": "기자단",   # 재택
    "ETC": "기타",
}
CHANNEL_KO = {
    "BLOG": "블로그",
    "REELS": "릴스",
    "SHORTS": "유튜브 쇼츠",
    "CLIP": "클립",
    "BLOG_CLIP": "블로그+클립",
}


# ──────────────────────────────────────────────
# 페이지 가져오기
# ──────────────────────────────────────────────
def fetch(url):
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.text


# ──────────────────────────────────────────────
# 파싱: 두 가지 방식
#   방식 1) Next.js __NEXT_DATA__ JSON  (가장 안정적, 우선 시도)
#   방식 2) HTML 태그 직접 파싱          (방식 1 실패 시 폴백)
#
# ★ Claude Code 할 일:
#   실제 debug_page.html 을 보고 둘 중 맞는 방식의 셀렉터/키를 조정할 것.
# ──────────────────────────────────────────────
def parse_campaigns(html):
    # ---- 방식 1: __NEXT_DATA__ JSON ----
    campaigns = _parse_from_next_data(html)
    if campaigns:
        print(f"[파싱] __NEXT_DATA__ JSON 방식으로 {len(campaigns)}건 파싱")
        return campaigns

    # ---- 방식 2: HTML 파싱 ----
    campaigns = _parse_from_html(html)
    print(f"[파싱] HTML 태그 방식으로 {len(campaigns)}건 파싱")
    return campaigns


def _parse_from_next_data(html):
    """
    Next.js 페이지는 보통 아래 형태의 script 안에 전체 데이터가 JSON으로 들어있다.
      <script id="__NEXT_DATA__" type="application/json">{...}</script>
    이 JSON 안에서 캠페인 배열을 찾는다.

    ★ Claude Code 할 일:
      debug_page.html 에서 __NEXT_DATA__ 안의 실제 JSON 경로를 확인하고
      아래 _extract_campaign_list() 의 탐색 키를 맞출 것.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return []

    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError:
        return []

    raw_list = _extract_campaign_list(data)
    result = []
    for item in raw_list:
        c = _normalize_campaign(item)
        if c:
            result.append(c)
    return result


def _extract_campaign_list(data):
    """
    캠페인 배열을 꺼낸다.

    실제 확인된 경로: data['props']['pageProps']['data']['objects']  (96건 배열)
    구조가 바뀌어 이 경로가 없으면 아래 휴리스틱 재귀 탐색으로 폴백한다.
    """
    try:
        objs = data["props"]["pageProps"]["data"]["objects"]
        if isinstance(objs, list) and objs:
            return objs
    except (KeyError, TypeError, IndexError):
        pass

    # ---- 폴백: 중첩 구조에서 '캠페인처럼 생긴' 배열을 재귀로 탐색 ----
    found = []

    def looks_like_campaign(obj):
        if not isinstance(obj, dict):
            return False
        has_id = any(k in obj for k in ("id", "campaignId", "_id", "seq"))
        has_title = any(k in obj for k in ("title", "name", "campaignName"))
        return has_id and has_title

    def walk(node):
        if isinstance(node, list):
            # 리스트 원소 다수가 캠페인처럼 생겼으면 이게 캠페인 배열
            if node and sum(1 for x in node[:5] if looks_like_campaign(x)) >= 1:
                for x in node:
                    if looks_like_campaign(x):
                        found.append(x)
            else:
                for x in node:
                    walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(data)
    return found


def _normalize_campaign(item):
    """
    캠페인 원본 dict(JSON) → 우리가 쓰는 표준 형태로 변환.
    실제 __NEXT_DATA__ 키에 맞게 매핑함.
    """
    cid = item.get("id")
    if cid is None:
        return None
    cid = str(cid)

    title = (item.get("title") or "").strip()

    # 지역: city=시도("서울"/"재택"), sido.name=시군구("송파구")
    city = item.get("city") or ""
    sido = item.get("sido") or {}
    sigungu = (sido.get("name") if isinstance(sido, dict) else "") or ""
    # "재택"이면 배송/온라인 → 지역 무관 (city·sido 둘 다 "재택"으로 옴)
    is_delivery = (city == "재택") or (sigungu == "재택")
    if is_delivery:
        region_field = "재택"
    else:
        region_field = " ".join(x for x in (city, sigungu) if x).strip()

    # 유형(sort) / 채널 — 영문 enum을 한글로
    ctype = SORT_KO.get(item.get("sort"), item.get("sort") or "")
    channel = CHANNEL_KO.get(item.get("channel"), item.get("channel") or "")

    # 신청자 / 모집인원
    applied = _to_int(item.get("applicantCount"))
    capacity = _to_int(item.get("infNum"))

    # 포인트 (정수, 0이면 없음)
    point = _to_int(item.get("infPoint"))

    # 제공 내역
    offer = (item.get("offer") or "").strip()

    # 신청 마감일 (UTC ISO → KST "M/D", 지났는지 여부)
    deadline, expired = _parse_deadline(item.get("applyEndAt"))

    return {
        "id": cid,
        "title": title,
        "region_field": region_field,
        "is_delivery": is_delivery,
        "type": str(ctype),
        "channel": str(channel),
        "applied": applied,
        "capacity": capacity,
        "point": point,
        "offer": offer,
        "deadline": deadline,
        "expired": expired,
        "status": item.get("status") or "",
        # 명시적 '100% 선정' 필드는 없음 → 모집≥신청 로직(passes_filter)으로 보충
        "guaranteed": False,
        # 상세 링크 (직접 접속 금지, 알림 링크용으로만)
        "url": f"{config.BASE_URL}/campaigns/{cid}",
    }


def _parse_deadline(iso_str):
    """ISO UTC 문자열(applyEndAt) → ('6/30', 마감지났는지 bool)"""
    if not iso_str:
        return "", False
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00")).astimezone(KST)
    except (ValueError, TypeError):
        return "", False
    return f"{dt.month}/{dt.day}", dt < datetime.now(KST)


def _parse_from_html(html):
    """
    HTML 태그 직접 파싱 (NEXT_DATA 실패 시 폴백).

    ★ Claude Code 할 일 (제일 중요):
      debug_page.html 을 열어서 캠페인 카드 하나의 실제 구조를 보고
      아래 셀렉터를 맞출 것. 지금 셀렉터는 추정값이라 그대로는 안 맞을 수 있음.

      확인할 것:
        - 캠페인 카드를 감싸는 요소 (예: <a href="/campaigns/123"> 전체가 카드일 수도)
        - 제목 텍스트 위치
        - "신청 4 / 3" 같은 숫자 텍스트 위치
        - 포인트 "100,000 P" 텍스트 위치
        - 유형 "배송형/방문형" 텍스트 위치
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []

    # 추정: 캠페인 상세로 가는 링크(<a href="/campaigns/숫자">)를 카드의 기준점으로 삼는다.
    # 같은 캠페인에 링크가 여러 개일 수 있으니 id 기준 중복 제거.
    seen_ids = set()
    for a in soup.find_all("a", href=re.compile(r"/campaigns/\d+")):
        href = a.get("href", "")
        m = re.search(r"/campaigns/(\d+)", href)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        # 카드 컨테이너: 링크의 부모 쪽으로 적당히 올라가 텍스트를 모은다
        card = a
        for _ in range(3):
            if card.parent:
                card = card.parent
        card_text = card.get_text(" ", strip=True)

        title = a.get_text(" ", strip=True) or _guess_title(card_text)

        # "신청 4 / 3" 또는 "신청 4 모집 3" 또는 "선착순 1 / 5" 패턴
        applied, capacity = _extract_counts(card_text)

        # 포인트 "100,000 P"
        point = _extract_point(card_text)

        # 유형
        ctype = _extract_type(card_text)

        # 100% 선정 배지
        guaranteed = ("100% 선정" in card_text) or ("100%선정" in card_text)

        result.append({
            "id": cid,
            "title": title,
            "region_field": "",
            "is_delivery": False,
            "type": ctype,
            "channel": "",
            "applied": applied,
            "capacity": capacity,
            "point": point,
            "offer": "",
            "deadline": "",
            "expired": False,
            "status": "",
            "guaranteed": guaranteed,
            "url": f"{config.BASE_URL}/campaigns/{cid}",
        })
    return result


# ──────────────────────────────────────────────
# 텍스트 추출 헬퍼
# ──────────────────────────────────────────────
def _to_int(v):
    if v is None:
        return None
    if isinstance(v, int):
        return v
    m = re.search(r"\d+", str(v).replace(",", ""))
    return int(m.group()) if m else None


def _extract_counts(text):
    """'신청 4 / 3', '선착순 1 / 5', '신청 4 모집 3' 등에서 (신청, 모집) 추출"""
    # 패턴 A: 신청 N / M  또는  선착순 N / M
    m = re.search(r"(?:신청|선착순)\s*([\d,]+)\s*/\s*([\d,]+)", text)
    if m:
        return _to_int(m.group(1)), _to_int(m.group(2))
    # 패턴 B: 신청 N ... 모집 M
    m = re.search(r"신청\s*([\d,]+).*?모집\s*([\d,]+)", text)
    if m:
        return _to_int(m.group(1)), _to_int(m.group(2))
    return None, None


def _extract_point(text):
    """'100,000 P' / '100,000P' 추출"""
    m = re.search(r"([\d,]+)\s*P\b", text)
    return m.group(1) + " P" if m else None


def _extract_type(text):
    for t in ["배송형", "방문형", "구매형", "포장", "기자단", "페이백"]:
        if t in text:
            return t
    return ""


def _guess_title(text):
    return text[:60]


# ──────────────────────────────────────────────
# 지역 추출 (제목의 [지역/시군구])
# ──────────────────────────────────────────────
def extract_region(campaign):
    """지역 문자열을 뽑는다.
    city/sido 로 만든 region_field 가 가장 정확하므로 그걸 우선한다.
    (제목의 [...] 는 '[부산 전역에서 참여 가능]' 같은 광고 문구일 수 있어 신뢰도가 낮음)
    region_field 가 없을 때(HTML 폴백 등)만 제목의 [지역] 패턴을 보조로 시도한다."""
    region_field = campaign.get("region_field") or ""
    if region_field:
        return region_field
    title = campaign.get("title") or ""
    m = re.search(r"\[([^\]]+)\]", title)
    if m:
        return m.group(1)  # 예: "서울/강북구"
    return ""


# ──────────────────────────────────────────────
# 필터: 내 조건에 맞는지
# ──────────────────────────────────────────────
def passes_filter(campaign):
    # 0) 신청 마감이 지난 캠페인은 제외
    if campaign.get("expired"):
        return False

    region_str = extract_region(campaign)

    # 1) 지역 필터 (유형이 아니라 실제 지역값으로 판정 — 가장 정확)
    #    - 재택(city=="재택"): 집에서 받음 → 지역 무관하게 통과
    #    - 지역이 찍힌 캠페인(방문형·방문페이백 등): 시군구가 내 동선이어야 통과
    #      예) '경기 양주시' 페이백은 양주 매장 방문이라, 내 동선 아니면 제외
    #    - 지역 정보를 아예 못 읽은 경우(HTML 폴백 등): 놓치지 않게 통과
    if campaign.get("is_delivery"):
        region_ok = True
    elif not region_str:
        region_ok = True
    else:
        region_ok = any(r in region_str for r in config.MY_REGIONS)

    if not region_ok:
        return False

    # 2) 채널 필터 (설정했을 때만)
    if config.MY_CHANNELS:
        ch = campaign.get("channel", "")
        # 채널 정보를 못 읽었으면 통과시킨다(놓치지 않으려고)
        if ch and not any(c in ch for c in config.MY_CHANNELS):
            return False

    # 3) 경쟁률 필터
    applied = campaign["applied"]
    capacity = campaign["capacity"]

    # 100% 선정 배지거나, 모집인원 >= 신청자 면 거의 확정 → 무조건 통과
    if config.ALWAYS_NOTIFY_GUARANTEED:
        if campaign["guaranteed"]:
            return True
        if applied is not None and capacity is not None and capacity >= applied:
            return True

    # 숫자를 못 읽었으면 통과(놓치지 않으려고)
    if applied is None or capacity is None or capacity == 0:
        return True

    ratio = applied / capacity
    return ratio <= config.MAX_COMPETITION_RATIO


# ──────────────────────────────────────────────
# 신규 판정용 저장/로드
# ──────────────────────────────────────────────
def load_seen():
    if not os.path.exists(config.SEEN_FILE):
        return set()
    try:
        with open(config.SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(ids):
    # 원자적 쓰기: 임시파일에 먼저 쓰고 교체.
    # (바로 "w"로 열면 쓰는 중 크래시 시 파일이 빈 채로 남아 seen 기록이 통째로 리셋됨)
    tmp = config.SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=0)
    os.replace(tmp, config.SEEN_FILE)


# ──────────────────────────────────────────────
# 알림 메시지 포맷
# ──────────────────────────────────────────────
def format_message(c):
    region = extract_region(c)
    star = "⭐ " if (c["guaranteed"] or (
        c["applied"] is not None and c["capacity"] is not None and c["capacity"] >= c["applied"]
    )) else ""

    lines = [f"{star}<b>{_esc(c['title'])}</b>"]

    meta = []
    if c["type"]:
        meta.append(c["type"])
    if region:
        meta.append(region)
    if meta:
        lines.append("📍 " + " · ".join(_esc(m) for m in meta))

    # 채널(블로그/릴스/유튜브 쇼츠/클립 등)을 별도 줄로 명확히 표시
    if c["channel"]:
        lines.append(f"📱 채널: {_esc(c['channel'])}")

    if c["applied"] is not None and c["capacity"] is not None:
        ratio = (c["applied"] / c["capacity"]) if c["capacity"] else 0
        lines.append(f"👥 신청 {c['applied']} / 모집 {c['capacity']}  (경쟁률 {ratio:.1f}:1)")

    gift = []
    if c["offer"]:
        gift.append(_esc(c["offer"][:60]))
    pt = _to_int(c["point"])  # JSON경로는 int, HTML폴백은 "100,000 P" 문자열 → 안전 정수화
    if pt:
        gift.append(f"{pt:,} P")
    if gift:
        lines.append("🎁 " + " + ".join(gift))

    if c["deadline"]:
        lines.append(f"⏰ ~{_esc(c['deadline'])} 마감")

    lines.append(f"🔗 {c['url']}")
    return "\n".join(lines)


def _esc(s):
    """텔레그램 HTML 파스 모드용 최소 이스케이프"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def run(debug=False, test=False):
    all_campaigns = {}
    fetch_failed = False

    for url in config.LIST_URLS:
        print(f"[수집] {url}")
        try:
            html = fetch(url)
        except requests.RequestException as e:
            print(f"  요청 실패: {e}")
            fetch_failed = True
            continue

        if debug:
            fname = "debug_page.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  → HTML 저장: {fname} (이걸 열어서 파싱 셀렉터를 맞추세요)")
            continue

        for c in parse_campaigns(html):
            all_campaigns[c["id"]] = c  # id 기준 중복 제거

        time.sleep(config.REQUEST_DELAY_SEC)

    if debug:
        print("[debug] HTML 저장만 하고 종료")
        return

    print(f"[수집] 총 {len(all_campaigns)}개 캠페인 파싱됨")

    # 필터
    matched = [c for c in all_campaigns.values() if passes_filter(c)]
    print(f"[필터] 조건 통과 {len(matched)}개")

    # 테스트 모드: 신규 판정 무시하고 1건 강제 전송
    if test:
        if matched:
            print("[테스트] 첫 매칭 캠페인 1건 텔레그램 전송")
            send_telegram(format_message(matched[0]))
        elif all_campaigns:
            print("[테스트] 매칭 0건 → 필터 무시하고 아무거나 1건 전송")
            send_telegram(format_message(next(iter(all_campaigns.values()))))
        else:
            print("[테스트] 파싱된 캠페인이 없음. 먼저 --debug 로 HTML 구조 확인 필요.")
        return

    # 신규 판정
    seen = load_seen()
    current_ids = set(all_campaigns.keys())

    if not seen and config.SILENT_FIRST_RUN:
        # 첫 실행: 알림 없이 현재 것 전부 '본 것'으로 저장.
        # 단, 일부 페이지 수집이 실패했다면 기준선이 부실하게 잡혀
        # 다음 실행에 누락분이 전부 '신규'로 폭주할 수 있으니 저장을 보류한다.
        if fetch_failed:
            print("[신규] 첫 실행인데 일부 페이지 수집 실패 → 기준선 저장 보류 (다음 정상 실행 때 시딩)")
            return
        save_seen(current_ids)
        print(f"[신규] 첫 실행 → 알림 생략, {len(current_ids)}개를 기준선으로 저장")
        return

    new_matched = [c for c in matched if c["id"] not in seen]
    print(f"[신규] 조건 통과 중 신규 {len(new_matched)}개")

    # 알림 전송
    sent = 0
    for c in new_matched:
        try:
            send_telegram(format_message(c))
            sent += 1
            time.sleep(0.5)  # 텔레그램 rate limit 여유
        except Exception as e:
            print(f"  전송 실패({c['id']}): {e}")

    print(f"[알림] {sent}건 전송")

    # 본 목록 갱신 (이번에 파싱된 전체를 저장 → 다음엔 이것들 제외하고 신규 판정)
    save_seen(seen | current_ids)
    print("[저장] seen 갱신 완료")


if __name__ == "__main__":
    run(
        debug="--debug" in sys.argv,
        test="--test" in sys.argv,
    )
