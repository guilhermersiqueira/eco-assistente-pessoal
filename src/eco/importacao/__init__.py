"""Importação de memória a partir das conversas exportadas do claude.ai (backlog item 5).

Fluxo: o dono baixa o export; a Eco lê as conversas (`leitor`), descarta as curtas e as já importadas (`filtro`),
extrai fatos sobre ele (`extrator`) e guarda tudo como proposta (`estado`). Só o que o dono aprovar vira lembrança.
"""
