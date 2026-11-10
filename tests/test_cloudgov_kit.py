from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "deploy" / "cloudgov"
SPEC = importlib.util.spec_from_file_location("cloudgov_verify", KIT / "verify.py")
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class FakeClient:
    def __init__(self, *, empty_search: bool = False, tool_error: bool = False) -> None:
        self.empty_search = empty_search
        self.tool_error = tool_error

    async def list_tools(self):
        return [SimpleNamespace(name=name) for name in verify.TOOLS]

    async def call_tool(self, name, arguments):
        responses = {
            "catalog_facets": {"portfolios": [{"value": "Test", "count": 1}]},
            "list_solutions": {"total": 1, "solutions": [{"uuid": "test-id"}]},
            "search_solutions": [] if self.empty_search else [{"uuid": "test-id"}],
            "get_solution": {"uuid": "test-id"},
            "search": {"results": [{"id": "test-id"}]},
            "fetch": {"id": "test-id", "text": "Public test record"},
        }
        return SimpleNamespace(
            is_error=self.tool_error, structured_content=responses[name], content=[]
        )


class CloudgovKitTests(unittest.TestCase):
    def run_publish_script(
        self, project: str, docker_exit: int = 0, flags: tuple[str, ...] = (), cache: bool = False
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            kit = root / "deploy" / "cloudgov"
            kit.mkdir(parents=True)
            script = kit / "build-and-push.sh"
            script.write_text((KIT / "build-and-push.sh").read_text())
            if cache:
                (kit / "build" / "models").mkdir(parents=True)
            (root / "pyproject.toml").write_text(project)
            binaries = root / "bin"
            binaries.mkdir()
            log = root / "docker-args"
            docker = binaries / "docker"
            docker.write_text(
                '#!/bin/bash\npwd > "$TEST_ROOT_LOG"\n'
                'printf "%s\\n" "$@" > "$TEST_DOCKER_LOG"\n'
                f"exit {docker_exit}\n"
            )
            git = binaries / "git"
            git.write_text("#!/bin/bash\nprintf 'test-revision\\n'\n")
            docker.chmod(0o755)
            git.chmod(0o755)
            root_log = root / "working-directory"
            env = {
                **os.environ,
                "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
                "TEST_DOCKER_LOG": str(log),
                "TEST_ROOT_LOG": str(root_log),
                "DIU_LOCAL_MODEL_CACHE": str(kit / "build" / "models"),
            }
            result = subprocess.run(
                ["bash", str(script), *flags], cwd=binaries, env=env,
                capture_output=True, text=True, timeout=10, check=False,
            )
            arguments = log.read_text().splitlines() if log.exists() else []
            correct_root = root_log.exists() and root_log.read_text().strip() == str(root)
            return result, arguments, correct_root

    def test_publish_version_tags_platform_and_repository_root(self) -> None:
        result, arguments, correct_root = self.run_publish_script(
            '[tool.example]\nversion = "9.9.9"\n[project]\nversion = "1.2.3"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(correct_root)
        self.assertEqual(arguments[:4], ["buildx", "build", "--platform", "linux/amd64"])
        image = "ghcr.io/gsa-tts/mcp-diu-solutions-catalog"
        self.assertIn(f"{image}:1.2.3", arguments)
        self.assertIn(f"{image}:latest", arguments)
        self.assertIn("org.opencontainers.image.revision=test-revision", arguments)
        self.assertIn("org.opencontainers.image.version=1.2.3", arguments)
        self.assertEqual(arguments[-2:], ["--push", "."])

    def test_publish_rejects_missing_or_invalid_version(self) -> None:
        for project in ('[project]\nname = "example"\n', '[project]\nversion = "bad/tag"\n'):
            with self.subTest(project=project):
                result, arguments, _ = self.run_publish_script(project)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FATAL", result.stderr)
                self.assertEqual(arguments, [])

    def test_publish_stops_on_docker_failure(self) -> None:
        result, _, _ = self.run_publish_script('[project]\nversion = "1.2.3"\n', docker_exit=7)
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("Pushed ", result.stdout)

    def test_local_model_load_without_publish(self) -> None:
        result, arguments, _ = self.run_publish_script(
            '[project]\nversion = "1.2.3"\n', flags=("--local-model", "--load"), cache=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deploy/cloudgov/Dockerfile.local-model", arguments)
        self.assertTrue(any(arg.startswith("local_models=") for arg in arguments))
        self.assertEqual(arguments[-2:], ["--load", "."])
        self.assertNotIn("--push", arguments)
        self.assertNotIn("Pushed ", result.stdout)

    def test_local_model_publish_and_missing_cache(self) -> None:
        project = '[project]\nversion = "1.2.3"\n'
        result, arguments, _ = self.run_publish_script(
            project, flags=("--local-model",), cache=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--push", arguments)
        result, arguments, _ = self.run_publish_script(project, flags=("--local-model",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cache directory is missing", result.stderr)
        self.assertEqual(arguments, [])

    def test_unknown_build_option_fails_closed(self) -> None:
        result, arguments, _ = self.run_publish_script(
            '[project]\nversion = "1.2.3"\n', flags=("--unknown",)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(arguments, [])

    def test_local_model_dockerfile_tracks_root(self) -> None:
        root = (ROOT / "Dockerfile").read_text()
        local = (KIT / "Dockerfile.local-model").read_text()
        self.assertIn("RUN --network=none .venv/bin/diu-catalog-ingest", local)
        self.assertEqual(local.count("    HF_HUB_OFFLINE=1"), 2)
        # Only cache import, offline flags, ingestion network, and comments differ.
        normalized = "\n".join(
            line for line in local.splitlines()
            if not line.startswith("#")
            and "COPY --from=local_models" not in line
            and "HF_HUB_OFFLINE=1" not in line
        ) + "\n"
        normalized = normalized.replace("RUN --network=none ", "RUN ")
        normalized = normalized.replace("    HF_HUB_DISABLE_XET=1 \\\n\n", "    HF_HUB_DISABLE_XET=1\n\n")
        self.assertEqual(normalized, root)

    def test_manifest_reuses_image_and_internal_route(self) -> None:
        text = (KIT / "manifest.yml").read_text()
        self.assertIn("image: ((image))", text)
        self.assertIn("((internal_hostname)).apps.internal", text)
        self.assertIn("instances: 1", text)
        self.assertIn("health-check-http-endpoint: /health", text)
        self.assertNotIn("app.cloud.gov", text)
        self.assertNotIn("buildpacks:", text)
        self.assertNotIn("      PORT:", text)

    def test_local_values_and_outputs_are_ignored(self) -> None:
        text = (KIT / ".gitignore").read_text()
        self.assertIn("*.local.yml", text)
        self.assertIn("verification*.json", text)

    def test_urls(self) -> None:
        for url in (
            "http://localhost:8001/mcp", "http://127.0.0.1:8001/mcp",
            "http://example.apps.internal:8001/mcp", "https://example.com/mcp-connect/id",
        ):
            self.assertEqual(verify.validate_url(url), url)
        for url in (
            "http://example.com/mcp", "https://user:secret@example.com/mcp",
            "https://example.com/mcp?token=secret", "https://example.com/mcp#fragment",
            "file:///data/index", "https:///mcp",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                verify.validate_url(url)

    def test_json_response_fallback_and_result_wrapper(self) -> None:
        result = SimpleNamespace(
            is_error=False, structured_content=None,
            content=[SimpleNamespace(type="text", text='{"id": "test-id"}')],
        )
        self.assertEqual(verify.payload(result), {"id": "test-id"})
        result.structured_content = {"result": [{"uuid": "test-id"}]}
        self.assertEqual(verify.payload(result), [{"uuid": "test-id"}])
        result.structured_content = None
        result.content = []
        with self.assertRaises(ValueError):
            verify.payload(result)

    def test_six_tools_without_administrative_status(self) -> None:
        result = asyncio.run(verify.verify_tools(FakeClient(), "communications"))
        self.assertEqual(result["catalog_records"], 1)
        self.assertEqual(set(result["tools"]), verify.TOOLS)
        self.assertNotIn("catalog_status", result["tools"])

    def test_search_and_tool_failures(self) -> None:
        with self.assertRaises(ValueError):
            asyncio.run(verify.verify_tools(FakeClient(empty_search=True), "query"))
        with self.assertRaises(RuntimeError):
            asyncio.run(verify.verify_tools(FakeClient(tool_error=True), "query"))


if __name__ == "__main__":
    unittest.main()
