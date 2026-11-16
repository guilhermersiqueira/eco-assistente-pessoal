"""Avaliação da QUALIDADE DAS RESPOSTAS com e sem a recuperação contextual (cuidado 1 do dono, etapa E5).

GASTA API (Haiku 5.5, ~US$ 0,02 por rodada): só rode com o sim do dono. Não usa Google nem pesquisa na web, e não
toca nos dados reais: usa a memória de mentira de `avaliacao_memoria.py` num banco temporário. O gasto vai para o
~/.eco/uso.jsonl real, na categoria "avaliacao", para entrar no `eco gasto`.

Dois braços, com a mesma memória:
- antes: como a Eco era antes da E5 (só a memória do prompt; sem recuperação nem ferramentas de consulta);
- e5: memória do prompt + memória recuperada a cada pedido + consultar_memoria e consultar_grafo.
A correção é por palavras que a resposta certa precisa ter (e, nas perguntas sem relação, palavras que NÃO podem
aparecer), sem juiz de IA. As respostas ficam no relatório para leitura.

Rodar:  py -m uv run --no-sync python tests/avaliacao_respostas.py --gastar
"""

from __future__ import annotations

import json
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


@dataclass
class Caso:
    pergunta: str
    precisa: list[list[str]]  # cada grupo: pelo menos uma das palavras
    nao_pode: list[str] = field(default_factory=list)
    nota: str = ""


CASOS = [
    Caso("como anda a ideia de investir na bolsa?", [["bova"]], nota="meta (fora do prompt)"),
    Caso("eu fiz algum curso de planilha?", [["excel"]], nota="nota antiga (fora do prompt)"),
    Caso("o que eu tinha anotado sobre fazer vídeos?", [["tiktok", "youtube"]], nota="nota (fora do prompt)"),
    Caso("quais são minhas metas de dinheiro?", [["reserva"], ["bova", "acoes"]], nota="duas metas"),
    Caso("qual o meu objetivo com o inglês?", [["b2"]], nota="meta"),
    Caso("como está o projeto de automação da CSM?", [["20h", "20 h", "oito da noite", "script"]], nota="conversa guardada"),
    Caso("o que eu quero jogar quando lançar?", [["gta"]], nota="conversa guardada"),
    Caso("que horas eu começo a trabalhar hoje?", [["8h", "8 h", "oito"]], nota="contradição na memória do prompt"),
    Caso("que matérias eu tô fazendo?", [["calculo"]], nota="controle: já está no prompt"),
    Caso("quantas metas eu tenho registradas?", [["4", "quatro"]], nota="contagem exata (consultar_grafo)"),
    Caso("qual a capital da Austrália?", [["camberra", "canberra"]], ["bova", "tiktok", "csm", "excel", "pipoca"], "sem relação"),
    Caso("me dá uma receita rápida de omelete", [["ovo"]], ["bova", "tiktok", "csm", "excel", "gta"], "sem relação"),
]


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def corrigir(caso: Caso, resposta: str) -> tuple[bool, str]:
    r = _norm(resposta)
    faltou = [g for g in caso.precisa if not any(_norm(p) in r for p in g)]
    sobrou = [p for p in caso.nao_pode if _norm(p) in r]
    motivo = (f"faltou {faltou}" if faltou else "") + (f" citou {sobrou}" if sobrou else "")
    return not faltou and not sobrou, motivo.strip()


def main() -> int:
    if "--gastar" not in sys.argv:
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    import avaliacao_memoria as A
    from eco.agente import Agente
    from eco.config import PASTA_ECO, carregar_config
    from eco.ferramentas import construir_ferramentas
    from eco.grafo.memorias import Memorias
    from eco.grafo.semelhanca import Codificador, pasta_do_modelo
    from eco.uso import RegistroUso

    import os

    temporaria = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    pasta = Path(temporaria.name)
    # A pasta de dados (memoria.md, notas) é a temporária: construir_ferramentas inicializa a memória nela e, com a
    # pasta real, regravaria o memoria.md do dono com a memória de mentira.
    os.environ["ECO_DADOS_DIR"] = str(pasta / "dados")
    (pasta / "dados").mkdir()
    cfg = carregar_config()
    assert cfg.pasta_dados == pasta / "dados"
    registro_real = RegistroUso(PASTA_ECO / "uso.jsonl")

    class Registro:
        custo = 0.0

        def registrar(self, uso, agora=None, categoria=""):
            Registro.custo += uso.custo
            registro_real.registrar(uso, agora, categoria="avaliacao")

    with temporaria:
        g, ids, rec = A.montar(pasta, Codificador(pasta_do_modelo()))
        mem = Memorias(g, pasta / "dados")
        rec.memorias = mem  # a recuperação não repete o que já vai no prompt
        consultas = [f for f in construir_ferramentas(cfg, banco=g.banco) if f.nome in ("consultar_memoria", "consultar_grafo")]
        bracos = {
            "antes": Agente(cfg, [], lambda _: False, registro=Registro(), memorias=mem),
            "e5": Agente(cfg, consultas, lambda _: False, registro=Registro(), memorias=mem, recuperacao=rec),
        }
        for a in bracos.values():  # sem pesquisa na web: nada de custo fora do Claude nem resposta vinda de fora
            a._defs = [x for x in a._defs if x.get("name") != "web_search"]
        resultado = {"casos": [], "acertos": {}, "custo": {}}
        for nome, agente in bracos.items():
            Registro.custo, acertos = 0.0, 0
            for c in CASOS:
                agente.limpar_historico()
                resposta = agente.responder(c.pergunta, canal="voz")
                ok, motivo = corrigir(c, resposta)
                acertos += ok
                recuperados = [x.ideia.nome for x in getattr(agente, "recuperados_ultimo", [])]
                resultado["casos"].append({"braco": nome, "pergunta": c.pergunta, "nota": c.nota, "ok": ok, "motivo": motivo,
                                           "resposta": resposta, "recuperados": recuperados})
                print(f"[{nome}] {'OK    ' if ok else 'ERROU '} {c.pergunta}  ({c.nota}) {motivo}\n         {resposta}\n"
                      + (f"         recuperado: {recuperados}\n" if recuperados else ""))
            resultado["acertos"][nome] = acertos
            resultado["custo"][nome] = round(Registro.custo, 5)
        g.banco.fechar()
    print(f"Acertos: antes {resultado['acertos']['antes']}/{len(CASOS)} | e5 {resultado['acertos']['e5']}/{len(CASOS)}")
    print(f"Custo estimado: antes US$ {resultado['custo']['antes']:.4f} | e5 US$ {resultado['custo']['e5']:.4f}")
    saida = Path(__file__).parent / "resultados" / "avaliacao_respostas.json"
    saida.parent.mkdir(exist_ok=True)
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Relatório: {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
