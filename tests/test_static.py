import re
from urllib.parse import urljoin

from fastapi.testclient import TestClient

from moldloft.app import create_app
from tests import StoreCase


class StaticTests(StoreCase):
    def test_workbench_and_module_dependencies_are_served(self):
        with TestClient(create_app(self.store.db_path, repos={})) as client:
            index = client.get("/")
            self.assertEqual(index.status_code, 200)
            self.assertIn("text/html", index.headers["content-type"])
            self.assertIn('id="dag"', index.text)
            pending = re.findall(r'(?:src|href)="(/static/[^"]+)"', index.text)
            pending.extend(f"/static/vendor/elk/{name}" for name in ("README.md", "LICENSE.md"))
            seen = set()
            while pending:
                url = pending.pop()
                if url in seen:
                    continue
                with self.subTest(url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200)
                    if url.endswith(".js") and "/vendor/" not in url:
                        refs = re.findall(r"""['"]((?:\./|/static/)[\w./-]+\.js)['"]""", response.text)
                        pending.extend(urljoin(url, ref) for ref in refs)
                    seen.add(url)
            self.assertTrue({
                "/static/app.css", "/static/main.js", "/static/vendor/elk/elk-api.js",
                "/static/vendor/elk/elk-worker.min.js",
            } <= seen)
