# Roadmap

Ordem de prioridade da spec (§ INSTRUÇÃO FINAL): confiabilidade de evento →
pessoa → horário → permitido/negado → foto → abrir porta → histórico → UI.

**Estado atual: v0.2.8 — Fases 1 e 2 completas, em produção** em dois
DS-K1T342MWX (FW V4.48.40).

---

## Fase 1 — MVP backend ✅ completa

- [x] Descoberta no hardware real (`docs/DISCOVERY-DS-K1T342MWX.md`)
- [x] `api.py` — cliente ISAPI async (Digest fresh-per-request, detecção de lockout,
      XML/JSON sniff, limite de corpo em downloads)
- [x] `capabilities.py` — feature probing
- [x] `event_mapper.py` — (major,minor) → normalizado, do histograma real
- [x] `config_flow.py` + `options_flow` + **reauth** (`reauth_confirm`, v0.2.8)
- [x] `__init__.py` + `coordinator.py` (health/AcsWorkStatus, call/callStatus, lock-aware)
- [x] `storage.py` — SQLite próprio + `schema_version` + dedupe `event_uid`
- [x] `event_parser.py` — multipart/JSON → `AccessEvent`
- [x] `gateway.py` — funil único: dedupe → enriquecer → entidades + bus + `_maybe_upgrade`
- [x] `push.py` — HTTP View com token + fila limitada + `async_claim_slot` (opt-in, não rouba EHome)
- [x] `event_listener.py` — alertStream em streaming, reconnect exponencial, filtro `currentEvent`
- [x] `event_reconciler.py` — `AcsEvent` incremental por serialNo, overlap + backfill de fotos
- [x] `image_manager.py` — download `pictureURL`/`faceURL`, retenção, partição por mês
- [x] `person_manager.py` — cache `UserInfo/Search` (TTL 24h) + cache negativo (1h) + `async_prime`
- [x] `event.py` + bus event `hikvision_access_event`
- [x] `image.py` + `http_api.py` (views autenticadas) + WS `hikvision_access/subscribe`
- [x] Sensores de último acesso + "acessos hoje"
- [x] `binary_sensor` (online/porta/tamper/campainha), `sensor` (conexão/firmware), `button` (abrir porta)
- [x] `services.py` — `open_door`, `reconcile_now`, `sync_persons`, `setup_push`, `remove_push` (removidos no unload)
- [x] `diagnostics.py` (device/caps/health/listener/pictures/recent_decisions)
- [x] Testes leves (39) + `tests/ha/` (ciclo de vida) + CI (ruff, pytest, hassfest, HACS)
- [x] Auditoria de segurança/confiabilidade/recursos (v0.2.7–v0.2.8)

## Fase 2 — UI ✅ completa

- [x] `hikvision-access-card` — timeline com foto/pessoa/método/resultado, filtros
      (período/resultado/pessoa), paginação, live via WS, lightbox, valores escapados (XSS)
- [x] Auto-registro do card (`add_extra_js_url`, sem passo de "Recurso")
- [x] `camera.py` — RTSP 1080p + snapshot ISAPI
- [x] Campainha / vídeo-porteiro — `HikvisionCallCoordinator`, `binary_sensor.*_campainha`,
      `event.*_campainha`, intervalo de poll configurável
- [x] Restore de "último acesso" do banco no restart

---

## Pendências de validação em hardware (não bloqueiam uso)

1. [ ] Capturar 1 evento de **acesso negado** ao vivo — confirmar `minor` e o mapeamento
2. [ ] Capturar 1 evento **ao vivo** (`currentEvent:true`) com foto inline (rota push)
3. [ ] Testar `PUT RemoteControl/door/1` de verdade (ação física)
4. [ ] Confirmar `doorStatus [4]` no `AcsWorkStatus` (mapa provisório: 1 = aberta)
5. [ ] Registrar `httpHosts` slot 2 sem afetar o slot 1 da Avant (rota push real)

