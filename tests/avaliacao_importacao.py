"""Ensaio da importação das conversas do claude.ai (M4 e M5) com o Haiku de verdade, no export que está em Downloads.

GASTA API (~US$ 0,01 com as conversas de 10/11/2026): só com o sim do dono. O banco e a pasta de dados são temporários
(nada do eco.db real é aberto ou alterado); a memória real (memoria.md) só é LIDA, para o extrator e a conferência
saberem o que a Eco já sabe. O gasto vai para ~/.eco/uso.jsonl na categoria "importacao".

O resultado fica em ~/.eco/ensaio-importacao.txt (fora do repositório: traz trechos das conversas do dono).

Rodar:  py -m uv run --no-sync python tests/avaliacao_importacao.py --gastar [--max N] [--so "parte do título"] [--sem-memoria] [--saida arquivo]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _opcao(nome: str, padrao: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(nome) + 1] if nome in sys.argv and sys.argv.index(nome) + 1 < len(sys.argv) else padrao


def main() -> int:
    if "--gastar" not in sys.argv:
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    from eco.chat.armazenamento import Banco
    from eco.config import PASTA_ECO, carregar_config
    from eco.ferramentas import memoria
    from eco.grafo.dados import Grafo
    from eco.grafo.higiene import Higiene, completador_padrao
    from eco.grafo.memorias import Memorias
    from eco.grafo.semelhanca import Codificador, Semelhanca, pasta_do_modelo
    from eco.importacao import filtro
    from eco.importacao.conferencia import Conferencia
    from eco.importacao.estado import Importacoes
    from eco.importacao.extrator import Extrator, lembrancas_da_memoria
    from eco.importacao.leitor import ler_export, ler_memorias, localizar_export
    from eco.uso import IMPORTACAO, RegistroUso

    cfg = carregar_config()
    saida = Path(_opcao("--saida") or PASTA_ECO / "ensaio-importacao.txt")
    export = localizar_export()
    if export is None:
        print("Não achei nenhum export de conversas em Downloads.")
        return 1
    leitura = ler_export(export.arquivos)
    if export.memorias and "--sem-memoria" not in sys.argv:  # os assuntos da memória do claude.ai entram como conversas
        leitura.conversas.extend(ler_memorias(export.memorias).conversas)
    reais = memoria.fatos(cfg.pasta_dados)  # só leitura
    linhas = [f"Export: {', '.join(a.name for a in export.arquivos)} (baixado em {export.baixado_em:%d/%m/%Y %H:%M})",
              *export.avisos, f"Lembranças reais lidas do memoria.md: {len(reais)}"]
    real = RegistroUso(PASTA_ECO / "uso.jsonl")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        pasta = Path(tmp)
        banco = Banco(pasta / "ensaio.db")
        grafo = Grafo(banco)
        mem = Memorias(grafo, pasta / "dados")
        mem.inicializar()
        for f in reais:
            mem.lembrar_no(f.texto)
        cod = Codificador(pasta_do_modelo())
        linhas.append("Modelo de semelhança: " + ("carregado" if cod.disponivel else "NÃO disponível (só busca por palavra)"))
        estado = Importacoes(banco)
        plano = filtro.planejar(leitura.conversas, estado, cfg.modelo_grafo, cfg.importar_teto_usd)
        linhas.append(plano.frase())
        alvo = plano.analisar
        if _opcao("--so"):  # só as conversas cujo título contém o texto (para repetir uma que deu erro)
            alvo = [c for c in alvo if _opcao("--so").lower() in c.titulo.lower()]
        alvo = alvo[: int(_opcao("--max") or len(alvo))]
        importacao_id = estado.registrar_importacao("ensaio", leitura.hash, len(leitura.conversas))
        extrator = Extrator(completador_padrao(cfg, real, IMPORTACAO, 2000), estado, cfg.modelo_grafo, cfg.importar_teto_usd,
                            lambda: lembrancas_da_memoria(mem))
        r1 = extrator.executar(alvo, importacao_id, ao_progresso=lambda n, t, titulo, r: print(f"  [{n}/{t}] {titulo[:60]}"))
        linhas.append("EXTRAÇÃO: " + r1.frase())
        higiene = Higiene(grafo, completador_padrao(cfg, real, IMPORTACAO, 1500), Semelhanca(grafo, cod), memorias=mem)
        r2 = Conferencia(estado, higiene, cfg.importar_teto_usd).executar(gasto_antes=r1.custo)
        linhas.append("CONFERÊNCIA: " + r2.frase())
        linhas.append(f"CUSTO TOTAL: US$ {r1.custo + r2.custo:.4f}")
        linhas.append("\n== Conversas")
        for c in alvo:
            reg = estado.conversa(c.uuid)
            linhas.append(f"- {c.titulo[:70]} ({c.criada_em[:10]}) -> {reg.estado if reg else 'não tentada'}"
                          + (f" [{reg.erro}]" if reg and reg.erro else ""))
        for titulo, estado_proposta in (("Para revisar", "pendente"), ("Descartadas por já estarem na memória", "duplicada")):
            lista = estado.propostas(estado_proposta)
            linhas.append(f"\n== {titulo}: {len(lista)}")
            for p in lista:
                linhas.append(f"* {p.texto}")
                if p.trecho:
                    linhas.append(f"    dito por você: \"{p.trecho}\"")
                else:
                    linhas.append("    (sem citação confirmada)")
                linhas.append(f"    conversa: {p.conversa_titulo[:60]} ({p.conversa_data[:10]})"
                              f" | duração: {p.duracao or 'não dita'}{' até ' + p.validade if p.validade else ''}")
                if p.com_conflito:
                    linhas.append(f"    CONFLITO ({p.conflito_relacao}) com a lembrança: \"{p.conflito_texto}\" — {p.conflito_motivo}")
                if p.estado == "duplicada":
                    linhas.append(f"    {p.motivo}")
        banco.fechar()
    texto = "\n".join(linhas)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(texto + "\n", encoding="utf-8")
    print(texto)
    print(f"\nResultado salvo em {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
