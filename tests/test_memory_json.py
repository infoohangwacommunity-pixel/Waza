from wax.memory.service import MemoryService


def test_safe_json_plain():
    # MemoryService needs a session; test the parser via a dummy instance method
    class Dummy:
        pass
    # reuse the static-ish parser by instantiating with None is not ideal;
    # call the module-level logic by creating object with session=None carefully
    from wax.memory import service as mem_mod
    # instantiate without calling DB methods
    svc = object.__new__(MemoryService)
    assert svc._safe_json('{"a": 1}') == {"a": 1}
    assert svc._safe_json('```json\n{"b": 2}\n```') == {"b": 2}
    assert svc._safe_json("not json") == {}
