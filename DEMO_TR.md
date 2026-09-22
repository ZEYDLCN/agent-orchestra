# Agent Orchestra — 2 dakikalık Türkçe tanıtım videosu

Video gerçek dashboard ve API üzerinde kaydedilir. Planner → trader agent'lar →
Reviewer döngüsü, worker yönetimi, gerçek backtest görevleri, sonuçlar ve
aktivite akışı gösterilir. Türkçe anlatım metni
[`scripts/demo_narration_tr.txt`](scripts/demo_narration_tr.txt) dosyasındadır.

## Hazırlık

```powershell
docker compose --profile local-llm up -d redis ollama
uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
```

Piper'ın açık kaynak Türkçe DFKI modelini bir kez indirin:

```powershell
python -m piper.download_voices tr_TR-dfki-medium --download-dir workspace/demo/piper
```

## Videoyu üretme

```powershell
python scripts/build_demo_video.py
```

Betik üç adımı otomatik tamamlar:

1. 241 kelimelik Türkçe anlatımı yerel Piper modeliyle tamamen offline üretir.
2. Playwright ile 1440×900 çözünürlükte, gerçek API üzerinde 120 saniyelik
   dashboard kaydı alır.
3. Sesi EBU uyumlu seviyeye normalize eder ve H.264/AAC MP4 içine ekler.

Nihai çıktı:

```text
workspace/demo/agent-orchestra-tanitim-tr.mp4
```

Ara çıktılar `agent-orchestra-demo-tr.wav` ve
`agent-orchestra-live-demo.webm` olarak aynı klasörde tutulur.

## Zaman çizelgesi

| Süre | Ekrandaki hareket | Anlatılan nokta |
|---|---|---|
| 00:00–00:12 | Tamamlanmış Qwen agent akışı | Planner, trader ve Reviewer mimarisi |
| 00:12–00:27 | AI hedefi ver formu | Doğal dil hedefi ve kontrol sınırları |
| 00:27–00:40 | Worker yönetimi | Ayrı süreç, profil ve model görünürlüğü |
| 00:40–01:05 | İki gerçek backtest görevi | Redis kuyruğu ve canlı ilerleme |
| 01:05–01:28 | Sonuçlar ve detay penceresi | Skor, parametre, model ve agent yorumu |
| 01:28–01:39 | Aktivite akışı | Görev yaşam döngüsü ve denetlenebilirlik |
| 01:39–01:50 | Agent final paneli | Reviewer ve otomatik refine kararı |
| 01:50–02:00 | Koyu tema kapanışı | Ürün özeti |

Anlatımın ilk cümlesi sesin yapay zekâ ile üretildiğini açıkça belirtir.
Piper üretimi yereldir; anlatım metni herhangi bir harici TTS servisine
gönderilmez.
