"""
gateway.py — Microsserviço Gateway

Ponto de entrada humano do sistema. Expõe um menu interativo via terminal
para cadastrar promoções, listar promoções validadas e registrar votos.

Fluxo de mensagens
------------------
Publica (assinado com chave privada do Gateway):
    - ``promocao.recebida`` dados da nova promoção para o MS Promoção.
    - ``promocao.voto``     voto de usuário para o MS Ranking.

Consome (valida assinatura do MS Promoção):
    - ``promocao.publicada`` promoções já validadas; mantém lista local.

Dependências externas: RabbitMQ rodando em localhost:5672.
"""
import json
import threading
import uuid

import pika

from crypto_utils import build_envelope, load_private_key, load_public_key, validate_envelope

# ── Configurações ────────────────────────────────────────────────────────────
RABBITMQ_HOST = "localhost"
EXCHANGE      = "Promocoes"
FILA_GATEWAY  = "Fila_Gateway"

# Chave privada usada para assinar todos os eventos publicados por este serviço
PRIVATE_KEY = load_private_key("keys/gateway_private.pem")

# Chave pública do MS Promoção, usada para validar 'promocao.publicada'
PROMOCAO_PUB_KEY = load_public_key("keys/promocao_public.pem")

# Lista de promoções validadas recebidas via 'promocao.publicada'
promocoes_validadas: list[dict] = []

# Lock para acesso thread-safe à lista (consumidor roda em thread separada)
lock = threading.Lock()


# ── Helpers de conexão ───────────────────────────────────────────────────────

def nova_conexao() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """
    Abre uma nova conexão com o RabbitMQ e declara o exchange principal.

    Returns:
        Tupla (conexão, canal) prontos para uso.
    """
    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    ch   = conn.channel()
    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    return conn, ch


# ── Consumidor em background (promocao.publicada) ────────────────────────────

def _consumidor_thread():
    """
    Thread de consumo: escuta 'promocao.publicada' e atualiza a lista local.

    Valida a assinatura do MS Promoção antes de aceitar cada mensagem.
    Usa lock para acesso seguro ao estado compartilhado ``promocoes_validadas``.
    Executa em daemon thread para encerrar junto com o processo principal.
    """
    conn, ch = nova_conexao()

    ch.queue_declare(queue=FILA_GATEWAY, durable=True)
    ch.queue_bind(exchange=EXCHANGE, queue=FILA_GATEWAY, routing_key="promocao.publicada")

    # prefetch_count=1 garante que o worker só receba 1 mensagem por vez (fair dispatch)
    ch.basic_qos(prefetch_count=1)

    def callback(ch, method, _props, body):
        """Processa uma mensagem de 'promocao.publicada'."""
        try:
            envelope = json.loads(body)
            payload  = validate_envelope(envelope, PROMOCAO_PUB_KEY)

            if payload is None:
                print("\n[Gateway] Assinatura inválida em 'promocao.publicada'. Descartado.")
            else:
                with lock:
                    # Evita duplicatas caso a mensagem seja re-entregue pelo broker
                    ids = {p["id"] for p in promocoes_validadas}
                    if payload["id"] not in ids:
                        promocoes_validadas.append(payload)

                print(
                    f"\n[Gateway] Promoção validada: [{payload['categoria']}] "
                    f"{payload['titulo']} — R$ {payload['preco']}"
                )
        except Exception as e:
            print(f"\n[Gateway] Erro ao processar mensagem: {e}")
        finally:
            # ACK sempre enviado para evitar que a mensagem fique requeued indefinidamente
            ch.basic_ack(delivery_tag=method.delivery_tag)

    ch.basic_consume(queue=FILA_GATEWAY, on_message_callback=callback)
    ch.start_consuming()


# ── Funções de publicação ────────────────────────────────────────────────────

