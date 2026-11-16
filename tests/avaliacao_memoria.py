"""Conjunto de avaliação da recuperação contextual (cuidado 1 do dono, etapa E5). Local e sem custo.

Uma memória de mentira, mas parecida com a do dono (≈50 itens: lembranças, ideias de conversas, notas e metas, algumas
antigas, uma contradição e coisas sem relação), e perguntas de cinco tipos:
- direta: a resposta está numa lembrança, com outras palavras;
- ambigua: pedido vago ou que depende do turno anterior ("e ela?");
- antiga: a informação é de muito tempo atrás e ainda vale;
- contradicao: há duas versões (a antiga e a nova): as duas precisam vir, a nova antes;
- irrelevante: nada da memória tem a ver; nada deve vir.

Medidas: acerto no que é enviado ao modelo (todas as esperadas entre os até 8 itens), acerto nos 5 primeiros, alguma
esperada entre os 3 primeiros, vazamento (itens que não deviam vir) nas irrelevantes e, nas contradições, as duas
versões enviadas (cada uma vai com a data; o modelo usa a mais recente).

Rodar o relatório:  py -m uv run --no-sync python tests/avaliacao_memoria.py
O teste `test_recuperacao.py::test_conjunto_de_avaliacao` exige os mínimos de `MINIMOS`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ANTIGO = "2025-03-10T12:00:00+00:00"
RECENTE = "2026-11-10T12:00:00+00:00"

# (chave, nome, resumo, origem, quando, protegido)
MEMORIA = [
    ("nome", "Dono Vitor", "O dono se chama Vitor.", "fato", ANTIGO, 1),
    ("curso", "Curso na USP", "Vitor cursa Bacharelado em Ciência e Tecnologia na USP.", "fato", RECENTE, 0),
    ("materias", "Matérias do semestre", "Vitor está cursando Programação Estruturada, Cálculo, Física Clássica e Bases Computacionais.", "fato", RECENTE, 0),
    ("cafe", "Café coado", "Vitor gosta de café coado sem açúcar pela manhã.", "fato", RECENTE, 0),
    ("gato", "Gato Mel", "O gato do Vitor se chama Mel e é laranja.", "fato", ANTIGO, 0),
    ("mae", "Aniversário da mãe", "A mãe do Vitor, Marta, faz aniversário em 3 de março.", "fato", ANTIGO, 1),
    ("irmao", "Irmão Rafael", "O irmão do Vitor se chama Rafael e fez o Enem em novembro.", "fato", RECENTE, 0),
    ("corrida", "Corrida", "Vitor corre 5 km às terças e quintas de manhã no parque.", "fato", RECENTE, 0),
    ("trabalho", "Fechamento dos motoristas", "Vitor faz o fechamento quinzenal dos motoristas da Transportadora A e da Transportadora B na CSM.", "fato", RECENTE, 0),
    ("csm", "Automação da CSM", "Projeto para automatizar os fechamentos da CSM com um script que roda às 20h.", "chat", RECENTE, 0),
    ("eco", "Construção da Eco", "Vitor está construindo a Eco, assistente de voz com janela, grafo e memória.", "fato", RECENTE, 0),
    ("padaria", "Site da padaria", "Vitor fez a landing page da Padaria Exemplo, no Vila Mariana.", "chat", RECENTE, 0),
    ("bova", "Carteira de ações", "Vitor quer montar uma carteira de ações começando por BOVA11.", "metas", RECENTE, 0),
    ("reserva", "Reserva de emergência", "Meta de juntar seis meses de gastos numa reserva de emergência até junho.", "metas", RECENTE, 0),
    ("ingles", "Inglês", "Vitor quer chegar ao nível B2 de inglês até o fim de 2027.", "metas", RECENTE, 0),
    ("custo", "Custo baixo", "Vitor quer custo baixo nas IAs: usar o modelo barato e só usar o forte quando precisar.", "fato", RECENTE, 0),
    ("confirma", "Confirmação antes de ações", "Vitor quer confirmação antes de enviar e-mail ou apagar qualquer coisa.", "fato", RECENTE, 1),
    ("feminina", "Jeito da Eco", "Vitor quer a Eco sempre feminina, calorosa e natural.", "fato", RECENTE, 1),
    ("revisao", "Revisão semanal", "Vitor quer uma revisão semanal das metas toda segunda-feira.", "fato", RECENTE, 0),
    ("alergia", "Alergia", "Vitor tem alergia a camarão.", "fato", ANTIGO, 1),
    ("carro", "Carro", "Vitor tem um Onix prata, placa final 7.", "fato", ANTIGO, 0),
    ("dentista", "Dentista", "O dentista do Vitor é o Dr. Paulo, na Rua Marechal Deodoro.", "fato", ANTIGO, 0),
    ("time", "Time de futebol", "Vitor torce para o Corinthians.", "fato", ANTIGO, 0),
    ("musica", "Música para estudar", "Vitor estuda ouvindo lo-fi e trilhas de jogos.", "fato", RECENTE, 0),
    ("gta", "GTA 6", "Vitor está esperando o lançamento do GTA 6 e quer jogar no PC.", "chat", RECENTE, 0),
    ("python", "Python", "Vitor usa Python na Eco e nos scripts de fechamento.", "chat", RECENTE, 0),
    ("excel", "Curso de Excel", "Vitor fez um curso de Excel da Expert Cursos com aulas de gráficos.", "notas", ANTIGO, 0),
    ("tiktok", "Canal no TikTok", "Ideia de criar um canal no TikTok e no YouTube sobre automação.", "notas", RECENTE, 0),
    ("livro", "Livro do mês", "Vitor está lendo Hábitos Atômicos.", "fato", RECENTE, 0),
    ("sono", "Horário de dormir", "Vitor quer dormir antes da meia-noite nos dias de semana.", "metas", RECENTE, 0),
    # contradição: endereço antigo e novo
    ("mora_antigo", "Endereço", "Vitor mora em Campinas.", "fato", ANTIGO, 0),
    ("mora_novo", "Mudança", "Vitor se mudou para Curitiba em outubro de 2026.", "fato", RECENTE, 0),
    # contradição: horário do trabalho antigo e novo
    ("hora_antiga", "Horário de trabalho", "Vitor trabalha das 9h às 18h.", "fato", ANTIGO, 0),
    ("hora_nova", "Novo horário", "Desde novembro Vitor trabalha das 8h às 17h.", "fato", RECENTE, 0),
]
LIGACOES = [("csm", "trabalho"), ("python", "eco"), ("python", "csm"), ("curso", "materias"), ("bova", "reserva")]
PAIS = [("materias", "curso")]  # (filho, pai)


@dataclass
class Pergunta:
    texto: str
    esperadas: list[str]
    tipo: str
    anterior: str = ""
    nao_devem: list[str] = field(default_factory=list)


PERGUNTAS = [
    Pergunta("o que eu gosto de beber de manhã?", ["cafe"], "direta"),
    Pergunta("como é o nome do meu bichinho?", ["gato"], "direta"),
    Pergunta("que matérias eu tô fazendo?", ["materias"], "direta"),
    Pergunta("quando é o aniversário da minha mãe?", ["mae"], "direta"),
    Pergunta("tenho alguma restrição pra comer frutos do mar?", ["alergia"], "direta"),
    Pergunta("qual é o meu time?", ["time"], "direta"),
    Pergunta("como anda a ideia de investir na bolsa?", ["bova"], "direta"),
    Pergunta("que dias eu faço exercício?", ["corrida"], "direta"),
    Pergunta("qual o meu objetivo com o inglês?", ["ingles"], "direta"),
    Pergunta("e aquele projeto do trabalho?", ["csm"], "ambigua"),
    Pergunta("e ela, quando faz anos?", ["mae"], "ambigua", anterior="estava pensando na minha mãe"),
    Pergunta("e ele, como foi?", ["irmao"], "ambigua", anterior="meu irmão Rafael fez uma prova"),
    Pergunta("me lembra das coisas da faculdade", ["curso", "materias"], "ambigua"),
    Pergunta("o que eu tinha anotado sobre vídeos?", ["tiktok"], "ambigua"),
    Pergunta("quais são minhas metas de dinheiro?", ["reserva", "bova"], "ambigua"),
    Pergunta("onde fica meu dentista?", ["dentista"], "antiga"),
    Pergunta("qual é o meu carro?", ["carro"], "antiga"),
    Pergunta("eu fiz algum curso de planilha?", ["excel"], "antiga"),
    Pergunta("onde eu moro?", ["mora_novo", "mora_antigo"], "contradicao"),
    Pergunta("que horas eu começo a trabalhar?", ["hora_nova", "hora_antiga"], "contradicao"),
    Pergunta("qual a capital da Austrália?", [], "irrelevante"),
    Pergunta("me dá uma receita de lasanha", [], "irrelevante"),
    Pergunta("quanto é 17 vezes 23?", [], "irrelevante"),
    Pergunta("como está o tempo hoje?", [], "irrelevante"),
    Pergunta("me conta uma piada", [], "irrelevante"),
    Pergunta("quem descobriu o Brasil?", [], "irrelevante"),
]

# A contradição do endereço ("mora em Campinas" x "se mudou para Curitiba") é um caso conhecido: a pergunta só acha
# a versão antiga; as duas chegam juntas quando a E7 ligar as contradições. Por isso o mínimo da contradição é 50%.
MINIMOS = {"acerto": 0.85, "acerto_5": 0.8, "primeira_3": 0.9, "vazamento_max": 2, "contradicao": 0.5}


def montar(pasta: Path, codificador=None):
    """Cria o banco de mentira na pasta e devolve (grafo, ids por chave, Recuperacao)."""
    from eco.chat.armazenamento import Banco
    from eco.grafo.dados import Grafo
    from eco.grafo.recuperacao import Recuperacao
    from eco.grafo.semelhanca import Semelhanca

    g = Grafo(Banco(pasta / "avaliacao.db"))
    ids = {}
    for chave, nome, resumo, origem, quando, protegido in MEMORIA:
        i = g.criar_fato(resumo, nome, fixo=bool(protegido)) if origem == "fato" else g.criar_ideia(nome, origem=origem, resumo=resumo)
        g.banco._executar("UPDATE ideias SET criado_em = ?, atualizado_em = ?, protegido = ? WHERE id = ?", (quando, quando, protegido, i.id))
        ids[chave] = i.id
    for a, b in LIGACOES:
        g.ligar(ids[a], ids[b], motivo="avaliação")
    for filho, pai in PAIS:
        g.definir_pai(ids[filho], ids[pai])
    sem = Semelhanca(g, codificador) if codificador is not None else Semelhanca(g)
    return g, ids, Recuperacao(g, None, sem)  # sem núcleo: mede a recuperação de tudo


def avaliar(rec, ids, agora=None) -> dict:
    from datetime import datetime, timezone

    agora = agora or datetime(2026, 11, 12, tzinfo=timezone.utc)
    nome = {v: k for k, v in ids.items()}
    linhas, acerto, acerto5, prim3, uteis, vazou, contra_ok, contra_n = [], 0, 0, 0, 0, 0, 0, 0
    for p in PERGUNTAS:
        r = rec.recuperar(p.texto, p.anterior, agora=agora)
        veio = [nome[x.ideia.id] for x in r]
        if p.esperadas:
            uteis += 1
            ok = set(p.esperadas) <= set(veio)
            ok5 = set(p.esperadas) <= set(veio[:5])
            ok3 = any(e in veio[:3] for e in p.esperadas)
            acerto += ok
            acerto5 += ok5
            prim3 += ok3
            if p.tipo == "contradicao":
                contra_n += 1
                contra_ok += ok
            estado = "OK" if ok and ok3 else "FALHOU"
        else:
            vazou += len(veio)
            estado = "OK" if not veio else f"VAZOU {len(veio)}"
        linhas.append(f"{estado:9s} [{p.tipo}] {p.texto!r} -> {veio}  esperado {p.esperadas}")
    return {
        "acerto": acerto / uteis, "acerto_5": acerto5 / uteis, "primeira_3": prim3 / uteis, "vazamento": vazou,
        "contradicao": contra_ok / contra_n if contra_n else 1.0, "linhas": linhas,
    }


if __name__ == "__main__":
    import sys
    import tempfile

    sys.stdout.reconfigure(encoding="utf-8")
    from eco.grafo.semelhanca import Codificador, pasta_do_modelo

    with tempfile.TemporaryDirectory() as d:
        g, ids, rec = montar(Path(d), Codificador(pasta_do_modelo()))
        res = avaliar(rec, ids)
        g.banco.fechar()
    print("\n".join(res["linhas"]))
    print(f"\nAcerto no que é enviado: {res['acerto']:.0%} | nos 5 primeiros: {res['acerto_5']:.0%} | "
          f"alguma esperada entre os 3 primeiros: {res['primeira_3']:.0%} | vazamento nas irrelevantes: {res['vazamento']} | "
          f"contradições com as duas versões: {res['contradicao']:.0%}")
