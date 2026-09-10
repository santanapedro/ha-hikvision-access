# Ícone / logo da integração

O Home Assistant **não** carrega ícones de integrações personalizadas a partir
deste repositório — eles vêm do repositório central
[`home-assistant/brands`](https://github.com/home-assistant/brands), em
`custom_integrations/hikvision_access/`.

Enquanto o PR ao `brands` não é aceito, a integração aparece com o ícone genérico.

## Como gerar os arquivos para o PR

1. Salve a arte original (PNG, fundo transparente de preferência) em
   `brand/source.png`.
2. Rode:

   ```bash
   python brand/make_brand_assets.py
   ```

   Isso gera, em `brand/out/`:

   - `icon.png`      256×256  (quadrado, recortado no conteúdo)
   - `icon@2x.png`   512×512
   - `logo.png`      até 512 de largura, altura proporcional
   - `logo@2x.png`

3. Abra um PR em `home-assistant/brands` adicionando esses arquivos em
   `custom_integrations/hikvision_access/` (seguindo o CONTRIBUTING de lá:
   PNG, sRGB, sem metadados, fundo transparente, recorte justo).

Requisitos oficiais: https://github.com/home-assistant/brands#adding-a-new-brand
