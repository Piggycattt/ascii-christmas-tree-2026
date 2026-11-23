#!/usr/bin/env python3
"""纯 Python 3D ASCII 圣诞树渲染器。

技术栈：NumPy 负责 3D 点云、逐层旋转、深度排序与颜色；
Pillow 负责字符图集、发光和文字；FFmpeg 负责编码 MP4。

常用命令：
    python python_ascii_tree.py --preview
    python python_ascii_tree.py --render

本文件顶部的 CONFIG 是最适合新手修改的区域。
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ============================================================
# 新手修改区：改数字或引号里的文字即可
# ============================================================
CONFIG = {
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "duration": 20,
    "seed": 20261225,
    "tree_layers": 10,          # 树冠水平枝层数量
    "tree_height": 1190,        # 树冠高度
    "tree_radius": 455,         # 最下层半径
    "tree_base_y": 1495,        # 树冠底部位置
    "rotation_speed": 0.43,     # 基础转速（弧度/秒）
    "spiral_turns": 5.6,        # 金色灯带圈数
    "greeting": "MERRY CHRISTMAS 2026",
    "dedication": "TO THE ONE I WANT TO SPEND CHRISTMAS WITH",
}

WIDTH = int(CONFIG["width"])
HEIGHT = int(CONFIG["height"])
FPS = int(CONFIG["fps"])
DURATION = int(CONFIG["duration"])
TOTAL_FRAMES = FPS * DURATION
SEED = int(CONFIG["seed"])

# 参考视频可见的字符网格参数：120 列 × 128 行。
CELL_W = 9
CELL_H = 15
COLS = WIDTH // CELL_W
ROWS = HEIGHT // CELL_H
EMPTY = 0
TAU = math.tau

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_DIR / "ASCII-Christmas-Tree-20s-1080x1920.mp4"
FRAMES_DIR = PROJECT_DIR / ".python_render_frames"
PREVIEW_DIR = PROJECT_DIR / "previews"

# 纯 ASCII 字符；空格必须排第一位。
CHARSET_RAW = " .,:;`'-~+=*xX<>[]{}()/\\|_!MW#%@&Oo0Qq$123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHARSET = "".join(dict.fromkeys(CHARSET_RAW))
CHAR_TO_INDEX = {ch: i for i, ch in enumerate(CHARSET)}

TREE_BACK = np.array([CHAR_TO_INDEX[c] for c in ".,`:;'" if c in CHAR_TO_INDEX], dtype=np.int16)
TREE_MID = np.array([CHAR_TO_INDEX[c] for c in "+xX<>[]{}MW" if c in CHAR_TO_INDEX], dtype=np.int16)
TREE_FRONT = np.array([CHAR_TO_INDEX[c] for c in "MW#%@&QX" if c in CHAR_TO_INDEX], dtype=np.int16)
TREE_EDGE = np.array([CHAR_TO_INDEX[c] for c in "<>/\\[]{}WM#" if c in CHAR_TO_INDEX], dtype=np.int16)
GOLD_CHARS = np.array([CHAR_TO_INDEX[c] for c in ".-~+=*xX#@" if c in CHAR_TO_INDEX], dtype=np.int16)
RED_CHARS = np.array([CHAR_TO_INDEX[c] for c in "oO0Q@*" if c in CHAR_TO_INDEX], dtype=np.int16)
SNOW_CHARS = np.array([CHAR_TO_INDEX[c] for c in ".+*o" if c in CHAR_TO_INDEX], dtype=np.int16)
TRUNK_CHARS = np.array([CHAR_TO_INDEX[c] for c in "|/\\[]{}MW#" if c in CHAR_TO_INDEX], dtype=np.int16)
GIFT_CHARS = np.array([CHAR_TO_INDEX[c] for c in "[]{}#MW@+=XO" if c in CHAR_TO_INDEX], dtype=np.int16)

TREE_PALETTE = np.array(
    [
        (16, 105, 47),
        (20, 145, 62),
        (20, 188, 82),
        (26, 226, 108),
        (23, 194, 145),
        (16, 142, 119),
        (14, 112, 88),
    ],
    dtype=np.float32,
)


@dataclass
class PointBatch:
    sx: np.ndarray
    sy: np.ndarray
    depth: np.ndarray
    glyph: np.ndarray
    color: np.ndarray
    priority: np.ndarray


@dataclass
class TreeCloud:
    h: np.ndarray
    radius: np.ndarray
    theta0: np.ndarray
    y: np.ndarray
    layer: np.ndarray
    kind: np.ndarray
    seed: np.ndarray
    stripe: np.ndarray


@dataclass
class SimpleCloud:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    seed: np.ndarray
    kind: np.ndarray
    color_id: np.ndarray


def clamp_u8(value: np.ndarray) -> np.ndarray:
    return np.clip(value, 0, 255).astype(np.uint8)


def smoothstep(x: float | np.ndarray) -> float | np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def find_font(bold: bool = False) -> Path:
    names = (
        ["consolab.ttf", "CascadiaMono-Bold.ttf", "courbd.ttf"]
        if bold
        else ["consola.ttf", "CascadiaMono.ttf", "cour.ttf"]
    )
    roots = [Path(r"C:\Windows\Fonts"), Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    for root in roots:
        for name in names:
            path = root / name
            if path.exists():
                return path
    raise FileNotFoundError("未找到 Consolas/Cascadia Mono/Courier New 字体。")


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FileNotFoundError("未找到 FFmpeg。")
    return path


def ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FileNotFoundError("未找到 FFprobe。")
    return path


def radius_profile(h: np.ndarray) -> np.ndarray:
    """参考视频同类的 10 层锯齿锥形半径。

    每层由宽变窄，到下一层突然展开，转动时会形成明显水平枝层。
    """
    layers = int(CONFIG["tree_layers"])
    local = np.mod(h * layers, 1.0)
    # 每个枝层的下沿明显展开、上沿收窄；比普通圆锥更像参考视频的阶梯树冠。
    tier = 0.46 + 0.54 * (1.0 - local) ** 0.48
    taper = np.power(np.clip(1.0 - h, 0.0, 1.0), 0.82)
    crown = 0.96 + 0.04 * np.sin(h * math.pi)
    return float(CONFIG["tree_radius"]) * taper * tier * crown


def branch_lobes(theta: np.ndarray, layer: np.ndarray, strength: float) -> np.ndarray:
    """给每层加入不同方向的枝梢凸起。

    如果各层是完全圆形，旋转后外轮廓不会变化，看起来仍像平面转盘。
    这里叠加 3/5/2 个不对称叶瓣，让每层转动时轮廓和遮挡都真实变化。
    """
    phase = layer.astype(np.float32) * 1.371
    wave = (
        0.58 * np.cos(theta * 3.0 + phase)
        + 0.28 * np.cos(theta * 5.0 - phase * 0.73)
        + 0.14 * np.cos(theta * 2.0 + phase * 1.43)
    )
    return 1.0 + strength * wave


def build_tree_cloud(rng: np.random.Generator) -> TreeCloud:
    layers = int(CONFIG["tree_layers"])

    # 外壳：主要负责完整树形和旋转色块。
    n_shell = 34_000
    raw_shell = rng.random(n_shell).astype(np.float32)
    layer_shell = np.minimum((raw_shell * layers).astype(np.int16), layers - 1)
    local_shell = raw_shell * layers - layer_shell
    layer_gap = 0.060
    h_shell = ((layer_shell + layer_gap + (1.0 - layer_gap * 2.0) * local_shell) / layers).astype(np.float32)
    theta_shell = rng.random(n_shell).astype(np.float32) * TAU
    r_shell = (
        radius_profile(h_shell)
        * (0.80 + 0.20 * np.power(rng.random(n_shell), 0.32))
        * branch_lobes(theta_shell, layer_shell, 0.125)
    )
    y_shell = (
        float(CONFIG["tree_base_y"])
        - h_shell * float(CONFIG["tree_height"])
        + rng.normal(0.0, 5.0, n_shell)
    ).astype(np.float32)

    # 内部枝叶：背面和缝隙中仍有暗字符，增加空间厚度。
    n_inner = 7_500
    raw_inner = rng.random(n_inner).astype(np.float32)
    layer_inner = np.minimum((raw_inner * layers).astype(np.int16), layers - 1)
    local_inner = raw_inner * layers - layer_inner
    h_inner = ((layer_inner + layer_gap + (1.0 - layer_gap * 2.0) * local_inner) / layers).astype(np.float32)
    theta_inner = rng.random(n_inner).astype(np.float32) * TAU
    r_inner = (
        radius_profile(h_inner)
        * (0.12 + 0.72 * np.sqrt(rng.random(n_inner)))
        * branch_lobes(theta_inner, layer_inner, 0.070)
    )
    y_inner = (
        float(CONFIG["tree_base_y"])
        - h_inner * float(CONFIG["tree_height"])
        + rng.normal(0.0, 8.0, n_inner)
    ).astype(np.float32)

    # 每层下沿的宽枝环：转动时形成一层一层的锯齿轮廓。
    edge_h_parts: list[np.ndarray] = []
    edge_r_parts: list[np.ndarray] = []
    edge_t_parts: list[np.ndarray] = []
    edge_l_parts: list[np.ndarray] = []
    edge_y_parts: list[np.ndarray] = []
    for layer in range(layers):
        n_edge = 980 if layer < layers - 2 else 720
        base_h = (layer + layer_gap + 0.012) / layers
        h = np.clip(base_h + rng.normal(0.0, 0.010, n_edge), 0.0, 0.995).astype(np.float32)
        theta = rng.random(n_edge).astype(np.float32) * TAU
        layer_arr = np.full(n_edge, layer, dtype=np.int16)
        radius = (
            radius_profile(h)
            * (1.035 + 0.105 * rng.random(n_edge))
            * branch_lobes(theta, layer_arr, 0.185)
        )
        # 枝梢略向下垂，形成更清楚的水平层边缘。
        y = (
            float(CONFIG["tree_base_y"])
            - h * float(CONFIG["tree_height"])
            + 3.0
            + 19.0 * rng.random(n_edge)
        ).astype(np.float32)
        edge_h_parts.append(h)
        edge_r_parts.append(radius.astype(np.float32))
        edge_t_parts.append(theta)
        edge_l_parts.append(layer_arr)
        edge_y_parts.append(y)

    h_edge = np.concatenate(edge_h_parts)
    r_edge = np.concatenate(edge_r_parts)
    theta_edge = np.concatenate(edge_t_parts)
    layer_edge = np.concatenate(edge_l_parts)
    y_edge = np.concatenate(edge_y_parts)

    h = np.concatenate([h_shell, h_inner, h_edge]).astype(np.float32)
    radius = np.concatenate([r_shell, r_inner, r_edge]).astype(np.float32)
    theta0 = np.concatenate([theta_shell, theta_inner, theta_edge]).astype(np.float32)
    y = np.concatenate([y_shell, y_inner, y_edge]).astype(np.float32)
    layer = np.concatenate([layer_shell, layer_inner, layer_edge]).astype(np.int16)
    kind = np.concatenate(
        [
            np.zeros(n_shell, dtype=np.int8),
            np.full(n_inner, 2, dtype=np.int8),
            np.ones(len(h_edge), dtype=np.int8),
        ]
    )
    seed = rng.integers(0, 32767, len(h), dtype=np.int32)
    stripe = (
        np.floor((theta0 / TAU) * len(TREE_PALETTE) + layer * 0.61 + h * 2.2).astype(np.int16)
        % len(TREE_PALETTE)
    )
    return TreeCloud(h, radius, theta0, y, layer, kind, seed, stripe)


def build_spiral(rng: np.random.Generator) -> tuple[TreeCloud, TreeCloud]:
    turns = float(CONFIG["spiral_turns"])
    layers = int(CONFIG["tree_layers"])

    # 三股紧邻字符组成发光灯带厚度。
    n_line = 940
    u_base = np.linspace(0.0, 1.0, n_line, endpoint=True, dtype=np.float32)
    us: list[np.ndarray] = []
    rs: list[np.ndarray] = []
    ts: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    for offset in (-0.018, 0.0, 0.018):
        u = u_base.copy()
        h = 0.055 + 0.875 * u
        radius = radius_profile(h) * (1.035 + offset)
        theta = TAU * (turns * u + 0.035 * np.sin(u * TAU * 3.0)) + offset * 18.0
        y = float(CONFIG["tree_base_y"]) - h * float(CONFIG["tree_height"])
        us.append(h.astype(np.float32))
        rs.append(radius.astype(np.float32))
        ts.append(theta.astype(np.float32))
        ys.append(y.astype(np.float32))
    h_line = np.concatenate(us)
    r_line = np.concatenate(rs)
    t_line = np.concatenate(ts)
    y_line = np.concatenate(ys)
    layer_line = np.minimum((h_line * layers).astype(np.int16), layers - 1)
    line = TreeCloud(
        h_line,
        r_line,
        t_line,
        y_line,
        layer_line,
        np.zeros(len(h_line), dtype=np.int8),
        rng.integers(0, 32767, len(h_line), dtype=np.int32),
        np.zeros(len(h_line), dtype=np.int16),
    )

    # 红色灯球：沿螺旋均匀分布，每个灯球由多个 ASCII 字符点构成。
    bulb_h: list[float] = []
    bulb_r: list[float] = []
    bulb_t: list[float] = []
    bulb_y: list[float] = []
    bulb_seed: list[int] = []
    n_bulbs = 34
    for bi in range(n_bulbs):
        u0 = (bi + 0.5) / n_bulbs
        h0 = 0.055 + 0.875 * u0
        r0 = float(radius_profile(np.array([h0], dtype=np.float32))[0]) * 1.065
        t0 = TAU * (turns * u0 + 0.035 * math.sin(u0 * TAU * 3.0))
        for j in range(13):
            a = TAU * j / 13.0
            bulb_h.append(h0 + 0.004 * math.sin(a))
            bulb_r.append(r0 + 11.0 * math.cos(a))
            bulb_t.append(t0 + 0.026 * math.sin(a))
            bulb_y.append(float(CONFIG["tree_base_y"]) - h0 * float(CONFIG["tree_height"]) + 10.0 * math.sin(a))
            bulb_seed.append(bi * 29 + j)
    h_bulb = np.asarray(bulb_h, dtype=np.float32)
    bulb = TreeCloud(
        h_bulb,
        np.asarray(bulb_r, dtype=np.float32),
        np.asarray(bulb_t, dtype=np.float32),
        np.asarray(bulb_y, dtype=np.float32),
        np.minimum((h_bulb * layers).astype(np.int16), layers - 1),
        np.ones(len(h_bulb), dtype=np.int8),
        np.asarray(bulb_seed, dtype=np.int32),
        np.zeros(len(h_bulb), dtype=np.int16),
    )
    return line, bulb


def build_star(rng: np.random.Generator) -> SimpleCloud:
    size = 310
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    cx = cy = size / 2
    outer = 145.0
    inner = 60.0
    points: list[tuple[float, float]] = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        r = outer if i % 2 == 0 else inner
        points.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    draw.polygon(points, fill=255)
    yy, xx = np.nonzero(np.asarray(mask) > 0)
    chosen = rng.choice(len(xx), size=3100, replace=False)
    local_x = (xx[chosen].astype(np.float32) - cx) * 0.78
    local_y = (yy[chosen].astype(np.float32) - cy) * 0.78
    # 前后两面 + 少量内部厚度，转动时可以看到星星侧面。
    face = rng.choice(np.array([-1.0, 1.0], dtype=np.float32), size=len(chosen))
    local_z = face * (30.0 + rng.random(len(chosen)).astype(np.float32) * 8.0)
    x = local_x
    y = 218.0 + local_y
    z = local_z
    seed = rng.integers(0, 32767, len(x), dtype=np.int32)
    kind = (face > 0).astype(np.int8)
    color_id = rng.integers(0, 3, len(x), dtype=np.int8)
    return SimpleCloud(x, y.astype(np.float32), z, seed, kind, color_id)


def build_trunk_and_roots(rng: np.random.Generator) -> SimpleCloud:
    n_trunk = 4200
    u = rng.random(n_trunk).astype(np.float32)
    theta = rng.random(n_trunk).astype(np.float32) * TAU
    radius = (58.0 - 14.0 * u) * (0.55 + 0.45 * rng.random(n_trunk))
    x = radius * np.cos(theta)
    z = radius * np.sin(theta)
    y = float(CONFIG["tree_base_y"]) + 8.0 + u * 190.0

    root_x: list[np.ndarray] = []
    root_y: list[np.ndarray] = []
    root_z: list[np.ndarray] = []
    for ri in range(13):
        n = 230
        s = np.linspace(0.0, 1.0, n, dtype=np.float32)
        a = TAU * ri / 13.0 + rng.normal(0.0, 0.07)
        spread = 48.0 + 165.0 * s
        root_x.append(spread * math.cos(a) + rng.normal(0.0, 5.0, n))
        root_z.append(spread * math.sin(a) + rng.normal(0.0, 5.0, n))
        root_y.append(float(CONFIG["tree_base_y"]) + 170.0 + 52.0 * s + 8.0 * np.sin(s * math.pi))

    rx = np.concatenate(root_x).astype(np.float32)
    ry = np.concatenate(root_y).astype(np.float32)
    rz = np.concatenate(root_z).astype(np.float32)
    x_all = np.concatenate([x.astype(np.float32), rx])
    y_all = np.concatenate([y.astype(np.float32), ry])
    z_all = np.concatenate([z.astype(np.float32), rz])
    kind = np.concatenate([np.zeros(n_trunk, dtype=np.int8), np.ones(len(rx), dtype=np.int8)])
    return SimpleCloud(
        x_all,
        y_all,
        z_all,
        rng.integers(0, 32767, len(x_all), dtype=np.int32),
        kind,
        np.zeros(len(x_all), dtype=np.int8),
    )


def sample_box(
    rng: np.random.Generator,
    cx: float,
    cy: float,
    cz: float,
    width: float,
    height: float,
    depth: float,
    color_id: int,
) -> SimpleCloud:
    spacing = 10.5
    pts: list[tuple[float, float, float, int]] = []

    xs = np.arange(-width / 2, width / 2 + 0.1, spacing)
    ys = np.arange(-height / 2, height / 2 + 0.1, spacing)
    zs = np.arange(-depth / 2, depth / 2 + 0.1, spacing)

    for zface in (-depth / 2, depth / 2):
        for y in ys:
            for x in xs:
                pts.append((x, y, zface, 0))
    for xface in (-width / 2, width / 2):
        for y in ys:
            for z in zs:
                pts.append((xface, y, z, 1))
    for yface in (-height / 2, height / 2):
        for z in zs:
            for x in xs:
                pts.append((x, yface, z, 2))

    arr = np.asarray(pts, dtype=np.float32)
    x = arr[:, 0] + cx
    y = arr[:, 1] + cy
    z = arr[:, 2] + cz
    kind = arr[:, 3].astype(np.int8)

    # 金色十字缎带，额外点略浮在礼盒表面。
    ribbon_pts: list[tuple[float, float, float, int]] = []
    for yv in ys:
        for xv in np.arange(-17.0, 17.1, 8.5):
            ribbon_pts.append((xv + cx, yv + cy, depth / 2 + cz + 4.0, 3))
    for xv in xs:
        for yv in np.arange(-17.0, 17.1, 8.5):
            ribbon_pts.append((xv + cx, yv + cy, depth / 2 + cz + 4.0, 3))
    ribbon = np.asarray(ribbon_pts, dtype=np.float32)
    x = np.concatenate([x, ribbon[:, 0]])
    y = np.concatenate([y, ribbon[:, 1]])
    z = np.concatenate([z, ribbon[:, 2]])
    kind = np.concatenate([kind, ribbon[:, 3].astype(np.int8)])
    return SimpleCloud(
        x.astype(np.float32),
        y.astype(np.float32),
        z.astype(np.float32),
        rng.integers(0, 32767, len(x), dtype=np.int32),
        kind,
        np.full(len(x), color_id, dtype=np.int8),
    )


def build_gifts(rng: np.random.Generator) -> list[SimpleCloud]:
    return [
        sample_box(rng, -315.0, 1588.0, 150.0, 235.0, 185.0, 175.0, 0),
        sample_box(rng, 315.0, 1582.0, 190.0, 255.0, 210.0, 195.0, 1),
        sample_box(rng, -140.0, 1652.0, 245.0, 150.0, 125.0, 135.0, 2),
    ]


class AsciiTreeRenderer:
    def __init__(self) -> None:
        self.rng = np.random.default_rng(SEED)
        self.font_path = find_font(False)
        self.bold_font_path = find_font(True)
        self.atlas = self._make_atlas()
        self.tree = build_tree_cloud(self.rng)
        self.spiral, self.bulbs = build_spiral(self.rng)
        self.star = build_star(self.rng)
        self.trunk = build_trunk_and_roots(self.rng)
        self.gifts = build_gifts(self.rng)
        self._build_snow()
        self.layer_phase = np.array([0.00, 0.16, -0.11, 0.18, -0.13, 0.14, -0.17, 0.15, -0.09, 0.12], dtype=np.float32)
        self.layer_speed = np.array([0.860, 1.120, 0.910, 1.105, 0.880, 1.130, 0.925, 1.100, 0.895, 1.120], dtype=np.float32)
        self.background = self._make_background()
        self.vignette = self._make_vignette()
        self.font_title = ImageFont.truetype(str(self.bold_font_path), 31)
        self.font_subtitle = ImageFont.truetype(str(self.font_path), 17)
        self.font_small = ImageFont.truetype(str(self.font_path), 15)

    def _make_atlas(self) -> np.ndarray:
        font = ImageFont.truetype(str(self.font_path), 13)
        atlas = np.zeros((len(CHARSET), CELL_H, CELL_W), dtype=np.uint8)
        for idx, ch in enumerate(CHARSET):
            if ch == " ":
                continue
            img = Image.new("L", (CELL_W, CELL_H), 0)
            draw = ImageDraw.Draw(img)
            bbox = draw.textbbox((0, 0), ch, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x = (CELL_W - tw) // 2 - bbox[0]
            y = (CELL_H - th) // 2 - bbox[1] - 1
            draw.text((x, y), ch, fill=255, font=font)
            atlas[idx] = np.asarray(img, dtype=np.uint8)
        return atlas

    def _make_background(self) -> np.ndarray:
        yy = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None]
        xx = np.linspace(-1.0, 1.0, WIDTH, dtype=np.float32)[None, :]
        radial = np.clip(1.0 - np.sqrt(xx * xx + ((yy - 0.48) * 1.45) ** 2), 0.0, 1.0)
        bg = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
        bg[:, :, 0] = 1.8 + 3.0 * radial
        bg[:, :, 1] = 5.0 + 9.0 * radial + yy * 2.0
        bg[:, :, 2] = 10.0 + 17.0 * radial + yy * 3.0
        return clamp_u8(bg)

    def _make_vignette(self) -> np.ndarray:
        y = np.linspace(-1.0, 1.0, HEIGHT, dtype=np.float32)[:, None]
        x = np.linspace(-1.0, 1.0, WIDTH, dtype=np.float32)[None, :]
        d = np.sqrt((x * 0.92) ** 2 + (y * 0.78) ** 2)
        return np.clip(1.06 - 0.38 * np.power(d, 1.55), 0.52, 1.0).astype(np.float32)

    def _build_snow(self) -> None:
        n = 730
        self.snow_x = self.rng.random(n).astype(np.float32) * WIDTH
        self.snow_y = self.rng.random(n).astype(np.float32) * HEIGHT
        self.snow_speed = (24.0 + 82.0 * self.rng.random(n)).astype(np.float32)
        self.snow_amp = (3.0 + 18.0 * self.rng.random(n)).astype(np.float32)
        self.snow_phase = (self.rng.random(n) * TAU).astype(np.float32)
        self.snow_depth = self.rng.random(n).astype(np.float32)
        self.snow_seed = self.rng.integers(0, 32767, n, dtype=np.int32)

    def _project_tree(self, cloud: TreeCloud, t: float, asset: str) -> PointBatch:
        base = float(CONFIG["rotation_speed"]) * t
        if asset == "tree":
            layer_angle = (
                base * self.layer_speed[cloud.layer]
                + self.layer_phase[cloud.layer]
                + 0.065 * np.sin(0.64 * t + cloud.layer * 1.17)
            )
        else:
            # 灯带整体随树旋转，但保留轻微相位差，形成穿前绕后的动感。
            layer_angle = base * 1.01 + 0.025 * np.sin(0.75 * t + cloud.h * 8.0)

        angle = cloud.theta0 + layer_angle
        ca = np.cos(angle)
        sa = np.sin(angle)
        xr = cloud.radius * ca
        zr = cloud.radius * sa
        perspective = 1.0 + 0.080 * np.clip(zr / 520.0, -1.0, 1.0)
        sx = WIDTH * 0.5 + xr * perspective
        sy = cloud.y - 0.030 * zr
        front = np.clip(0.5 + 0.5 * zr / np.maximum(cloud.radius, 25.0), 0.0, 1.0)

        if asset == "tree":
            base_color = TREE_PALETTE[cloud.stripe]
            shade = 0.25 + 0.82 * np.power(front, 1.32)
            shade *= 0.96 + 0.07 * np.sin(t * 1.6 + cloud.layer * 0.8 + cloud.seed * 0.011)
            shade = np.where(cloud.kind == 2, shade * 0.58, shade)
            shade = np.where(cloud.kind == 1, shade * 1.16, shade)
            color = base_color * shade[:, None]

            glyph = np.empty(len(cloud.h), dtype=np.int16)
            back_mask = front < 0.37
            front_mask = front > 0.69
            mid_mask = ~(back_mask | front_mask)
            glyph[back_mask] = TREE_BACK[cloud.seed[back_mask] % len(TREE_BACK)]
            glyph[mid_mask] = TREE_MID[cloud.seed[mid_mask] % len(TREE_MID)]
            glyph[front_mask] = TREE_FRONT[cloud.seed[front_mask] % len(TREE_FRONT)]
            edge_mask = cloud.kind == 1
            glyph[edge_mask] = TREE_EDGE[cloud.seed[edge_mask] % len(TREE_EDGE)]

            # 从树顶向树底生长出现，前 2.2 秒完成。
            reveal = float(smoothstep(t / 2.2))
            visible = cloud.h >= (1.0 - reveal - 0.035)
            color = np.where(visible[:, None], color, 0.0)
            priority = zr + np.where(cloud.kind == 1, 9.0, 0.0)
        elif asset == "spiral":
            wave = 0.72 + 0.28 * np.sin(t * 5.4 - cloud.h * 53.0 + cloud.seed * 0.017)
            shade = (0.17 + 1.12 * np.power(front, 1.55)) * wave
            color = np.stack([255.0 * shade, 190.0 * shade, 48.0 * shade], axis=1)
            char_pos = np.clip((front * (len(GOLD_CHARS) - 1)).astype(np.int16), 0, len(GOLD_CHARS) - 1)
            glyph = GOLD_CHARS[(char_pos + cloud.seed % 3) % len(GOLD_CHARS)]
            priority = zr + 18.0
        else:
            twinkle = 0.78 + 0.30 * np.sin(t * 6.2 + cloud.seed * 0.73)
            shade = (0.30 + 0.93 * np.power(front, 1.25)) * twinkle
            color = np.stack([255.0 * shade, 43.0 * shade, 35.0 * shade], axis=1)
            glyph = RED_CHARS[cloud.seed % len(RED_CHARS)]
            priority = zr + 27.0

        return PointBatch(
            sx.astype(np.float32),
            sy.astype(np.float32),
            zr.astype(np.float32),
            glyph.astype(np.int16),
            clamp_u8(color),
            priority.astype(np.float32),
        )

    def _project_star(self, t: float) -> PointBatch:
        # 星星只在正面附近摆动，既能看见侧面厚度，也不会转到完全侧面对观众消失。
        angle = 0.34 * math.sin(float(CONFIG["rotation_speed"]) * t * 0.82)
        ca, sa = math.cos(angle), math.sin(angle)
        xr = self.star.x * ca - self.star.z * sa
        zr = self.star.x * sa + self.star.z * ca
        sx = WIDTH * 0.5 + xr * (1.0 + 0.10 * zr / 170.0)
        sy = self.star.y - 0.035 * zr
        front = np.clip(0.5 + zr / 210.0, 0.0, 1.0)
        flash = 0.82 + 0.34 * max(0.0, math.sin(t * 2.7)) ** 8
        shade = (0.36 + 0.88 * front) * flash
        colors = np.stack([255.0 * shade, 193.0 * shade, 55.0 * shade], axis=1)
        glyph = GOLD_CHARS[(self.star.seed + (front * 5).astype(np.int16)) % len(GOLD_CHARS)]
        reveal = float(smoothstep((t - 0.35) / 1.2))
        colors *= reveal
        return PointBatch(sx, sy, zr + 50.0, glyph, clamp_u8(colors), zr + 86.0)

    def _project_trunk(self, t: float) -> PointBatch:
        angle = float(CONFIG["rotation_speed"]) * t * 0.90
        ca, sa = math.cos(angle), math.sin(angle)
        xr = self.trunk.x * ca - self.trunk.z * sa
        zr = self.trunk.x * sa + self.trunk.z * ca
        sx = WIDTH * 0.5 + xr
        sy = self.trunk.y - 0.025 * zr
        front = np.clip(0.5 + zr / 210.0, 0.0, 1.0)
        shade = 0.28 + 0.82 * front
        shade *= np.where(self.trunk.kind == 1, 0.82, 1.0)
        colors = np.stack([176.0 * shade, 100.0 * shade, 38.0 * shade], axis=1)
        glyph = TRUNK_CHARS[self.trunk.seed % len(TRUNK_CHARS)]
        return PointBatch(sx, sy, zr - 40.0, glyph, clamp_u8(colors), zr - 55.0)

    def _project_gift(self, gift: SimpleCloud, t: float, index: int) -> PointBatch:
        centers_x = (-315.0, 315.0, -140.0)
        centers_z = (150.0, 190.0, 245.0)
        cx = centers_x[index]
        cz = centers_z[index]
        x0 = gift.x - cx
        z0 = gift.z - cz
        angle = (0.42, -0.48, 0.30)[index] + 0.10 * math.sin(t * 0.72 + index * 1.7)
        ca, sa = math.cos(angle), math.sin(angle)
        xr = x0 * ca - z0 * sa + cx
        zr = x0 * sa + z0 * ca + cz
        sx = WIDTH * 0.5 + xr
        sy = gift.y - 0.022 * zr
        front = np.clip(0.5 + (zr - cz) / 240.0, 0.0, 1.0)

        palettes = np.array(
            [
                [(176, 24, 42), (246, 52, 68)],
                [(23, 77, 166), (38, 142, 236)],
                [(117, 38, 151), (214, 80, 238)],
            ],
            dtype=np.float32,
        )
        base = palettes[index, (front > 0.52).astype(np.int8)]
        shade = 0.42 + 0.72 * front
        colors = base * shade[:, None]
        ribbon = gift.kind == 3
        sparkle = 0.86 + 0.28 * np.sin(t * 4.4 + gift.seed * 0.05)
        colors[ribbon] = np.stack(
            [255.0 * sparkle[ribbon], 188.0 * sparkle[ribbon], 55.0 * sparkle[ribbon]], axis=1
        )
        glyph = GIFT_CHARS[(gift.seed + gift.kind * 3) % len(GIFT_CHARS)]
        glyph[ribbon] = GOLD_CHARS[gift.seed[ribbon] % len(GOLD_CHARS)]
        return PointBatch(sx, sy, zr, glyph, clamp_u8(colors), zr + 120.0)

    def _project_snow(self, t: float) -> PointBatch:
        y = np.mod(self.snow_y + self.snow_speed * t, HEIGHT + 80.0) - 40.0
        x = np.mod(
            self.snow_x + self.snow_amp * np.sin(self.snow_phase + t * (0.55 + self.snow_depth)),
            WIDTH,
        )
        front = self.snow_depth > 0.86
        brightness = 75.0 + 160.0 * self.snow_depth
        color = np.stack([brightness * 0.79, brightness * 0.90, brightness], axis=1)
        glyph = SNOW_CHARS[(self.snow_seed + (self.snow_depth * 3).astype(np.int16)) % len(SNOW_CHARS)]
        depth = np.where(front, 920.0, -920.0).astype(np.float32)
        return PointBatch(x, y, depth, glyph, clamp_u8(color), depth)

    def _rasterize(self, batches: list[PointBatch]) -> tuple[np.ndarray, np.ndarray]:
        sx = np.concatenate([b.sx for b in batches])
        sy = np.concatenate([b.sy for b in batches])
        glyph = np.concatenate([b.glyph for b in batches])
        color = np.concatenate([b.color for b in batches])
        priority = np.concatenate([b.priority for b in batches])

        col = np.floor(sx / CELL_W).astype(np.int32)
        row = np.floor(sy / CELL_H).astype(np.int32)
        valid = (
            (col >= 0)
            & (col < COLS)
            & (row >= 0)
            & (row < ROWS)
            & (np.max(color, axis=1) > 1)
        )
        col = col[valid]
        row = row[valid]
        glyph = glyph[valid]
        color = color[valid]
        priority = priority[valid]

        cell = row * COLS + col
        order = np.lexsort((priority, cell))
        sorted_cell = cell[order]
        keep = np.empty(len(order), dtype=bool)
        if len(order):
            keep[:-1] = sorted_cell[:-1] != sorted_cell[1:]
            keep[-1] = True
        chosen = order[keep]

        grid_chars = np.zeros((ROWS, COLS), dtype=np.int16)
        grid_colors = np.zeros((ROWS, COLS, 3), dtype=np.uint8)
        selected_cells = cell[chosen]
        rr = selected_cells // COLS
        cc = selected_cells % COLS
        grid_chars[rr, cc] = glyph[chosen]
        grid_colors[rr, cc] = color[chosen]
        return grid_chars, grid_colors

    def _compose_ascii(self, chars: np.ndarray, colors: np.ndarray) -> np.ndarray:
        masks = self.atlas[chars]
        blocks = (
            masks[:, :, :, :, None].astype(np.uint16)
            * colors[:, :, None, None, :].astype(np.uint16)
            + 127
        ) // 255
        return blocks.transpose(0, 2, 1, 3, 4).reshape(HEIGHT, WIDTH, 3).astype(np.uint8)

    def _apply_bloom(self, canvas: np.ndarray) -> np.ndarray:
        lum = np.max(canvas, axis=2)
        bright = np.where((lum > 135)[:, :, None], canvas, 0).astype(np.uint8)
        small = Image.fromarray(bright).resize((WIDTH // 4, HEIGHT // 4), Image.Resampling.BILINEAR)
        blurred = small.filter(ImageFilter.GaussianBlur(radius=6.0)).resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
        glow = np.asarray(blurred, dtype=np.float32)
        out = canvas.astype(np.float32) + glow * 0.62
        return clamp_u8(out)

    def _draw_text(self, canvas: np.ndarray, t: float) -> np.ndarray:
        img = Image.fromarray(canvas)
        glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        fg = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fg)

        def centered(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int, int]) -> None:
            box = draw.textbbox((0, 0), text, font=font)
            x = (WIDTH - (box[2] - box[0])) // 2
            draw.text((x, y), text, font=font, fill=fill)

        text_alpha = int(255 * smoothstep((t - 1.25) / 1.5))
        pulse = 0.88 + 0.12 * math.sin(t * 2.0)
        gold = (int(255 * pulse), int(204 * pulse), int(86 * pulse), text_alpha)
        centered(gd, str(CONFIG["greeting"]), 1782, self.font_title, (255, 175, 35, text_alpha))
        centered(fd, str(CONFIG["greeting"]), 1782, self.font_title, gold)
        centered(fd, str(CONFIG["dedication"]), 1835, self.font_subtitle, (180, 218, 223, int(text_alpha * 0.88)))
        centered(fd, "PYTHON // NUMPY // PILLOW // 3D ASCII", 1881, self.font_small, (74, 126, 136, int(text_alpha * 0.72)))
        centered(fd, "MERRY CHRISTMAS // LAYERED ROTATION", 38, self.font_small, (70, 134, 116, int(text_alpha * 0.64)))

        glow = glow.filter(ImageFilter.GaussianBlur(radius=10.0))
        img = Image.alpha_composite(img.convert("RGBA"), glow)
        img = Image.alpha_composite(img, fg)
        return np.asarray(img.convert("RGB"), dtype=np.uint8)

    def render_frame(self, frame_index: int) -> np.ndarray:
        t = frame_index / FPS
        batches = [
            self._project_snow(t),
            self._project_trunk(t),
            self._project_tree(self.tree, t, "tree"),
            self._project_tree(self.spiral, t, "spiral"),
            self._project_tree(self.bulbs, t, "bulbs"),
            self._project_star(t),
        ]
        for index, gift in enumerate(self.gifts):
            batches.append(self._project_gift(gift, t, index))

        chars, colors = self._rasterize(batches)
        ascii_layer = self._compose_ascii(chars, colors)
        canvas = np.maximum(self.background, ascii_layer)
        canvas = self._apply_bloom(canvas)
        canvas = clamp_u8(canvas.astype(np.float32) * self.vignette[:, :, None])

        # 很轻的块状胶片颗粒；每帧固定种子，保证可重复导出。
        noise_rng = np.random.default_rng(SEED + frame_index * 7919)
        noise_small = noise_rng.integers(-4, 5, size=(HEIGHT // 3 + 1, WIDTH // 3 + 1, 1), dtype=np.int16)
        noise = np.repeat(np.repeat(noise_small, 3, axis=0), 3, axis=1)[:HEIGHT, :WIDTH]
        canvas = clamp_u8(canvas.astype(np.int16) + noise)
        canvas = self._draw_text(canvas, t)

        # 开场从黑场平滑进入。
        fade = float(smoothstep(t / 0.65))
        if fade < 1.0:
            canvas = clamp_u8(canvas.astype(np.float32) * fade)
        return canvas


def save_jpeg(frame: np.ndarray, path: Path, quality: int = 95) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(frame).save(path, "JPEG", quality=quality, subsampling=0, optimize=False)


def make_contact_sheet(images: list[Image.Image], labels: list[str], output: Path, cols: int = 4) -> None:
    thumb_w = 270
    thumb_h = 480
    label_h = 34
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), (3, 6, 10))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(find_font(False)), 18)
    for i, image in enumerate(images):
        x = (i % cols) * thumb_w
        y = (i // cols) * (thumb_h + label_h)
        sheet.paste(image.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS), (x, y))
        draw.text((x + 8, y + thumb_h + 6), labels[i], font=font, fill=(190, 220, 220))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "JPEG", quality=94, subsampling=0)


def render_previews() -> None:
    renderer = AsciiTreeRenderer()
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    times = [0.8, 2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 18.5]
    images: list[Image.Image] = []
    labels: list[str] = []
    for t in times:
        fi = min(TOTAL_FRAMES - 1, int(round(t * FPS)))
        frame = renderer.render_frame(fi)
        path = PREVIEW_DIR / f"python-tree-{t:04.1f}s.jpg"
        save_jpeg(frame, path, 96)
        images.append(Image.fromarray(frame))
        labels.append(f"t={t:04.1f}s")
        print(f"预览帧: {path}")
    make_contact_sheet(images, labels, PREVIEW_DIR / "python-tree-contact-sheet.jpg", cols=4)

    # 12 秒附近每 0.25 秒一帧，专门检查枝层相对运动和前后穿插。
    motion_times = [12.0 + 0.25 * i for i in range(8)]
    motion_images = []
    motion_labels = []
    for t in motion_times:
        frame = renderer.render_frame(int(round(t * FPS)))
        motion_images.append(Image.fromarray(frame))
        motion_labels.append(f"t={t:.2f}s")
    make_contact_sheet(motion_images, motion_labels, PREVIEW_DIR / "python-tree-layer-motion.jpg", cols=4)
    print(f"接触表: {PREVIEW_DIR / 'python-tree-contact-sheet.jpg'}")
    print(f"连续帧: {PREVIEW_DIR / 'python-tree-layer-motion.jpg'}")


_WORKER_RENDERER: AsciiTreeRenderer | None = None
_WORKER_DIR: Path | None = None


def worker_init(frames_dir: str) -> None:
    global _WORKER_RENDERER, _WORKER_DIR
    _WORKER_RENDERER = AsciiTreeRenderer()
    _WORKER_DIR = Path(frames_dir)


def worker_render(frame_index: int) -> tuple[int, int]:
    if _WORKER_RENDERER is None or _WORKER_DIR is None:
        raise RuntimeError("渲染进程未初始化")
    path = _WORKER_DIR / f"frame-{frame_index:04d}.jpg"
    frame = _WORKER_RENDERER.render_frame(frame_index)
    save_jpeg(frame, path, 95)
    return frame_index, path.stat().st_size


def render_frames(workers: int, resume: bool) -> None:
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    pending = []
    for frame in range(TOTAL_FRAMES):
        path = FRAMES_DIR / f"frame-{frame:04d}.jpg"
        if resume and path.exists() and path.stat().st_size > 30_000:
            continue
        pending.append(frame)
    if not pending:
        print("全部帧已存在，跳过逐帧渲染。")
        return

    print(f"开始纯 Python 渲染：{len(pending)} 帧，{workers} 个进程")
    started = time.perf_counter()
    completed = 0
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=worker_init,
        initargs=(str(FRAMES_DIR),),
    ) as pool:
        for frame_index, size in pool.map(worker_render, pending, chunksize=2):
            completed += 1
            if completed == 1 or completed % 30 == 0 or completed == len(pending):
                elapsed = time.perf_counter() - started
                speed = completed / max(elapsed, 0.001)
                remaining = (len(pending) - completed) / max(speed, 0.001)
                print(
                    f"[{completed:3d}/{len(pending)}] frame={frame_index:04d} "
                    f"size={size // 1024:4d}KiB speed={speed:4.1f}fps left~{remaining:5.1f}s",
                    flush=True,
                )


def encode_video(output: Path) -> None:
    cmd = [
        ffmpeg_path(),
        "-y",
        "-framerate",
        str(FPS),
        "-i",
        str(FRAMES_DIR / "frame-%04d.jpg"),
        "-vf",
        "scale=iw:ih:in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "17",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-r",
        str(FPS),
        "-movflags",
        "+faststart",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-metadata",
        "title=3D Layered ASCII Christmas Tree - Python",
        "-metadata",
        "comment=Rendered with Python, NumPy, Pillow and FFmpeg",
        str(output),
    ]
    print("编码命令:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)


def probe_and_validate(output: Path) -> dict:
    completed = subprocess.run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_name,width,height,r_frame_rate,pix_fmt,nb_frames,color_space,color_range",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(completed.stdout)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    checks = {
        "codec": stream.get("codec_name") == "h264",
        "resolution": [stream.get("width"), stream.get("height")] == [WIDTH, HEIGHT],
        "fps": stream.get("r_frame_rate") == f"{FPS}/1",
        "duration": abs(duration - DURATION) < 0.05,
        "pixel_format": stream.get("pix_fmt") == "yuv420p",
        "frames": int(stream.get("nb_frames") or 0) == TOTAL_FRAMES,
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))
    failed = [key for key, value in checks.items() if not value]
    if failed:
        raise RuntimeError("视频参数验证失败: " + ", ".join(failed))
    print("视频参数验证通过:", ", ".join(checks))
    return data


def full_decode_check(output: Path) -> None:
    subprocess.run(
        [ffmpeg_path(), "-v", "error", "-i", str(output), "-f", "null", "-"],
        check=True,
    )
    print("完整解码检查通过: full_decode=ok")


def render_video(output: Path, workers: int, resume: bool, keep_frames: bool) -> None:
    render_frames(workers, resume)
    encode_video(output)
    probe_and_validate(output)
    full_decode_check(output)
    if not keep_frames:
        shutil.rmtree(FRAMES_DIR, ignore_errors=True)
    print(f"完成: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description="纯 Python 3D ASCII 圣诞树")
    parser.add_argument("--preview", action="store_true", help="生成关键帧与连续运动接触表")
    parser.add_argument("--render", action="store_true", help="渲染 20 秒 MP4")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=max(1, min(10, (os.cpu_count() or 4) - 2)))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()

    if not args.preview and not args.render:
        args.preview = True
    if args.preview:
        render_previews()
    if args.render:
        output = args.output if args.output.is_absolute() else PROJECT_DIR / args.output
        render_video(output, max(1, args.workers), args.resume, args.keep_frames)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。加 --resume 可以继续未完成的逐帧渲染。")
        raise SystemExit(130)
