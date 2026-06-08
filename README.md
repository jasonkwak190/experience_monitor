# 리뷰노트 체험단 모니터 — 셋업 가이드

리뷰노트 신규 체험단 중 **내 조건(지역/카테고리/경쟁률)에 맞는 것**이 뜨면
텔레그램으로 알려주는 봇. GitHub Actions로 무료로 24시간 돌아간다 (PC 안 켜둬도 됨).

---

## 전체 그림

```
GitHub Actions (2시간마다, 새벽 제외)
   → 리뷰노트 목록 페이지 크롤링 (상세는 robots.txt 막혀서 안 건드림)
   → 내 조건 필터 (지역/카테고리/경쟁률)
   → 신규만 골라서
   → 텔레그램으로 알림 (캠페인 정보 + 상세 링크)
   → 나는 링크 눌러서 조건 확인하고 직접 신청
```

---

## STEP 1. 텔레그램 봇 만들기 (5분)

1. 텔레그램 앱에서 **@BotFather** 검색해서 대화 시작
2. `/newbot` 입력
3. 봇 이름 정하기 (아무거나, 예: `리뷰노트알림`)
4. 봇 username 정하기 (`_bot` 으로 끝나야 함, 예: `my_reviewnote_bot`)
5. BotFather가 **토큰**을 준다. 이렇게 생김:
   ```
   8012345678:AAH1a2b3c4d5e6f7g8h9i0j-kLmNoPqRsTu
   ```
   → 이게 `TELEGRAM_BOT_TOKEN`. 복사해둔다.

## STEP 2. 내 chat_id 알아내기 (3분)

1. 방금 만든 봇을 검색해서 대화 시작 → 아무 메시지나 보낸다 (예: `hi`)
   - **이 단계 꼭 해야 함.** 봇한테 먼저 말 걸어야 봇이 나한테 메시지 보낼 수 있음.
2. 브라우저에서 아래 주소 열기 (토큰 자리에 본인 토큰):
   ```
   https://api.telegram.org/bot<여기에_토큰>/getUpdates
   ```
   예:
   ```
   https://api.telegram.org/bot8012345678:AAH1a2.../getUpdates
   ```
3. 나오는 JSON에서 `"chat":{"id":123456789` 의 숫자를 찾는다.
   - 그 숫자가 `TELEGRAM_CHAT_ID`. 복사해둔다.
   - 안 보이면 봇한테 메시지 한 번 더 보내고 새로고침.

## STEP 3. 로컬에서 먼저 테스트 (Claude Code로)

> 이 단계는 GitHub에 올리기 전에 코드가 잘 도는지 확인하는 과정.
> Claude Code에서 이 폴더 열고 진행하면 된다.

```bash
# 1) 의존성 설치
pip install -r requirements.txt

# 2) 텔레그램 정보 환경변수로 등록 (본인 값으로)
export TELEGRAM_BOT_TOKEN="8012345678:AAH1a2..."
export TELEGRAM_CHAT_ID="123456789"

# 3) 텔레그램 연결만 먼저 테스트
python telegram_notify.py
#  → 텔레그램에 "✅ 연결 테스트 성공" 오면 OK

# 4) 실제 페이지 HTML 받아서 구조 확인  ★중요★
python monitor.py --debug
#  → debug_page.html 생성됨. 이걸 열어서 캠페인 카드 구조 확인.
#  → 아래 STEP 4 (파싱 맞추기) 진행.

# 5) 파싱 맞춘 뒤, 강제 전송 테스트
python monitor.py --test
#  → 조건 맞는 캠페인 1건이 텔레그램으로 오면 성공

# 6) 신규 감지까지 정상 동작 확인
python monitor.py
#  → 첫 실행은 (SILENT_FIRST_RUN=True 라) 알림 없이 기준선만 저장
#  → 한 번 더 실행하면 그 사이 올라온 신규만 알림
```

## STEP 4. 파싱 맞추기 (Claude Code에게 시킬 일) ★가장 중요★

`monitor.py` 의 파싱 부분은 **실제 HTML 구조에 맞게 조정이 필요**하다.
(작성자가 본 건 마크다운 변환본이라 실제 태그/클래스가 다를 수 있음)

**Claude Code 에게 이렇게 시키면 된다:**

> "debug_page.html 을 열어서 리뷰노트 캠페인 목록의 실제 구조를 분석해줘.
> 그리고 monitor.py 의 parse_campaigns 가 제대로 파싱하도록 셀렉터를 맞춰줘.
> 우선 `<script id="__NEXT_DATA__">` 안에 JSON으로 캠페인 데이터가 있는지 확인하고,
> 있으면 그 JSON 경로를 _extract_campaign_list 와 _normalize_campaign 에 정확히 반영해줘.
> 없으면 HTML 태그 파싱(_parse_from_html)의 셀렉터를 실제 카드 구조에 맞춰줘."

확인 포인트:
- 캠페인 데이터가 `__NEXT_DATA__` JSON 안에 있는가? (Next.js라 그럴 가능성 높음)
  - 있으면: JSON 경로만 맞추면 끝 (가장 안정적)
  - 없으면: HTML 카드 셀렉터 맞추기
