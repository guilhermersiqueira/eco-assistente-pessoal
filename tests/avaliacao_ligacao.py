"""Ensaio da ligação automática (E6) com o Haiku de verdade, na memória de mentira de `avaliacao_memoria.py`.

GASTA API (~US$ 0,01 por rodada): só com o sim do dono. Banco e pasta de dados temporários; gasto no ~/.eco/uso.jsonl
na categoria "avaliacao".

Mede: pares que devem ser ligados (e com qual relação), pares que NÃO devem (só palavras ou o nome do dono em comum)
e a distribuição de confiança.

Rodar:  py -m uv run --no-sync python tests/avaliacao_ligacao.py --gastar
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# (a, b, relações aceitas; "*" = qualquer uma). Curso x Matérias não entra: Matérias já está DENTRO de Curso na
# hierarquia, e pai e filhos não são candidatos (a hierarquia já diz "faz parte de").
DEVEM = [
    ("hora_antiga", "hora_nova", {"substitui", "contradiz"}),
    ("mora_antigo", "mora_novo", {"substitui", "contradiz"}),
    ("csm", "trabalho", {"*"}),
    ("python", "eco", {"*"}),
    ("python", "csm", {"*"}),
]
NAO_DEVEM = [("gato", "nome"), ("mora_antigo", "nome"), ("bova", "gta"), ("alergia", "python"), ("time", "hora_antiga"),
             ("cafe", "bova"), ("carro", "dentista"), ("musica", "alergia")]


def main() -> int:
    if "--gastar" not in sys.argv:
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    temporaria = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    pasta = Path(temporaria.name)
    os.environ["ECO_DADOS_DIR"] = str(pasta / "dados")
    os.environ["ECO_LIGACAO_AUTO"] = "sim"
    (pasta / "dados").mkdir()
    import avaliacao_memoria as A
    from eco.config import PASTA_ECO, carregar_config
    from eco.grafo.ligacao import Ligador, ligador_padrao, nivel, rotulo
    from eco.grafo.semelhanca import Codificador, pasta_do_modelo
    from eco.uso import RegistroUso

    cfg = carregar_config()
    assert cfg.pasta_dados == pasta / "dados"
    real = RegistroUso(PASTA_ECO / "uso.jsonl")

    class Registro:
        def registrar(self, uso, agora=None, categoria=""):
            real.registrar(uso, agora, categoria="avaliacao")

    with temporaria:
        g, ids, rec = A.montar(pasta, Codificador(pasta_do_modelo()))
        g.banco._executar("DELETE FROM ligacoes")  # parte do zero: o que vale é o que a ligação automática acha
        padrao = ligador_padrao(cfg, g.banco, Registro())
        lig = Ligador(g, padrao.completador, rec.sem, teto_usd=None)
        r = lig.executar(limite=100)
        nome = {v: k for k, v in ids.items()}
        feitas = {}
        for status in ("ativa", "revisar", "sugestao"):
            for l in g.ligacoes_com_status(status):
                feitas[frozenset((nome[l.a], nome[l.b]))] = l
        linhas, acertos, erros = [], 0, 0
        for a, b, aceitas in DEVEM:
            l = feitas.get(frozenset((a, b)))
            ok = l is not None and l.status != "sugestao" and ("*" in aceitas or l.relacao in aceitas)
            acertos += ok
            linhas.append(f"{'OK    ' if ok else 'FALTOU'} {a} x {b}: " + (f"{rotulo(l, g)} [{l.status}, {nivel(l.confianca)}] {l.motivo}" if l else "sem ligação"))
        for a, b in NAO_DEVEM:
            l = feitas.get(frozenset((a, b)))
            ruim = l is not None and l.status != "sugestao"
            erros += ruim
            linhas.append(f"{'ERRO  ' if ruim else 'OK    '} não ligar {a} x {b}" + (f": {rotulo(l, g)} [{l.status}] {l.motivo}" if l else ""))
        linhas.append("\nTodas as ligações feitas:")
        for par, l in sorted(feitas.items(), key=lambda x: x[1].status):
            linhas.append(f"  [{l.status:8s} {nivel(l.confianca):5s}] {' x '.join(sorted(par))}: {rotulo(l, g)} — {l.motivo}")
        g.banco.fechar()
    print("\n".join(linhas))
    print(f"\n{r.frase()} Custo: US$ {r.custo:.4f}. Erros: {r.erros or 'nenhum'}")
    print(f"Pares certos: {acertos}/{len(DEVEM)} | ligações indevidas (ativas ou para revisar): {erros}/{len(NAO_DEVEM)}")
    saida = Path(__file__).parent / "resultados" / "avaliacao_ligacao.txt"
    saida.write_text("\n".join(linhas) + f"\n\n{r.frase()} Custo US$ {r.custo:.4f}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
