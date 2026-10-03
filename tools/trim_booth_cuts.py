"""
부스컷 가장자리의 흰 여백을 잘라내는 스크립트.

공식 안내 이미지에서 부스컷을 잘라낼 때 검은 테두리 바깥의 흰 배경이
1~7px 정도 함께 들어가, 사이트의 둥근 모서리 안에서 하얗게 보였다.
각 변마다 바깥쪽부터 검은 테두리 줄이 나올 때까지의 줄을 잘라낸다.
테두리가 없는 변은 바깥 한 줄이 안쪽보다 눈에 띄게 밝을 때만 그 줄을 잘라낸다.

사용법: python3 tools/trim_booth_cuts.py [booth-cuts 폴더]  (기본: booth-cuts)
이미 잘라낸 이미지에 다시 돌려도 더 잘리지 않는다.
"""
import glob
import os
import sys

import numpy as np
from PIL import Image, JpegImagePlugin

MAX_MARGIN = 14      # 이보다 두꺼운 여백은 그림의 일부로 보고 건드리지 않는다
DARK = 90            # 이 밝기보다 어두운 픽셀을 테두리로 본다
DARK_RATIO = 0.6     # 한 줄의 이 비율 이상이 어두우면 테두리 줄
LIGHT_EDGE = 25      # 테두리 없는 변: 바깥 줄이 안쪽 줄보다 이만큼 밝으면 자른다


def edge_trim(lines):
    """lines[0]이 가장 바깥 줄. 잘라낼 줄 수를 돌려준다."""
    for i in range(MAX_MARGIN):
        if (lines[i] < DARK).mean() > DARK_RATIO:
            return i
    if lines[0].mean() - lines[1].mean() > LIGHT_EDGE:
        return 1
    return 0


def trim(path):
    im = Image.open(path)
    g = np.asarray(im.convert("L")).astype(float)
    top = edge_trim(g)
    bottom = edge_trim(g[::-1])
    left = edge_trim(g.T)
    right = edge_trim(g.T[::-1])
    if not (top or bottom or left or right):
        return None
    w, h = im.size
    out = im.convert("RGB").crop((left, top, w - right, h - bottom))
    out.save(
        path,
        "JPEG",
        qtables=im.quantization,
        subsampling=JpegImagePlugin.get_sampling(im),
    )
    return top, bottom, left, right


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "booth-cuts"
    for path in sorted(glob.glob(os.path.join(folder, "*.jpg"))):
        r = trim(path)
        if r:
            print(f"{os.path.basename(path)}: 위{r[0]} 아래{r[1]} 왼{r[2]} 오른{r[3]}px 잘라냄")


if __name__ == "__main__":
    main()
