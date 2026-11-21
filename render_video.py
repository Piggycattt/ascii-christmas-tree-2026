#!/usr/bin/env python3
"""Deterministically render the ASCII tree to a 20-second portrait MP4.

Run from this folder:
    uv run --with playwright python render_video.py

The script uses the locally installed Chrome and FFmpeg. It captures exactly
600 frames at 30 fps, encodes H.264, probes the result, and removes temporary
frames after a successful render.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

WIDTH = 1080
HEIGHT = 1920
FPS = 30
DURATION = 20
TOTAL_FRAMES = FPS * DURATION


def find_chrome() -> Path:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到 Chrome/Edge，请先安装浏览器。")


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def capture_frames(project: Path, frames_dir: Path, resume: bool) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "缺少 Playwright。请用以下命令运行：\n"
            "uv run --with playwright python render_video.py"
        ) from exc

    html = project / "index.html"
    if not html.exists():
        raise FileNotFoundError(html)

    frames_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    url = html.resolve().as_uri() + "?export=1"

    print(f"Chrome: {chrome}")
    print(f"页面: {url}")
    print(f"帧数: {TOTAL_FRAMES} ({FPS} fps x {DURATION} s)")

    started = time.perf_counter()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(chrome),
            args=[
                "--hide-scrollbars",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--force-color-profile=srgb",
                "--font-render-hinting=none",
            ],
        )
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            screen={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            color_scheme="dark",
        )
        page = context.new_page()
        page.goto(url, wait_until="load", timeout=60_000)
        page.wait_for_function("window.__ASCIITREE_READY === true", timeout=60_000)

        info = page.evaluate(
            """() => ({
                canvas: [canvas.width, canvas.height],
                duration: window.__ASCIITREE_CONFIG.duration,
                fps: window.__ASCIITREE_CONFIG.fps,
                points: scene.length
            })"""
        )
        print("页面就绪:", json.dumps(info, ensure_ascii=False))
        if info["canvas"] != [WIDTH, HEIGHT]:
            raise RuntimeError(f"画布尺寸错误: {info['canvas']}")

        for frame in range(TOTAL_FRAMES):
            target = frames_dir / f"frame-{frame:04d}.jpg"
            if resume and target.exists() and target.stat().st_size > 10_000:
                continue

            page.evaluate("frame => window.renderExportFrame(frame)", frame)
            page.screenshot(
                path=str(target),
                type="jpeg",
                quality=96,
                full_page=False,
                animations="disabled",
                caret="hide",
            )

            if frame == 0 or (frame + 1) % 30 == 0 or frame == TOTAL_FRAMES - 1:
                elapsed = time.perf_counter() - started
                done = frame + 1
                speed = done / elapsed if elapsed else 0
                remaining = (TOTAL_FRAMES - done) / speed if speed else 0
                print(
                    f"[{done:3d}/{TOTAL_FRAMES}] "
                    f"{elapsed:6.1f}s elapsed, ~{remaining:5.1f}s left",
                    flush=True,
                )

        context.close()
        browser.close()


def encode_video(project: Path, frames_dir: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("未找到 ffmpeg。")

    run(
        [
            ffmpeg,
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frames_dir / "frame-%04d.jpg"),
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
            "title=3D ASCII Christmas Tree",
            "-metadata",
            "comment=Original deterministic browser animation",
            str(output),
        ],
        cwd=project,
    )


def probe_video(output: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FileNotFoundError("未找到 ffprobe。")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_name,width,height,r_frame_rate,pix_fmt,nb_frames",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(completed.stdout)
    print("验证信息:")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def validate_probe(data: dict) -> None:
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    checks = {
        "codec": stream.get("codec_name") == "h264",
        "resolution": [stream.get("width"), stream.get("height")] == [WIDTH, HEIGHT],
        "fps": stream.get("r_frame_rate") == f"{FPS}/1",
        "duration": abs(duration - DURATION) < 0.05,
        "pixel_format": stream.get("pix_fmt") == "yuv420p",
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError("视频参数验证失败: " + ", ".join(failed))
    print("视频参数验证通过:", ", ".join(checks))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ASCII-Christmas-Tree-20s-1080x1920.mp4"),
    )
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    project = Path(__file__).resolve().parent
    output = args.output if args.output.is_absolute() else project / args.output
    frames_dir = project / ".render_frames"

    capture_frames(project, frames_dir, args.resume)
    encode_video(project, frames_dir, output)
    data = probe_video(output)
    validate_probe(data)

    if not args.keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"完成: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消；已有帧保留在 .render_frames，可加 --resume 继续。")
        raise SystemExit(130)
