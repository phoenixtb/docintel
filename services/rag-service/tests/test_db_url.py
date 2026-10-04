"""rag-service must reach Postgres through psycopg2 whatever SQLAlchemy's default driver is."""

from src.db import engine, psycopg2_url


def test_bare_postgresql_url_uses_psycopg2():
    assert (
        psycopg2_url("postgresql://u:p@postgres:5432/docintel").drivername == "postgresql+psycopg2"
    )


def test_explicit_driver_is_kept():
    assert psycopg2_url("postgresql+psycopg://u:p@h/db").drivername == "postgresql+psycopg"


def test_credentials_and_database_survive_the_rewrite():
    url = psycopg2_url("postgresql://docintel_rag:s3cret@postgres:5432/docintel")
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "docintel_rag",
        "s3cret",
        "postgres",
        5432,
        "docintel",
    )


def test_service_engine_is_built_on_psycopg2():
    assert engine.dialect.driver == "psycopg2"
