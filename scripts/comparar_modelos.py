"""Compara dois modelos na análise do grafo, com as mesmas conversas, e escreve um relatório lado a lado.

    py -m uv run python scripts/comparar_modelos.py                  # usa conversas de AMOSTRA (banco temporário)
    py -m uv run python scripts/comparar_modelos.py --db ~/.eco/eco.db   # usa as suas conversas reais (só lê: trabalha numa cópia)

Custa centavos (cada modelo analisa as mesmas conversas). O relatório vai para o caminho de --saida.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eco.chat.armazenamento import Banco  # noqa: E402
from eco.config import PASTA_ECO, carregar_config  # noqa: E402
from eco.grafo.analise import Analisador, CompletadorClaude  # noqa: E402
from eco.grafo.dados import Grafo  # noqa: E402
from eco.uso import RegistroUso  # noqa: E402

AMOSTRA = {
    "Investimentos": [
        ("Montar reserva de emergência",
         "Ganho cerca de R$ 4.000 por mês e gasto uns R$ 2.500. Quero montar uma reserva de emergência antes de pensar em investir mais. Quanto devo guardar e onde?",
         "Uma reserva de emergência costuma cobrir de 6 a 12 meses de gastos, então no seu caso entre R$ 15.000 e R$ 30.000. Para ela, o mais indicado é liquidez diária e baixo risco: Tesouro Selic ou um CDB de liquidez diária que pague pelo menos 100% do CDI. Só depois de completar a reserva vale pensar em investir o restante em outros produtos."),
        ("Tesouro Selic ou CDB",
         "Entre Tesouro Selic e CDB de liquidez diária, qual é melhor para a reserva? Vi que o CDB tem cobertura do FGC.",
         "Os dois servem. O Tesouro Selic tem o risco do governo federal e taxa de custódia só acima de R$ 10 mil (0,2% ao ano). O CDB de liquidez diária com 100% do CDI costuma render parecido, e o FGC cobre até R$ 250 mil por instituição. Para a reserva, o importante é liquidez diária e não passar do limite do FGC em um banco só."),
        ("Dividendos e ações",
         "Depois que a reserva estiver pronta, quero começar em ações que paguem dividendos. Como escolher?",
         "Olhe o histórico de dividendos de pelo menos 5 anos, o dividend yield, a dívida da empresa e se o lucro é recorrente. Setores como elétricas e bancos costumam pagar bem. Comece pequeno, diversifique em mais de uma empresa e não use dinheiro da reserva de emergência para isso."),
        ("Renda fixa para o longo prazo",
         "E para objetivos de 5 a 10 anos, como a entrada de um apartamento, vale renda fixa? Tenho dúvida entre IPCA+ e prefixado.",
         "Para esse prazo, o Tesouro IPCA+ protege da inflação e é bom para objetivos de longo prazo, desde que você segure até perto do vencimento, porque o preço oscila antes disso. Prefixado só compensa se você acreditar que os juros vão cair. Você pode combinar com um pouco de ações de dividendos para diversificar."),
    ],
    "Estudos": [
        ("Prova de cálculo",
         "Tenho prova de Cálculo 2 na sexta, de integrais por partes e substituição trigonométrica. Como estudar em três dias?",
         "Faça uma lista de exercícios por tipo, resolva sem olhar a solução e só depois confira. Use revisão espaçada: no primeiro dia resolva os tipos básicos, no segundo revise os erros e no terceiro faça uma prova simulada com tempo. Anote as fórmulas de substituição trigonométrica em uma folha única."),
        ("Revisão espaçada",
         "Você falou de revisão espaçada. Como aplico isso para todas as matérias da faculdade, inclusive Engenharia de Software?",
         "Revise o conteúdo 1 dia, 3 dias, 7 dias e 21 dias depois de estudar. Use flashcards para conceitos como padrões de projeto e UML, e problemas resolvidos para cálculo. Reserve 20 minutos por dia só para revisões, e deixe os blocos longos para conteúdo novo."),
    ],
    "Leitura": [
        ("Meta de livros",
         "Quero ler 12 livros este ano, mas só li 3. Como criar o hábito de leitura diário? Estou lendo O Investidor Inteligente.",
         "Leia 20 minutos por dia, sempre no mesmo horário, e deixe o livro à vista. Para O Investidor Inteligente, divida por capítulos e anote uma ideia por capítulo. Livros de investimento combinam bem com o que você está aprendendo sobre renda fixa e ações."),
        ("Livros sobre investimento",
         "Pode sugerir mais livros sobre finanças pessoais para ler depois de O Investidor Inteligente?",
         "Pai Rico, Pai Pobre para mentalidade, Psicologia Financeira para comportamento e Casais Inteligentes Enriquecem Juntos para organização do orçamento. Comece pelo que responde a dúvida que você tem agora: se é montar a reserva de emergência, o mais prático é um livro de orçamento."),
    ],
}


def montar_amostra(banco: Banco) -> None:
    for nome_espaco, conversas in AMOSTRA.items():
        e = banco.criar_espaco(nome_espaco)
        for titulo, pergunta, resposta in conversas:
            c = banco.criar_conversa(e.id, titulo)
            banco.adicionar_mensagem(c.id, "user", pergunta)
            banco.adicionar_mensagem(c.id, "assistant", resposta)


def rodar(modelo: str, origem: Path) -> dict:
    pasta = Path(tempfile.mkdtemp())
    copia = pasta / "eco.db"
    shutil.copy(origem, copia)
    cfg = carregar_config()
    banco = Banco(copia)
    grafo = Grafo(banco)
    registro = RegistroUso(PASTA_ECO / "uso.jsonl")
    an = Analisador(banco, grafo, CompletadorClaude(cfg, registro=registro, modelo=modelo), cfg)
    import dataclasses

    an.cfg = dataclasses.replace(cfg, modelo_grafo=modelo, teto_grafo_usd=5.0)
    resumo = an.executar(an.pendentes())
    nomes = {i.id: i for i in grafo.listar_ideias()}
    espacos = {e.id: e.nome for e in banco.listar_espacos()}
    return {
        "modelo": modelo,
        "resumo": resumo,
        "ideias": [(espacos.get(i.espaco_id, "Geral"), i.nome, i.resumo) for i in nomes.values()],
        "ligacoes": [(nomes[l.a].nome, nomes[l.b].nome, l.motivo) for l in grafo.listar_ligacoes()],
    }


def relatorio(a: dict, b: dict) -> str:
    out = ["# Comparação de modelos na análise do grafo\n"]
    for r in (a, b):
        s = r["resumo"]
        out.append(
            f"- **{r['modelo']}**: {s.analisadas} conversas, {len(r['ideias'])} ideias, {len(r['ligacoes'])} ligações, "
            f"custo ~US$ {s.custo:.4f}, erros: {len(s.erros)}"
        )
    for titulo, chave in (("Ideias", "ideias"), ("Ligações", "ligacoes")):
        out.append(f"\n## {titulo}\n")
        for r in (a, b):
            out.append(f"### {r['modelo']}\n")
            for item in r[chave]:
                out.append("- " + " | ".join(str(x) for x in item if x))
            out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="banco com conversas reais (padrão: amostra)")
    ap.add_argument("--a", default="claude-haiku-5-5")
    ap.add_argument("--b", default="claude-sonnet-5-5")
    ap.add_argument("--saida", default="comparacao-modelos.md")
    args = ap.parse_args()
    if args.db:
        origem = Path(args.db).expanduser()
    else:
        origem = Path(tempfile.mkdtemp()) / "amostra.db"
        b = Banco(origem)
        montar_amostra(b)
        b.fechar()
    ra, rb = rodar(args.a, origem), rodar(args.b, origem)
    texto = relatorio(ra, rb)
    Path(args.saida).write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
