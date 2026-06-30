from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from pyfuzz import paths

from pfui.artifact_service import FILE_PREVIEW_TEXT_LIMIT, _file_payload, parse_group_spec, validate_local_filename
from pfui.input_service import (
    contained_input_file,
    delete_input_file,
    input_file_payload,
    input_tree,
    update_input_file,
)
from pfui.project_service import create_project, find_default_project, update_project_config
from pfui.protocol import ProtocolError, RequestContext, RequestEnvelope, Router
from pfui.tasks import TaskManager
from pyfuzz.project import Project


class ProjectResolutionTests(unittest.TestCase):
    def test_nearest_default_project_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = root / "one" / "two"
            child.mkdir(parents=True)
            (root / ".pyfuzz_project").write_text("outer\n")
            (root / "one" / ".pyfuzz_project").write_text("inner\n")
            self.assertEqual(find_default_project(child), "inner")

    def test_missing_default_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(find_default_project(Path(temporary)))


class ProjectManagementTests(unittest.TestCase):
    def test_create_rejects_unsafe_names(self) -> None:
        for name in ("../escape", "has space", ".hidden", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                create_project(name)

    def test_create_and_update_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(paths, "PROJECT_ROOT", Path(temporary) / "projects"):
            project = create_project("new-project")
            self.assertEqual(project.name, "new-project")
            updated = update_project_config(project, '{"repo": "python/cpython", "asan": true,}')
            self.assertTrue(updated.asan)
            self.assertEqual(json.loads(updated.config_path.read_text()), {"asan": True})
            with self.assertRaisesRegex(ValueError, "Unknown project configuration field"):
                update_project_config(updated, '{"not_a_setting": true}')


class ArtifactValidationTests(unittest.TestCase):
    def test_group_specs(self) -> None:
        self.assertEqual(parse_group_spec("type").kind, "type")
        self.assertEqual(parse_group_spec("meta:worker").argument, "worker")
        self.assertEqual(parse_group_spec("file:analysis.txt").argument, "analysis.txt")

    def test_local_filename_rejects_traversal(self) -> None:
        for value in ("../secret", "/tmp/secret", "dir/file", "dir\\file", ".", ".."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_local_filename(value)

    def test_file_preview_detects_binary_and_caps_long_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "binary"
            binary.write_bytes(b"hello\x00world")
            self.assertTrue(_file_payload(binary, None, Project())["isBinary"])
            long_text = Path(temporary) / "long.txt"
            long_text.write_text("x" * (FILE_PREVIEW_TEXT_LIMIT + 100))
            payload = _file_payload(long_text, None, Project())
            self.assertEqual(len(payload["preview"]), FILE_PREVIEW_TEXT_LIMIT)
            self.assertFalse(payload["previewComplete"])


class InputServiceTests(unittest.TestCase):
    def test_tree_lists_inputs_directory_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(paths, "PROJECT_ROOT", Path(temporary) / "projects"):
            project = create_project("alpha")
            inputs = project.path("inputs")
            (inputs / "beta").mkdir()
            (inputs / "beta" / "input_2.txt").write_text("two")
            (inputs / "alpha").mkdir()
            (inputs / "alpha" / "nested").mkdir()
            (inputs / "alpha" / "nested" / "input.bin").write_bytes(b"\x00")
            (inputs / "root.txt").write_text("root")

            tree = input_tree(project)
            self.assertEqual([node["name"] for node in tree], ["alpha", "beta", "root.txt"])
            self.assertEqual(tree[0]["children"][0]["name"], "nested")
            self.assertEqual(tree[0]["children"][0]["children"][0]["path"], "alpha/nested/input.bin")

    def test_paths_stay_under_inputs_and_must_be_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(paths, "PROJECT_ROOT", Path(temporary) / "projects"):
            project = create_project("alpha")
            (project.path("inputs") / "seed").mkdir()
            (project.path("inputs") / "seed" / "case.txt").write_text("case")

            self.assertEqual(contained_input_file(project, "seed/case.txt").name, "case.txt")
            for value in ("../secret", "/tmp/secret", "seed\\case.txt", "seed/../case.txt", "seed"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    contained_input_file(project, value)

    def test_read_update_and_delete_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(paths, "PROJECT_ROOT", Path(temporary) / "projects"):
            project = create_project("alpha")
            seed = project.path("inputs") / "seed"
            seed.mkdir()
            path = seed / "case.txt"
            path.write_bytes(b"line\n\x00\\\xff")

            payload = input_file_payload(project, "seed/case.txt")
            self.assertTrue(payload["content"].isascii())
            self.assertIn("line\n", payload["content"])
            self.assertNotIn("\\n", payload["content"])
            self.assertIn("\\x00", payload["content"])
            self.assertIn("\\xff", payload["content"])

            updated = update_input_file(project, "seed/case.txt", "next\n\\x00\\\\")
            self.assertEqual(updated["content"], "next\n\\x00\\\\")
            self.assertEqual(path.read_bytes(), b"next\n\x00\\")

            tree = delete_input_file(project, "seed/case.txt")
            self.assertFalse(path.exists())
            self.assertTrue(seed.exists())
            self.assertEqual(tree, [{"path": "seed", "name": "seed", "kind": "directory", "children": []}])


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_method_has_stable_error_code(self) -> None:
        request = RequestEnvelope(id="one", method="missing.method", params={})
        with self.assertRaises(ProtocolError) as raised:
            await Router().dispatch(RequestContext(tasks=None), request)
        self.assertEqual(raised.exception.code, "method_not_found")

    def test_envelope_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            RequestEnvelope.model_validate({"id": "one", "method": "projects.list", "params": {}, "extra": True})


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[dict] = []

        async def broadcast(event: dict) -> None:
            self.events.append(event)

        self.manager = TaskManager(broadcast)

    async def asyncTearDown(self) -> None:
        await self.manager.close()

    async def test_exclusive_task_and_cancellation(self) -> None:
        first = self.manager.start("fuzz", "fuzz", "alpha", asyncio.sleep(10), exclusive_key="fuzz:alpha")
        duplicate = asyncio.sleep(10)
        with self.assertRaises(ValueError):
            self.manager.start("fuzz", "fuzz", "alpha", duplicate, exclusive_key="fuzz:alpha")
        self.assertTrue((await self.manager.stop(first.id))["stopped"])
        await asyncio.sleep(0)
        self.assertEqual(first.status, "cancelled")


class AppIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        try:
            from aiohttp.test_utils import TestClient, TestServer
        except ImportError as exc:
            self.skipTest(str(exc))
        from pfui.app import create_app

        self.temporary = tempfile.TemporaryDirectory()
        project_root = Path(self.temporary.name) / "projects"
        config = project_root / "alpha" / "config" / "project.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({}))
        (project_root / "alpha" / "inputs").mkdir(parents=True)
        self.paths_patch = patch.object(paths, "PROJECT_ROOT", project_root)
        self.paths_patch.start()
        self.client = TestClient(TestServer(create_app(initial_project="alpha")))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        if hasattr(self, "client"):
            await self.client.close()
            self.paths_patch.stop()
            self.temporary.cleanup()

    async def test_health_and_websocket_protocol(self) -> None:
        response = await self.client.get("/health")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["service"], "pfui")
        index = await self.client.get("/")
        self.assertEqual(index.status, 200)
        self.assertIn("<title>PFUI</title>", await index.text())

        socket = await self.client.ws_connect("/ws")
        ready = await socket.receive_json()
        self.assertEqual(ready["event"], "session.ready")
        self.assertEqual(ready["data"]["defaultProject"], "alpha")
        await socket.send_json({"id": "one", "method": "projects.list", "params": {}})
        result = await socket.receive_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["projects"], ["alpha"])
        await socket.send_json({"id": "two", "method": "project.get", "params": {}})
        failure = await socket.receive_json()
        self.assertEqual(failure["error"]["code"], "project_required")
        await socket.send_json({"id": "three", "method": "project.get", "project": "alpha", "params": {}})
        project = await socket.receive_json()
        self.assertEqual(project["project"], "alpha")
        self.assertEqual(project["result"]["project"]["name"], "alpha")
        await socket.close()

    async def test_websocket_input_editing(self) -> None:
        root = paths.PROJECT_ROOT / "alpha" / "inputs" / "seed"
        root.mkdir()
        (root / "case.txt").write_bytes(b"hello\n")

        socket = await self.client.ws_connect("/ws")
        await socket.receive_json()

        await socket.send_json({"id": "list", "method": "inputs.list", "project": "alpha", "params": {}})
        listed = await socket.receive_json()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["result"]["tree"][0]["children"][0]["path"], "seed/case.txt")

        await socket.send_json({"id": "read", "method": "input.read", "project": "alpha", "params": {"path": "seed/case.txt"}})
        read = await socket.receive_json()
        self.assertEqual(read["result"]["content"], "hello\n")

        await socket.send_json({"id": "update", "method": "input.update", "project": "alpha", "params": {"path": "seed/case.txt", "content": "bye\n"}})
        updated = await socket.receive_json()
        self.assertTrue(updated["ok"])
        self.assertEqual((root / "case.txt").read_bytes(), b"bye\n")

        await socket.send_json({"id": "delete", "method": "input.delete", "project": "alpha", "params": {"path": "seed/case.txt"}})
        deleted = await socket.receive_json()
        self.assertTrue(deleted["ok"])
        self.assertFalse((root / "case.txt").exists())
        self.assertTrue(root.exists())
        await socket.close()

    async def test_task_updates_are_broadcast_to_all_clients(self) -> None:
        from pfui.app import TASKS

        first = await self.client.ws_connect("/ws")
        second = await self.client.ws_connect("/ws")
        await first.receive_json()
        await second.receive_json()
        manager = self.client.server.app[TASKS]
        manager.start("brief", "test", "alpha", asyncio.sleep(0.05))
        first_event, second_event = await asyncio.gather(first.receive_json(), second.receive_json())
        self.assertEqual(first_event["event"], "tasks.changed")
        self.assertEqual(second_event["event"], "tasks.changed")
        self.assertEqual(first_event["data"]["tasks"][0]["project"], "alpha")
        await first.close()
        await second.close()

    async def test_rejects_cross_origin_websocket(self) -> None:
        with self.assertRaises(Exception):
            await self.client.ws_connect("/ws", headers={"Origin": "http://attacker.invalid"})


if __name__ == "__main__":
    unittest.main()
