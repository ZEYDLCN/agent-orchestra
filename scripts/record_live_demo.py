"""Record a two-minute Turkish product demo against the real dashboard/API."""

import argparse
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "workspace" / "demo" / "agent-orchestra-live-demo.webm"


def add_demo_overlay(page: Page) -> None:
    page.evaluate(
        """() => {
          const style = document.createElement('style');
          style.textContent = `
            #demo-caption {
              position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%);
              z-index: 2147483647; width: min(980px, calc(100vw - 80px));
              padding: 14px 24px; border: 1px solid rgba(255,255,255,.38);
              border-radius: 16px; color: white; background: rgba(22,20,31,.88);
              box-shadow: 0 14px 40px rgba(25,15,45,.28);
              font: 600 19px/1.35 Inter, system-ui, sans-serif;
              text-align: center; backdrop-filter: blur(12px);
              transition: opacity .35s ease, transform .35s ease;
            }
            #demo-brand {
              position: fixed; right: 28px; top: 22px; z-index: 2147483647;
              padding: 9px 14px; border-radius: 999px; color: #3f3950;
              background: rgba(255,255,255,.9); border: 1px solid rgba(98,74,135,.18);
              font: 700 12px/1 Inter, system-ui, sans-serif; letter-spacing: .04em;
              backdrop-filter: blur(10px);
            }
            #demo-focus { position: fixed; inset: 0; pointer-events: none; z-index: 2147483646;
              box-shadow: inset 0 0 90px rgba(113,78,174,.08); }
          `;
          document.head.appendChild(style);
          const caption = document.createElement('div');
          caption.id = 'demo-caption';
          caption.setAttribute('role', 'status');
          document.body.appendChild(caption);
          const brand = document.createElement('div');
          brand.id = 'demo-brand';
          brand.textContent = 'AGENT ORCHESTRA · CANLI DEMO';
          document.body.appendChild(brand);
          const focus = document.createElement('div');
          focus.id = 'demo-focus';
          document.body.appendChild(focus);
        }"""
    )


def caption(page: Page, text: str) -> None:
    page.locator("#demo-caption").evaluate("(node, value) => node.textContent = value", text)


def wait_until(started: float, second: float) -> None:
    remaining = second - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)


def record(base_url: str, output: Path, duration: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = output.parent / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(raw_dir),
            record_video_size={"width": 1440, "height": 900},
            accept_downloads=True,
        )
        page = context.new_page()
        response = page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(page.locator("#connectionText")).to_have_text("Bağlı", timeout=15_000)
        add_demo_overlay(page)
        video = page.video
        assert video is not None
        started = time.monotonic()

        caption(page, "Tek hedef · Planner · Trader agent’lar · Reviewer · Denetlenmiş final")
        expect(page.locator("#agentRunPanel")).to_be_visible(timeout=15_000)
        wait_until(started, 12)

        caption(page, "Planner doğal dildeki hedefi doğrulanmış görevlere dönüştürür.")
        page.locator("#newAgentRunButton").click()
        page.locator("#agentGoal").fill(
            "En iyi hareketli ortalama stratejisini bul; gerekiyorsa ikinci turda aramayı daralt."
        )
        wait_until(started, 27)

        caption(page, "Tur, görev ve trader sınırları kullanıcı tarafından kontrol edilir.")
        page.locator("#agentRunDialog [data-close-dialog]").first.click()
        page.locator(".insights-panel [data-open-workers]").click()
        page.locator("#scaleInput").fill("1")
        page.locator("#scaleButton").click()
        expect(page.locator("#workersList .worker-row")).to_have_count(1, timeout=30_000)
        wait_until(started, 40)

        caption(page, "Her trader ayrı süreç, profil ve model bilgisiyle izlenir.")
        page.locator("#workersDialog [data-close-dialog]").click()
        page.locator(".page-heading [data-new-job]").click()
        page.locator("#fastParams").fill("5, 10")
        page.locator("#slowParams").fill("50")
        expect(page.locator("#gridSummary")).to_contain_text("2 görev")
        wait_until(started, 51)

        caption(page, "İki gerçek backtest görevi dayanıklı Redis kuyruğuna gönderiliyor.")
        page.locator("#submitJobButton").click()
        expect(page.locator("#newJobDialog")).not_to_be_visible(timeout=20_000)
        expect(page.locator("#jobDone")).to_have_text("2", timeout=35_000)
        wait_until(started, 65)

        caption(page, "İlerleme, skorlar ve agent yorumları arayüze canlı olarak gelir.")
        page.locator(".view-tab[data-view=results]").click()
        expect(page.locator("#allResults .result-row")).to_have_count(2)
        wait_until(started, 76)

        caption(page, "Her sonuçta worker, model, parametre, süre ve değerlendirme birlikte görünür.")
        page.locator("#allResults .result-row").first.click()
        expect(page.locator("#resultDialog")).to_be_visible()
        wait_until(started, 88)

        page.locator("#resultDialog [data-close-dialog]").click()
        page.locator(".view-tab[data-view=activity]").click()
        caption(page, "Aktivite akışı her görevin yaşam döngüsünü denetlenebilir kılar.")
        wait_until(started, 99)

        page.locator(".view-tab[data-view=overview]").click()
        caption(page, "Reviewer sonuçları bitirir veya otomatik refine turu başlatır.")
        page.locator("#agentRunPanel").scroll_into_view_if_needed()
        wait_until(started, 110)

        caption(page, "Agent Orchestra · Fikirden plana, paralel çalışmadan güvenilir finale.")
        page.locator("#themeButton").click()
        wait_until(started, duration)

        page.close()
        context.close()
        raw_path = Path(video.path())
        shutil.copy2(raw_path, output)
        browser.close()

    print(f"video={output} duration~{duration}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--duration", type=int, default=120)
    args = parser.parse_args()
    record(args.base_url.rstrip("/"), args.output, args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
