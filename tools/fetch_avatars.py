"""부스원들의 X 프로필 사진을 크롤링해서 avatars/아이디.jpg 로 저장하는 스크립트.

유료 API나 API 키 없이, 일반 브라우저가 보는 공개 페이지에서 프로필 사진 주소를 찾아 받아옴.
  1) X 임베드 위젯이 쓰는 공개 페이지(syndication.twitter.com/srv/timeline-profile/...)의
     HTML 안에 들어 있는 profile_image_url_https 를 찾음 (파이썬 기본 라이브러리만 사용)
  2) 1)에서 못 찾으면(이 위젯 페이지는 IP 단위로 429 가 자주 남), 링크 미리보기용 오픈소스
     서비스 FxTwitter 의 공개 JSON(api.fxtwitter.com/아이디, 무료·키 없음)에서 avatar_url 을 찾음
  3) 그래도 못 찾으면, Playwright 가 설치되어 있을 때만 x.com/아이디 페이지를
     헤드리스 브라우저로 열어 프로필 사진 이미지를 찾음
서버에 부담을 주지 않도록 요청 사이에 쉬는 시간을 두고, 이미 받은 사진은 건너뜀.

사용법 (저장소 루트에서):
    python3 tools/fetch_avatars.py                # 사진이 없는 부스원만 받기
    python3 tools/fetch_avatars.py --force        # 이미 있는 사진도 다시 받기
    python3 tools/fetch_avatars.py ringo2ALS ...  # 특정 아이디만 받기
    python3 tools/fetch_avatars.py --delay 3      # 요청 간격(초) 조절, 기본 2초

Playwright 를 함께 쓰려면(선택):
    pip install playwright && python -m playwright install chromium

끝나면 python3 tools/build_booths.py 를 다시 실행하면
data/x-handles-missing-avatars.txt 의 "사진 없는 아이디" 목록이 갱신됨.
"""
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_booths  # noqa: E402

ROOT = build_booths.ROOT
AVATAR_DIR = ROOT / "avatars"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)

# Windows 인증서 저장소에 만료된 중간/교차 인증서가 남아 있으면 일부 사이트(api.fxtwitter.com 등)에서
# "certificate has expired" 오류가 남. certifi 가 설치되어 있으면 그 인증서 묶음을 씀(선택: pip install certifi)
try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()


def http_get(url, retries=3):
    """GET 요청. 429(요청 과다)나 일시 오류면 점점 길게 쉬었다가 다시 시도함."""
    wait = 10
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                print(f"    {e.code} 응답 → {wait}초 쉬고 다시 시도")
                time.sleep(wait)
                wait *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(wait)
                continue
            raise
    return None


def bigger(url):
    # _normal(48px) 대신 400x400 크기로 받음
    return re.sub(r"_(normal|bigger|mini)(\.\w+)$", r"_400x400\2", url)


_syndication_blocked = False


def find_via_syndication(handle):
    global _syndication_blocked
    if _syndication_blocked:
        return None
    try:
        raw = http_get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}", retries=1)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # IP 단위 제한이라 계속 두드려도 소용없음 → 이번 실행에서는 더 쓰지 않음
            print("    공개 위젯 페이지가 429(요청 과다) → 이번 실행에서는 건너뜀")
            _syndication_blocked = True
            return None
        raise
    html = raw.decode("utf-8", "replace")
    return _parse_syndication(html, handle)


def find_via_fxtwitter(handle):
    try:
        data = json.loads(http_get(f"https://api.fxtwitter.com/{handle}"))
    except (urllib.error.HTTPError, ValueError):
        # 없는 계정이면 JSON 이 아닌 페이지로 넘어감
        return None
    user = data.get("user") or {}
    if str(user.get("screen_name", "")).lower() != handle.lower() or not user.get("avatar_url"):
        return None
    return bigger(user["avatar_url"])


def _parse_syndication(html, handle):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    found = []

    def walk(node):
        # 리트윗 등으로 다른 사람의 정보도 섞여 있으므로 screen_name 이 같은 것만 고름
        if isinstance(node, dict):
            if str(node.get("screen_name", "")).lower() == handle.lower() and node.get("profile_image_url_https"):
                found.append(node["profile_image_url_https"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads(m.group(1)))
    return bigger(found[0]) if found else None


_browser = None


def find_via_playwright(handle):
    global _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    if _browser is None:
        _browser = sync_playwright().start().chromium.launch()
    page = _browser.new_page(user_agent=USER_AGENT)
    try:
        page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=30000)
        # 프로필 사진 링크(/아이디/photo) 안의 이미지가 뜰 때까지 기다림
        img = page.wait_for_selector(f'a[href$="/{handle}/photo" i] img, img[src*="profile_images"]', timeout=15000)
        src = img.get_attribute("src") if img else None
        return bigger(src) if src else None
    except Exception:
        return None
    finally:
        page.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    delay = 2.0
    if "--delay" in sys.argv:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])
        args = [a for a in args if a != sys.argv[sys.argv.index("--delay") + 1]]

    handles = args or sorted(
        {m["twitter"] for b in build_booths.build() for m in b["members"] if m["twitter"]}, key=str.lower
    )
    AVATAR_DIR.mkdir(exist_ok=True)
    ok, skipped, failed = 0, 0, []
    for i, handle in enumerate(handles, 1):
        # 파일 이름은 사이트 데이터의 아이디 대소문자 그대로 (GitHub Pages/Cloudflare는 대소문자 구분)
        path = AVATAR_DIR / f"{handle}.jpg"
        if path.exists() and not force:
            skipped += 1
            continue
        print(f"[{i}/{len(handles)}] @{handle}")
        url = None
        try:
            url = find_via_syndication(handle)
        except Exception as e:
            print(f"    공개 위젯 페이지 실패: {e}")
        if not url:
            try:
                url = find_via_fxtwitter(handle)
            except Exception as e:
                print(f"    FxTwitter 실패: {e}")
        if not url:
            url = find_via_playwright(handle)
        if not url:
            print("    프로필 사진을 찾지 못함 (계정이 없거나 비공개/정지일 수 있음)")
            failed.append(handle)
        else:
            try:
                path.write_bytes(http_get(url))
                ok += 1
                print(f"    저장: {path.relative_to(ROOT)}")
            except Exception as e:
                print(f"    이미지 받기 실패: {e}")
                failed.append(handle)
        time.sleep(delay)

    print(f"\n완료: 새로 받음 {ok}개, 이미 있어서 건너뜀 {skipped}개, 실패 {len(failed)}개")
    if failed:
        print("실패한 아이디:", ", ".join(failed))


if __name__ == "__main__":
    main()
