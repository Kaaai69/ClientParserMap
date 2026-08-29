from pathlib import Path


def test_runtime_commands_never_resolve_dependencies() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'command: ["uv", "run", "--no-sync", "alembic", "upgrade", "head"]' in compose
    assert 'command: ["uv", "run", "--no-sync", "uvicorn"' in compose
    assert 'command: ["uv", "run", "--no-sync", "python", "-m", "app.worker"]' in compose
    assert 'CMD ["uv", "run", "--no-sync", "uvicorn"' in dockerfile
