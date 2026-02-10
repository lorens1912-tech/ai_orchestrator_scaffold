import pytest

from app.response_contract import build_response, validate_response


def test_build_response_ok_defaults():
    r = build_response()
    assert r == {"status": "ok", "data": {}, "errors": []}
    validate_response(r)


def test_build_response_with_data():
    r = build_response(status="ok", data={"x": 1})
    assert r["status"] == "ok"
    assert r["data"] == {"x": 1}
    assert r["errors"] == []
    validate_response(r)


def test_build_response_error_from_string():
    r = build_response(status="error", errors="boom")
    assert r["status"] == "error"
    assert isinstance(r["errors"], list)
    assert r["errors"][0]["code"] == "E_GENERIC"
    assert "boom" in r["errors"][0]["message"]
    validate_response(r)


def test_build_response_forces_error_when_errors_present():
    r = build_response(status="ok", data={"a": 1}, errors=[{"code": "E1", "message": "bad"}])
    assert r["status"] == "error"
    validate_response(r)


def test_validate_rejects_bad_payload():
    with pytest.raises(ValueError):
        validate_response({"status": "ok", "data": {}})

    with pytest.raises(ValueError):
        validate_response({"status": "weird", "data": {}, "errors": []})

    with pytest.raises(ValueError):
        validate_response({"status": "error", "data": {}, "errors": [{}]})
