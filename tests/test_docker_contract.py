from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
COMPOSE = PROJECT_ROOT / "compose.yaml"
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"


@pytest.mark.docker_contract
class DockerDeploymentContractTests(unittest.TestCase):
    def test_image_is_reproducible_and_runs_as_non_root(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("ghcr.io/astral-sh/uv:0.8.15", dockerfile)
        self.assertIn("uv sync --locked --no-dev --no-install-project", dockerfile)
        self.assertIn("USER app:app", dockerfile)
        self.assertIn(
            'CMD ["uvicorn", "api:api", "--host", "0.0.0.0", "--port", "8000"]',
            dockerfile,
        )

    def test_compose_defines_postgres_loader_and_healthy_app(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")

        for service in ("postgres", "loader", "app"):
            self.assertRegex(compose, rf"(?m)^  {service}:$")
        self.assertIn("image: postgres:17-alpine", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn("--if-empty", compose)
        self.assertGreaterEqual(compose.count("healthcheck:"), 2)

    def test_app_uses_only_the_readonly_database_role(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        app_block = compose.split("\n  app:\n", maxsplit=1)[1].split(
            "\nvolumes:\n", maxsplit=1
        )[0]

        self.assertIn("postgresql://agent_readonly:", app_block)
        self.assertIn("@postgres:5432/", app_block)
        self.assertNotIn("BI_MIGRATION_DATABASE_URL", app_block)
        self.assertNotRegex(
            compose,
            re.compile(r"(?i)(password|api[_-]?key)\s*:\s*[\"']?[A-Za-z0-9_-]{16,}"),
        )

    def test_build_context_excludes_local_and_generated_artifacts(self) -> None:
        ignored = set(DOCKERIGNORE.read_text(encoding="utf-8").splitlines())

        required = {
            ".git",
            ".env",
            ".env.*",
            ".venv",
            "__pycache__",
            "tests",
            "data/raw",
            "*.db",
            "*.sqlite",
            "*.zip",
            "logs",
            "build",
            "dist",
        }
        self.assertTrue(required.issubset(ignored), required - ignored)


if __name__ == "__main__":
    unittest.main()
