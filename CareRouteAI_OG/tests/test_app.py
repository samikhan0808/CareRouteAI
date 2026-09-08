import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("FLASK_SECRET", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")

from web.app import app


class CareRouteAppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_home_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_triage_start_and_message_work_without_gemini_key(self):
        start_resp = self.client.post(
            "/api/triage/start",
            json={"name": "Test", "gender": "male", "phone": "9999999999", "city": "Thane", "language": "en"},
        )
        self.assertEqual(start_resp.status_code, 200)

        message_resp = self.client.post(
            "/api/triage/message",
            json={"message": "I have chest pain", "sid": start_resp.get_json()["sid"]},
        )
        self.assertEqual(message_resp.status_code, 200)
        self.assertIn("type", message_resp.get_json())

    def test_admin_login_and_queues_endpoint(self):
        login_resp = self.client.post(
            "/admin/login",
            data={"password": "test-password"},
            follow_redirects=False,
        )
        self.assertEqual(login_resp.status_code, 302)

        queues_resp = self.client.get("/api/admin/queues")
        self.assertEqual(queues_resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
