# Agent Orchestra — 2 dakikalık canlı demo

Bu akış, sistemi çalışan ürün olarak iki dakikada gösterir. Anlatım metni
[`scripts/demo_narration_tr.txt`](scripts/demo_narration_tr.txt) dosyasındadır.

## Hazırlık

```powershell
docker compose up -d redis
docker build -t agent-orchestrator-sandbox:latest ./sandbox
uvicorn orchestrator.main:app --reload
```

Tarayıcıda `http://127.0.0.1:8000/dashboard` adresini açın. Demo öncesinde:

1. Worker kümesini `3` yapın.
2. Yeni iş formundaki değerleri `fast: 5, 10, 20` ve
   `slow: 50, 100, 200` olarak bırakın.
3. Pencereyi 1440×900 civarında, Genel Bakış sekmesi açık tutun.
4. Sesli anlatım kullanılıyorsa bunun yapay zekâ tarafından üretildiğini
   dinleyiciye açıkça belirtin. Hazır metin bu açıklamayla başlar.

## Zaman çizelgesi

| Süre | Ekrandaki hareket | Anlatılan nokta |
|---|---|---|
| 00:00–00:15 | Dashboard genel görünümü | Tek merkezden iş, worker ve sonuç yönetimi |
| 00:15–00:30 | Worker yönetimini açın; üç worker ve heartbeat bilgilerini gösterin | İzole worktree ve Docker sandbox |
| 00:30–00:50 | Yeni iş oluşturun; 3×3 grid özetini gösterip gönderin | Dokuz görevin otomatik dağıtılması |
| 00:50–01:10 | Canlı ilerlemeyi, aktiviteyi ve worker görevlerini gösterin | Redis lease, retry ve crash recovery |
| 01:10–01:30 | En iyi sonuçları ve Agent Insights kartını gösterin | Skor sıralaması ve LLM yorumu |
| 01:30–01:45 | Daraltılmış tarama düğmesine basın | En iyi sonuçların yeni işe dönüşmesi |
| 01:45–01:55 | CSV/JSON indirme ve tekrar çalıştırma ikonlarını gösterin | Sonuçların tekrar kullanılabilmesi |
| 01:55–02:00 | Sistem hazır rozeti ve çalışan worker’larla kapanış | Dayanıklı, gözlemlenebilir orkestrasyon |

## Türkçe ses dosyası

OpenAI anahtarı `OPENAI_API_KEY` veya `ORCH_OPENAI_API_KEY` içinde tanımlıysa:

```powershell
python scripts/generate_demo_voice.py
```

Çıktı `workspace/demo/agent-orchestra-demo-tr.mp3` olur. Varsayılan model
`gpt-4o-mini-tts`, ses `marin`’dir. Metni ve seçilen ayarları API çağrısı
yapmadan kontrol etmek için:

```powershell
python scripts/generate_demo_voice.py --dry-run
```

