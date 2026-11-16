"""Imagens de conferência da E8 (aba Grafo com o painel Gerenciar e o cartão), com as fontes reais do Windows, num banco
temporário (não toca no real). Rodar:
    set QT_QPA_PLATFORM=windows
    py -m uv run --no-sync python tests/imagens_e8.py tests/resultados/imagens
"""
import os, sys, tempfile, time
from pathlib import Path

d = Path(tempfile.mkdtemp())
os.environ["ECO_DADOS_DIR"] = str(d / "dados")
os.environ["ECO_ENV_FILE"] = str(d / "nao.env")
os.environ["ECO_GRAFO_VISTA"] = "2d"
os.environ["ECO_LIGACAO_AUTO"] = "nao"
(d / "dados").mkdir()
import eco.config as config
config.PASTA_ECO = d / ".eco"
config.PASTA_ECO.mkdir()
from PySide6.QtWidgets import QApplication
app = QApplication([])
from eco.chat.armazenamento import Banco
from eco.grafo.dados import Grafo
from eco.grafo.higiene import Higiene
from eco.visual.grafo import PainelGrafo
import eco.visual.grafo as vg
vg.PASTA_ECO = config.PASTA_ECO
from eco.config import carregar_config

banco = Banco(d / "eco.db")
g = Grafo(banco)
ids = {n: g.criar_ideia(n, origem="manual", resumo=r).id for n, r in [
    ("Programação", "estudos de programação"), ("Python", "linguagem que o dono estuda"),
    ("Automação CSM", "scripts de fechamento dos motoristas"), ("Eco", "assistente de voz"), ("Viagem", "Ouro Preto em julho"),
    ("Rascunho antigo", "ideia velha")]}
g.definir_pai(ids["Python"], ids["Programação"])
g.ligar_classificada(ids["Automação CSM"], ids["Python"], "depende_de", 0.9, "os scripts são em Python", "ativa", de=ids["Automação CSM"], tipo="causal")
g.ligar_classificada(ids["Eco"], ids["Python"], "relacionado_a", 0.65, "a Eco é feita em Python", "revisar")
g.ligar_classificada(ids["Viagem"], ids["Eco"], "relacionado_a", 0.35, "talvez", "sugestao")
g.arquivar(ids["Rascunho antigo"], "não uso mais")
g.proteger(ids["Viagem"])
a = g.criar_fato("Vitor trabalha às 8h.", "Horário")
b = g.criar_fato("Vitor trabalha às 9h.", "Horário novo")
h = Higiene(g, None)
h._propor("contradicao", [a.id, b.id], "horários diferentes", {})
h._propor("fundir", [ids["Eco"], ids["Python"]], "exemplo", {"texto": "texto juntado"})
p = PainelGrafo(carregar_config(), banco, object(), confirmar=lambda t: True)
p.resize(1400, 900)
p.move(-3000, -3000)
p.show()
p.vista.navegar(ids["Python"])
p.alternar_gerenciar(True)
for _ in range(40):
    app.processEvents(); time.sleep(0.01)
saida = Path(sys.argv[1])
saida.mkdir(parents=True, exist_ok=True)
p.grab().save(str(saida / "e8_real_2d.png"))
p.gerenciar.verticalScrollBar().setValue(600)
for _ in range(10):
    app.processEvents(); time.sleep(0.01)
p.grab().save(str(saida / "e8_real_2d_rolado.png"))
p.alternar_vista("orbe")
p.vista.navegar(ids["Python"])
for _ in range(40):
    app.processEvents(); time.sleep(0.01)
p.grab().save(str(saida / "e8_real_orbe.png"))
print("ok")
