"""Unit tests for sampling.py — the promotion cohort + provenance fingerprints.

Pure / CI-runnable (no frames). The promotion cohort is NOT order-dependent reservoir
sampling: membership is a stable per-id hash so a frame rebuild/reorder cannot silently
shift the cohort (review pt 5). data_snapshot_id is a composite, not a statistical
fingerprint (review pt 5: two frames can share stats).
"""
import hashlib

from sampling import (canonical_id_hash, in_cohort, select_cohort,
                      sample_fingerprint, data_snapshot_id, compute_data_snapshot_id)


def test_canonical_id_hash_is_sha256_of_ascii_int():
    # pinned canonical representation — sha256(str(int(test_id)).encode("ascii"))
    assert canonical_id_hash(123) == hashlib.sha256(b"123").digest()


def test_canonical_id_hash_normalises_int_and_str_and_is_stable():
    assert canonical_id_hash(123) == canonical_id_hash("123") == canonical_id_hash(123)


def test_in_cohort_fraction_zero_excludes_all():
    assert all(not in_cohort(i, 0.0) for i in range(200))


def test_in_cohort_fraction_one_includes_all():
    assert all(in_cohort(i, 1.0) for i in range(200))


def test_in_cohort_is_deterministic_across_calls():
    ids = list(range(1000))
    assert [in_cohort(i, 0.5) for i in ids] == [in_cohort(i, 0.5) for i in ids]


def test_in_cohort_fraction_is_roughly_proportional():
    n, frac = 5000, 0.30
    k = sum(in_cohort(i, frac) for i in range(n))
    assert 0.25 * n < k < 0.35 * n


def test_select_cohort_membership_is_order_independent():
    ids = [5, 3, 9, 1, 7]
    assert sorted(select_cohort(ids, 1.0)) == sorted(ids)
    # membership (the SET) is order-independent; list order follows input order
    assert set(select_cohort(ids, 0.5)) == set(select_cohort(list(reversed(ids)), 0.5))


def test_sample_fingerprint_is_order_independent():
    assert sample_fingerprint([3, 1, 2]) == sample_fingerprint([1, 2, 3])


def test_sample_fingerprint_changes_with_membership():
    assert sample_fingerprint([1, 2, 3]) != sample_fingerprint([1, 2, 4])


def test_data_snapshot_id_is_stable_and_sensitive_to_every_component():
    base = dict(frame_path="f.parquet", row_count=100, schema_hash="abc",
                file_content_sha256="def", test_id_set_hash="ghi",
                min_date="2020-01-01", max_date="2021-01-01")
    a = data_snapshot_id(**base)
    assert a == data_snapshot_id(**base)
    for key, newval in [("row_count", 101), ("schema_hash", "abc2"),
                        ("file_content_sha256", "def2"), ("test_id_set_hash", "ghi2"),
                        ("min_date", "2020-02-01"), ("max_date", "2021-02-01"),
                        ("frame_path", "g.parquet")]:
        assert a != data_snapshot_id(**dict(base, **{key: newval}))


def test_compute_data_snapshot_id_is_stable_and_detects_content_change(tmp_path):
    import duckdb
    p = str(tmp_path / "frame.parquet")

    def write(rows):
        con = duckdb.connect()
        vals = ",".join(f"({i},{v})" for i, v in rows)
        con.execute(f"COPY (SELECT * FROM (VALUES {vals}) t(test_id, v)) "
                    f"TO '{p}' (FORMAT PARQUET)")
        con.close()

    write([(1, 0.1), (2, 0.2), (3, 0.3)])
    s1 = compute_data_snapshot_id(p)
    assert s1 == compute_data_snapshot_id(p)            # stable on identical content
    write([(1, 0.1), (2, 0.2), (3, 0.3), (4, 0.4)])     # same path, extra row
    assert compute_data_snapshot_id(p) != s1
