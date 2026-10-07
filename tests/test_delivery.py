from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFESTS = ROOT / "deploy" / "kubernetes"


def test_ci_blocks_the_image_until_the_evaluation_gate_passes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    quality = workflow.split("container:", 1)[0]
    container = workflow.split("container:", 1)[1]

    assert "python -m evals.run evals/cases/v1.json" in quality
    assert "--cov-fail-under=85" in quality
    assert "needs: [quality]" in container


def test_kubernetes_manifests_match_the_compose_processes() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MANIFESTS.glob("*.yaml"))
    )

    for kind in (
        "kind: Namespace",
        "kind: ConfigMap",
        "kind: Secret",
        "kind: PersistentVolumeClaim",
        "kind: Deployment",
        "kind: Service",
        "kind: Job",
        "kind: Kustomization",
    ):
        assert kind in text
    for command in (
        'command: ["alembic", "upgrade", "head"]',
        'command: ["python", "-m", "app.worker"]',
        'command: ["python", "-m", "app.reaper"]',
        "path: /health",
        "path: /ready",
        "COMPLIANCE_MODEL_PROVIDER: deterministic",
        "image: ai-compliance-agent:local",
        "image: pgvector/pgvector:pg17",
    ):
        assert command in text
    assert "notifier" not in text
    assert "ollama" not in text
