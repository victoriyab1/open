import pytest

from connect_neon import validate_url


def test_accepts_encrypted_neon_connection():
    value = "postgresql://user:dummy@ep-test-pooler.eu-central-1.aws.neon.tech/db?sslmode=require"
    assert validate_url(value) == value


@pytest.mark.parametrize("value", [
    "psql 'postgresql://example'", "postgresql://user:dummy@example.com/db?sslmode=require",
    "postgresql://user:dummy@ep-test.neon.tech/db?sslmode=disable",
    "postgresql://user:dummy@ep-test.neon.tech/db\nOTHER=x",
])
def test_rejects_wrong_or_unencrypted_connection(value):
    with pytest.raises(ValueError):
        validate_url(value)
