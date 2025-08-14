import json

import app.app as app_module


class DummyRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def test_cached_license_key_includes_email_and_key(monkeypatch):
    dummy = DummyRedis()
    monkeypatch.setattr(app_module, "redis_conn", dummy)

    def fake_check_license_and_quota(email, license_key):
        return {"success": True, "key": license_key}

    monkeypatch.setattr(app_module, "check_license_and_quota", fake_check_license_and_quota)

    email = "user@example.com"
    license_key = "abc123"

    # Seed old cache entry using the previous key format
    dummy.setex(f"license_cache:{email}", 10, json.dumps({"success": True}))

    info = app_module.get_cached_license_info(email, license_key)

    assert dummy.get(f"license_cache:{email}") is None
    cached = dummy.get(f"license_cache:{email}:{license_key}")
    assert cached is not None
    assert json.loads(cached) == info

