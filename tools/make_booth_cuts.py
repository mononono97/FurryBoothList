"""고화질 부스컷 원본(파일 이름이 "[A01]부스이름.png" 형식)에 부스 번호를 넣고 webp 로 저장한다.

- 부스 번호는 공식 부스컷 안내 이미지처럼 왼쪽 위 흰 칸(검은 테두리)에 "A-1" 형식으로 넣는다.
  원본 템플릿의 흰 칸이 비어 있으면 그 칸에 글자만 쓰고, 칸이 그림으로 채워져 있거나 없으면
  같은 자리에 흰 칸 + 검은 테두리를 새로 그린다.
- 두 칸 부스: 두 칸 그림이 같으면(A11-12, A37-38) 앞 칸 하나만, 다르면(B31-32, C19-20) 가로로 이어 붙인다.
- 출력: booth-cuts/부스번호.webp (예: booth-cuts/A01.webp, booth-cuts/A37-38.webp)
- 원본 폴더에 없는 부스의 기존 이미지는 건드리지 않는다.

사용법: python3 tools/make_booth_cuts.py <원본 폴더> --font <굵은 글꼴 파일> [--out booth-cuts] [--quality 80]
글꼴은 Pretendard ExtraBold 를 썼다(npm 의 pretendard 패키지, dist/public/static/Pretendard-ExtraBold.otf).
필요: pip install pillow
"""
import argparse
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

# 템플릿(가로 827px) 기준 번호 칸: 바깥 검은 테두리 0~9px, 흰 칸 10~239px, 오른쪽·아래 검은 선 240~249px
TEMPLATE_W = 827
BOX_INNER = (10, 10, 240, 240)
BOX_OUTER = 250
MAX_W = 830  # 이보다 넓은 원본(C21 1660px)은 이 너비로 줄임
MERGED = {"A11": "A11-12", "A12": "A11-12", "A37": "A37-38", "A38": "A37-38",
          "B31": "B31-32", "B32": "B31-32", "C19": "C19-20", "C20": "C19-20"}
SAME_PICTURE = {"A11-12", "A37-38"}


def decode_name(name):
    # 일부 압축 해제 도구가 한글 파일 이름을 "#Uc548" 처럼 풀어 놓는 경우
    return re.sub(r"#U([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), name)


def flatten(im):
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def find_white_box(im):
    """왼쪽 위의 비어 있는 흰 칸(안쪽 좌표)을 찾는다. 없거나 그림이 들어 있으면 None."""
    g = im.convert("L")
    w, h = g.size
    px = g.load()
    start = None
    seen_dark = False
    for i in range(min(w, h) // 3):
        v = px[i, i]
        if v < 100:
            seen_dark = True
        elif seen_dark and v > 200:
            start = (i, i)
            break
    if not start:
        return None
    x, y = start

    def walk(dx, dy):
        cx, cy = x, y
        while 0 <= cx + dx < w and 0 <= cy + dy < h and px[cx + dx, cy + dy] > 128:
            cx += dx
            cy += dy
        return cx, cy

    l, t, r, b = walk(-1, 0)[0], walk(0, -1)[1], walk(1, 0)[0] + 1, walk(0, 1)[1] + 1
    bw, bh = r - l, b - t
    if not (0.22 * w <= bw <= 0.32 * w and 0.8 <= bw / max(bh, 1) <= 1.25):
        return None
    # 칸 안이 거의 다 흰색이어야 "빈 칸"
    rgb = im.crop((l + 2, t + 2, r - 2, b - 2)).get_flattened_data()
    white = sum(1 for p in rgb if min(p) > 235) / max(1, len(rgb))
    return (l, t, r, b) if white > 0.98 else None


def label_text(cell):
    return f"{cell[0]}-{int(cell[1:])}"


def draw_label(im, cell, font_path):
    w, _ = im.size
    box = find_white_box(im)
    d = ImageDraw.Draw(im)
    if box is None:
        s = w / TEMPLATE_W
        d.rectangle((0, 0, round(BOX_OUTER * s) - 1, round(BOX_OUTER * s) - 1), fill="black")
        box = tuple(round(v * s) for v in BOX_INNER)
        d.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill="white")
    else:
        d.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill="white")
    l, t, r, b = box
    bw = r - l
    # 글자 크기는 칸 너비 기준으로 모든 부스가 같게("A-41" 같은 네 글자가 칸의 약 72%)
    size = 10
    while True:
        f = ImageFont.truetype(font_path, size + 1)
        if d.textlength("A-41", font=f) > bw * 0.72:
            break
        size += 1
    font = ImageFont.truetype(font_path, size)
    d.text(((l + r) / 2, (t + b) / 2), label_text(cell), font=font, fill="black", anchor="mm")
    return im


def load(path):
    im = flatten(Image.open(path))
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--font", required=True)
    ap.add_argument("--out", default="booth-cuts")
    ap.add_argument("--quality", type=int, default=80)
    args = ap.parse_args()

    cells = {}
    for name in os.listdir(args.src):
        m = re.match(r"\s*\[([ABC])(\d{1,2})\]", decode_name(name))
        if m:
            cells[f"{m.group(1)}{int(m.group(2)):02d}"] = os.path.join(args.src, name)

    outputs = {}
    for cell in sorted(cells):
        booth = MERGED.get(cell, cell)
        outputs.setdefault(booth, []).append(cell)

    total = 0
    for booth, parts in sorted(outputs.items()):
        if booth in SAME_PICTURE:
            parts = parts[:1]
        ims = [draw_label(load(cells[c]), c, args.font) for c in parts]
        if len(ims) > 1:
            h = min(i.height for i in ims)
            ims = [i if i.height == h else i.resize((round(i.width * h / i.height), h), Image.LANCZOS) for i in ims]
            joined = Image.new("RGB", (sum(i.width for i in ims), h), "white")
            x = 0
            for i in ims:
                joined.paste(i, (x, 0))
                x += i.width
            im = joined
        else:
            im = ims[0]
        out = os.path.join(args.out, f"{booth}.webp")
        im.save(out, "WEBP", quality=args.quality, method=6)
        total += os.path.getsize(out)
        print(f"{booth}: {im.size[0]}x{im.size[1]} {os.path.getsize(out) // 1024}KB")
    print(f"{len(outputs)}개, 합계 {total / 1024 / 1024:.1f}MB")


if __name__ == "__main__":
    sys.exit(main())
