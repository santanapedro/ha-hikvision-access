# AVANT - Integração Facial Hikvision (Home Assistant)

Integração **local-first** (sem nuvem) para terminais Hikvision de **controle de
acesso** da linha MinMoe (DS-K1T3xx), via ISAPI. Recebe eventos de acesso em
tempo real, identifica a pessoa, o método, o resultado e a foto, mantém histórico
local e permite abertura remota da porta.

> **Status: Fases 1 e 2 completas, em produção.** Backend (cliente ISAPI, descoberta
> de capacidades, config flow + reauth, SQLite, parser, reconciliador, listener
> alertStream, rota push, imagens/pessoas, entidades, serviços) **e** UI (card
> Lovelace com timeline/filtros/live, câmera RTSP, sensor de campainha) prontos e
> rodando contra dois **DS-K1T342MWX (FW V4.48.40)** reais. Próximo: gestão de
> pessoas (Fase 3). Ver [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Testado

| Modelo | Firmware | Eventos | Histórico | Foto do evento | Foto cadastrada | Abrir porta | Estado porta |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| DS-K1T342MWX | V3.16.1 | ✅¹ | ✅ | ✅ | ✅ | 🧪 | 🧪 |

Legenda: ✅ testado · 🧪 código pronto, aguardando teste no hardware · ⚠ parcial · ❌ não suportado
¹ acesso permitido (face) e ciclo de porta confirmados no histórico; evento negado ao vivo ainda não capturado.

## Requisitos no terminal

1. **ISAPI habilitado** (padrão nos MinMoe).
2. Conta de usuário dedicada, de menor privilégio possível (evite `admin` — spec §29).
3. Acesso HTTPS na rede local (certificado self-signed é aceito com "Validar SSL" desligado).
4. O terminal **bloqueia logins** após poucas falhas de autenticação — confira a senha antes de adicionar.

## Instalação (HACS)

1. HACS → Integrações → menu → *Repositórios personalizados* → `https://github.com/santanapedro/ha-hikvision-access` (categoria: Integração).
2. Instale "AVANT - Integração Facial Hikvision" e reinicie o Home Assistant.
3. *Configurações → Dispositivos e Serviços → Adicionar integração → AVANT - Integração Facial Hikvision*.

## Rotas de evento

| Rota | Como funciona | Observação |
|---|---|---|
| **push** (padrão) | O terminal faz `POST` dos eventos para uma rota HTTP do HA (`httpHosts`). Foto vem junto. | Usa o slot livre do terminal; sem re-dump histórico. |
| **stream** | O HA mantém `alertStream` aberto no terminal. | Neste firmware o terminal re-despeja o log inteiro ao conectar; melhor só como fallback. |

Nos dois casos, `AcsEvent` é consultado periodicamente para reconciliar (spec §10).

## Recursos e armazenamento

- **Disco**: as fotos ficam em `<config>/hikvision_access/<entry_id>/media/` (nunca em
  `/config/www`), particionadas por mês, purgadas a cada 6 h conforme
  **Retenção de imagens** (padrão 365 dias; `0` = ilimitado). Estimativa: ~40 KB por
  acesso — com ~50 acessos/dia e 1 ano, ~730 MB por terminal. As **linhas de evento**
  no SQLite (`hikvision_access.db`, separado do recorder do HA) seguem a **Retenção de
  eventos no histórico** (padrão 365 dias); depois da purga o banco é compactado
  (`VACUUM`). Ligar *Salvar payload bruto do evento* aumenta bastante o banco — deixe
  desligado salvo para depurar.
- **Rede/CPU**: cada chamada ISAPI faz um desafio Digest novo (2 requisições) para
  não esbarrar no anti-brute-force do terminal. Em regime normal: `alertStream`
  aberto + reconciliação a cada 60 s + status da porta a cada 30 s. Se o terminal
  tiver interfone, há também um *poll* da campainha — ajuste **Intervalo de checagem
  da campainha** (padrão 15 s) para reduzir o tráfego.
- **Memória**: alguns MB por terminal; downloads de foto são limitados e a fila de
  push é limitada.

## Segurança

- **Validar SSL** vem **desligado** por padrão porque o terminal usa certificado
  self-signed. Nesse modo um ataque MITM na rede local é teoricamente possível;
  mantenha o terminal numa VLAN de confiança. Se você instalar um certificado
  válido no terminal, ligue a opção.
- A câmera usa **RTSP com as credenciais embutidas na URL** (padrão do HA para
  câmeras RTSP). O token da rota *push* fica gravado no `httpHosts` do terminal.
- Eventos e fotos são servidos apenas pela rota autenticada do HA — qualquer
  usuário logado no HA consegue vê-los (igual às câmeras do HA).

## Privacidade (LGPD — spec §28)

Armazenamento **somente local**, retenção configurável, imagens servidas apenas
por rota autenticada do HA, sem upload externo, logs sem dados biométricos.

## Desenvolvimento

`tools/` contém utilitários de homologação read-only:

```bash
pip install requests aiohttp
python tools/discovery.py            # matriz de endpoints + fixtures
python tools/event_histogram.py      # distribuição real de (major,minor)
python tools/test_api.py             # exercita custom_components/.../api.py no device
python tools/test_pipeline.py        # reconcilia -> SQLite -> foto, ponta a ponta

python -m pytest                     # suíte leve (lógica pura, sem HA)
pytest tests/ha -p pytest_homeassistant_custom_component   # ciclo de vida (Linux/CI)
```

Credenciais ficam em `tools/device.local.json` (git-ignored).

### Serviços

| serviço | efeito |
|---|---|
| `hikvision_access.open_door` | libera a porta momentaneamente (ação física) |
| `hikvision_access.reconcile_now` | força varredura do histórico |
| `hikvision_access.sync_persons` | carrega todos os usuários para o cache |
| `hikvision_access.setup_push` | grava um slot `httpHosts` do terminal apontando pro HA |
| `hikvision_access.remove_push` | libera o slot `httpHosts` registrado |

### API para o frontend

```
GET /api/hikvision_access/events?entry_id=&start=&end=&result=&person_id=&limit=&cursor=
GET /api/hikvision_access/events/{event_uid}
GET /api/hikvision_access/events/{event_uid}/image
GET /api/hikvision_access/persons/{entry_id}/{person_id}/image
WS  hikvision_access/subscribe
```
