"""The 0-row guard in cli.load: a parse that yields nothing must
be recorded as FAILED with a reason, never as an empty success — content
detection can route a file to the wrong adapter, which then quietly returns [].

No DB: the engine is a stub that records every statement, so the test asserts
on what would have been written to bronze.raw_files.
"""

import cashato.cli.load as load_mod


class _Rows:
    def first(self):
        return None  # no existing file with this sha256

    def scalar_one(self):
        return 42  # the INSERT ... RETURNING id


class _Conn:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((str(sql), params))
        return _Rows()


class _Ctx:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return _Conn(self.log)

    def __exit__(self, *exc):
        return False


class _Engine:
    def __init__(self):
        self.log = []

    def begin(self):
        return _Ctx(self.log)


def test_zero_row_parse_is_recorded_as_failed(tmp_path, monkeypatch):
    f = tmp_path / "misrouted.pdf"
    f.write_bytes(b"%PDF-1.4 not really an intesa statement")
    engine = _Engine()
    monkeypatch.setattr(load_mod, "get_engine", lambda: engine)
    monkeypatch.setitem(load_mod.ADAPTERS, "intesa", lambda p: [])

    assert load_mod.load(f, "intesa") == 0

    updates = [(s, p) for s, p in engine.log if "UPDATE bronze.raw_files" in s]
    assert updates, "the guard never wrote a status update"
    sql, params = updates[-1]
    assert "'failed'" in sql
    assert "0 transactions" in params["e"]
    # And nothing was ever written to silver.
    assert not any("silver.transactions" in s for s, _ in engine.log)
