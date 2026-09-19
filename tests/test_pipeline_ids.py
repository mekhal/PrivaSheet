"""Pipeline id generation."""

from privasheet.pipeline.ids import new_id


def test_ids_are_unique_sortable_and_increasing():
    ids = [new_id("doc") for _ in range(10_000)]

    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert all(value.startswith("doc_") for value in ids)


def test_id_prefixes_are_preserved():
    assert new_id("bat").startswith("bat_")
    assert new_id("res").startswith("res_")
