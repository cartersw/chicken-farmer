import os
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from cs2_data import immutable_evidence as guards
from cs2_data.io import sha256_file

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows sharing semantics')


def test_guard_prevents_mutation_and_reuses_hash_until_release(tmp_path,monkeypatch):
    path=tmp_path/'ledger.jsonl';path.write_bytes(b'original')
    digest=sha256_file(path)
    with guards.hold_files({str(path):digest}):
        with pytest.raises(PermissionError):path.write_bytes(b'changed')
        with pytest.raises(PermissionError):path.unlink()
        with monkeypatch.context() as patched:
            patched.setattr(Path,'open',lambda *a,**kw:pytest.fail('Locked evidence must not be reread'))
            assert sha256_file(path)==digest
    path.write_bytes(b'changed')
    assert sha256_file(path)!=digest


def test_failed_binding_and_exception_release_handles(tmp_path):
    path=tmp_path/'ledger.jsonl';path.write_bytes(b'original')
    with pytest.raises(ValueError,match='changed'):
        with guards.hold_files({str(path):'0'*64}):pass
    digest=sha256_file(path)
    with pytest.raises(RuntimeError):
        with guards.hold_files({str(path):digest}):raise RuntimeError('cancel')
    path.unlink()
    assert not path.exists()


def test_nested_guards_and_readonly_sqlite(tmp_path):
    path=(tmp_path/'index.sqlite').resolve()
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE records(n)')
        db.commit()
    digest=sha256_file(path)
    with guards.hold_files({str(path):digest}):
        with guards.hold_files({str(path):digest}):
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                assert db.execute('SELECT COUNT(*) FROM records').fetchone()==(0,)
        with pytest.raises(PermissionError):path.write_bytes(b'changed')
    path.write_bytes(b'changed')


def test_path_identity_and_code_files_are_not_silently_cached(tmp_path,monkeypatch):
    path=tmp_path/'ledger.jsonl';path.write_bytes(b'original')
    with guards.hold_files({str(path):sha256_file(path)}):
        monkeypatch.setattr(guards,'_identity',lambda path:(0,0,0))
        with pytest.raises(ValueError,match='identity'):sha256_file(path)
    code=tmp_path/'code.py';code.write_text('code')
    with pytest.raises(ValueError,match='Only immutable session data'):
        with guards.hold_files({str(code):sha256_file(code)}):pass
