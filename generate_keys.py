"""
generate_keys.py — Geração de Pares de Chaves RSA para os Microsserviços

Gera pares de chaves RSA 2048 bits (privada + pública) para cada serviço
produtor de mensagens assinadas: gateway, promocao e ranking.

Deve ser executado **uma única vez** antes de iniciar o sistema:

    python generate_keys.py

Os arquivos são salvos em ``./keys/``:
    keys/gateway_private.pem   keys/gateway_public.pem
    keys/promocao_private.pem  keys/promocao_public.pem
    keys/ranking_private.pem   keys/ranking_public.pem
"""
import os
from Crypto.PublicKey import RSA

# Serviços que necessitam de par de chaves (apenas os produtores de eventos assinados)
SERVICOS = ["gateway", "promocao", "ranking"]

# Diretório de saída para os arquivos PEM
KEYS_DIR = "keys"

os.makedirs(KEYS_DIR, exist_ok=True)

for servico in SERVICOS:
    # Gera chave RSA de 2048 bits (recomendação mínima atual de segurança)
    chave = RSA.generate(2048)

    priv_path = os.path.join(KEYS_DIR, f"{servico}_private.pem")
    pub_path  = os.path.join(KEYS_DIR, f"{servico}_public.pem")

    # Salva chave privada em formato PEM (PKCS#1)
    with open(priv_path, "wb") as f:
        f.write(chave.export_key())

    # Salva chave pública em formato PEM (PKCS#1)
    with open(pub_path, "wb") as f:
        f.write(chave.publickey().export_key())

    print(f"[OK] Chaves geradas para '{servico}': {priv_path}, {pub_path}")

print("\nChaves salvas em ./keys/")
