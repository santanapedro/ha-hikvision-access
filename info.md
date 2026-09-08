# Hikvision Access

Integração **local-first** para terminais Hikvision de controle de acesso (linha
MinMoe DS-K1T3xx) via ISAPI — sem nuvem.

- Eventos de acesso em tempo real (push `httpHosts` ou `alertStream`)
- Pessoa, método, resultado permitido/negado, horário e **foto** do acesso
- Histórico local em SQLite (não usa o Recorder do HA)
- Reconciliação automática — nenhum evento perdido em quedas de rede
- Abertura remota da porta, estado do relé/porta, violação
- EventEntity + evento no bus (`hikvision_access_event`) para automações
- API autenticada + WebSocket para dashboards

Testado em **DS-K1T342MWX / FW V3.16.1**. Configuração pela UI, sem YAML.