## Fase 3 — Gestão de pessoas (planejado)

- [ ] Listar usuários cadastrados (sensor/atributos ou painel)
- [ ] Criar / atualizar / excluir usuário via serviço
- [ ] Cadastrar face (upload de imagem) e cartão
- [ ] Sincronização bidirecional pessoa ↔ HA (opt-in)

## Fase 4 — Multi-fabricante (planejado)

- [ ] Abstração `AccessControlProvider`
- [ ] `ControlIDProvider`, `ZKTecoProvider`
- [ ] Config flow escolhe o fabricante; o resto da integração é agnóstico

---

## Melhorias candidatas (backlog priorizado)

### Alta — qualidade de integração HA

- [ ] **`async_step_reconfigure`** — hoje não dá para trocar host/porta/HTTPS sem
      remover e readicionar a integração (`supports_reconfigure: false`)
- [ ] **Descoberta automática** — SADP (UDP 37020) ou WS-Discovery/ONVIF → o terminal
      aparece sozinho em *Dispositivos e Serviços*
- [ ] **Repair issues** (`ir.async_create_issue`) para estados acionáveis: lockout
      persistente, conflito de slot de push, `alertStream` preso — em vez de só log
- [ ] **`configuration_url`** no `DeviceInfo` (link para a UI web do terminal)
- [ ] **Device triggers/conditions** para automações ("quando fulano tiver acesso concedido")

### Alta — retenção e armazenamento

- [ ] **Retenção de eventos** (linhas do SQLite) — hoje só as fotos são purgadas; as
      linhas de evento nunca são apagadas e o banco cresce para sempre
- [ ] `PRAGMA synchronous=NORMAL` (seguro com WAL) + `VACUUM` periódico após purga

### Média — segurança e robustez

- [ ] `web.FileResponse` no lugar de `read_bytes()` nas views de imagem (streaming,
      cache, suporte a Range) — hoje cada request carrega o arquivo inteiro na memória
- [ ] Rotação do token de push (serviço/botão) — hoje é fixo pela vida da entry
- [ ] Opção de **pinar o fingerprint do certificado** do terminal, alternativa ao
      `verify_ssl` ligado/desligado
- [ ] Restringir as views/WS a usuários admin (dados de acesso são sensíveis) — ou
      documentar explicitamente

### Média — performance

- [ ] Fundir os polls de health e callStatus num só ciclo (ambos batem no terminal)
- [ ] Cache de TTL curto no snapshot da câmera (HA busca a still da entidade
      periodicamente quando um dashboard mostra o card)

### Média — cobertura de testes

- [ ] Testes para `gateway._maybe_upgrade`, reconciliador (widening por serial),
      `push` (fila cheia, token), views do `http_api`, config flow (reauth/options)
- [ ] Gate de cobertura no CI
- [ ] `mypy`/`pyright` estrito + `py.typed`

### Baixa — card e distribuição

- [ ] i18n do card (strings pt-BR estão hardcoded) + seletor de intervalo custom
- [ ] Lista virtualizada para históricos grandes
- [ ] Blueprint de automação "notificar com foto no acesso/negado"
- [ ] `CHANGELOG.md` (keep-a-changelog), `CONTRIBUTING.md`
- [ ] PR ao `home-assistant/brands` (logo/ícone) — check "brands" do HACS está ignorado no CI
- [ ] Submeter ao repositório default do HACS

## Pendências de hardware (ver fim de `docs/DISCOVERY-DS-K1T342MWX.md`)

1. `PUT RemoteControl/door/1` — payload/resposta reais
2. evento ao vivo (`currentEvent:true`)
3. evento de acesso negado — existe? qual `minor`?
4. `doorStatus` no `AcsWorkStatus` = `[4]` — mapear valores
5. `httpHosts` slot 2 — registrar sem afetar o slot 1 da Avant
6. `isSupportEventOptimizationCfg` / `isSupportEventStorageCfg` — podem controlar o
   re-dump histórico do alertStream
