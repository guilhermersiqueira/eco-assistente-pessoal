"""Ensaio da higiene da memória (E7) com o Haiku de verdade, na memória de mentira de `avaliacao_memoria.py`.

GASTA API (~US$ 0,005 por rodada): só com o sim do dono. Banco e pasta de dados temporários; gasto no ~/.eco/uso.jsonl
na categoria "avaliacao".

Mede a conferência ao guardar (repetição, contradição, nova) e a revisão (duplicata proposta para juntar, sem juntar
coisas diferentes).

Rodar:  py -m uv run --no-sync python tests/avaliacao_higiene.py --gastar
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# (lembrança nova, vereditos aceitos, chave da existente esperada ou None)
ESCRITA = [
    ("Vitor gosta de tomar café coado sem açúcar logo de manhã.", {"igual"}, "cafe"),
    ("O Mel, gato do Vitor, tem pelo laranja.", {"igual"}, "gato"),
    ("Vitor agora trabalha das 10h às 19h.", {"conflito", "pendencia"}, "hora_nova"),
    ("Vitor torce para o Palmeiras.", {"conflito", "pendencia"}, "time"),
    ("Vitor começou a aprender violão.", {"nova"}, None),
    ("Vitor tem alergia a amendoim.", {"nova"}, None),  # outra alergia: não contradiz a de camarão
    ("Vitor corre 5 km às terças e quintas.", {"igual"}, "corrida"),
]


def main() -> int:
    if "--gastar" not in sys.argv:
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    temporaria = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    pasta = Path(temporaria.name)
    os.environ["ECO_DADOS_DIR"] = str(pasta / "dados")
    (pasta / "dados").mkdir()
    import avaliacao_memoria as A
    from eco.config import PASTA_ECO, carregar_config
    from eco.grafo.higiene import Higiene, completador_padrao
    from eco.grafo.memorias import Memorias
    from eco.grafo.semelhanca import Codificador, pasta_do_modelo
    from eco.uso import RegistroUso

    cfg = carregar_config()
    assert cfg.pasta_dados == pasta / "dados"
    real = RegistroUso(PASTA_ECO / "uso.jsonl")

    class Registro:
        custo = 0.0

        def registrar(self, uso, agora=None, categoria=""):
            Registro.custo += uso.custo
            real.registrar(uso, agora, categoria="avaliacao")

    linhas, acertos = [], 0
    with temporaria:
        g, ids, rec = A.montar(pasta, Codificador(pasta_do_modelo()))
        nome = {v: k for k, v in ids.items()}
        mem = Memorias(g, pasta / "dados")
        h = Higiene(g, completador_padrao(cfg, Registro(), "avaliacao"), rec.sem, memorias=mem)
        for texto, aceitos, chave in ESCRITA:
            v = h.verificar_escrita(texto)
            achou = nome.get(v.existente.id) if v.existente else None
            ok = v.tipo in aceitos and (chave is None or achou == chave)
            acertos += ok
            linhas.append(f"{'OK    ' if ok else 'ERROU '} {texto} -> {v.tipo} {achou or ''} {v.relacao} ({v.motivo})")
        # revisão: uma duplicata de verdade e nada de juntar coisas diferentes
        dup = mem.lembrar_no("Vitor prefere café coado, sem açúcar, pela manhã.")
        nome[dup.id] = "cafe_2"
        r = h.revisar()
        juntar = [p for p in h.propostas() if p.tipo == "fundir"]
        pares = [{nome.get(i, i) for i in p.ideias} for p in juntar]
        dup_ok = {"cafe", "cafe_2"} in pares
        errados = [p for p in pares if p != {"cafe", "cafe_2"}]
        linhas.append(f"\nRevisão: {r.frase(len(h.propostas()))}")
        for p in h.propostas():
            linhas.append(f"  - {h.descrever(p)}")
        linhas.append(f"{'OK    ' if dup_ok else 'ERROU '} duplicata do café proposta para juntar")
        linhas.append(f"{'OK    ' if not errados else 'ERROU '} nenhuma junção indevida {errados or ''}")
        g.banco.fechar()
    print("\n".join(linhas))
    print(f"\nConferência ao guardar: {acertos}/{len(ESCRITA)} | duplicata: {'sim' if dup_ok else 'não'} | "
          f"junções indevidas: {len(errados)} | custo: US$ {Registro.custo:.4f}")
    (Path(__file__).parent / "resultados" / "avaliacao_higiene.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
