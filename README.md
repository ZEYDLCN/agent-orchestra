# Agent Orchestration Tool

Merkezi bir orchestrator'ın, git worktree ile izole edilmiş worker process'lere
görev dağıttığı, Redis kuyruğu üzerinden koordine olan bir çoklu-agent
orkestrasyon altyapısı. Demo senaryosu: bir trading stratejisinin parametre
grid'ini worker'lara dağıtıp sonuçları karşılaştırma (clustered backtest).

## Mimari

```
FastAPI orchestrator --push--> Redis queue --pop--> Worker process
        |                          |                     |
        |                    pub/sub events         LLM (multi-provider)
        |                          |                     |
        +--> git worktree per worker              shared memory (RAG-lite)
```

- `orchestrator/` — FastAPI servisi: iş kabul eder, worker spawn/scale eder, sonuç toplar
- `orchestrator/queue.py` — Redis tabanlı task kuyruğu + durum takibi
- `orchestrator/messaging.py` — inter-agent pub/sub (worker'lar sonuçlarını yayınlar)
- `orchestrator/memory.py` — shared memory + RAG-lite (parametre-uzayı benzerliğine göre geçmiş sonuç getirme)
- `orchestrator/llm/` — multi-LLM soyutlaması: `mock` (varsayılan, key gerektirmez), `anthropic`, `openai`
- `orchestrator/mcp_server.py` — orchestrator'ı MCP tool'ları olarak dışarı açar (submit_job, get_job_status, scale_workers, list_workers)
- `worker/` — worker process: kuyruktan görev çeker, kendi worktree'sinde çalıştırır, LLM'den yorum alır, sonucu yayınlar
- `target_repo_seed/` — worker worktree'lerinin bootstrap edildiği örnek "çalışma kod tabanı" (sample strategy.py); `target_repo/` ilk çalıştırmada buradan üretilir ve git-ignore'ludur
- `scripts/demo.py` — uçtan uca demo client
- `scripts/listen.py` — bir job'ın canlı inter-agent mesaj akışını izler

Sırada: gerçek multi-machine clustering (Redis zaten network-erişilebilir
olduğu için worker'lar farklı makinelerde de çalışabilir — sadece
`ORCH_REDIS_URL`'i paylaşılan bir Redis'e işaret ettirmek yeterli).

### Multi-LLM

`.env`'de `ORCH_LLM_PROVIDER=anthropic` (+ `ORCH_ANTHROPIC_API_KEY=...`) veya
`ORCH_LLM_PROVIDER=openai` (+ `ORCH_OPENAI_API_KEY=...`) ayarlanmazsa sistem
otomatik olarak key gerektirmeyen `mock` provider'a düşer — demo API key
olmadan da tam çalışır.

### MCP

Orchestrator API'sini bir MCP client'a (Claude Desktop/Code) araç olarak
açmak için (orchestrator ayrı bir terminalde çalışıyor olmalı):

```powershell
python -m orchestrator.mcp_server
```

## Kurulum

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Redis'i ayağa kaldır (Docker Desktop çalışıyor olmalı):

```powershell
docker compose up -d redis
```

## Çalıştırma

Terminal 1 — orchestrator:

```powershell
uvicorn orchestrator.main:app --reload --port 8000
```

**Seçenek A — dashboard'dan (sunum için önerilen):** tarayıcıda
`http://localhost:8000/dashboard` aç. "Worker’ları yönet" üzerinden
"Kümeyi büyüt" ile worker ekle; "Yeni iş oluştur" ile parametre grid’ini
başlat. Varsayılan grid 9 görev içerir, değerler formdan değiştirilebilir.

Dashboard açık/pastel tema, koyu tema ve mobil düzen içerir. Soldaki iş
geçmişinden çalışmaları seçebilir, kimlikle arayabilir (`Ctrl/Cmd + K`) ve
duruma göre filtreleyebilirsin. Genel bakış, Sonuçlar ve Aktivite akışı
görünümlerinde ilerleme, hata ayrıntıları ve LLM yorumları görüntülenir.
Sonuçlar görev kayıtlarından okunur; SSE olayları erken yenilemeyi tetikler,
4 saniyelik sorgulama bağlantı kesintilerinde takibi sürdürür. Böylece olay
geçmişinin süresi dolmuş eski işlerin sonuçları da görüntülenebilir.

"Agent Insights" en yüksek skorlu sonucun mevcut LLM yorumunu gösterir;
ek bir model çağrısı yapmaz. Mock sağlayıcının yorumları açıkça etiketlenir.
Worker sayısını azaltmak için yönetim penceresinden worker’ları ayrı ayrı
durdurmak gerekir; "Kümeyi büyüt" yalnızca yeni worker ekler.

**Seçenek B — CLI'dan:**

Terminal 2 — demo (5 worker spawn eder, parametre grid'i dağıtır, sonuçları
gösterir):

```powershell
python scripts/demo.py --workers 5
```

Terminal 3 (opsiyonel) — bir job'ın canlı mesaj akışını izle (job_id'yi
demo çıktısından ya da `POST /jobs` cevabından al):

```powershell
python scripts/listen.py <job_id>
```

## API

- `GET /dashboard` — canlı web dashboard (worker durumu, job ilerlemesi, sıralı sonuçlar)
- `POST /workers/scale {"count": 5}` — worker sayısını N'e tamamla
- `GET /workers` — aktif worker listesi
- `DELETE /workers/{id}` — worker'ı durdur, worktree'sini temizle
- `POST /jobs {"tasks": [{"params": {...}}, ...]}` — iş gönder, task'lara böl
- `GET /jobs?limit=20` — son job'ların özet listesi (takip/geçmiş)
- `GET /jobs/{job_id}` — iş durumu ve sonuçları
- `GET /jobs/{job_id}/events` — job'ın inter-agent mesaj akışı, Server-Sent Events olarak (geç bağlanan client'lar için son 200 event replay edilir, sonra canlıya geçilir)

## Test

```powershell
pip install -r requirements-dev.txt
pytest
```

İsteğe bağlı tarayıcı kontrolü (orchestrator çalışırken):

```powershell
pip install playwright
python -m playwright install chromium
python scripts/check_dashboard.py --base-url http://127.0.0.1:8000
```

Bu kontrol gerçek HTML/CSS/JavaScript dosyalarını kullanır; API cevaplarını
test verileriyle değiştirir, gerçek worker veya iş oluşturmaz. İş oluşturma,
filtreleme, worker yönetimi, hata/bağlantı durumları, sonuç detayları, tema
kalıcılığı ve farklı ekran boyutlarını kontrol eder. Ekran görüntüleri
`workspace/ui-check/` dizinine kaydedilir.
