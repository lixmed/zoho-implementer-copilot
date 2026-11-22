import pytest

from copilot.auth.pkce import ALLOWED_CHARS, challenge_s256, generate_verifier


def test_rfc7636_appendix_b_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_challenge_has_no_padding():
    assert "=" not in challenge_s256(generate_verifier())


@pytest.mark.parametrize("length", [43, 64, 128])
def test_verifier_length_and_charset(length):
    v = generate_verifier(length)
    assert len(v) == length
    assert set(v) <= set(ALLOWED_CHARS)


def test_default_length_is_64():
    assert len(generate_verifier()) == 64


@pytest.mark.parametrize("length", [0, 42, 129])
def test_verifier_rejects_bad_length(length):
    with pytest.raises(ValueError):
        generate_verifier(length)


def test_verifiers_are_random():
    assert len({generate_verifier() for _ in range(50)}) == 50


@pytest.mark.parametrize("bad", ["short", "a" * 129, "a" * 42 + " ", "a" * 42 + "é"])
def test_challenge_rejects_invalid_verifier(bad):
    with pytest.raises(ValueError):
        challenge_s256(bad)
