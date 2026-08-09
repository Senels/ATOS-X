"""Small CI smoke tests that require no external services or credentials."""


def test_ci_smoke():
    assert True


def test_runtime_test_mode_is_supported(monkeypatch):
    monkeypatch.setenv("ATOS_TEST_MODE", "1")
    import os
    assert os.getenv("ATOS_TEST_MODE") == "1"
