# Roadmap

Ordem de prioridade da spec (§ INSTRUÇÃO FINAL): confiabilidade de evento →
pessoa → horário → permitido/negado → foto → abrir porta → histórico → UI.

## Fase 1 — MVP backend  ✅ completa

- [x] Descoberta no hardware real (`docs/DISCOVERY-DS-K1T342MWX.md`)
- [x] `api.py` — cliente ISAPI async (Digest, lockout, XML/JSON), validado no device
- [x] `capabilities.py` — feature probing
- [x] `event_mapper.py` — (major,minor) → normalizado, do histograma real
- [x] `config_flow.py` + `options_flow`
- [x] `__init__.py` + `coordinator.py` (health / AcsWorkStatus)
- [x] `storage.py` — SQLite próprio (`<config>/hikvision_access/<entry>/hikvision_access.db`) + `schema_version` + dedupe `event_uid`
- [x] `event_parser.py` — multipart/JSON → `AccessEvent` (envelope `AccessControllerEvent`, `major/minor` vs `majorEventType/subEventType`)
- [x] `gateway.py` — funil único: dedupe → enriquecer (nome/foto) → entidades + bus event
- [x] `push.py` — HTTP View com token + `async_claim_slot` (registra slot `httpHosts` livre, opt-in)
- [x] `event_listener.py` — alertStream (multipart em streaming), reconnect exponencial, filtro `currentEvent`
- [x] `event_reconciler.py` — `AcsEvent` incremental por `beginSerialNo`+`endSerialNo`, overlap, 1ª varredura em background
- [x] `image_manager.py` — download `pictureURL`/`faceURL`, retenção configurável, `<config>/hikvision_access/<entry>/media/`
- [x] `person_manager.py` — cache `UserInfo/Search` (TTL 24h) + `async_prime`
- [x] `event.py` (EventEntity) + bus event `hikvision_access_event`
- [x] `image.py` (última foto) + `http_api.py` views autenticadas + WS `hikvision_access/subscribe`
- [x] Sensores de último acesso (pessoa/horário/resultado/método/porta) + "acessos hoje"
- [x] Entidades: `binary_sensor` (online/porta/tamper), `sensor` (conexão/firmware), `button` (abrir porta)
- [x] `services.yaml` + `services.py` — `open_door`, `reconcile_now`, `sync_persons`, `setup_push`, `remove_push`
- [x] `diagnostics.py`
- [x] Testes: `test_event_mapper`, `test_api_digest`, `test_event_parser` (fixtures reais), `test_storage` — 29 casos
- [x] `tests/ha/` — teste de ciclo de vida com `pytest-homeassistant-custom-component` (rodar em Linux/CI)
- [x] Validação: todos os 27 módulos importam contra HA real; pipeline completo testado no DS-K1T342MWX

### Pendências pequenas da Fase 1 (não bloqueiam uso)
- [ ] Rodar `tests/ha/` em CI Linux (ptcc não funciona no Windows)
- [ ] Capturar 1 evento **ao vivo** (`currentEvent:true`) e 1 **negado** para fixtures
- [ ] Confirmar `doorStatus` `[4]` no `AcsWorkStatus` (mapa provisório: 1=aberta)
- [ ] Testar `PUT RemoteControl/door/1` de verdade (ação física)
- [ ] `.github/workflows/` — hassfest + HACS validation + pytest

## Fase 2 — UI (planejado)

- [ ] `hikvision-access-card` (timeline, filtros, paginação)
- [ ] WebSocket `hikvision_access/subscribe`

## Fase 3 — Gestão de pessoas

- [ ] listar / criar / atualizar / excluir usuário, cadastrar face/cartão

## Fase 4 — Multi-fabricante

- [ ] abstração `AccessControlProvider`; `ControlIDProvider`, `ZKTecoProvider`

## Pendências de hardware (ver fim de `docs/DISCOVERY-DS-K1T342MWX.md`)

1. `PUT RemoteControl/door/1` — payload/resposta reais
2. evento ao vivo (`currentEvent:true`)
3. evento de acesso negado — existe? qual `minor`?
4. `doorStatus` no `AcsWorkStatus` = `[4]` — mapear valores
5. `httpHosts` slot 2 — registrar sem afetar o slot 1 da Avant
6. encoding dos nomes (`CÁSSIA` chegou como `C�SSIA` no JSON) — charset do terminal
7. `isSupportEventOptimizationCfg` / `isSupportEventStorageCfg` — podem controlar o re-dump histórico do alertStream

## Fase 2 — parcial (feito nesta rodada)

- [x] `camera.py` — câmera ao vivo (RTSP 1080p + snapshot ISAPI)
- [x] Campainha / vídeo-porteiro — `HikvisionCallCoordinator` (poll `VideoIntercom/callStatus` 3s),
      `binary_sensor.*_campainha`, `event.*_campainha` (ring/answered/ended + bus event)
- [x] Restore de "último acesso" do banco no restart
- [ ] `hikvision-access-card` (timeline Lovelace) — ainda não
- [ ] Capturar o evento de chamada no alertStream p/ trocar o poll por push
