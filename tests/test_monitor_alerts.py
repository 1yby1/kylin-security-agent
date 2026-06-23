import unittest

from backend.monitor.alerts import Alert, AlertStore


def _alert(source="disk", message="m"):
    return Alert(severity="warning", source=source, metric="x", value=1, threshold=0, message=message)


class AlertStoreTest(unittest.TestCase):
    def test_add_stamps_timestamp_and_recent_is_newest_first(self):
        clock = [100.0]
        store = AlertStore(clock=lambda: clock[0])
        store.add(_alert(message="first"))
        clock[0] = 200.0
        store.add(_alert(message="second"))
        recent = store.recent()
        self.assertEqual([a["message"] for a in recent], ["second", "first"])
        self.assertEqual(recent[0]["timestamp"], 200.0)

    def test_max_alerts_evicts_oldest(self):
        store = AlertStore(max_alerts=2, clock=lambda: 0.0)
        for i in range(5):
            store.add(_alert(message=f"a{i}"))
        messages = [a["message"] for a in store.recent()]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages, ["a4", "a3"])

    def test_ttl_prunes_expired(self):
        clock = [0.0]
        store = AlertStore(ttl_seconds=10, clock=lambda: clock[0])
        store.add(_alert(message="old"))
        clock[0] = 11.0
        store.add(_alert(message="new"))
        messages = [a["message"] for a in store.recent()]
        self.assertEqual(messages, ["new"])

    def test_reset(self):
        store = AlertStore(clock=lambda: 0.0)
        store.add(_alert())
        store.reset()
        self.assertEqual(store.recent(), [])


if __name__ == "__main__":
    unittest.main()
