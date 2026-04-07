"""
crypto_utils.py — Utilitários de Criptografia Assimétrica (RSA + SHA-256)

Fornece funções para assinar e verificar mensagens JSON usando RSA-PKCS1v1.5,
garantindo autenticidade e integridade entre os microsserviços.

Dependência: pycryptodome (pip install pycryptodome)
"""
import json
import base64

from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256


# ── Carregamento de chaves ────────────────────────────────────────────────────

def load_private_key(path: str) -> RSA.RsaKey:
    """
    Carrega uma chave RSA privada a partir de um arquivo PEM.

    Args:
        path: Caminho para o arquivo .pem da chave privada.

    Returns:
        Objeto RsaKey com a chave privada carregada.
    """
    with open(path, "rb") as f:
        return RSA.import_key(f.read())


def load_public_key(path: str) -> RSA.RsaKey:
    """
    Carrega uma chave RSA pública a partir de um arquivo PEM.

    Args:
        path: Caminho para o arquivo .pem da chave pública.

    Returns:
        Objeto RsaKey com a chave pública carregada.
    """
    with open(path, "rb") as f:
        return RSA.import_key(f.read())


# ── Assinatura e verificação ──────────────────────────────────────────────────

def sign_message(payload: dict, private_key: RSA.RsaKey) -> str:
    """
    Serializa o payload como JSON (chaves ordenadas), calcula SHA-256
    e assina com RSA-PKCS1v1.5.

    A serialização com ``sort_keys=True`` é obrigatória para garantir que
    a mesma sequência de bytes seja produzida tanto na assinatura quanto na
    verificação, independentemente da ordem de inserção das chaves no dict.

    Args:
        payload:     Dicionário com os dados a serem assinados.
        private_key: Chave RSA privada do remetente.

    Returns:
        Assinatura digital codificada em Base64 (string UTF-8).
    """
    payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    h = SHA256.new(payload_bytes)
    signature = pkcs1_15.new(private_key).sign(h)
    return base64.b64encode(signature).decode("utf-8")


def verify_signature(payload: dict, signature_b64: str, public_key: RSA.RsaKey) -> bool:
    """
    Verifica uma assinatura RSA-PKCS1v1.5 contra o payload fornecido.

    Reconstrói os bytes do payload com as mesmas configurações usadas na
    assinatura (sort_keys=True) antes de verificar.

    Args:
        payload:       Dicionário com os dados originais.
        signature_b64: Assinatura em Base64 a ser verificada.
        public_key:    Chave RSA pública do remetente esperado.

    Returns:
        True se a assinatura for válida; False caso contrário.
    """
    try:
        payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        h = SHA256.new(payload_bytes)
        signature = base64.b64decode(signature_b64)
        pkcs1_15.new(public_key).verify(h, signature)
        return True
    except (ValueError, TypeError):
        return False


# ── Envelope assinado ─────────────────────────────────────────────────────────

def build_envelope(payload: dict, private_key: RSA.RsaKey) -> dict:
    """
    Cria um envelope assinado no formato padrão do sistema.

    Estrutura do envelope::

        {
            "payload":   { ...dados originais... },
            "signature": "<base64 da assinatura RSA>"
        }

    Args:
        payload:     Dados a serem enviados.
        private_key: Chave privada do remetente para assinar.

    Returns:
        Dicionário com ``payload`` e ``signature``.
    """
    return {
        "payload":   payload,
        "signature": sign_message(payload, private_key),
    }


def validate_envelope(envelope: dict, public_key: RSA.RsaKey) -> dict | None:
    """
    Valida um envelope assinado e extrai o payload se a assinatura for válida.

    Verifica que o envelope contém os campos obrigatórios ``payload`` e
    ``signature`` antes de checar a assinatura RSA.

    Args:
        envelope:   Dicionário com ``payload`` e ``signature``.
        public_key: Chave pública do remetente esperado.

    Returns:
        O dicionário ``payload`` se a assinatura for válida; ``None`` caso
        o envelope esteja malformado ou a assinatura seja inválida.
    """
    if "payload" not in envelope or "signature" not in envelope:
        return None
    if verify_signature(envelope["payload"], envelope["signature"], public_key):
        return envelope["payload"]
    return None
