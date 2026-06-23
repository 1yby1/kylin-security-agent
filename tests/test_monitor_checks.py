import unittest
from types import SimpleNamespace

from backend.monitor.checks import check_auth, check_disk, check_service, run_all_checks


class ChecksTest(unittest.TestCase):
    def test_disk_over_threshold_alerts(self):
        alerts = check_disk({"used_percent": 95.0}, 90)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "critical")
        self.assertEqual(alerts[0].source, "disk")

    def test_disk_under_threshold_silent(self):
        self.assertEqual(check_disk({"used_percent": 80.0}, 90), [])

    def test_disk_missing_field_silent(self):
        self.assertEqual(check_disk({}, 90), [])
        self.assertEqual(check_disk({"error": "x"}, 90), [])

    def test_service_failed_alerts(self):
        alerts = check_service({"analysis": {"failed_count": 3}})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].value, 3)

    def test_service_no_failed_silent(self):
        self.assertEqual(check_service({"analysis": {"failed_count": 0}}), [])
        self.assertEqual(check_service({}), [])

    def test_auth_over_threshold_alerts(self):
        alerts = check_auth({"analysis": {"failed_login_count": 25}}, 20)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].source, "auth")

    def test_auth_at_or_under_threshold_silent(self):
        self.assertEqual(check_auth({"analysis": {"failed_login_count": 20}}, 20), [])
        self.assertEqual(check_auth({}, 20), [])

    def test_run_all_checks_aggregates(self):
        settings = SimpleNamespace(disk_percent=90, failed_login=20)
        outputs = {
            "disk": {"used_percent": 99.0},
            "service": {"analysis": {"failed_count": 1}},
            "auth": {"analysis": {"failed_login_count": 50}},
        }
        sources = {a.source for a in run_all_checks(outputs, settings)}
        self.assertEqual(sources, {"disk", "service", "auth"})


if __name__ == "__main__":
    unittest.main()
