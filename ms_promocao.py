"""
ms_promocao.py — Microsserviço Promoção

Responsável por validar promoções recebidas do Gateway e republica-las
como eventos oficiais para o restante do sistema.

Fluxo de mensagens
------------------
Consome (valida assinatura do Gateway):
    - ``promocao.recebida`` nova promoção submetida pelo operador.

Publica (assinado com chave privada do MS Promoção):
    - ``promocao.publicada`` promoção validada e registrada localmente.

O registro local (``promocoes``) evita que uma mesma promoção seja publicada
duas vezes caso o broker re-entregue a mensagem (idempotência pelo ``id``).
"""
import json
import pika

from crypto_utils import build_envelope, load_private_key, load_public_key, validate_envelope

# ── Configurações ────────────────────────────────────────────────────────────
RABBITMQ_HOST = "localhost"
EXCHANGE      = "Promocoes"
FILA_PROMOCAO = "Fila_Promocao"

# Chave privada usada para assinar os eventos 'promocao.publicada'
PRIVATE_KEY = load_private_key("keys/promocao_private.pem")

# Chave pública do Gateway para validar as mensagens recebidas
GATEWAY_PUB_KEY = load_public_key("keys/gateway_public.pem")

# Registro em memória: { promocao_id: payload } — garante idempotência
promocoes: dict[str, dict] = {}


# ── Callback ─────────────────────────────────────────────────────────────────

def callback(ch, method, _props, body):
    """
    Processa um evento 'promocao.recebida'.

    Etapas:
        1. Desserializa e valida o envelope assinado pelo Gateway.
        2. Verifica duplicidade pelo ID da promoção.
        3. Registra a promoção localmente.
        4. Re-publica como 'promocao.publicada' assinado com a chave do MS Promoção.

    Args:
        ch:     Canal RabbitMQ.
        method: Metadados de entrega (delivery_tag, routing_key, etc.).
        _props: Propriedades da mensagem AMQP (não utilizado).
        body:   Corpo da mensagem em bytes (JSON serializado).
    """
    try:
        envelope = json.loads(body)
        payload  = validate_envelope(envelope, GATEWAY_PUB_KEY)

        if payload is None:
            print("[MS Promoção] Assinatura inválida. Evento descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        pid = payload["id"]

        # Idempotência: ignora promoção que já foi processada
        if pid in promocoes:
            print(f"[MS Promoção] Promoção já registrada: {pid}. Ignorando.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # Registra localmente antes de publicar (evita re-publicar em caso de falha parcial)
        promocoes[pid] = payload
        print(
            f"[MS Promoção] Promoção registrada: [{payload['categoria']}] "
            f"{payload['titulo']} — R$ {payload['preco']}"
        )

        # Republica com assinatura própria do MS Promoção para os demais consumidores
        envelope_pub = build_envelope(payload, PRIVATE_KEY)
        ch.basic_publish(
            exchange    = EXCHANGE,
            routing_key = "promocao.publicada",
            body        = json.dumps(envelope_pub, ensure_ascii=False),
            properties  = pika.BasicProperties(delivery_mode=2),
        )
        print("[MS Promoção] Evento publicado: 'promocao.publicada'")

    except Exception as e:
        print(f"[MS Promoção] Erro: {e}")
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """
    Inicializa o microsserviço Promoção e começa a consumir eventos.

    Declara o exchange, a fila durável e o binding antes de entrar
    no loop de consumo bloqueante.
    """
    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    ch   = conn.channel()

    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    ch.queue_declare(queue=FILA_PROMOCAO, durable=True)
    ch.queue_bind(exchange=EXCHANGE, queue=FILA_PROMOCAO, routing_key="promocao.recebida")
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=FILA_PROMOCAO, on_message_callback=callback)

    print("=" * 50)
    print("   MS Promoção — aguardando eventos...")
    print("=" * 50)

    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        print("\n[MS Promoção] Encerrando.")
        conn.close()


if __name__ == "__main__":
    main()
