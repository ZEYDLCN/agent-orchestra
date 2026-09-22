"""Generate the Turkish two-minute demo narration with OpenAI TTS.

The generated voice is AI-created. The bundled narration starts with the
required disclosure. An API key can be supplied through OPENAI_API_KEY or
the project's existing ORCH_OPENAI_API_KEY setting.
"""
import argparse
import os
from pathlib import Path

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = Path(__file__).with_name("demo_narration_tr.txt")
DEFAULT_OUTPUT = REPO_ROOT / "workspace" / "demo" / "agent-orchestra-demo-tr.mp3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="gpt-4o-mini-tts")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    narration = args.input.read_text(encoding="utf-8").strip()
    word_count = len(narration.split())
    print(f"model={args.model} voice={args.voice} words={word_count} output={args.output}")
    if args.dry_run:
        return 0

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ORCH_OPENAI_API_KEY")
    if not api_key:
        parser.error("OPENAI_API_KEY or ORCH_OPENAI_API_KEY is required to generate audio")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=api_key)
    with client.audio.speech.with_streaming_response.create(
        model=args.model,
        voice=args.voice,
        input=narration,
        instructions=(
            "Türkçe konuş. Profesyonel ve güven veren bir ürün demosu tonu kullan. "
            "Doğal duraklamalarla, anlaşılır ve orta hızda oku; toplam süreyi yaklaşık "
            "iki dakika tut. Agent Orchestra, Redis, Docker ve API terimlerini net söyle."
        ),
        response_format="mp3",
    ) as response:
        response.stream_to_file(args.output)

    print(f"generated={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
