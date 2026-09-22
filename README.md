# Agent Orchestration Tool

Merkezi bir orchestrator'ın, git worktree ile izole edilmiş ve Docker
container'ında sandbox'lanmış worker process'lere görev dağıttığı, Redis
üzerinden koordine olan dayanıklı bir çoklu-agent orkestrasyon altyapısı.
Demo senaryosu: bir trading stratejisinin parametre grid'ini worker'lara
dağıtıp sonuçları karşılaştırma (clustered backtest), en iyi sonuçlara göre
otomatik olarak daha dar bir takip taraması üretme.

Sunum için hazır iki dakikalık Türkçe akış, anlatım metni ve OpenAI
TTS üretim komutu: [`DEMO_TR.md`](DEMO_TR.md).

## Mimari

```
FastAPI orchestrator --push--> Redis (durable queue + registry) --pop--> Worker process
        |                          |                     |                    |
        |                    pub/sub events        job history/metrics   sandbox container
        |                          |                                          |
        +--> git worktree per worker                                   LLM + shared memory
```

- `orchestrator/` — FastAPI servisi: iş kabul eder, worker spawn/scale eder, sonuç toplar
- `orchestrator/queue.py` — Redis tabanlı, **lease/retry/dead-letter destekli** dayanıklı task kuyruğu
- `orchestrator/reaper.py` — kilitli kalan (worker çökmüş/öldürülmüş) task'ları geri kazanan arka plan süreci
- `orchestrator/worker_registry.py` — Redis tabanlı worker kaydı: heartbeat/TTL, cooperative durdurma
- `orchestrator/coordinator.py` — tamamlanan bir job'ın en iyi sonuçlarından yeni, daha dar bir parametre taraması üretir
- `orchestrator/messaging.py` — inter-agent pub/sub (worker'lar sonuçlarını yayınlar, geç bağlananlar için replay)
- `orchestrator/memory.py` — shared memory + RAG-lite (parametre-uzayı benzerliğine göre geçmiş sonuç getirme)
- `orchestrator/llm/` — multi-LLM soyutlaması: `mock` (varsayılan, key gerektirmez), `anthropic`, `openai`; timeout+retry sarmalayıcılı
- `orchestrator/mcp_server.py` — orchestrator'ı MCP tool'ları olarak dışarı açar
- `orchestrator/auth.py`, `orchestrator/rate_limit.py` — opsiyonel API key + rate limit
- `orchestrator/metrics.py` — Redis tabanlı iş metrikleri (`/metrics`, Prometheus formatı)
- `worker/` — worker process: kuyruktan görev çeker, **Docker sandbox** içinde çalıştırır, LLM'den yorum alır, sonucu yayınlar
- `sandbox/` — task'ların çalıştığı izole Docker image'ı (ağsız, salt-okunur, non-root, kaynak limitli)
- `target_repo_seed/` — worker worktree'lerinin bootstrap edildiği örnek "çalışma kod tabanı"; `target_repo/` ilk çalıştırmada buradan üretilir ve git-ignore'ludur
- `scripts/demo.py`, `scripts/listen.py`, `scripts/check_dashboard.py` — CLI demo client, canlı mesaj izleyici, dashboard UI kontrolü
- `scripts/generate_demo_voice.py` — iki dakikalık Türkçe demo anlatımını OpenAI TTS ile MP3'e çevirir

### Görev dayanıklılığı (durable queue)

Kuyruk `BLMOVE` ile task'ı `pending` listesinden atomik olarak `processing`
listesine taşır ve bir **lease** (görünürlük zaman aşımı, varsayılan 90sn)
verir. Worker çökerse, sert şekilde öldürülürse ya da makine kapanırsa,
task `processing` listesinde lease'i dolana kadar bekler; arka plandaki
**reaper** (varsayılan 15sn'de bir) süresi dolmuş lease'leri tarar ve
task'ı ya yeniden kuyruğa alır (deneme hakkı kaldıysa) ya da
`dead-letter`'a taşıyıp `failed` işaretler (`max_attempts`, varsayılan 3,
dolduğunda). `DELETE /workers/{id}` ile zarif durdurma cooperative
çalışır: orchestrator Redis'e bir "dur" bayrağı koyar, worker bunu
task'lar arasında kontrol eder ve yeni task almadan çıkar. O anda bir task
çalışıyorsa grace süresi boyunca tamamlanması beklenir; süre aşılırsa
process sonlandırılır ve lease reaper task'ı geri alır. Windows'ta
`Popen.terminate()` gerçek bir graceful hook sağlamadığı için normal
kapanış Redis bayrağıyla koordine edilir. Crash recovery mekanizması canlı
olarak test edildi: 2 worker'dan biri 120
görevlik bir iş ortasında sert şekilde öldürüldü, tek görev bile
kaybolmadı (reaper log'u: `reaper reclaimed 1 expired task(s)`), iş
%100 tamamlandı.

### Worker kaydı ve çoklu makine

Worker listesi artık orchestrator process'inin belleğinde değil,
**Redis'te** tutulur (`worker_registry.py`): her worker kendi kimliğini
heartbeat ile (varsayılan 5sn'de bir, TTL 15sn) günceller. Bu sayede:

- Orchestrator yeniden başlasa bile hâlâ çalışan worker'lar görünür kalır.
- Aynı Redis'i paylaşan **farklı bir makinede** elle başlatılan bir worker
  (`ORCH_REDIS_URL` paylaşılan Redis'e işaret ederse) otomatik olarak
  dashboard'da/`GET /workers`'da görünür ve `DELETE /workers/{id}` ile
  durdurulabilir — cooperative durdurma Redis üzerinden çalıştığı için
  orchestrator'ın o worker'ın process handle'ına sahip olması gerekmez.
- Eş zamanlı `scale` istekleri bir Redis kilidiyle serileştirilir.

### Güvenlik izolasyonu (sandbox)

Worker artık `strategy.py`'yi kendi process'ine import edip çalıştırmıyor
— her task, **ayrı bir Docker container**'ında çalışıyor
(`worker/sandbox_executor.py`, image: `sandbox/Dockerfile`):
ağ erişimi kapalı (`--network none`), dosya sistemi salt-okunur, worktree
salt-okunur mount edilir, non-root kullanıcı (uid 1000), CPU/bellek/pid
limitli, host'un ortam değişkenleri container'a hiç geçmez. Bunların
hepsi canlı olarak doğrulandı (ağ erişimi engellendi, salt-okunur fs
yazmayı reddetti, 128MB limitli container 1GB ayırmaya çalışınca OOM
kill aldı — exit 137). Docker mevcut değilse `development` modunda
sandboxsız çalışmaya düşer (uyarı loglanır); `production` modunda bu
durumda task hata verir, sessizce izolasyonsuz çalışmaz.

### Sonuçlardan yeni görev üretme (coordinator)

`POST /jobs/{job_id}/refine` — tamamlanmış bir job'ın en iyi N sonucunun
etrafında daha dar bir parametre grid'i üretip yeni bir job olarak
gönderir (`orchestrator/coordinator.py`). Bilinçli olarak LLM'e değil
deterministik bir "en iyi sonuca yakınlaş" sezgisiğine dayanır: sayısal
bir hedefi (skor) olan bu görev tipi için LLM'in serbest metin
üretip geri parse etmesinden daha güvenilir ve test edilebilir. Yeni
job `parent_job_id` ile eskisine bağlanır (`GET /jobs/{id}` → `meta`).

### Multi-LLM

`.env`'de `ORCH_LLM_PROVIDER=anthropic` (+ `ORCH_ANTHROPIC_API_KEY=...`) veya
`ORCH_LLM_PROVIDER=openai` (+ `ORCH_OPENAI_API_KEY=...`) ayarlanmazsa sistem
`development` modunda otomatik olarak key gerektirmeyen `mock`
provider'a düşer (uyarı loglanır); `production` modunda eksik/yanlış
yapılandırma başlangıçta hata verir, sessizce mock'a düşmez. Her LLM
çağrısı timeout (varsayılan 20sn) ve retry (varsayılan 2 deneme) ile
sarmalanır (`orchestrator/llm/resilient.py`). Backtest başarılı olup
yalnızca LLM yorumu başarısız/timeout olursa, task yine de `done` sayılır
(`execution_status=completed`, `analysis_status=failed`, `commentary=null`)
— skor ile yorum farklı yaşam döngülerine sahiptir.

### Gözlemlenebilirlik

- Hem orchestrator hem worker JSON formatında, korelasyonlu (worker_id/job_id/task_id) log basar (`orchestrator/logging_setup.py`).
- `GET /metrics` — Prometheus formatında: HTTP metrikleri
  (`prometheus-fastapi-instrumentator`) + iş metrikleri
  (`orchestrator_tasks_submitted_total`, `orchestrator_tasks_completed_total{status=}`,
  `orchestrator_tasks_reclaimed_total`, `orchestrator_task_duration_seconds`,
  `orchestrator_workers_active{status=}`). İş metrikleri Redis'te tutulur
  ve her scrape'te taze okunur — worker'lar ayrı process'ler (hatta ayrı
  makineler) olduğu için process-içi Python sayaçları bunları
  göremezdi; bu canlı test sırasında bulunup düzeltildi.

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
pip install -r requirements.lock.txt   # reproducible; requirements.txt de kullanılabilir
```

Redis'i ayağa kaldır (Docker Desktop çalışıyor olmalı) ve sandbox
image'ını build et:

```powershell
docker compose up -d redis
docker build -t agent-orchestrator-sandbox:latest ./sandbox
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

> Not: cancel/retry/refine/job silme endpoint'leri şu an dashboard'da buton
> olarak yok, sadece API üzerinden kullanılabilir (bkz. API bölümü) — bu
> UI'ı ayrı bir redesign'ı hak eden, bilinçli olarak kapsam dışı bırakılmış
> bir sonraki adım.

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

### Çoklu makine

Worker'ı farklı bir makinede başlatmak için, o makinede repo'yu klonla,
`ORCH_REDIS_URL`'i paylaşılan Redis'e işaret ettir ve doğrudan çalıştır:

```powershell
python -m worker.run_worker --worker-id worker-remote-1 --workdir <bir git worktree yolu>
```

Worker kendi kendine kayıt olur ve heartbeat atar; orchestrator'ın onu
spawn etmiş olması gerekmez. Merkezi bir "uzak worker başlat" endpoint'i
henüz yok — bu manuel adım hâlâ elle yapılıyor.

## Production dağıtımı

`Dockerfile` (orchestrator + worker) ve `docker-compose.yml`
(`redis` + `orchestrator` servisleri, AOF kalıcılığı, healthcheck) mevcut.
Worker'lar orchestrator container'ı içinde sibling subprocess olarak
çalışır (worktree'ler container içinde); task sandbox'ı için orchestrator
container'ının host'un Docker socket'ine erişimi gerekir (`docker-compose.yml`
içinde mount edilir). **Not:** bu container-orchestrator-kontrolü-host-docker
akışı Linux'ta standarttır; bu repoda Windows/Docker Desktop üzerinde
uçtan uca doğrulanmadı (socket mount davranışı platforma göre değişiyor).
Doğrulanan yol: orchestrator'ı host'ta `uvicorn` ile çalıştırıp task'ları
yine host'un Docker daemon'ı üzerinden sandbox'lamak (bu repoda geliştirme
boyunca kullanılan ve canlı test edilen kurulum).

## API

- `GET /dashboard` — canlı web dashboard
- `GET /health` — sağlık kontrolü
- `GET /metrics` — Prometheus metrikleri
- `POST /workers/scale {"count": 5}` — worker sayısını N'e tamamla
- `GET /workers` — aktif worker listesi (Redis registry'den, yerel + uzak)
- `DELETE /workers/{id}` — worker'ı zarif şekilde durdur (elindeki task'ı bırakır), worktree'sini temizle
- `POST /jobs {"tasks": [...], "required_capability"?, "kind"?}` — iş gönder, task'lara böl
- `GET /jobs?limit=20` — son job'ların özet listesi (takip/geçmiş)
- `GET /jobs/{job_id}` — iş durumu ve sonuçları
- `POST /jobs/{job_id}/cancel` — henüz alınmamış task'ları iptal et (çalışmakta olanlar tamamlanır, task'lar çok hızlı olduğu için ara kesme yok)
- `POST /jobs/{job_id}/tasks/{task_id}/retry` — başarısız/iptal edilmiş bir task'ı sıfırdan yeniden dene
- `POST /jobs/{job_id}/refine` — en iyi sonuçlardan daha dar bir takip job'ı üret
- `DELETE /jobs/{job_id}` — job'ı ve tüm task'larını sil/arşivle
- `GET /jobs/{job_id}/events` — job'ın inter-agent mesaj akışı, Server-Sent Events (geç bağlanan client'lar için son 200 event replay edilir, sonra canlıya geçilir)

