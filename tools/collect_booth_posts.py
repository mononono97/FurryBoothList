"""부스 X 계정의 새 글 중 "상품 글 같아 보이는 글"을 모아 후보 목록(candidates.json)을 만드는 스크립트.

GitHub Actions(.github/workflows/collect-booth-posts.yml)가 1시간마다 실행함.
후보는 사이트에 바로 나가지 않고, 관리 화면(admin.html)에서 운영자가 "반영"을 눌러야 부스 상세에 보임.

사용법 (저장소 루트에서):
    python3 tools/collect_booth_posts.py <후보 폴더>            # <후보 폴더>/candidates.json 갱신
    python3 tools/collect_booth_posts.py <후보 폴더> --dry-run  # 파일은 건드리지 않고 결과만 출력

동작:
1. index.html 의 BOOTHS 줄에서 부스장 X 아이디를 읽음
2. 아이디마다 FxTwitter 공개 JSON(무료·키 없음)으로 최근 글 약 20개를 받음
   (클라우드 개발 환경에서는 FxTwitter 가 막혀 있어 GitHub Actions 에서만 동작함)
3. 리트윗·답글·POST_SINCE 이전 글은 거르고, judge() 로 점수를 매겨 MIN_SCORE 이상이면 후보로 추가
4. 이미 후보에 있는 글은 다시 넣지 않음. 후보는 지우지 않고 계속 쌓임(반영/제외 여부는 Firestore 에 저장됨)

AI 판단을 붙일 때는 judge() 안에서 키워드 점수가 애매한 글만 Claude API 로 다시 물어보도록 바꾸면 됨.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "index.html"
PROTECTED_PATH = ROOT / "data" / "x-protected-handles.txt"

# 이 시각 이전 글은 후보로 보지 않음. 부스 작가 상당수가 2026-09-20 케모켓에도 나가서,
# 그 행사 인포 글이 섞이지 않도록 케모켓 다음 날부터 봄 (2026-10-04 첫 시험 결과)
POST_SINCE = datetime(2026, 9, 21, tzinfo=timezone.utc)
MIN_SCORE = 2
REQUEST_GAP_SECONDS = 1.0
USER_AGENT = "FurryBoothList booth post collector (+https://github.com/mononono97/FurryBoothList)"

# (정규식, 점수, 설명). 한국어·일본어·영어 작가가 섞여 있어서 세 언어를 다 봄.
KEYWORDS = [
    (r"인포|お品書き|おしながき|品書き|\binfo\b", 3, "인포"),
    (r"신간|新刊|既刊|구간|new release", 3, "신간"),
    (r"통판|通販|선입금|예약 ?판매|사전 ?예약|予約|pre-?order", 2, "통판·예약"),
    (r"굿즈|グッズ|상품|頒布|판매|販売|merch|goods", 2, "굿즈·판매"),
    (r"아크릴|アクリル|acryl|스티커|ステッカー|sticker|엽서|ポストカード|postcard|키링|キーホルダー|keychain|"
     r"뱃지|배지|缶バッジ|badge|회지|동인지|同人誌|일러스트북|イラスト集|artbook|포카|태피스트리|タペストリー|"
     r"마우스패드|머그|티셔츠|tシャツ|t-shirt|포스터|ポスター|print", 1, "품목"),
    (r"\d[\d,]*\s?원|₩\s?\d|\d[\d,]*\s?円|¥\s?\d|\d[\d,]*\s?(?:krw|jpy)", 2, "가격"),
    (r"퍼스트\s?클래스|퍼클|furst\s?class|ファーストクラス|부스|ブース|booth|(?<![A-Za-z0-9])[A-C]-?\d{1,2}(?!\d)", 1, "행사·부스"),
]
KEYWORD_RES = [(re.compile(p, re.I), s, label) for p, s, label in KEYWORDS]


def load_booths():
    html = HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r"^const BOOTHS = (.*);$", html, re.M)
    if not m:
        sys.exit("index.html 에서 'const BOOTHS = ...;' 줄을 찾지 못했습니다.")
    return json.loads(m.group(1))


def load_protected():
    if not PROTECTED_PATH.exists():
        return set()
    lines = PROTECTED_PATH.read_text(encoding="utf-8").splitlines()
    return {l.strip().lstrip("@").lower() for l in lines if l.strip() and not l.startswith("#")}


def handle_map(booths):
    """소문자 X 아이디 → (표기 그대로의 아이디, [부스 id...])"""
    protected = load_protected()
    result = {}
    for booth in booths:
        # 부스장(booth.twitter) 계정 글만 봄. 부스원 계정까지 보면 후보가 너무 많아져서 뺌 (2026-10-04 사용자 요청)
        for h in filter(None, [booth.get("twitter")]):
            if h.lower() in protected:
                continue
            entry = result.setdefault(h.lower(), (h, []))
            if booth["id"] not in entry[1]:
                entry[1].append(booth["id"])
    return result


def fetch_statuses(handle):
    # FxTwitter 는 같은 계정이라도 가끔 빈 결과(404)를 줌(대문자 아이디일 때 특히 자주).
    # 소문자 → 표기 그대로 순서로, 잠깐 쉬었다가 한 번씩 더 시도함
    last_error = None
    for name in dict.fromkeys([handle.lower(), handle]):
        for attempt in range(2):
            url = f"https://api.fxtwitter.com/2/profile/{name}/statuses"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=20) as res:
                    return json.load(res).get("results") or []
            except urllib.error.HTTPError as e:
                last_error = e
                time.sleep(2)
    raise last_error


def post_text(p):
    return (p.get("raw_text") or {}).get("text") or p.get("text") or ""


def post_images(p):
    media = p.get("media") or {}
    urls = []
    for photo in media.get("photos") or []:
        if photo.get("url"):
            urls.append(photo["url"])
    for video in media.get("videos") or []:
        if video.get("thumbnail_url"):
            urls.append(video["thumbnail_url"])
    return urls[:4]


def judge(text, images):
    """(점수, 걸린 키워드 설명 목록). 키워드가 하나라도 걸리고 이미지가 있으면 +1."""
    text = re.sub(r"https?://\S+", " ", text)  # t.co 링크 안의 글자가 키워드로 잡히지 않게
    score, reasons = 0, []
    for regex, points, label in KEYWORD_RES:
        if regex.search(text):
            score += points
            reasons.append(label)
    if images and score:
        score += 1
        reasons.append("이미지")
    return score, reasons


def collect(feed_dir, dry_run=False):
    booths = load_booths()
    names = {b["id"]: b["name"] for b in booths}
    handles = handle_map(booths)
    path = Path(feed_dir) / "candidates.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"posts": []}
    # 지금 보는 계정(부스장)이 아닌 글은 후보에서 뺌. 부스원 계정도 모으던 때(2026-10-04) 쌓인 후보 정리용
    before = len(data["posts"])
    data["posts"] = [p for p in data["posts"] if p.get("handle", "").lower() in handles]
    removed = before - len(data["posts"])
    known = {p["id"] for p in data["posts"]}

    added, failed = [], []
    for key, (handle, booth_ids) in sorted(handles.items()):
        try:
            results = fetch_statuses(handle)
        except Exception as e:  # 계정 하나가 실패해도 나머지는 계속 모음
            failed.append(f"{handle}: {e}")
            results = []
        for p in results:
            pid = str(p.get("id") or "")
            author = ((p.get("author") or {}).get("screen_name") or "").lower()
            if not pid or pid in known or p.get("reposted_by") or p.get("replying_to") or author != key:
                continue
            created = datetime.fromtimestamp(p.get("created_timestamp") or 0, tz=timezone.utc)
            if created < POST_SINCE:
                continue
            text = post_text(p)
            images = post_images(p)
            score, reasons = judge(text, images)
            if score < MIN_SCORE:
                continue
            known.add(pid)
            added.append({
                "id": pid,
                "handle": (p.get("author") or {}).get("screen_name") or handle,
                "booths": booth_ids,
                "boothNames": [names.get(b, b) for b in booth_ids],
                "created": created.isoformat(),
                "text": text,
                "images": images,
                "score": score,
                # X 는 민감한 글로 표시된 글을 로그인하지 않은 방문자에게 임베드해 주지 않음("Not found").
                # 이런 글은 사이트에서 임베드 대신 글과 링크만 보여줌
                "sensitive": bool(p.get("possibly_sensitive")),
                # 인용한 글이 있는 글. X 임베드는 인용 글을 숨길 수 없어 사이트에서 본문·이미지 카드로 보여줌
                "quote": bool(p.get("quote")),
                "reasons": reasons,
                "foundAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
        time.sleep(REQUEST_GAP_SECONDS)

    print(f"계정 {len(handles)}개 확인, 새 후보 {len(added)}개, 실패 {len(failed)}개")
    for c in added:
        print(f"  + {c['booths']} @{c['handle']} 점수 {c['score']} {c['reasons']} {c['text'][:60]!r}")
    for f in failed:
        print(f"  ! {f}")
    if removed:
        print(f"  부스장이 아닌 계정의 후보 {removed}개 정리")
    if dry_run or not (added or removed):
        return  # 새 후보가 없으면 파일을 그대로 둬서 쓸데없는 커밋이 생기지 않게 함

    # 최신 글이 위로 오도록 id(시간순으로 커지는 숫자) 기준 내림차순
    posts = sorted(data["posts"] + added, key=lambda c: int(c["id"]), reverse=True)
    out = {"updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "posts": posts}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    collect(args[0], dry_run="--dry-run" in sys.argv)
