# Descoberta — DS-K1T342MWX (homologação)

Resultado da fase de discovery (spec §38–39, §65). Feito com `tools/discovery.py`
contra o equipamento real em `https://192.0.2.10`, firmware **V3.16.1** (build 240528).

## Identidade

| campo | valor |
|---|---|
| model | DS-K1T342MWX |
| deviceType | ACS / accessControlTerminal |
| firmware | V3.16.1 (build 240528), BSP V1.6.1 |
| portas | 1 (`electroLockNum=1`, `doorNo` min/max = 1) |
| nome da porta | "PORTA DOS FUNDOS" (`openDuration=5s`) |
| usuários | 7 (todos com face, 0 cartões) |
| relógio | NTP OK, timezone `CST+4` (America/Cuiaba, -04:00) |
| rede | **conectado por Wi‑Fi** (interface cabeada `disconnect`) — relevante p/ reconexão |
| extra | expõe RTSP 1080p H.264 nos canais 101/102 (câmera opcional no HA) |

## Autenticação

- **HTTPS + HTTP Digest** funciona (realm `DS-6E392E44`, qop `auth`). Certificado self-signed → `verify_ssl=false`.
- **Lockout anti-brute-force**: após poucas falhas de auth o terminal responde
  `401` com `<userCheck><lockStatus>lock</lockStatus><unlockTime>N</unlockTime>`.
  → cliente ISAPI deve usar **só Digest** (sem fallback Basic) e nunca marcar
  "unsupported" um endpoint que respondeu durante o lock.

## Matriz de endpoints

| função | endpoint | resultado |
|---|---|---|
| device info | `GET /ISAPI/System/deviceInfo` | ✅ XML |
| hora | `GET /ISAPI/System/time` | ✅ |
| capabilities AC | `GET /ISAPI/AccessControl/capabilities?format=json` | ✅ |
| **stream tempo real** | `GET /ISAPI/Event/notification/alertStream` | ✅ `multipart/mixed; boundary=MIME_boundary`, partes `application/json` |
| **busca histórico** | `POST /ISAPI/AccessControl/AcsEvent?format=json` | ✅ `totalMatches`, paginação `searchResultPosition`, `maxResults` máx **30** |
| busca usuários | `POST /ISAPI/AccessControl/UserInfo/Search?format=json` | ✅ |
| contagem usuários | `GET /ISAPI/AccessControl/UserInfo/Count?format=json` | ✅ (`userNumber`, `bindFaceUserNumber`) |
| estado porta/relé | `GET /ISAPI/AccessControl/AcsWorkStatus?format=json` | ✅ `doorLockStatus`, `doorStatus`, `magneticStatus` |
| parâmetros porta | `GET /ISAPI/AccessControl/Door/param/1?format=json` | ✅ (nome, durações) |
| **abrir porta** | `PUT /ISAPI/AccessControl/RemoteControl/door/1` | caps ✅ — `cmd opt="open,close,alwaysOpen,alwaysClose"` (PUT não disparado no discovery) |
| foto do evento | campo `pictureURL` no `AcsEvent` | ✅ `GET` com Digest → `image/jpeg` 768×432 (~30 KB) |
| foto cadastrada | campo `faceURL` no `UserInfo` | ✅ `.../LOCALS/pic/enrlFace/...jpg` |
| push de eventos | `GET/PUT /ISAPI/Event/notification/httpHosts` | ✅ **já há 1 host configurado** (ver abaixo) |
| `Event/capabilities`, `Event/triggers` | — | ❌ `notSupport` (terminal de acesso usa `AccessControl/*`) |

## Formato de evento

**Diferença de nomes entre as duas rotas** (o mapper precisa normalizar os dois):

| | alertStream | AcsEvent (histórico) |
|---|---|---|
| tipo maior | `majorEventType` | `major` |
| tipo menor | `subEventType` | `minor` |
| horário | `dateTime` | `time` |
| id sequencial | `serialNo` + `frontSerialNo` | `serialNo` |
| ao vivo? | `currentEvent` (`true`/`false`) | — |
| foto | **não vem** (só `FaceRect`) | `pictureURL` |

Envelope comum: `eventType:"AccessControllerEvent"`, `deviceName`, `name`,
`employeeNoString`, `userType`, `currentVerifyMode`, `mask`, `doorNo`, `cardReaderNo`.

### Códigos realmente emitidos por este terminal (histograma de ~200 dias, 6321 eventos)

| major.minor | freq | significado | tem pessoa/foto |
|---|---:|---|---|
| **5.75** | 610 | **face verificada → acesso concedido** | ✅ `name`, `employeeNoString`, `pictureURL` |
| 5.21 | 999 | relé/porta destravado | — |
| 5.22 | 962 | relé/porta travado | — |
| 5.23 | 437 | porta aberta (sensor magnético) | — |
| 5.24 | 435 | porta fechada (sensor magnético) | — |
| 3.1029 | 131 | evento de operação | — |
| 2.39 / 2.1031 / 2.1024 / 3.1024 / 3.112 | <10 cada | exceções / operação remota | `remoteHostAddr` em alguns |