Mutasyon yapan endpoint'ler (`POST`/`DELETE`) `ORCH_API_KEY` ayarlıysa
`X-API-Key` header'ı ister; ayarlanmazsa (varsayılan) serbesttir.

## Test

```powershell
pip install -r requirements-dev.txt
pytest -v --cov --cov-report=term-missing
ruff check orchestrator worker scripts tests
mypy orchestrator worker
```

60 test geçiyor (fakeredis ile, gerçek Redis/Docker gerekmez).
`worker_manager.py`, `worktree.py`, `sandbox_executor.py`, `run_worker.py`
gibi process/Docker-ağırlıklı modüllerin coverage'ı bilinçli olarak düşük
— bunlar unit test yerine canlı/manuel entegrasyon testiyle doğrulandı
(worker çökmesi/kurtarma, zarif durdurma, sandbox izolasyonu vb. — bkz.
yukarıdaki "Görev dayanıklılığı" ve "Güvenlik izolasyonu" bölümleri).

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

## CI

`.github/workflows/ci.yml` her push/PR'da lint (ruff), type-check (mypy),
test (pytest+coverage) ve iki Docker image'ının (orchestrator + sandbox)
build edilebilirliğini kontrol eder. `.github/dependabot.yml` pip/docker/
actions bağımlılıklarını haftalık tarar.

## Bilinen kapsam dışı bırakılan noktalar

Dürüstlük için: aşağıdakiler bilinçli olarak bu turda yapılmadı, "eksik"
değil "ileri faz" olarak bırakıldı:

- Tam DAG tabanlı çoklu-agent planlayıcı / genel amaçlı tool-calling
  protokolü — mevcut tek görev tipi (parametre taraması) için `refine`
  endpoint'i gerçek, test edilmiş bir "sonuç → yeni görev" döngüsü
  sağlıyor; bunun ötesinde soyutlama inşa etmek doğrulayacak ikinci bir
  görev tipi olmadan spekülatif olurdu.
- Container-içinde-orchestrator'ın host Docker'ını kontrol ettiği
  production akışı Windows'ta uçtan uca doğrulanmadı (bkz. "Production
  dağıtımı").
- Cancel/retry/refine/job-silme için dashboard'da buton yok, sadece API.
- Tam mypy strict modu / %100 coverage hedeflenmedi; redis-py'nin
  sync/async stub belirsizliği modül bazında (gerekçeli) devre dışı
  bırakıldı (bkz. `pyproject.toml`).
