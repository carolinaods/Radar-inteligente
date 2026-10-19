# Radar Inteligente

SAP, Reforma Tributária e Inteligência de Clientes — portal de inteligência de mercado para gestão de projetos de TI.

**Como funciona (sem servidor):**

| Peça | Onde |
|---|---|
| Coleta diária | GitHub Actions, todos os dias às 12h (Brasília) — `.github/workflows/coleta-diaria.yml` |
| Dados | JSON versionado em `docs/data/` (`news.json`, `runs.json`, `config.json`) |
| Site | GitHub Pages, publicado a partir de `docs/` |
| Configuração | `config/sources.yml`, `config/clients.yml`, `config/keywords.yml`, `config/manual_items.yml` |
| Classificação | Regras (sem IA paga). Há um gancho (`llm_hook` em `collector/run.py`) para plugar a API da Anthropic no futuro |

Princípios: nunca inventar notícia, data ou link; uma fonte com erro não derruba as demais; se tudo falhar, a última base válida é preservada; o log de cada execução fica em `docs/data/runs.json` e na seção "Status da coleta" do site.

## Primeira publicação (uma vez)

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Actions → "Coleta diária e publicação" → Run workflow** (execução manual).
3. Abra a aba da execução e leia o log: fontes com erro aparecem com o motivo.
4. O site fica em `https://carolinaods.github.io/Radar-inteligente/` depois do primeiro deploy.

## Ajustes sem programar

- **Fontes:** edite `config/sources.yml` (adicionar, desativar com `enabled: false`, trocar URL).
- **Clientes:** edite `config/clients.yml` — revise `aliases` e `context` de cada cliente para evitar falsos positivos.
- **Palavras-chave e pesos:** `config/keywords.yml`.
- **Fontes sem feed (DOU, SAP Community, SAP Help):** inclua publicações reais em `config/manual_items.yml`.

## Testes

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Os textos usados nos testes são sintéticos e nunca são gravados em `docs/data` nem exibidos no site.

## Aviso

Repositório e site **públicos**. Use apenas informações públicas; não inclua dados internos de projetos ou de clientes.
