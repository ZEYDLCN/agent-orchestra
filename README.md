# Agent Orchestration Tool

Merkezi bir orchestrator'ın, git worktree ile izole edilmiş worker process'lere
görev dağıttığı, Redis kuyruğu üzerinden koordine olan bir çoklu-agent
orkestrasyon altyapısı. Demo senaryosu: bir trading stratejisinin parametre
grid'ini worker'lara dağıtıp sonuçları karşılaştırma (clustered backtest).

## Mimari (Day 1)

```
FastAPI orchestrator --push--> Redis queue --pop--> Worker process
        |                                                |
        +--> her worker'a ayrı git worktree (target_repo/) tahsis eder
```

- `orchestrator/` — FastAPI servisi: iş kabul eder, worker spawn/scale eder, sonuç toplar
- `worker/` — worker process: kuyruktan görev çeker, kendi worktree'sinde çalıştırır
- `target_repo/` — worker'ların worktree aldığı örnek "çalışma kod tabanı" (sample strategy.py)
- `scripts/demo.py` — uçtan uca demo client

Sonraki günlerde eklenecek: multi-LLM, inter-agent mesajlaşma (pub/sub),
RAG/shared memory, MCP entegrasyonu, gerçek multi-machine clustering.

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

Terminal 2 — demo (5 worker spawn eder, parametre grid'i dağıtır, sonuçları
gösterir):

```powershell
python scripts/demo.py --workers 5
```

## API

- `POST /workers/scale {"count": 5}` — worker sayısını N'e tamamla
- `GET /workers` — aktif worker listesi
- `DELETE /workers/{id}` — worker'ı durdur, worktree'sini temizle
- `POST /jobs {"tasks": [{"params": {...}}, ...]}` — iş gönder, task'lara böl
- `GET /jobs/{job_id}` — iş durumu ve sonuçları

## Test

```powershell
pip install -r requirements-dev.txt
pytest
```
