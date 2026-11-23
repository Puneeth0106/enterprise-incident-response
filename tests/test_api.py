import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app as api
from src.config import Settings
from src.uploads import ingest_upload


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.settings = Settings(database_path=str(self.root / "audit.db"), max_upload_bytes=8)
        for target, value in [
            ("src.db.get_settings", lambda: self.settings),
            ("src.uploads.get_settings", lambda: self.settings),
            ("app.UPLOADS", self.uploads),
            ("app.namespace", lambda: "test-runbooks"),
        ]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ingest = self.enterContext(patch("app.ingest_file", return_value=2))
        self.graph = self.enterContext(patch("app.run_self_rag", return_value={
            "answer": "Check the deployment", "route": "Private Runbooks",
            "used_web_search": False, "thread_id": "incident-1",
            "trace": ["retrieved"], "sources": [],
        }))
        self.client = self.enterContext(TestClient(api.app))

    def upload(self, data=b"runbook", name="runbook.md"):
        return self.client.post("/api/upload", files={"file": (name, data)})

    def test_home_static_and_health(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        self.graph.assert_not_called()
        self.ingest.assert_not_called()

    def test_upload_preserves_basename_and_removes_staging(self):
        seen = []
        def ingest(path):
            seen.append(path)
            self.assertEqual(path.read_bytes(), b"runbook")
            self.assertEqual(path.name, "runbook.MD")
            self.assertEqual(path.parent.parent, self.uploads)
            return 2
        self.ingest.side_effect = ingest
        response = self.upload(name="../../runbook.MD")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "filename": "runbook.MD", "chunks_indexed": 2, "namespace": "test-runbooks",
        })
        self.assertFalse(seen[0].exists())
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_windows_filename_is_reduced_to_basename(self):
        response = self.upload(name="C:\\notes\\runbook.md")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["filename"], "runbook.md")

    def test_unsupported_upload_never_ingests(self):
        self.assertEqual(self.upload(name="runbook.exe").status_code, 400)
        self.ingest.assert_not_called()
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_empty_upload_never_ingests_and_cleans_staging(self):
        self.assertEqual(self.upload(data=b"").status_code, 400)
        self.ingest.assert_not_called()
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_exact_limit_is_accepted(self):
        self.assertEqual(self.upload(data=b"12345678").status_code, 200)

    def test_limit_plus_one_is_rejected_and_cleaned(self):
        self.assertEqual(self.upload(data=b"123456789").status_code, 413)
        self.ingest.assert_not_called()
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_failure_cleans_staging_and_hides_provider_error(self):
        self.ingest.side_effect = RuntimeError("provider-private-detail")
        with self.assertLogs("app", level="ERROR"):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to ingest the document"})
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_existing_document_is_never_overwritten(self):
        existing = self.uploads / "runbook.md"
        existing.write_bytes(b"original")
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(existing.read_bytes(), b"original")
        self.assertEqual(list(self.uploads.iterdir()), [existing])

    def test_same_name_concurrent_uploads_have_distinct_paths(self):
        barrier = threading.Barrier(2)
        def ingest(path):
            barrier.wait(timeout=5)
            return path, path.read_bytes()
        def stage(content):
            return ingest_upload(io.BytesIO(content), "same.md", self.uploads, ingest)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(stage, content) for content in [b"first", b"second"]]
            results = [future.result(timeout=10)[1] for future in futures]
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual([result[1] for result in results], [b"first", b"second"])
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_invalid_names_never_ingest(self):
        for name in [None, "", "dir/", "nul\x00.md", "a" * 241 + ".md"]:
            with self.subTest(name=name):
                with self.assertRaises(api.HTTPException) as raised:
                    ingest_upload(io.BytesIO(b"text"), name, self.uploads, self.ingest)
                self.assertEqual(raised.exception.status_code, 400)
        self.ingest.assert_not_called()

    def test_stream_failure_removes_partial_staging(self):
        stream = unittest.mock.Mock()
        stream.read.side_effect = [b"part", OSError("read failed")]
        with self.assertRaises(OSError):
            ingest_upload(stream, "runbook.md", self.uploads, self.ingest)
        self.assertEqual(list(self.uploads.iterdir()), [])
        self.ingest.assert_not_called()

    def test_chat_normalizes_before_validation_and_audit(self):
        response = self.client.post("/api/chat", json={
            "question": "  What failed?  ", "thread_id": "  incident-1  ",
        })
        self.assertEqual(response.status_code, 200)
        self.graph.assert_called_once_with("What failed?", "incident-1")
        audits = self.client.get("/api/audits").json()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["question"], "What failed?")
        self.assertEqual(audits[0]["answer"], "Check the deployment")

    def test_blank_or_short_chat_values_do_not_call_graph(self):
        for question, thread in [("   ", "valid"), (" x ", "valid"), ("valid", "   "), ("valid", " a ")]:
            with self.subTest(question=question, thread=thread):
                response = self.client.post("/api/chat", json={"question": question, "thread_id": thread})
                self.assertEqual(response.status_code, 422)
        self.graph.assert_not_called()
        self.assertEqual(self.client.get("/api/audits").json(), [])

    def test_chat_failure_does_not_write_audit_or_expose_error(self):
        self.graph.side_effect = RuntimeError("provider-private-detail")
        with self.assertLogs("app", level="ERROR"):
            response = self.client.post("/api/chat", json={"question": "What failed?", "thread_id": "incident-1"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to process the question"})
        self.assertEqual(self.client.get("/api/audits").json(), [])

    def test_invalid_graph_response_is_not_audited(self):
        self.graph.return_value = {"answer": "incomplete"}
        with self.assertLogs("app", level="ERROR"):
            response = self.client.post("/api/chat", json={"question": "What failed?", "thread_id": "incident-1"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get("/api/audits").json(), [])

    def test_audit_failure_returns_generic_error(self):
        with patch("app.save_audit", side_effect=RuntimeError("private-db-path")):
            with self.assertLogs("app", level="ERROR"):
                response = self.client.post("/api/chat", json={"question": "What failed?", "thread_id": "incident-1"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-db-path", response.text)

    def test_audits_are_newest_first_with_limit(self):
        for question in ["First incident?", "Second incident?"]:
            self.assertEqual(self.client.post("/api/chat", json={"question": question, "thread_id": "incident-1"}).status_code, 200)
        audits = self.client.get("/api/audits?limit=1").json()
        self.assertEqual([row["question"] for row in audits], ["Second incident?"])


class UploadSettingsTests(unittest.TestCase):
    def test_environment_limit_is_validated(self):
        for value in ["0", "-1", "104857601", "not-a-number"]:
            with self.subTest(value=value), patch.dict("os.environ", {"MAX_UPLOAD_BYTES": value}):
                with self.assertRaises((ValueError, ValidationError)):
                    Settings()
        with patch.dict("os.environ", {"MAX_UPLOAD_BYTES": "512"}):
            self.assertEqual(Settings().max_upload_bytes, 512)


if __name__ == "__main__":
    unittest.main()
