from wax.security.secrets import encrypt_str, decrypt_str

def test_roundtrip():
    plain = "super-secret-value-42"
    token = encrypt_str(plain)
    assert token != plain
    assert decrypt_str(token) == plain

def test_empty():
    assert encrypt_str("") == ""
    assert decrypt_str("") == ""