def publicar(ch, routing_key: str, payload: dict):
    """
    Assina o payload e publica no exchange com a routing key especificada.

    O envelope assinado garante que os consumidores downstream possam verificar
    que a mensagem realmente veio do Gateway.

    Args:
        ch:          Canal RabbitMQ ativo.
        routing_key: Routing key de destino (ex.: 'promocao.recebida').
        payload:     Dados a serem enviados.
    """
    envelope = build_envelope(payload, PRIVATE_KEY)
    ch.basic_publish(
        exchange    = EXCHANGE,
        routing_key = routing_key,
        body        = json.dumps(envelope, ensure_ascii=False),
        # delivery_mode=2 mensagem persistente (sobrevive a restart do broker)
        properties  = pika.BasicProperties(delivery_mode=2),
    )
    print(f"[Gateway] Evento publicado: '{routing_key}'")


# ── Interface do usuário ─────────────────────────────────────────────────────

def menu_cadastrar(ch):
    """
    Lê os dados de uma nova promoção via terminal e publica 'promocao.recebida'.

    Args:
        ch: Canal RabbitMQ para publicação.
    """
    print("\n── Cadastrar Promoção ──")

    titulo    = input("  Título       : ").strip()
    descricao = input("  Descrição    : ").strip()
    categoria = input("  Categoria    : ").strip().lower()
    preco     = input("  Preço (R$)   : ").strip()

    payload = {
        "id":        str(uuid.uuid4()),  # ID único gerado localmente
        "titulo":    titulo,
        "descricao": descricao,
        "categoria": categoria,
        "preco":     preco,
    }
    publicar(ch, "promocao.recebida", payload)


def menu_listar():
    """Exibe no terminal todas as promoções validadas recebidas até o momento."""
    print("\n── Promoções Validadas ──")

    with lock:
        lista = list(promocoes_validadas)  # cópia para liberar o lock rapidamente

    if not lista:
        print("  Nenhuma promoção disponível ainda.")
        return

    for i, p in enumerate(lista, 1):
        print(f"  {i:2d}. [{p['categoria']:10s}] {p['titulo']:30s}  R$ {p['preco']}")


def menu_votar(ch):
    """
    Permite ao usuário votar em uma promoção existente e publica 'promocao.voto'.

    Args:
        ch: Canal RabbitMQ para publicação.
    """
    with lock:
        lista = list(promocoes_validadas)

    if not lista:
        print("\n  Nenhuma promoção disponível para votar.")
        return

    print("\n── Votar em Promoção ──")
    menu_listar()

    try:
        idx = int(input("\n  Número da promoção : ")) - 1
        if not (0 <= idx < len(lista)):
            print("  Número inválido.")
            return
    except ValueError:
        print("  Entrada inválida.")
        return

    voto = input("  Voto [p]ositivo / [n]egativo : ").strip().lower()
    if voto in ("p", "positivo"):
        voto = "positivo"
    elif voto in ("n", "negativo"):
        voto = "negativo"
    else:
        print("  Opção inválida.")
        return

    promo = lista[idx]
    payload = {
        "promocao_id": promo["id"],
        "titulo":      promo["titulo"],
        "categoria":   promo["categoria"],
        "voto":        voto,
    }
    publicar(ch, "promocao.voto", payload)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """
    Ponto de entrada do Gateway.

    1. Inicia a thread consumidora em background.
    2. Abre uma conexão dedicada para publicação.
    3. Entra no loop do menu interativo.
    """
    # Thread daemon: encerra automaticamente quando o processo principal sair
    t = threading.Thread(target=_consumidor_thread, daemon=True)
    t.start()

    # Conexão separada para o loop de publicação (thread principal)
    conn, ch = nova_conexao()

    print("=" * 50)
    print("     Sistema de Promoções — MS Gateway")
    print("=" * 50)

    try:
        while True:
            print("\n┌─ Menu ────────────────────────────┐")
            print("│  1. Cadastrar nova promoção       │")
            print("│  2. Listar promoções validadas    │")
            print("│  3. Votar em uma promoção         │")
            print("│  0. Sair                          │")
            print("└───────────────────────────────────┘")
            opcao = input("  Opção: ").strip()

            if opcao == "1":
                menu_cadastrar(ch)
            elif opcao == "2":
                menu_listar()
            elif opcao == "3":
                menu_votar(ch)
            elif opcao == "0":
                print("Encerrando Gateway...")
                break
            else:
                print("  Opção inválida.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
