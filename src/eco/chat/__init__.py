"""Chat da Eco: espaços, conversas, histórico local, provedor de IA e fala das respostas.

Camadas (a interface só conhece `ServicoChat`; trocar de provedor de IA é implementar `Provedor`):
  armazenamento.py  SQLite local (~/.eco/eco.db)
  provedor.py       interface `Provedor` + implementação do Claude (streaming)
  servico.py        regras do chat: monta o prompt do espaço, guarda o histórico, gera e para a resposta
  fala.py           transforma a resposta que chega aos poucos em fala interrompível
  render.py         Markdown -> HTML com blocos de código copiáveis
"""
