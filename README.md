# Sistema de Promoções com Microsserviços e RabbitMQ

Sistema distribuído de gerenciamento e distribuição de promoções, implementado com microsserviços que se comunicam via **RabbitMQ** (exchange do tipo `topic`) e **assinaturas digitais RSA** para garantir autenticidade das mensagens.

---

## Arquitetura

```
                        ┌──────────────────────────────────────────────┐
                        │              Exchange: Promocoes             │
                        │              (type: topic, durable)          │
                        └──────────┬──────────────┬────────────────────┘
                                   │              │
              promocao.recebida ───┘              └─── promocao.voto
                     │                                       │
            ┌────────▼────────┐                   ┌──────────▼──────────┐
            │   MS Promoção   │                   │     MS Ranking      │
            │ (Fila_Promocao) │                   │   (Fila_Ranking)    │
            └────────┬────────┘                   └──────────┬──────────┘
                     │                                       │
        promocao.publicada                        promocao.destaque
                     │                                       │
          ┌──────────┴──────────────────────────────────────┤
          │                                                  │
  ┌───────▼──────────┐                            ┌─────────▼────────────┐
  │   MS Notificação │                            │  Gateway (consumo)   │
  │ (Fila_Notificacao│                            │  (Fila_Gateway)      │
  └───────┬──────────┘                            └─────────┬────────────┘
          │                                                  │
  promocao.<categoria>                               (lista local de
  (hot_deal ou nova)                                 promoções validadas)
          │
          └──────────────► Clientes (filas exclusivas/efêmeras)
```

### Microsserviços

| Serviço          | Arquivo             | Consome                                       | Publica                                    |
|------------------|---------------------|-----------------------------------------------|--------------------------------------------|
| **Gateway**      | `gateway.py`        | `promocao.publicada`                          | `promocao.recebida`, `promocao.voto`       |
| **MS Promoção**  | `ms_promocao.py`    | `promocao.recebida`                           | `promocao.publicada`                       |
| **MS Ranking**   | `ms_ranking.py`     | `promocao.voto`                               | `promocao.destaque`                        |
| **MS Notificação** | `ms_notificacao.py` | `promocao.publicada`, `promocao.destaque` | `promocao.<categoria>`                     |
| **Cliente**      | `cliente.py`        | `promocao.<cat>`, `promocao.destaque`         | —                                          |

### Segurança — Assinaturas Digitais RSA

Cada produtor assina suas mensagens com **RSA-PKCS1v1.5 + SHA-256**. O envelope trafegado tem o formato:

```json
{
  "payload":   { "...dados originais..." },
  "signature": "<assinatura base64>"
}
```

| Produtor         | Assina                                | Verificado por                         |
|------------------|---------------------------------------|----------------------------------------|
| Gateway          | `promocao.recebida`, `promocao.voto`  | MS Promoção, MS Ranking                |
| MS Promoção      | `promocao.publicada`                  | Gateway, MS Notificação                |
| MS Ranking       | `promocao.destaque`                   | MS Notificação, Cliente                |
| MS Notificação   | *(não assina)*                        | —                                      |

### Lógica de Hot Deal

O MS Ranking acumula votos por promoção e calcula:

```
score = votos_positivos - votos_negativos
```

Quando `score >= HOT_DEAL_THRESHOLD` (padrão: **3**) e a promoção ainda não foi promovida, o MS Ranking publica `promocao.destaque`. Cada promoção é promovida **no máximo uma vez**.

---

## Pré-requisitos

- Python 3.10+
- RabbitMQ rodando em `localhost:5672`
- Dependências Python (ver `requirements.txt`)

### Instalação do RabbitMQ (Arch Linux)

```bash
sudo pacman -S rabbitmq
sudo systemctl enable --now rabbitmq
```

### Instalação das dependências Python

```bash
pip install -r requirements.txt
```

---

## Configuração inicial

Gere os pares de chaves RSA **antes** de iniciar qualquer serviço:

```bash
python generate_keys.py
```

Isso cria o diretório `keys/` com seis arquivos PEM:

```
keys/
├── gateway_private.pem
├── gateway_public.pem
├── promocao_private.pem
├── promocao_public.pem
├── ranking_private.pem
└── ranking_public.pem
```

> **Atenção:** as chaves privadas não devem ser commitadas no repositório. Adicione `keys/` ao `.gitignore`.

---

## Execução

Abra um terminal para cada processo (ordem recomendada):

```bash
# 1. MS Promoção
python ms_promocao.py

# 2. MS Ranking
python ms_ranking.py

# 3. MS Notificação
python ms_notificacao.py

# 4. Gateway (interface do operador)
python gateway.py

# 5. Um ou mais clientes consumidores
python cliente.py

# Ou em modo CLI para testes:
python cliente.py --nome Alice --cats livro,destaque
python cliente.py --nome Bob   --cats jogo,eletronico
```

---

## Fluxo de uma promoção (passo a passo)

```
Operador (Gateway)
  │  Cadastra promoção → publica promocao.recebida (assinado)
  ▼
MS Promoção
  │  Valida assinatura → registra → publica promocao.publicada (assinado)
  ▼
MS Notificação                  Gateway (consumo)
  │  Valida → publica           │  Valida → adiciona à lista local
  │  promocao.<categoria>       │
  ▼                             ▼
Clientes da categoria        Operador vê promoção listada

# Após votos suficientes:
Operador (Gateway)
  │  Vota positivo → publica promocao.voto (assinado)
  ▼
MS Ranking
  │  Acumula votos → score >= 3 → publica promocao.destaque (assinado)
  ▼
MS Notificação                  Clientes com "destaque"
  │  Publica hot_deal na        │  Recebem envelope assinado
  │  categoria                  │  diretamente do Ranking
  ▼                             ▼
Clientes da categoria        Exibe HOT DEAL com validação
```

---

## Estrutura do projeto

```
.
├── generate_keys.py      # Geração única de chaves RSA
├── crypto_utils.py       # Funções de assinatura/verificação RSA
├── gateway.py            # Interface do operador (cadastro, listagem, voto)
├── ms_promocao.py        # Microsserviço Promoção
├── ms_ranking.py         # Microsserviço Ranking
├── ms_notificacao.py     # Microsserviço Notificação
├── cliente.py            # Processo consumidor de notificações
├── requirements.txt
├── README.md
└── keys/                 # Gerado por generate_keys.py (não versionar!)
    ├── gateway_private.pem
    ├── gateway_public.pem
    └── ...
```

---

## Observações técnicas

- **Durabilidade**: filas e mensagens marcadas como `durable`/`delivery_mode=2` sobrevivem a restarts do broker.
- **Fila exclusiva do cliente**: criada sem nome (`queue=""`), destruída automaticamente ao desconectar.
- **Idempotência**: MS Promoção e MS Ranking ignoram mensagens duplicadas via set/dict de IDs já processados.
- **Thread-safety no Gateway**: o consumidor de `promocao.publicada` roda em daemon thread separada; o acesso à lista compartilhada é protegido por `threading.Lock`.
- **Sem loop**: o MS Notificação publica em `promocao.<categoria>`, routing key à qual sua própria fila (`Fila_Notificacao`) não está vinculada.
