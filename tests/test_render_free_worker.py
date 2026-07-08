from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_render_free_starts_embedded_worker_after_migrations() -> None:
    script = (REPO / "start_render_free.sh").read_text(encoding="utf-8")
    assert "alembic upgrade heads" in script
    assert "python -m arq app.worker.WorkerSettings &" in script
    assert "uvicorn app.main:app" in script
    assert script.index("alembic upgrade heads") < script.index("python -m arq app.worker.WorkerSettings &")
    assert script.index("python -m arq app.worker.WorkerSettings &") < script.index("uvicorn app.main:app")
    assert "embedded ARQ worker exited; stopping web process" in script


def test_render_blueprint_uses_single_free_web_service_with_embedded_worker() -> None:
    render = (REPO / "render.yaml").read_text(encoding="utf-8")
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "startCommand: /app/start_render_free.sh" in render
    assert "START_EMBEDDED_WORKER" in render
    assert "type: worker" not in render
    assert "chmod +x /app/start.sh /app/start_render_free.sh" in dockerfile
