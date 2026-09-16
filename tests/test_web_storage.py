from drawing2step.web_storage import LocalWebStorage


def test_job_scoped_overwrites_do_not_cross_jobs(tmp_path):
    storage = LocalWebStorage(tmp_path)

    storage.put_bytes("a" * 32, "model.stl", b"job-a-v1", "model/stl")
    storage.put_bytes("b" * 32, "model.stl", b"job-b-v1", "model/stl")
    storage.put_bytes("a" * 32, "model.stl", b"job-a-v2", "model/stl")

    assert storage.get_bytes("a" * 32, "model.stl") == b"job-a-v2"
    assert storage.get_bytes("b" * 32, "model.stl") == b"job-b-v1"
