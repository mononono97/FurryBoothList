"""Furst Class 부스 시트(CSV) → index.html 안의 BOOTHS 데이터로 변환하는 스크립트.

사용법 (저장소 루트에서):
    python3 tools/build_booths.py            # data/furstclass_booths.csv 를 읽어 index.html 갱신
    python3 tools/build_booths.py --check    # index.html은 건드리지 않고 파싱 결과만 출력

시트 열 구성 (구글 시트 → 파일 → 다운로드 → CSV 로 받은 파일 기준):
    A 부스 번호 / B 일반·성인 / C 부스 이름 / D 부스 리더 / E, F 부스원
    G, H (입금 여부, 추가 인원) 는 사용하지 않음 / I 부스컷 제출 여부(O면 제출)

부스 리더·부스원 칸은 "닉네임 / @X아이디", "닉네임 @X아이디", "닉네임(@X아이디)",
"@X아이디" 만 있는 경우, X 아이디 없이 닉네임만 있는 경우 등이 섞여 있어서
parse_person() 에서 최대한 정리함. 자동으로 판단하기 애매한 칸은 OVERRIDES 에 직접 적어둠.
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "furstclass_booths.csv"
HTML_PATH = ROOT / "index.html"
AVATAR_DIR = ROOT / "avatars"
MISSING_AVATARS_PATH = ROOT / "data" / "x-handles-missing-avatars.txt"

# X(트위터) 아이디: 영문/숫자/밑줄 1~15자
HANDLE_RE = r"[A-Za-z0-9_]{1,15}"

# 비어있는 것으로 취급할 칸
EMPTY_VALUES = {"", "없음", "-", "x", "X", "추후 추가하겠습니다."}

# 자동 파싱이 애매한 칸은 원문 그대로를 키로 해서 직접 지정함.
# 값: (표시할 닉네임, X 아이디 또는 None)
OVERRIDES = {
    # 시트에는 "@Ringo@2ALS" 로 적혀 있지만 실제 아이디는 ringo2ALS (2026-10-02 확인)
    "해태/@Ringo@2ALS": ("해태", "ringo2ALS"),
    # 시트의 young_wonyang 은 X에 없는 계정. 실제 아이디는 youngwonyang (2026-10-02 확인)
    "영원양 /@young_wonyang": ("영원양", "youngwonyang"),
    # @ 뒤가 한글이라 아이디가 아님. 슬래시 뒤 영문이 실제 아이디로 보임
    "@타패/tappe124": ("타패", "tappe124"),
    # @ 없이 "닉네임/아이디" 형태로 적힌 리더 칸
    "콩조림/kongzorim": ("콩조림", "kongzorim"),
    # 전각 ＠ 는 아이디 구분자가 아니라 이름의 일부
    "凛＠STUDIO雄虎凛/@GTDignified": ("凛＠STUDIO雄虎凛", "GTDignified"),
}


def clean_name(text):
    """아이디를 떼어낸 뒤 남은 닉네임 부분을 정리함."""
    text = re.sub(r"\(\s*\)", "", text)          # 아이디만 들어있던 빈 괄호 제거
    text = text.strip(" /\t")
    # 짝이 맞지 않는 괄호만 떼어냄 ("Keishi(KC)" 처럼 이름에 원래 있는 괄호는 유지)
    if text.endswith(")") and text.count("(") < text.count(")"):
        text = text[:-1].strip()
    if text.startswith("(") and text.count("(") > text.count(")"):
        text = text[1:].strip()
    text = text.strip(" /\t")
    # "이데아/ 이데아" 처럼 같은 이름이 슬래시로 반복되면 하나로 합침
    parts = [p.strip() for p in re.split(r"\s*/\s*", text) if p.strip()]
    deduped = []
    for p in parts:
        if p.lower() not in [d.lower() for d in deduped]:
            deduped.append(p)
    return " / ".join(deduped)


def parse_person(raw):
    """칸 하나를 {"name": 닉네임, "twitter": X 아이디 또는 None} 으로 변환. 빈 칸이면 None."""
    text = (raw or "").strip()
    if text in EMPTY_VALUES:
        return None
    if text in OVERRIDES:
        name, handle = OVERRIDES[text]
        return {"name": name, "twitter": handle}

    # 반각 @ 뒤에 오는 아이디 (중간에 공백이 끼어 있어도 허용: "@ J_ip721")
    m = re.search(r"@\s*(" + HANDLE_RE + r")(?![A-Za-z0-9_@])", text)
    if m:
        handle = m.group(1)
        name = clean_name(text[:m.start()] + text[m.end():])
        return {"name": name or handle, "twitter": handle}
    return {"name": clean_name(text) or text, "twitter": None}


def same_person(a, b):
    if a["twitter"] and b["twitter"]:
        return a["twitter"].lower() == b["twitter"].lower()
    return a["name"].lower() == b["name"].lower()


def build():
    booths = []
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # 헤더
        for row in reader:
            row = (row + [""] * 9)[:9]
            booth_id = row[0].strip()
            if not re.fullmatch(r"[A-Z]\d+", booth_id):
                continue
            leader = parse_person(row[3])
            members = []
            if leader:
                members.append(dict(leader, role="leader"))
            for cell in (row[4], row[5]):
                person = parse_person(cell)
                # 리더/다른 부스원과 같은 사람이 부스원 칸에 또 적힌 경우(예: "엘븐/@elvendays" + "엘븐")는 한 번만 표시
                if person and not any(same_person(person, m) for m in members):
                    members.append(dict(person, role="member"))
            handle = next((m["twitter"] for m in members if m["twitter"]), None)
            booths.append({
                "id": booth_id,
                "block": booth_id[0],
                "num": booth_id[1:],
                "adult": "adult" in row[1].lower(),
                "name": row[2].strip(),
                "rep": members[0]["name"] if members else "",
                "twitter": handle,
                "members": members,
                "cut": row[8].strip().upper() == "O",
            })
    return merge_double_booths(booths)


def merge_double_booths(booths):
    """같은 열에서 번호가 이어지고 부스 이름이 같은 부스(예: A37, A38)를 한 부스 "A37-38" 로 합침.
    부스원은 두 칸을 합쳐 중복 없이, 성인/부스컷은 둘 중 하나라도 해당하면 표시."""
    merged = []
    for b in booths:
        prev = merged[-1] if merged else None
        if (
            prev
            and prev["block"] == b["block"]
            and int(prev["nums"][-1]) + 1 == int(b["num"])
            and prev["name"].strip().lower() == b["name"].strip().lower()
        ):
            prev["nums"].append(b["num"])
            prev["id"] = f'{prev["block"]}{prev["nums"][0]}-{prev["nums"][-1]}'
            prev["num"] = f'{prev["nums"][0]}-{prev["nums"][-1]}'
            prev["adult"] = prev["adult"] or b["adult"]
            prev["cut"] = prev["cut"] or b["cut"]
            for m in b["members"]:
                if not any(same_person(m, x) for x in prev["members"]):
                    prev["members"].append(dict(m, role="member"))
            continue
        merged.append(dict(b, nums=[b["num"]]))
    return merged


def main():
    booths = build()
    if "--check" in sys.argv:
        for b in booths:
            people = ", ".join(f'{m["name"]}' + (f' (@{m["twitter"]})' if m["twitter"] else "") for m in b["members"])
            print(f'{b["id"]} {"[성인]" if b["adult"] else "[일반]"} {b["name"]} | {people} | 부스컷:{"O" if b["cut"] else "-"}')
        print(f"총 {len(booths)}개 부스")
        return

    data_line = "const BOOTHS = " + json.dumps(booths, ensure_ascii=False) + ";"
    html = HTML_PATH.read_text(encoding="utf-8")
    new_html, count = re.subn(r"^const BOOTHS = .*;$", lambda _: data_line, html, count=1, flags=re.M)
    if count != 1:
        sys.exit("index.html 에서 'const BOOTHS = ...;' 줄을 찾지 못했습니다.")
    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"index.html 갱신 완료: {len(booths)}개 부스")

    # avatars/ 에 프로필 사진(아이디.jpg)이 아직 없는 X 아이디 목록 (프로필 사진 수집용)
    existing = {p.stem for p in AVATAR_DIR.glob("*.jpg")}
    handles = sorted({m["twitter"] for b in booths for m in b["members"] if m["twitter"]}, key=str.lower)
    missing = [h for h in handles if h not in existing]
    MISSING_AVATARS_PATH.write_text("\n".join(missing) + "\n", encoding="utf-8")
    print(f"프로필 사진 없는 X 아이디 {len(missing)}개 → {MISSING_AVATARS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
