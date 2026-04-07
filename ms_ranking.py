"""
ms_ranking.py — Microsserviço Ranking

Processa votos de usuários, mantém o placar por promoção e dispara eventos
de destaque (hot deal) quando uma promoção ultrapassa o limiar de score.

Fluxo de mensagens
------------------
Consome (valida assinatura do Gateway):
    - ``promocao.voto`` voto positivo ou negativo em uma promoção.

Publica (assinado com chave privada do MS Ranking):
    - ``promocao.destaque`` promoção com score >= HOT_DEAL_THRESHOLD.
      Publicado apenas uma vez por promoção (controle via set ``hot_deals``).

Score
-----
    score = votos_positivos - votos_negativos

Quando score >= HOT_DEAL_THRESHOLD e a promoção ainda não foi promovida,
ela é publicada como hot deal.
"""
import json
import pika

from crypto_utils import build_envelope, load_private_key, load_public_key, validate_envelope

# ── Configurações ────────────────────────────────────────────────────────────
RABBITMQ_HOST = "localhost"
EXCHANGE      = "Promocoes"
FILA_RANKING  = "Fila_Ranking"

# Chave privada usada para assinar eventos 'promocao.destaque'
PRIVATE_KEY = load_private_key("keys/ranking_private.pem")

# Chave pública do Gateway para validar votos recebidos
GATEWAY_PUB_KEY = load_public_key("keys/gateway_public.pem")

# Score mínimo (positivos - negativos) para promover a promoção como hot deal
HOT_DEAL_THRESHOLD = 3

# Estado interno dos votos:
# { promocao_id: { 'positivo': int, 'negativo': int, 'titulo': str, 'categoria': str } }
votos: dict[str, dict] = {}

# IDs de promoções que já foram publicadas como hot deal (evita re-publicação)
hot_deals: set[str] = set()


# ── Callback ─────────────────────────────────────────────────────────────────

def callback(ch, method, _props, body):
    """
    Processa um evento 'promocao.voto'.

    Etapas:
        1. Valida o envelope assinado pelo Gateway.
        2. Inicializa os contadores da promoção se necessário.
        3. Incrementa o contador do tipo de voto (positivo/negativo).
        4. Calcula o score e verifica se atingiu o limiar de hot deal.
        5. Se hot deal inédito, publica 'promocao.destaque' assinado.

    Args:
        ch:     Canal RabbitMQ.
        method: Metadados de entrega.
        _props: Propriedades AMQP (não utilizado).
        body:   Corpo da mensagem em bytes (JSON serializado).
    """
    try:
        envelope = json.loads(body)
        payload  = validate_envelope(envelope, GATEWAY_PUB_KEY)

        if payload is None:
            print("[MS Ranking] Assinatura inválida. Evento descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        pid       = payload["promocao_id"]
        voto      = payload["voto"]                     # "positivo" | "negativo"
        titulo    = payload.get("titulo", pid)
        categoria = payload.get("categoria", "geral")

        # Inicializa contadores na primeira aparição da promoção
        if pid not in votos:
            votos[pid] = {
                "positivo":  0,
                "negativo":  0,
                "titulo":    titulo,
                "categoria": categoria,
            }

        votos[pid][voto] += 1
        score = votos[pid]["positivo"] - votos[pid]["negativo"]

        print(
            f"[MS Ranking] Voto '{voto}' para '{titulo}' | "
            f"+{votos[pid]['positivo']} / -{votos[pid]['negativo']} | Score: {score}"
        )

        # Promove a hot deal apenas uma vez por promoção
        if score >= HOT_DEAL_THRESHOLD and pid not in hot_deals:
            hot_deals.add(pid)

            payload_destaque = {
                "promocao_id": pid,
                "titulo":      titulo,
                "categoria":   categoria,
                "score":       score,
            }
            envelope_destaque = build_envelope(payload_destaque, PRIVATE_KEY)
            ch.basic_publish(
                exchange    = EXCHANGE,
                routing_key = "promocao.destaque",
                body        = json.dumps(envelope_destaque, ensure_ascii=False),
                properties  = pika.BasicProperties(delivery_mode=2),
            )
            print(f"[MS Ranking] HOT DEAL! '{titulo}' (score {score}) 'promocao.destaque'")

    except Exception as e:
        print(f"[MS Ranking] Erro: {e}")
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """
    Inicializa o microsserviço Ranking e começa a consumir votos.
    """
    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    ch   = conn.channel()

    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    ch.queue_declare(queue=FILA_RANKING, durable=True)
    ch.queue_bind(exchange=EXCHANGE, queue=FILA_RANKING, routing_key="promocao.voto")
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=FILA_RANKING, on_message_callback=callback)

    print("=" * 50)
    print(f"  MS Ranking — aguardando votos (hot deal ≥ {HOT_DEAL_THRESHOLD})...")
    print("=" * 50)

    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        print("\n[MS Ranking] Encerrando.")
        conn.close()


if __name__ == "__main__":
    main()
