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


def test_login_bypasses_cached_license(monkeypatch):
    dummy = DummyRedis()
    email = "user@example.com"
    license_key = "abc123"

    # Seed cache with a failure entry that should be ignored
    dummy.setex(
        f"license_cache:{email}:{license_key}", 10, json.dumps({"success": False})
    )
    monkeypatch.setattr(app_module, "redis_conn", dummy)

    calls = []

    def fake_check_license_and_quota(e, k):
        calls.append((e, k))
        return {"success": True}

    monkeypatch.setattr(app_module, "check_license_and_quota", fake_check_license_and_quota)

    client = app_module.app.test_client()
    resp = client.post("/", data={"email": email, "key": license_key})

    # A fresh check should be performed regardless of the cached failure
    assert calls == [(email, license_key)]
    assert resp.status_code == 302
    cached = dummy.get(f"license_cache:{email}:{license_key}")
    assert json.loads(cached) == {"success": True}

