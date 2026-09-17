"""SQLite schema, connection policy, and atomic migration contracts."""

import sqlite3

import pytest

from privasheet.store import (
    StoreError,
    dumps,
    loads,
    migrate,
    open_db,
    schema,
    transaction,
)


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "store.db")
    yield connection
    connection.close()


def test_connection(conn):
    assert conn.isolation_level is None
    assert conn.row_factory is sqlite3.Row
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_old_sqlite_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.37.2")
    with pytest.raises(StoreError, match="3.38"):
        open_db(tmp_path / "old.db")


def test_missing_json_refused(monkeypatch, tmp_path):
    connect = sqlite3.connect

    def without_json(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.create_function("json_valid", 1, None)
        return connection

    monkeypatch.setattr(sqlite3, "connect", without_json)
    with pytest.raises(StoreError, match="JSON"):
        open_db(tmp_path / "no-json.db")


def test_fresh_and_idempotent(conn):
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert {
        r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    } == {"templates", "documents", "snapshots", "batches", "results"}
    conn.execute("INSERT INTO snapshots(snapshot_id, doc) VALUES ('s', '{}')")
    before = list(map(tuple, conn.execute("SELECT * FROM sqlite_schema")))
    migrate(conn)
    assert list(map(tuple, conn.execute("SELECT * FROM sqlite_schema"))) == before
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    indexed = {
        tuple(r[2] for r in conn.execute(f"PRAGMA index_info({row[1]})"))
        for row in conn.execute("PRAGMA index_list(results)")
    }
    assert ("batch_id",) in indexed
    assert ("status",) in indexed


def test_failed_migration_is_atomic(conn, monkeypatch):
    migrate(conn)
    conn.execute("INSERT INTO snapshots(snapshot_id, doc) VALUES ('s', '{}')")

    def broken(connection):
        connection.execute("CREATE TABLE temporary_migration (value TEXT)")
        connection.execute("UPDATE snapshots SET doc = '{\"changed\":true}'")
        connection.execute("INSERT INTO absent_table VALUES (1)")

    monkeypatch.setattr(schema, "MIGRATIONS", [*schema.MIGRATIONS, broken])
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT doc FROM snapshots").fetchone()[0] == "{}"
    assert not conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='temporary_migration'"
    ).fetchall()
    assert not conn.in_transaction


def test_newer_version_refused(conn):
    conn.execute("PRAGMA user_version=999")
    with pytest.raises(StoreError, match="newer"):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 999


def test_transactions(conn):
    conn.execute("CREATE TABLE sample (value INTEGER)")
    statements = []
    conn.set_trace_callback(statements.append)
    with transaction(conn):
        conn.execute("INSERT INTO sample VALUES (1)")
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO sample VALUES (2)")
        raise RuntimeError("abort")
    assert [r[0] for r in conn.execute("SELECT value FROM sample")] == [1]
    assert statements.count("BEGIN IMMEDIATE") == 2
    assert "COMMIT" in statements and "ROLLBACK" in statements


@pytest.mark.parametrize(
    "table,key",
    [
        ("templates", "template_id, version"),
        ("snapshots", "snapshot_id"),
        ("batches", "batch_id"),
        ("results", "result_id"),
    ],
)
def test_json_constraint(conn, table, key):
    migrate(conn)
    values = "'t', 1" if table == "templates" else "'id'"
    ddl = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE name=?", (table,)
    ).fetchone()[0]
    assert "CHECK (json_valid(doc))" in ddl
    # Generated json_extract expressions can reject malformed JSON before CHECK.
    for invalid in ("not json", None):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                f"INSERT INTO {table} ({key}, doc) VALUES ({values}, ?)", (invalid,)
            )


def test_generated_columns_and_foreign_keys(conn):
    migrate(conn)
    conn.execute(
        "INSERT INTO snapshots(snapshot_id, doc) VALUES (?, ?)",
        ("s", dumps({"created_at": "today"})),
    )
    conn.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
        ("d", "hash", "sample.pdf", "uploads/sample.pdf", "s"),
    )
    conn.execute(
        "INSERT INTO templates(template_id, version, doc) VALUES (?, ?, ?)",
        ("t", 1, dumps({"name": "Synthetic", "created_at": "today"})),
    )
    conn.execute(
        "INSERT INTO batches(batch_id, doc) VALUES (?, ?)",
        ("b", dumps({"template": {"id": "t", "version": 1}, "created_at": "today"})),
    )
    result = {
        "batch_id": "b",
        "document_id": "d",
        "status": "queued",
        "revision": 1,
        "job": 2,
        "updated_at": "today",
    }
    conn.execute(
        "INSERT INTO results(result_id, doc) VALUES (?, ?)", ("r", dumps(result))
    )
    result.update(status="passed", revision=2, job=3, updated_at="tomorrow")
    conn.execute("UPDATE results SET doc=?", (dumps(result),))
    row = conn.execute("SELECT * FROM results").fetchone()
    assert all(row[k] == v for k, v in result.items())
    for table, expected in [
        ("templates", {"name": "Synthetic", "created_at": "today"}),
        ("snapshots", {"created_at": "today"}),
        ("batches", {"template_id": "t", "template_version": 1, "created_at": "today"}),
    ]:
        row = conn.execute(f"SELECT * FROM {table}").fetchone()
        assert all(row[k] == v for k, v in expected.items())
    for key in ("batch_id", "document_id"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE results SET doc=?", (dumps(dict(result, **{key: "missing"})),)
            )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE batches SET doc=?",
            (dumps({"template": {"id": "t", "version": 2}}),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE documents SET snapshot_id='missing'")
    for table in ("templates", "snapshots", "batches", "documents"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE results SET status='failed'")


def test_json_serialization():
    doc = {"name": "ใบแจ้งหนี้", "amount": 3.5}
    assert "ใบแจ้งหนี้" in dumps(doc)
    assert loads(dumps(doc)) == doc
    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            dumps({"amount": value})
