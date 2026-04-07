"""
cliente.py — Processo Cliente Consumidor de Promoções

Cria uma fila exclusiva e efêmera (auto-delete) vinculada às routing keys
de interesse do usuário, exibindo notificações no terminal em tempo real.

Routing keys suportadas
-----------------------
``promocao.<categoria>``
    Novas promoções e hot deals da categoria, publicados pelo MS Notificação.
    Chegam **sem assinatura digital**.

``promocao.destaque``
    Hot deals publicados diretamente pelo MS Ranking.
    Chegam como **envelope assinado** pelo MS Ranking e são validados aqui.

Modos de execução
-----------------
Interativo (padrão)::

    python cliente.py

Argumentos CLI (útil para testes automatizados)::

    python cliente.py --nome Alice --cats livro,destaque
"""
import json
from datetime import datetime
import pika

from crypto_utils import load_public_key, validate_envelope

# ── Configurações ────────────────────────────────────────────────────────────
RABBITMQ_HOST = "localhost"
EXCHANGE      = "Promocoes"

# Chave pública do MS Ranking para validar hot deals recebidos via 'promocao.destaque'
RANKING_PUB_KEY = load_public_key("keys/ranking_public.pem")


# ── Exibição ─────────────────────────────────────────────────────────────────

def exibir_notificacao(routing_key: str, dados: dict, nome: str):
    """
    Formata e imprime uma notificação no terminal com bordas ASCII.

    Hot deals (flag ``hot_deal`` ou ``tipo == 'hot_deal'``) recebem um
    destaque visual diferente das promoções comuns.

    Args:
        routing_key: Routing key de origem da mensagem.
        dados:       Dicionário com os campos da notificação.
        nome:        Nome do cliente para personalização da mensagem.
    """
    hora = datetime.now().strftime("%H:%M:%S")
    sep  = "─" * 48

    if dados.get("hot_deal") or dados.get("tipo") == "hot_deal":
        print(f"\n┌{sep}┐")
        print(f"│  HOT DEAL para {nome} [{hora}]")
        print(f"│  Título    : {dados.get('titulo', '—')}")
        print(f"│  Categoria : {dados.get('categoria', routing_key)}")
        print(f"│  Score     : {dados.get('score', '—')}")
        print(f"└{sep}┘")
    else:
        print(f"\n┌{sep}┐")
        print(f"│  Nova Promoção para {nome} [{hora}]")
        print(f"│  Título    : {dados.get('titulo', '—')}")
        print(f"│  Categoria : {dados.get('categoria', routing_key)}")
        print(f"│  Preço     : R$ {dados.get('preco', '—')}")
        if dados.get("descricao"):
            print(f"│  Descrição : {dados['descricao']}")
        print(f"└{sep}┘")


# ── Callback ─────────────────────────────────────────────────────────────────

def make_callback(nome: str):
    """
    Cria e retorna um callback de consumo personalizado para o cliente.

    O closure captura o nome do cliente para uso na exibição.

    Tratamento por routing key:
        - ``promocao.destaque``: valida envelope assinado pelo MS Ranking.
          Mensagens com assinatura inválida são descartadas silenciosamente.
        - Demais keys: mensagem sem assinatura (publicada pelo MS Notificação),
          desserializa diretamente.

    Args:
        nome: Nome do cliente (capturado pelo closure).

    Returns:
        Função callback compatível com ``pika.basic_consume``.
    """
    def callback(ch, method, _props, body):
        routing_key = method.routing_key
        try:
            mensagem = json.loads(body)

            if routing_key == "promocao.destaque":
                # Hot deal vindo diretamente do MS Ranking requer validação
                payload = validate_envelope(mensagem, RANKING_PUB_KEY)
                if payload is None:
                    print(f"[Cliente] Assinatura inválida em '{routing_key}'. Descartado.")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return
                dados = {
                    "hot_deal":  True,
                    "titulo":    payload.get("titulo", "—"),
                    "categoria": payload.get("categoria", "—"),
                    "score":     payload.get("score"),
                }
            else:
                # Notificações de categoria: sem assinatura (publicadas pelo MS Notificação)
                dados = mensagem

            exibir_notificacao(routing_key, dados, nome)

        except Exception as e:
            print(f"[Cliente] Erro ao processar mensagem: {e}")
        finally:
            ch.basic_ack(delivery_tag=method.delivery_tag)

    return callback


# ── Leitura de dados do usuário ───────────────────────────────────────────────

def solicitar_dados() -> tuple[str, list[str]]:
    """
    Obtém nome e lista de categorias do usuário.

    Suporta dois modos:
        - **CLI**: ``--nome <nome> --cats <cat1,cat2,...>``
        - **Interativo**: prompts de texto no terminal.

    Returns:
        Tupla ``(nome, categorias)`` onde ``categorias`` é uma lista de
        strings em minúsculas (ex.: ``['livro', 'jogo', 'destaque']``).
    """
    import sys

    args = sys.argv[1:]

    # Modo não-interativo para scripts e testes
    if "--nome" in args and "--cats" in args:
        nome     = args[args.index("--nome") + 1]
        cats_raw = args[args.index("--cats") + 1]
        categorias = [c.strip().lower() for c in cats_raw.split(",") if c.strip()]
        return nome, categorias

    # Modo interativo
    print("=" * 50)
    print("  Cliente Consumidor de Promoções")
    print("=" * 50)

    nome = input("  Seu nome: ").strip()
    while not nome:
        nome = input("  Nome não pode ser vazio. Seu nome: ").strip()

    print("\n  Digite as categorias de interesse separadas por vírgula.")
    print("  Exemplo: livro, jogo, eletronico, destaque")
    print("  (Use 'destaque' para receber hot deals diretamente do MS Ranking)")

    while True:
        entrada    = input("  Categorias: ").strip()
        categorias = [c.strip().lower() for c in entrada.split(",") if c.strip()]
        if categorias:
            break
        print("  Informe ao menos uma categoria.")

    return nome, categorias


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """
    Ponto de entrada do cliente consumidor.

    1. Lê nome e categorias (interativo ou CLI).
    2. Cria fila exclusiva e efêmera no RabbitMQ.
    3. Vincula a fila às routing keys de interesse.
    4. Entra no loop de consumo bloqueante.
    """
    nome, categorias = solicitar_dados()

    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    ch   = conn.channel()
    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)

    # Fila exclusiva: nomeada automaticamente pelo broker, removida ao desconectar
    result       = ch.queue_declare(queue="", exclusive=True)
    fila_cliente = result.method.queue

    # Registra um binding por categoria de interesse
    routing_keys = []
    for cat in categorias:
        rk = f"promocao.{cat}"
        ch.queue_bind(exchange=EXCHANGE, queue=fila_cliente, routing_key=rk)
        routing_keys.append(rk)

    print("\n" + "=" * 50)
    print(f"  Olá, {nome}!")
    print(f"  Interesses : {', '.join(routing_keys)}")
    print("=" * 50)
    print("  Aguardando notificações... (Ctrl+C para sair)")

    ch.basic_consume(queue=fila_cliente, on_message_callback=make_callback(nome))

    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        print("\n[Cliente] Encerrando.")
        conn.close()


if __name__ == "__main__":
    main()
