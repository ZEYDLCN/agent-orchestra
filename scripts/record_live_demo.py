"""Record a two-minute, captioned demo against a running real orchestrator.

The script drives the actual dashboard and API. It does not mock requests.
Output defaults to workspace/demo/agent-orchestra-live-demo.webm.
"""
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
              position: fixed; left: 50%; bottom: 30px; transform: translateX(-50%);
              z-index: 2147483647; max-width: 1040px; padding: 15px 24px;
              border: 1px solid rgba(255,255,255,.35); border-radius: 16px;
              color: white; background: rgba(19,20,30,.88);
              box-shadow: 0 14px 40px rgba(25,15,45,.28);
              font: 600 20px/1.35 Inter, system-ui, sans-serif;
              text-align: center; backdrop-filter: blur(12px);
              transition: opacity .35s ease, transform .35s ease;
            }
            #demo-caption:empty { opacity: 0; transform: translate(-50%,12px); }
            #demo-brand {
              position: fixed; right: 28px; top: 22px; z-index: 2147483647;
              padding: 8px 13px; border-radius: 999px; color: #3f3950;
              background: rgba(255,255,255,.86); border: 1px solid rgba(98,74,135,.16);
              font: 700 12px/1 Inter, system-ui, sans-serif; letter-spacing: .04em;
              backdrop-filter: blur(10px);
            }
          `;
          document.head.appendChild(style);
          const caption = document.createElement('div');
          caption.id = 'demo-caption';
          caption.setAttribute('role', 'status');
          document.body.appendChild(caption);
          const brand = document.createElement('div');
          brand.id = 'demo-brand';
          brand.textContent = 'AGENT ORCHESTRA · LIVE DEMO';
          document.body.appendChild(brand);
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
        response = page.goto(f"{base_url}/dashboard", wait_until="networkidle")
        assert response is not None and response.status == 200
        expect(page.locator("#connectionText")).to_have_text("Bağlı")
        add_demo_overlay(page)
        video = page.video
        assert video is not None
        started = time.monotonic()

        caption(page, "Agent Orchestra: dağıtık agent çalışmalarını tek merkezden yönetin.")
        wait_until(started, 7)

        caption(page, "Worker kümesi · İzole Git worktree · Docker sandbox")
        page.locator(".insights-panel [data-open-workers]").click()
        page.locator("#scaleInput").fill("3")
        page.locator("#scaleButton").click()
        expect(page.locator("#workersList .worker-row")).to_have_count(3, timeout=30_000)
        wait_until(started, 20)

        page.locator("#workersDialog [data-close-dialog]").click()
        caption(page, "3 × 3 parametre grid’i: dokuz görev otomatik dağıtılıyor.")
        page.locator(".page-heading [data-new-job]").click()
        page.locator("#fastParams").fill("5, 10, 20")
        page.locator("#slowParams").fill("50, 100, 200")
        expect(page.locator("#gridSummary")).to_contain_text("9 görev")
        wait_until(started, 31)
        page.locator("#submitJobButton").click()
        expect(page.locator("#newJobDialog")).not_to_be_visible(timeout=20_000)

        caption(page, "Canlı ilerleme · Redis lease · Hata durumunda otomatik geri kazanım")
        wait_until(started, 43)
        expect(page.locator("#jobDone")).to_have_text("9", timeout=45_000)
        caption(page, "Dokuz görevin tamamı sonuçlandı; en iyi skor otomatik öne çıkarıldı.")
        wait_until(started, 55)

        page.locator(".view-tab[data-view=results]").click()
        caption(page, "Sonuçları skor veya çalışma süresine göre karşılaştırın.")
        expect(page.locator("#allResults .result-row")).to_have_count(9)
        page.locator("#resultSort").select_option("duration")
        wait_until(started, 66)

        page.locator("#allResults .result-row").first.click()
        caption(page, "Her sonuçta worker, süre, parametreler ve agent yorumu birlikte görünür.")
        expect(page.locator("#resultDialog")).to_be_visible()
        wait_until(started, 76)
        page.locator("#resultDialog [data-close-dialog]").click()

        page.locator(".view-tab[data-view=activity]").click()
        caption(page, "Aktivite akışı görev geçmişini ve deneme sayılarını izlenebilir kılar.")
        wait_until(started, 86)

        page.locator(".view-tab[data-view=overview]").click()
        caption(page, "Refinement: en iyi sonuçlar yeni ve daha dar bir taramaya dönüşür.")
        page.locator("#refineJobButton").click()
        expect(page.locator("#totalTasks")).to_have_text("18 görev", timeout=20_000)
        wait_until(started, 99)
        expect(page.locator("#jobDone")).to_have_text("18", timeout=45_000)

        caption(page, "CSV/JSON dışa aktarın, işi tekrarlayın veya şablon olarak saklayın.")
        with page.expect_download():
            page.locator("#exportJsonButton").click()
        wait_until(started, 109)

        caption(page, "Dayanıklı kuyruk, güvenli sandbox ve gözlemlenebilir agent orkestrasyonu.")
        page.locator("#themeButton").click()
        wait_until(started, duration - 3)
        caption(page, "Agent Orchestra · Fikirden sonuca, birlikte.")
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