**Não há nenhum evento de acesso NEGADO no log** (nenhuma "face não reconhecida",
sem cartões). A confirmar no hardware: se o terminal emite evento para tentativa
negada de face — pode exigir configuração, ou simplesmente não gerar evento (spec §51).

## Rota de eventos — decisão pendente

O terminal oferece **duas rotas**, e este equipamento **já usa a de push**:

### A) `alertStream` (pull — HA conecta no terminal)
- Funciona, formato conhecido.
- ⚠️ **Ao conectar, o terminal re-despeja o log inteiro** (`currentEvent:false`,
  eventos de 2025) antes de chegar aos atuais — ~19 mil eventos. O listener teria
  que descartar tudo com `currentEvent:false` e só agir em `currentEvent:true`.
- ⚠️ Terminal em Wi‑Fi → reconexões frequentes → re-dump a cada reconexão.

### B) `httpHosts` (push — terminal faz POST para um servidor HTTP)
- É o método "nativo". `pictureURLType=binary` → a **foto vem junto** no multipart do POST.
- Há **2 slots**. **Slot 1 já está ocupado** por um backend Avant:
  `http://<host>:5457/api/v1/acessos/hikvision/alt/DS-K1T342MWX...` (`eventMode=all`, heartbeat 30s).
  Slot 2 está livre (`EHome`, vazio).
- A integração precisaria subir uma HTTP view no HA e registrar o **slot 2**
  apontando para ela. Sem re-dump histórico; foto inclusa.

> Recomendação: **rota B (push, slot 2) como principal + `AcsEvent` como reconciliador**.
> Mantém a spec (§10 reconciliação) e evita o problema do re-dump. `alertStream`
> fica como fallback opcional.

## Itens ainda a validar no hardware

1. `PUT /ISAPI/AccessControl/RemoteControl/door/1` com `<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>` — payload e resposta reais (ação física, fazer sob demanda).
2. Um evento **ao vivo** (`currentEvent:true`) — capturar formato exato quando alguém usar o terminal.
3. Um evento de **acesso negado** — existe? qual `minor`?
4. `doorStatus` no `AcsWorkStatus` retornou `[4]` — mapear valores (0/1/2/4 = ?).
5. Foto cadastrada (`faceURL`) — confirmar download com Digest.
6. Slot 2 do `httpHosts` — testar registro de um segundo host sem afetar o slot 1 da Avant.

---

## Adendo — comportamento do FW V4.48.40 (descoberto rodando ao vivo)

Os dois terminais foram atualizados para **V4.48.40** depois da homologação inicial.
Mudanças de comportamento que quebraram o cliente e foram corrigidas no **v0.1.5**:

- **Reuso de nonce Digest é limitado**: o 3º uso do mesmo nonce é rejeitado com
  `401 <userCheck>` (sem header `WWW-Authenticate`). O `requests.HTTPDigestAuth`
  nunca trava porque pede um desafio novo a cada request — o cliente agora faz igual
  (`_fresh_digest()`, um `GET` sem auth por request). Requests sem auth **não** contam
  para o lockout.
- **realm** agora vem em minúsculas (`DS-6e392e44`).
- **`alertStream`**: o slot fica preso por bastante tempo depois que o cliente
  desconecta; toda nova conexão responde `404 <ResponseStatus>` até liberar.
  → `HikvisionStreamBusyError`, listener recua 120s, reconciliador cobre.
- 401 por nonce velho = `<ResponseStatus>` `invalidOperation`; 401 por senha errada /
  contador de tentativas = `<userCheck>` com `<retryLoginTime>N`; lock de fato =
  `<lockStatus>lock</lockStatus>` + `<unlockTime>`.

## Vídeo + campainha (VideoIntercom) — confirmado no .118

- **RTSP**: `rtsp://<user>:<pass>@<host>:554/Streaming/Channels/101` (principal, 1080p H.264)
  ou `/102` (sub). Canais em `GET /ISAPI/Streaming/channels`.
- **Snapshot**: `GET /ISAPI/Streaming/channels/101/picture` → `image/jpeg` (~69 KB). Digest normal.
- **Campainha / botão de chamada**: `GET /ISAPI/VideoIntercom/capabilities?format=json` →
  `isSupportCallStatus`, `isSupportCallSignal`, `isSupportCallerInfo`, `isSupportKeyCfg` = true.
  `GET /ISAPI/VideoIntercom/callStatus?format=json` → `{"CallStatus":{"status":"idle"}}` —
  vira `ring`/`onCall` quando o botão é apertado. A integração faz poll disso a cada 3s
  (`HikvisionCallCoordinator`) → `binary_sensor.*_campainha` + `event.*_campainha`.
- **A validar**: se o evento de chamada também chega pelo `alertStream` (seria push instantâneo
  em vez de poll). Capturar apertando o botão com o listener conectado.
