import copy

import pytest

from iakit import manifest, yamlio
from iakit.paths import MANIFEST


@pytest.fixture(scope="module")
def data():
    return yamlio.load(MANIFEST)


def errors_of(data):
    return yamlio.schema_errors(data, "manifest.schema.json") or manifest.consistency_errors(data)


def test_repository_manifest_passes(data):
    assert errors_of(data) == []


def test_received_entries_have_no_confirmed_bibliography_yet(data):
    # 書誌は原典で人が確認するまで空のまま（候補値で埋めない）
    for book in data["books"]:
        assert all(v is None for v in book["bibliography"].values()), book["book_id"]


def mutate(data, fn):
    d = copy.deepcopy(data)
    fn(d)
    return errors_of(d)


def book(d, book_id):
    return next(b for b in d["books"] if b["book_id"] == book_id)


def test_fabricated_hash_for_unreceived_book_is_rejected(data):
    assert mutate(data, lambda d: book(d, "BOOK-006")["original"].update(sha256="0" * 64))


def test_isbn_without_confirmation_is_rejected(data):
    assert mutate(data, lambda d: book(d, "BOOK-001")["bibliography"].update(isbn="9780000000000"))


def test_missing_book_is_rejected(data):
    assert mutate(data, lambda d: d["books"].pop())


def test_duplicate_hash_is_rejected(data):
    def dup(d):
        book(d, "BOOK-002")["original"]["sha256"] = book(d, "BOOK-001")["original"]["sha256"]
    assert mutate(data, dup)


def test_registered_status_requires_confirmed_bibliography(data):
    assert mutate(data, lambda d: book(d, "BOOK-001").update(status="registered"))


def test_mvp_selection_requires_reason(data):
    assert mutate(data, lambda d: book(d, "BOOK-001")["mvp"].update(selected=True))


def test_local_check_reports_not_run_without_originals(data, tmp_path):
    errors, status = manifest.local_errors(data, tmp_path / "missing")
    assert (errors, status) == ([], "not_run")


def test_local_check_detects_unregistered_original(data, tmp_path):
    (tmp_path / "stray.pdf").write_bytes(b"%PDF-1.7 self-made stray")
    errors, status = manifest.local_errors(data, tmp_path)
    assert status == "run"
    assert any("未登録の原典" in e for e in errors)


@pytest.mark.parametrize("text", [
    "a: 1\na: 2\n",              # 重複キー
    "a: !custom x\n",            # 独自タグ
    "a: 2026-01-01\n",           # 日時は文字列で書く
    "a: .nan\n",
    "a: .inf\n",
    "a: -.inf\n",
    "a: &loop [*loop]\n",
])
def test_yaml_loader_rejects_non_json_values(text):
    with pytest.raises(yamlio.DataError):
        yamlio.loads(text)


@pytest.mark.parametrize("change", [
    {"confirmed_by": "Reviewer"},
    {"confirmed_at": "2026-10-17T12:00:00+09:00"},
    {"confirmed_by": "Reviewer", "confirmed_at": "invalid"},
    {"confirmed_by": " ", "confirmed_at": "2026-10-17T12:00:00+09:00"},
    {"confirmed_by": "Reviewer", "confirmed_at": "2026-10-17T12:00:00+09:00"},
    {"confirmed_by": "Reviewer", "confirmed_at": "2026-10-17T12:00:00+09:00", "title": "Fixture"},
])
def test_registered_requires_valid_confirmation_and_minimum_bibliography(data, change):
    d = copy.deepcopy(data)
    b = book(d, "BOOK-001")
    b["status"] = "registered"
    b["bibliography"].update(change)
    assert errors_of(d)


def test_confirmed_minimum_bibliography_allows_unknown_isbn(data):
    d = copy.deepcopy(data)
    b = book(d, "BOOK-001")
    b["status"] = "registered"
    b["bibliography"].update(title="Self-written title", authors=["Fixture author"],
                             confirmed_by="Reviewer", confirmed_at="2026-10-17T12:00:00+09:00")
    assert errors_of(d) == []


def test_json_dumps_rejects_nonfinite_numbers():
    with pytest.raises(yamlio.DataError):
        yamlio.dumps({"value": float("nan")})


def test_shared_yaml_alias_is_json_compatible():
    assert yamlio.loads("a: &value [1]\nb: *value\n") == {"a": [1], "b": [1]}


@pytest.mark.parametrize("timestamp", ["invalid", "2026-10-17", "2026-10-17T12:00:00", "2026-02-30T12:00:00Z"])
def test_timestamp_validation_is_active_without_optional_jsonschema_extras(data, timestamp):
    d = copy.deepcopy(data)
    book(d, "BOOK-001")["bibliography"].update(
        title="Fixture", authors=["Author"], confirmed_by="Reviewer", confirmed_at=timestamp)
    assert yamlio.schema_errors(d, "manifest.schema.json")
