# Discovery tool — Hikvision access terminals

`discovery.py` probes a real terminal (DS-K1T323 / DS-K1T342 MinMoe family) so the
integration parser is built from real responses, not assumptions (spec §38, §39, §65, §67).

**Read-only.** GET requests + the documented `AcsEvent` / `UserInfo/Search` POSTs, plus
holding `alertStream` open. It never opens a door, writes a user, or changes config.

## Run

```bash
pip install requests
python discovery.py --host <IP> -u <user> -p <password> --duration 40
```

or create `tools/device.local.json` (git-ignored):

```json
{ "host": "192.168.1.50", "username": "integration_ha", "password": "secret",
  "use_https": true, "verify_ssl": false, "port": null }
```

then just `python discovery.py`.

Add `--http` if the terminal has no HTTPS, `--skip-stream` to skip the realtime capture.

While the alertStream countdown runs, **badge a face / card / PIN on the terminal a few
times** (both a valid user and an unknown one) so the capture contains real events.

## Output

`tools/output/discovery-<timestamp>/`

| file | purpose |
|---|---|
| `summary.json` | endpoint support matrix + alertStream result |
| `capabilities.json` | the `ds-k1t3xx-capabilities.json` from the spec |
| `raw/*.txt` | full redacted response for every endpoint |
| `fixtures/*` | redacted samples to seed the parser and tests |

Serial, MAC, IP, names and card numbers are redacted on the way out, but **review the
files before sharing them** — firmware quirks vary.