- 각 캠페인에서 추출되어야 할 것: id, 제목, 지역, 유형, 채널, 신청/모집 인원, 포인트, 마감일, 100%선정 배지

`python monitor.py --test` 로 텔레그램에 제대로 된 정보가 오면 파싱 성공.

## STEP 5. GitHub에 올리기

1. GitHub에서 **새 저장소(repository)** 생성
   - 이름 아무거나 (예: `reviewnote-monitor`)
   - **Private 추천** (공개해도 되지만 굳이)
   - 비공개여도 이 프로젝트는 가벼워서 무료 한도(월 2,000분) 한참 안에 들어옴
     (2시간 간격 × 하루 9회 × 약 1분 = 한 달 약 270분)

2. 이 폴더를 푸시:
   ```bash
   git init
   git add .
   git commit -m "리뷰노트 모니터 초기 커밋"
   git branch -M main
   git remote add origin https://github.com/본인계정/reviewnote-monitor.git
   git push -u origin main
   ```
   - **주의:** `seen_campaigns.json` 은 자동 생성/갱신되는 파일이라 처음엔 없어도 됨.
   - 토큰/chat_id 는 코드에 없으니(환경변수라) 그대로 올려도 안전.

## STEP 6. GitHub Secrets 등록 (토큰 보관)

GitHub 저장소에서:

1. **Settings → Secrets and variables → Actions** 이동
2. **New repository secret** 클릭해서 2개 등록:
   - 이름 `TELEGRAM_BOT_TOKEN`, 값: 본인 봇 토큰
   - 이름 `TELEGRAM_CHAT_ID`, 값: 본인 chat_id

이러면 Actions가 실행될 때 이 값들을 자동으로 사용한다.

## STEP 7. 첫 실행 & 확인

1. GitHub 저장소 → **Actions** 탭
2. 왼쪽에서 "리뷰노트 체험단 모니터" 워크플로우 선택
3. **Run workflow** 버튼으로 수동 실행 (스케줄 기다리지 말고 바로 테스트)
4. 로그 확인:
   - 첫 실행은 `[신규] 첫 실행 → 알림 생략, N개를 기준선으로 저장` 떠야 정상
5. 한 번 더 수동 실행하거나 다음 스케줄 기다리면 → 그 사이 신규만 텔레그램으로 옴

이후로는 **2시간마다(아침7시~밤11시) 자동으로 돌면서** 신규 캠페인을 알려준다.

---

## 설정 바꾸기 (config.py)

| 바꾸고 싶은 것 | config.py 에서 수정할 부분 |
|---|---|
| 지역 추가/제거 | `MY_REGIONS` 리스트 |
| 경쟁률 기준 (더 빡세게/느슨하게) | `MAX_COMPETITION_RATIO` (낮출수록 선정 쉬운 것만) |
| 특정 채널만 (블로그/릴스 등) | `MY_CHANNELS` 리스트 (빈 리스트=전체) |
| 방문형 빼고 배송형만 | `VISIT_TYPES = []` 로 비우기 |
| 알림 너무 많음 | `MAX_COMPETITION_RATIO` 낮추기 + `MY_CHANNELS` 좁히기 |
| 알림 너무 적음 | `MAX_COMPETITION_RATIO` 높이기 + 지역 추가 |

실행 간격 바꾸려면 `.github/workflows/monitor.yml` 의 cron 수정
(UTC 기준인 거 주의 — 파일 상단 주석에 한국시간 변환표 있음).

---

## 지킨 원칙 (중요)

- **목록만 크롤링.** 상세 페이지(`/campaigns/{id}`)는 robots.txt가 막아서 안 건드림.
  알림엔 링크만 넣고, 조건 확인은 사람이 직접 들어가서 본다.
- **자동 신청 안 함.** 알림만. 신청은 사람이 직접 (선정 한마디 매번 다르게 써야 선정률↑, 자동신청은 약관위반).
- **매너 크롤링.** 2시간 간격, 정상 User-Agent, 요청 사이 딜레이.

---

## 다음 단계 (선택)

잘 돌면 다른 체험단 사이트(강남맛집, 레뷰 등)도 같은 방식으로 추가 가능.
각 사이트 robots.txt 확인하고, monitor.py 의 파싱 함수를 사이트별로 추가하면
여러 플랫폼 신규 캠페인을 한 텔레그램으로 모아 받을 수 있다.

## 문제 해결

| 증상 | 원인/해결 |
|---|---|
| 파싱 0건 | HTML 구조 안 맞음 → `--debug` 로 debug_page.html 보고 셀렉터 수정 (STEP 4) |
| 텔레그램 안 옴 | 봇한테 먼저 메시지 보냈는지 확인 / Secrets 값 확인 |
| 첫 실행에 알림 폭탄 | `config.SILENT_FIRST_RUN = True` 인지 확인 |
| Actions 실패 (push 권한) | workflow 의 `permissions: contents: write` 확인 |
| 알림이 한국시간 안 맞음 | cron이 UTC 기준. monitor.yml 주석의 변환표 참고 |
