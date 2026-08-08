"""Verificación real de un token firmado por CRMBackend.

El resto de los tests sustituye el verificador por un doble, que es lo
correcto para probar qué hace la aplicación con una identidad. Pero eso deja
sin ejercitar la criptografía, y ahí se escondió un fallo que ningún test
veía: `pyjwt` sin el extra `[crypto]` solo sabe HMAC, así que **todo** token
RS256 se rechazaba con un genérico "token inválido".

Acá se firma con una clave real y se verifica contra un JWKS real. Si la
dependencia desaparece, esto falla en vez de esperar a producción.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import jwt.algorithms
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.core.crm_identity import CrmIdentityVerifier, InvalidIdentityError


@pytest.fixture(scope="module")
def keypair() -> tuple[rsa.RSAPrivateKey, str]:
    """Una clave, como la que el CRM guarda en secrets/."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return key, pem


def _jwks(key: rsa.RSAPrivateKey) -> dict[str, Any]:
    jwk: dict[str, Any] = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())  # pyright: ignore[reportUnknownMemberType]
    )
    return {"keys": [{**jwk, "alg": "RS256", "use": "sig", "kid": "de-prueba"}]}


def _token(pem: str, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": "5454a199-5ce8-42c8-9973-34068e7e80bd",
        "role": "cliente",
        "client_id": "801a7729-7925-4d9a-bbfe-a73233149922",
        "scope": "full",
        "aud": "monitor",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=30),
        **overrides,
    }
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "de-prueba"})


@pytest.fixture
def verifier(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[rsa.RSAPrivateKey, str]
) -> CrmIdentityVerifier:
    """Un verificador cuyo JWKS es el del par de prueba, sin salir a la red."""
    key, _ = keypair
    document = _jwks(key)

    class _FakeJWKClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get_signing_key_from_jwt(self, token: str) -> Any:
            return jwt.PyJWK(document["keys"][0])

    monkeypatch.setattr("app.core.crm_identity.PyJWKClient", _FakeJWKClient)
    settings = Settings(ENVIRONMENT="testing", CRM_BASE_URL="http://crm.de-prueba")
    return CrmIdentityVerifier(settings)


class TestVerifyingARealSignature:
    def test_a_token_from_the_crm_is_accepted(
        self, verifier: CrmIdentityVerifier, keypair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """El caso que falló en producción con la dependencia incompleta."""
        identity = verifier.verify(_token(keypair[1]))

        assert identity.client_id == "801a7729-7925-4d9a-bbfe-a73233149922"
        assert identity.role == "cliente"
        assert identity.must_change_password is False

    def test_rs256_is_actually_supported(self) -> None:
        """`pyjwt` sin el extra [crypto] solo sabe HMAC.

        Sin esta comprobación, la falta de `cryptography` se disfraza de
        "token inválido" y manda a buscar el problema al lado equivocado.
        """
        assert "RS256" in jwt.algorithms.get_default_algorithms()

    def test_a_pending_password_change_is_visible(
        self, verifier: CrmIdentityVerifier, keypair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        identity = verifier.verify(_token(keypair[1], scope="password_change"))

        assert identity.must_change_password is True


class TestWhatItRefuses:
    def test_a_token_signed_by_someone_else(
        self, verifier: CrmIdentityVerifier
    ) -> None:
        """La prueba de que la firma se comprueba de verdad."""
        impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = impostor.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

        with pytest.raises(InvalidIdentityError):
            verifier.verify(_token(pem))

    def test_an_expired_token(
        self, verifier: CrmIdentityVerifier, keypair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        past = datetime.now(UTC) - timedelta(minutes=1)

        with pytest.raises(InvalidIdentityError):
            verifier.verify(_token(keypair[1], exp=past))

    def test_a_token_for_the_crm_panel(
        self, verifier: CrmIdentityVerifier, keypair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """Un operador del CRM tiene un token válido y otra audiencia.

        Válido no alcanza: la audiencia `crm` es de quien administra la
        plataforma, no de un cliente mirando su consumo.
        """
        with pytest.raises(InvalidIdentityError):
            verifier.verify(_token(keypair[1], aud="crm"))

    def test_a_token_without_an_expiry(
        self, verifier: CrmIdentityVerifier, keypair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """Un token eterno no se puede retirar."""
        now = datetime.now(UTC)
        forever = jwt.encode(
            {"sub": "x", "aud": "monitor", "iat": now},
            keypair[1],
            algorithm="RS256",
            headers={"kid": "de-prueba"},
        )

        with pytest.raises(InvalidIdentityError):
            verifier.verify(forever)

    def test_garbage(self, verifier: CrmIdentityVerifier) -> None:
        with pytest.raises(InvalidIdentityError):
            verifier.verify("esto-no-es-un-token")


class TestWithoutTheCrm:
    def test_it_refuses_instead_of_crashing(self) -> None:
        """Sin CRM_BASE_URL no hay a quién preguntarle por la clave."""
        verifier = CrmIdentityVerifier(Settings(ENVIRONMENT="testing", CRM_BASE_URL=""))

        with pytest.raises(InvalidIdentityError, match="CRM_BASE_URL"):
            verifier.verify("lo-que-sea")
