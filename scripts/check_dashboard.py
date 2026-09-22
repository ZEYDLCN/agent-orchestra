"""Optional browser smoke check against a running dashboard, with isolated API fixtures.

Install playwright and its Chromium browser, start the app, then run:
    python scripts/check_dashboard.py --base-url http://127.0.0.1:8000
No real jobs or workers are created. Screenshots go to workspace/ui-check/.
"""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main(base_url: str):
    output = Path(__file__).resolve().parent.parent / "workspace" / "ui-check"
    output.mkdir(parents=True, exist_ok=True)
    now = time.time()
    workers: list[dict] = []
    jobs: list[dict] = []
    submissions: list[dict] = []
    agent_runs: list[dict] = []
    failures: list[str] = []
    details: dict[str, dict] = {}
    offline = False
    auth_required = False

    def respond(route):
        nonlocal offline, auth_required
        path = route.request.url.split(base_url, 1)[-1]
        method = route.request.method
        body = None
        if (auth_required and method in ('POST', 'DELETE', 'PUT', 'PATCH')
                and route.request.headers.get('x-api-key') != 'smoke-test-key'):
            route.fulfill(status=401, json={"detail": "invalid or missing API key"})
            return
        if path.startswith('/jobs') and offline:
            route.fulfill(status=503, json={"detail": "Test connection unavailable"})
            return
        if path == '/agent-runs' and method == 'POST':
            payload = route.request.post_data_json
            current_job_id = jobs[0]['job_id'] if jobs else None
            run = {"run_id": f"run-{len(agent_runs)+1:04d}", "goal": payload['goal'],
                   "status": "completed", "stage": "final",
                   "max_rounds": payload['max_rounds'],
                   "max_tasks_per_round": payload['max_tasks_per_round'],
                   "trader_count": payload['trader_count'], "current_round": 1,
                   "current_job_id": current_job_id,
                   "job_ids": [current_job_id] if current_job_id else [],
                   "plan": {"provider": "ollama:qwen2.5:3b", "summary": "Hedef için aday parametreler planlandı.", "tasks": []},
                   "reviews": [{"provider": "ollama:qwen2.5:3b", "decision": "final", "summary": "Reviewer en iyi sonucu seçti."}],
                   "final_result": {"summary": "Reviewer en iyi sonucu seçti.", "best_result": {"score": 94.8}, "rounds": 1},
                   "error": None, "created_at": now, "updated_at": now}
            agent_runs.insert(0, run)
            body = run
        elif path.startswith('/agent-runs?'):
            body = agent_runs[:1]
        elif path.startswith('/agent-runs/'):
            run_id = path.rsplit('/', 1)[-1]
            body = next(run for run in agent_runs if run['run_id'] == run_id)
        elif path == '/workers/scale':
            count = route.request.post_data_json['count']
            for index in range(len(workers), count):
                workers.append({"worker_id": f"worker-{index+1:08d}", "pid": 4800 + index,
                                "branch": f"agent/worker-{index+1:08d}",
                                "worktree_path": f"workspace/workers/worker-{index+1:08d}", "status": "running"})
            body = workers
        elif path.startswith('/workers/') and method == 'DELETE':
            worker_id = path.rsplit('/', 1)[-1]
            workers[:] = [worker for worker in workers if worker['worker_id'] != worker_id]
            body = {"stopped": worker_id}
        elif path == '/workers':
            body = workers
        elif path == '/jobs' and method == 'POST':
            payload = route.request.post_data_json
            submissions.append(payload)
            job_id = f"a{len(submissions):031d}"
            tasks = []
            for index, task in enumerate(payload['tasks']):
                tasks.append({"task_id": f"task-{index:04d}", "job_id": job_id, "payload": task,
                              "status": "done", "assigned_worker": f"worker-{index%3+1:08d}",
                              "updated_at": now + index, "created_at": now,
                              "result": {"worker_id": f"worker-{index%3+1:08d}", "params": task['params'],
                                         "score": 94.8 - index * 2.8, "duration_ms": 12.4 + index,
                                         "llm_provider": "mock",
                                         "commentary": "[mock] Parametre kombinasyonu değerlendirildi. Sonuçları karşılaştırarak en yüksek skorlu kombinasyonu inceleyebilirsiniz."}})
            details[job_id] = {"job_id": job_id, "total": len(tasks), "done": len(tasks),
                               "failed": 0, "pending_or_running": 0, "tasks": tasks}
            jobs.insert(0, {key: value for key, value in details[job_id].items() if key != 'tasks'} | {"created_at": now})
            body = {"job_id": job_id, "task_ids": [task['task_id'] for task in tasks]}
        elif path.startswith('/jobs?'):
            body = jobs
        elif path.endswith('/events'):
            route.fulfill(content_type='text/event-stream', body='data: {"type":"task_result"}\n\n')
            return
        elif path.endswith('/cancel') and method == 'POST':
            job_id = path.split('/')[2]
            cancelled = 0
            for task in details[job_id]['tasks']:
                if task['status'] in ('pending', 'running'):
                    task['status'] = 'cancelled'
                    cancelled += 1
            details[job_id]['pending_or_running'] = 0
            for j in jobs:
                if j['job_id'] == job_id:
                    j['pending_or_running'] = 0
            body = {"job_id": job_id, "cancelled": cancelled}
        elif '/tasks/' in path and path.endswith('/retry') and method == 'POST':
            _, _, job_id, _, task_id, _ = path.split('/')
            for task in details[job_id]['tasks']:
                if task['task_id'] == task_id:
                    task['status'] = 'pending'
                    task['error'] = None
            body = {"task_id": task_id, "status": "pending"}
        elif path.endswith('/refine') and method == 'POST':
            job_id = path.split('/')[2]
            new_job_id = f"r{len(jobs)+1:031d}"
            source_tasks = details[job_id]['tasks'][:2]
            new_tasks = [{"task_id": f"refine-{index:04d}", "job_id": new_job_id, "payload": task['payload'],
                          "status": "done", "assigned_worker": task.get('assigned_worker', 'worker-00000001'),
                          "updated_at": now, "created_at": now, "result": task.get('result')}
                         for index, task in enumerate(source_tasks)]
            details[new_job_id] = {"job_id": new_job_id, "total": len(new_tasks), "done": len(new_tasks),
                                    "failed": 0, "pending_or_running": 0, "tasks": new_tasks}
            jobs.insert(0, {key: value for key, value in details[new_job_id].items() if key != 'tasks'} | {"created_at": now})
            body = {"job_id": new_job_id, "task_ids": [task['task_id'] for task in new_tasks]}
        elif path.startswith('/jobs/') and method == 'DELETE':
            job_id = path.rsplit('/', 1)[-1]
            jobs[:] = [j for j in jobs if j['job_id'] != job_id]
            removed = len(details.pop(job_id, {}).get('tasks', []))
            body = {"job_id": job_id, "tasks_removed": removed}
        elif path.startswith('/jobs/'):
            body = details[path.rsplit('/', 1)[-1]]
        elif method in ('POST', 'DELETE', 'PUT', 'PATCH'):
            # Any mutating request that falls through unmatched is a bug in
            # this fixture, not something to silently forward -- forwarding
            # it would mutate the real, live orchestrator (workers spawned,
            # jobs submitted) instead of the isolated fixture data above.
            failures.append(f'unmocked mutating request: {method} {path}')
            route.fulfill(status=500, json={"detail": f"unmocked in test fixture: {method} {path}"})
            return
        else:
            route.continue_()
            return
        route.fulfill(content_type='application/json', body=json.dumps(body))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page.on('pageerror', lambda error: failures.append(str(error)))
        # A single catch-all instead of separate '/jobs**' / '/workers**'
        # globs: Playwright's glob-to-regex translation treats a bare
        # '**' with no preceding '/' inconsistently (it can collapse to
        # single-'*' semantics, which doesn't match '/'), so
        # '{base_url}/workers**' silently failed to match
        # '{base_url}/workers/scale' and let that POST reach the real
        # server. respond()'s own path checks already discriminate
        # precisely, so one origin-wide route is both simpler and correct.
        page.route(f'{base_url}/**', respond)
        response = page.goto(f'{base_url}/dashboard')
        assert response is not None and response.status == 200
        expect(page.locator('#jobList')).to_contain_text('Henüz bir çalışma yok')
        expect(page.locator('#connectionText')).to_have_text('Bağlı')
        assert page.locator('.workbench').evaluate("node => getComputedStyle(node).display") == 'grid'
        page.screenshot(path=str(output / 'desktop-empty.png'), full_page=True)

        page.locator('.insights-panel [data-open-workers]').click()
        page.locator('#scaleInput').fill('3')
        page.locator('#scaleButton').click()
        expect(page.locator('#workersList .worker-row')).to_have_count(3)
        page.locator('#workersDialog [data-close-dialog]').click()
        page.locator('.page-heading [data-new-job]').click()
        page.locator('#fastParams').fill('0, invalid')
        page.locator('#submitJobButton').click()
        expect(page.locator('#jobFormError')).to_contain_text('pozitif tam sayılar')
        assert not submissions
        page.locator('#fastParams').fill('5, 10, 20')
        page.locator('#submitJobButton').click()
        expect(page.locator('#jobDone')).to_have_text('9')
        assert len(submissions) == 1 and len(submissions[0]['tasks']) == 9
        expect(page.locator('#topResults .result-row')).to_have_count(3)
        expect(page.locator('#progressPercent')).to_have_text('100%')
        expect(page.locator('#insightProvider')).to_contain_text('Mock sağlayıcı')
        page.locator('#newAgentRunButton').click()
        page.locator('#agentGoal').fill('En iyi hareketli ortalama parametrelerini bul ve sonucu değerlendir.')
        page.locator('#agentMaxTasks').fill('2')
        page.locator('#startAgentRunButton').click()
        expect(page.locator('#agentRunPanel')).to_be_visible()
        expect(page.locator('#agentRunPanel')).to_contain_text('Agent akışı tamamlandı')
        expect(page.locator('#agentRunPanel')).to_contain_text('ollama:qwen2.5:3b')
        page.screenshot(path=str(output / 'desktop-results.png'), full_page=True)

        page.locator('.view-tab[data-view=results]').click()
        expect(page.locator('#allResults .result-row')).to_have_count(9)
        page.locator('#resultSort').select_option('duration')
        best_result = details[jobs[0]['job_id']]['tasks'][0]['result']
        original_commentary = best_result['commentary']
        best_result['commentary'] += '\n<img src=x onerror=alert(1)>'
        page.locator('#refreshButton').click()
        expect(page.locator('#insightText')).to_contain_text('<img src=x onerror=alert(1)>')
        page.locator('#allResults .result-row').first.click()
        expect(page.locator('#resultDetail')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator('#resultDetail img')).to_have_count(0)
        page.locator('#resultDialog [data-close-dialog]').click()
        best_result['commentary'] = original_commentary
        page.locator('#refreshButton').click()
        expect(page.locator('#insightText')).not_to_contain_text('<img')
        expect(page.locator('#stepReview')).to_have_class('done')
        page.locator('.view-tab[data-view=activity]').click()
        expect(page.locator('.activity-item')).to_have_count(9)
        page.locator('#jobSearch').fill('missing-job')
        expect(page.locator('#jobList')).to_contain_text('Eşleşen iş bulunamadı')
        page.locator('#jobSearch').fill('')
        page.locator('[data-filter=active]').click()
        expect(page.locator('#jobList')).to_contain_text('Eşleşen iş bulunamadı')
        page.locator('[data-filter=completed]').click()
        expect(page.locator('.job-item')).to_have_count(1)
        page.locator('[data-filter=all]').click()
        page.locator('.view-tab[data-view=overview]').click()

        for width in (1440, 1024, 768, 320, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Page overflow at {width}'
            overflowing = page.locator('.workbench, .detail-panel, .insights-panel').evaluate_all('(nodes) => nodes.filter(n => n.scrollWidth > n.clientWidth + 1).map(n => n.className)')
            assert not overflowing, f'Panel overflow at {width}: {overflowing}'
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        page.locator('#themeButton').click()
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
        page.set_viewport_size({"width": 1440, "height": 1050})
        page.screenshot(path=str(output / 'desktop-dark.png'), full_page=True)
        page.reload()
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
        expect(page.locator('#jobDone')).to_have_text('9')
        page.locator('#themeButton').click()

        # A failed task remains visible even when no event history is available.
        job_id = jobs[0]['job_id']
        details[job_id]['tasks'][0].update(status='failed', result=None, error='Strategy execution failed')
        details[job_id].update(done=8, failed=1)
        jobs[0].update(done=8, failed=1)
        page.locator('#refreshButton').click()
        expect(page.locator('#jobFailed')).to_have_text('1')
        page.locator('.view-tab[data-view=activity]').click()
        expect(page.locator('#activityList')).to_contain_text('Strategy execution failed')

        # An active job uses SSE as a refresh signal without duplicating snapshots.
        details[job_id]['tasks'][1].update(status='running', result=None)
        details[job_id].update(done=7, pending_or_running=1)
        jobs[0].update(done=7, pending_or_running=1)
        page.locator('#refreshButton').click()
        expect(page.locator('#jobPending')).to_have_text('1')
        expect(page.locator('#resultCount')).to_have_text('7')

        offline = True
        page.locator('#refreshButton').click()
        expect(page.locator('#connectionText')).to_have_text('Bağlantı sorunu')
        offline = False
        page.locator('#refreshButton').click()
        expect(page.locator('#connectionText')).to_have_text('Bağlı')
        page.locator('.insights-panel [data-open-workers]').click()
        page.locator('[data-stop-worker]').first.click()
        expect(page.locator('#workersList .worker-row')).to_have_count(2)
        page.keyboard.press('Escape')
        expect(page.locator('#workersDialog')).not_to_be_visible()

        page.locator('.page-heading [data-new-job]').click()
        page.locator('#fastParams').fill('7')
        page.locator('#slowParams').fill('70, 140')
        page.locator('#submitJobButton').click()
        expect(page.locator('#jobDone')).to_have_text('2')
        expect(page.locator('.job-item')).to_have_count(2)
        page.locator(f'[data-job-id="{job_id}"]').click()
        expect(page.locator('#jobDone')).to_have_text('7')
        expect(page.locator('#resultCount')).to_have_text('7')

        # --- P2: templates (save, apply, delete) ---
        page.locator('.page-heading [data-new-job]').click()
        page.locator('#fastParams').fill('11, 22')
        page.locator('#slowParams').fill('33, 44')
        page.locator('#templateName').fill('smoke-template')
        page.locator('#saveTemplateButton').click()
        page.locator('#fastParams').fill('1')
        page.locator('#templateSelect').select_option('smoke-template')
        expect(page.locator('#fastParams')).to_have_value('11, 22')
        page.locator('#deleteTemplateButton').click()
        expect(page.locator('#templateSelect')).to_have_value('')
        page.locator('#newJobDialog [data-close-dialog]').first.click()

        # --- P2: export JSON/CSV ---
        with page.expect_download() as download_info:
            page.locator('#exportJsonButton').click()
        assert download_info.value.suggested_filename.endswith('.json')
        with page.expect_download() as download_info:
            page.locator('#exportCsvButton').click()
        assert download_info.value.suggested_filename.endswith('.csv')

        # --- P2: rerun submits a fresh job with the same params ---
        jobs_before = len(jobs)
        page.locator('#rerunJobButton').click()
        expect(page.locator('#jobCount')).to_have_text(str(jobs_before + 1))

        # --- P2: refine submits a follow-up job ---
        jobs_before = len(jobs)
        page.locator('#refineJobButton').click()
        expect(page.locator('#jobCount')).to_have_text(str(jobs_before + 1))

        # --- P2: cancel hides/shows correctly and clears pending count ---
        cancel_job_id = jobs[0]['job_id']
        details[cancel_job_id]['tasks'][0]['status'] = 'pending'
        details[cancel_job_id]['pending_or_running'] = 1
        jobs[0]['pending_or_running'] = 1
        page.locator(f'[data-job-id="{cancel_job_id}"]').click()
        expect(page.locator('#cancelJobButton')).to_be_visible()
        page.locator('#cancelJobButton').click()
        expect(page.locator('#jobPending')).to_have_text('0')

        # --- P2: retry re-queues a failed task ---
        page.locator(f'[data-job-id="{job_id}"]').click()
        page.locator('.view-tab[data-view=activity]').click()
        expect(page.locator('[data-retry-task]')).to_have_count(1)
        page.locator('[data-retry-task]').click()
        expect(page.locator('[data-retry-task]')).to_have_count(0)

        # --- P2: API key stored/cleared locally, never sent unless present ---
        page.locator('#apiKeyButton').click()
        page.locator('#apiKeyInput').fill('smoke-test-key')
        page.locator('#apiKeyDialog button[type=submit]').click()
        assert page.evaluate("localStorage.getItem('agentos-api-key')") == 'smoke-test-key'
        auth_required = True
        authorized_count = page.evaluate("""async () => {
          const workers = await api('/workers/scale', {method:'POST', body:JSON.stringify({count:3})});
          return workers.length;
        }""")
        assert authorized_count == 3
        page.locator('#apiKeyButton').click()
        page.locator('#clearApiKeyButton').click()
        assert page.evaluate("localStorage.getItem('agentos-api-key')") is None
        unauthorized_message = page.evaluate("""async () => {
          try {
            await api('/workers/scale', {method:'POST', body:JSON.stringify({count:3})});
            return '';
          } catch (error) { return error.message; }
        }""")
        assert 'API anahtarı' in unauthorized_message
        auth_required = False
        page.locator('#apiKeyDialog [data-close-dialog]').click()

        # --- P2: delete removes the job from history ---
        page.on('dialog', lambda dialog: dialog.accept())
        jobs_before = len(jobs)
        page.locator('#deleteJobButton').click()
        expect(page.locator('#jobCount')).to_have_text(str(jobs_before - 1))

        assert not failures, failures
        browser.close()
    print(f'Dashboard browser checks passed. Screenshots: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    main(parser.parse_args().base_url.rstrip('/'))
