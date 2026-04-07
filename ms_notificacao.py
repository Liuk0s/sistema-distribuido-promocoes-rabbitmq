"""
ms_notificacao.py — Microsserviço Notificação

Atua como fanout de notificações: recebe eventos oficiais do sistema e os
redistribui na routing key específica de cada categoria para que clientes
consumidores recebam apenas o que lhes interessa.

Fluxo de mensagens
------------------
Consome:
    - ``promocao.publicada`` (valida assinatura do MS Promoção)
      republica como ``promocao.<categoria>`` (sem assinatura).

    - ``promocao.destaque``  (valida assinatura do MS Ranking)
      republica como ``promocao.<categoria>`` com flag ``hot_deal: True``.
      Clientes que assinaram ``promocao.destaque`` diretamente já recebem
      o hot deal do Ranking; esta publicação atinge clientes que assinaram
      apenas pela categoria.

Ausência de loop
----------------
A fila ``Fila_Notificacao`` está vinculada **somente** a
``promocao.publicada`` e ``promocao.destaque``.

As mensagens que este serviço publica (``promocao.<categoria>``) **não**
chegam a esta fila, portanto não há ciclo de re-entrega.
"""
import json
import pika

from crypto_utils import load_public_key, validate_envelope

# ── Configurações ────────────────────────────────────────────────────────────
RABBITMQ_HOST    = "localhost"
EXCHANGE         = "Promocoes"
FILA_NOTIFICACAO = "Fila_Notificacao"

# Chave pública do MS Promoção (valida 'promocao.publicada')
PROMOCAO_PUB_KEY = load_public_key("keys/promocao_public.pem")

# Chave pública do MS Ranking (valida 'promocao.destaque')
RANKING_PUB_KEY  = load_public_key("keys/ranking_public.pem")


# ── Helpers ──────────────────────────────────────────────────────────────────

def publicar_notificacao(ch, routing_key: str, notif: dict):
    """
    Publica uma notificação sem assinatura digital.

    O MS Notificação não é um produtor confiável no modelo de segurança
    atual — ele apenas redistribui eventos já validados por outros serviços.

    Args:
        ch:          Canal RabbitMQ ativo.
        routing_key: Routing key de destino (ex.: 'promocao.livro').
        notif:       Dados da notificação a publicar.
    """
    ch.basic_publish(
        exchange    = EXCHANGE,
        routing_key = routing_key,
        body        = json.dumps(notif, ensure_ascii=False),
        properties  = pika.BasicProperties(delivery_mode=2),
    )
    print(f"[MS Notificação] Notificação publicada em '{routing_key}'")


# ── Callback ─────────────────────────────────────────────────────────────────

def callback(ch, method, _props, body):
    """
    Callback unificado para 'promocao.publicada' e 'promocao.destaque'.

    Seleciona a chave pública de validação correta com base na routing key
    de origem, valida o envelope e republica a notificação formatada na
    routing key da categoria correspondente.

    Args:
        ch:     Canal RabbitMQ.
        method: Metadados de entrega (inclui ``routing_key``).
        _props: Propriedades AMQP (não utilizado).
        body:   Corpo da mensagem em bytes.
    """
    routing_key = method.routing_key

    try:
        envelope = json.loads(body)

        # Seleciona a chave pública correta conforme o produtor esperado
        if routing_key == "promocao.publicada":
            pub_key = PROMOCAO_PUB_KEY
        elif routing_key == "promocao.destaque":
            pub_key = RANKING_PUB_KEY
        else:
            # Routing key inesperada — binding incorreto ou bug no broker
            print(f"[MS Notificação] Routing key inesperada: '{routing_key}'. Descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        payload = validate_envelope(envelope, pub_key)

        if payload is None:
            print(f"[MS Notificação] Assinatura inválida em '{routing_key}'. Descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        categoria         = payload.get("categoria", "geral").lower()
        routing_key_notif = f"promocao.{categoria}"

        if routing_key == "promocao.publicada":
            # Nova promoção: notifica clientes interessados nessa categoria
            notif = {
                "tipo":      "nova_promocao",
                "titulo":    payload["titulo"],
                "categoria": categoria,
                "descricao": payload.get("descricao", ""),
                "preco":     payload.get("preco", ""),
            }
            print(f"[MS Notificação] Nova promoção em '{categoria}': {payload['titulo']}")
            publicar_notificacao(ch, routing_key_notif, notif)

        elif routing_key == "promocao.destaque":
            # Hot deal: notifica clientes inscritos na categoria com a flag hot_deal
            notif_categoria = {
                "tipo":      "hot_deal",
                "titulo":    payload["titulo"],
                "categoria": categoria,
                "score":     payload.get("score"),
                "hot_deal":  True,
            }
            print(f"[MS Notificação] HOT DEAL em '{categoria}': {payload['titulo']}")
            publicar_notificacao(ch, routing_key_notif, notif_categoria)

    except Exception as e:
        print(f"[MS Notificação] Erro: {e}")
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """
    Inicializa o MS Notificação e registra os bindings necessários.
    """
    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    ch   = conn.channel()

    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    ch.queue_declare(queue=FILA_NOTIFICACAO, durable=True)

    # Vincula à promoção publicada (MS Promoção) e a destaques (MS Ranking)
    ch.queue_bind(exchange=EXCHANGE, queue=FILA_NOTIFICACAO, routing_key="promocao.publicada")
    ch.queue_bind(exchange=EXCHANGE, queue=FILA_NOTIFICACAO, routing_key="promocao.destaque")

    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=FILA_NOTIFICACAO, on_message_callback=callback)

    print("=" * 50)
    print("  MS Notificação — aguardando eventos...")
    print("=" * 50)

    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        print("\n[MS Notificação] Encerrando.")
        conn.close()


if __name__ == "__main__":
    main()
