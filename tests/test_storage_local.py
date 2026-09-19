from wax.artifacts.storage import LocalStorage

def test_local_put_get(tmp_path, monkeypatch):
    monkeypatch.setenv("WAX_ARTIFACT_ROOT", str(tmp_path))
    s = LocalStorage(str(tmp_path))
    uri = s.put("p1/hello.txt", b"hello")
    assert uri.startswith("local://")
    assert s.get("p1/hello.txt") == b"hello"
    assert s.exists("p1/hello.txt")
