"""Build the narrated, shareable Turkish Agent Orchestra demo video."""

import argparse
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

REPO_ROOT = Path(__file__).resolve().parent.parent
NARRATION = Path(__file__).with_name("demo_narration_tr.txt")
DEMO_DIR = REPO_ROOT / "workspace" / "demo"
DEFAULT_MODEL = DEMO_DIR / "piper" / "tr_TR-dfki-medium.onnx"


def generate_voice(output: Path, model: Path, length_scale: float) -> None:
    run_checked(
        [
            sys.executable,
            "-m",
            "piper",
            "--model",
            str(model),
            "--input-file",
            str(NARRATION),
            "--output-file",
            str(output),
            "--length-scale",
            str(length_scale),
            "--volume",
            "0.92",
        ]
    )


def run_checked(command: list[str]) -> None:
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--length-scale", type=float, default=1.0)
    args = parser.parse_args()

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    audio = DEMO_DIR / "agent-orchestra-demo-tr.wav"
    screen = DEMO_DIR / "agent-orchestra-live-demo.webm"
    output = DEMO_DIR / "agent-orchestra-tanitim-tr.mp4"
    narration = NARRATION.read_text(encoding="utf-8").strip()

    if not args.model.exists():
        parser.error(
            f"Piper model not found: {args.model}. Download tr_TR-dfki-medium first."
        )
    print(f"voice={args.model.name} length_scale={args.length_scale} words={len(narration.split())}")
    generate_voice(audio, args.model, args.length_scale)
    run_checked(
        [
            sys.executable,
            str(Path(__file__).with_name("record_live_demo.py")),
            "--base-url",
            args.base_url,
            "--output",
            str(screen),
            "--duration",
            str(args.duration),
        ]
    )

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    run_checked(
        [
            ffmpeg,
            "-y",
            "-i",
            str(screen),
            "-i",
            str(audio),
            "-filter_complex",
            f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur={args.duration}[voice]",
            "-map",
            "0:v:0",
            "-map",
            "[voice]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-t",
            str(args.duration),
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
