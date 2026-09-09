# Card de linha do tempo (`hikvision-access-card`)

O card **já vem embutido na integração** e é registrado automaticamente — não
precisa adicionar recurso no Lovelace nem instalar nada à parte.

## Adicionar ao painel

Editar painel → *Adicionar card* → procurar **"Hikvision Access — Timeline"**,
ou colar YAML:

```yaml
type: custom:hikvision-access-card
title: Acessos
# entry_id: 01M21TE50M...    # opcional — sem isso, junta todos os terminais
# limit: 30                   # itens por página
# range: today                # today | 7d | 30d | all
# result: all                 # all | granted | denied
# compact: false
```

## O que mostra

Cada linha: **foto do acesso** (clique = tela cheia), nome da pessoa (ou
`#ID` / "Desconhecido"), data/hora, terminal e porta, ícone do método
(facial/cartão/…) e um selo **✓ Permitido / ✕ Negado**.

Filtros no topo: período, resultado e busca por ID de pessoa. Botão
**"Carregar mais"** pagina o histórico. Acessos novos aparecem no topo em
tempo real (WebSocket) enquanto o card está aberto.

## Como funciona por baixo

O card consome a API autenticada da integração:

```
GET /api/hikvision_access/events?entry_id=&start=&result=&person_id=&limit=&cursor=
GET /api/hikvision_access/events/{event_uid}/image     (via auth/sign_path)
WS  hikvision_access/subscribe
```

Tudo local — as fotos são servidas de `<config>/hikvision_access/<entry>/media/`
por rota autenticada, nunca de URL pública.
