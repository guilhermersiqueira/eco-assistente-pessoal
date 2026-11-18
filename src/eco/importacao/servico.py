"""Importação de ponta a ponta, sem interface (M7): o painel Gerenciar só chama isto.

Passos:
1. `preparar()`: acha o export mais recente, lê e monta o plano com a estimativa de custo. Não gasta nada nem grava.
2. `importar(max_conversas)`: extrai os fatos com o Haiku e confere com a memória. Gasta, dentro do teto da importação.
3. `propostas()`, `aprovar()`, `rejeitar()`: a revisão item a item (ver `aprovacao.py`).
Tudo que depende de rede, disco ou modelo é injetável, para os testes não chamarem a API nem tocarem nos dados reais.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from eco.importacao import filtro
from eco.importacao.aprovacao import Aprovador, Resultado
from eco.importacao.conferencia import Conferencia
from eco.importacao.estado import Importacoes, Proposta
from eco.importacao.extrator import Extrator, lembrancas_da_memoria
from eco.importacao.modelo import PREFIXO_MEMORIA
from eco.importacao.leitor import ExportInvalido, Leitura, ler_export, ler_memorias, localizar_export


@dataclass
class Previa:
    """O que a importação encontrou, antes de gastar qualquer centavo."""

    plano: filtro.Plano | None = None
    leitura: Leitura | None = None
    arquivos: list[Path] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    erro: str = ""
    pendentes_de_revisao: int = 0

    @property
    def pode_importar(self) -> bool:
        return not self.erro and self.plano is not None and bool(self.plano.analisar)

    def frase(self) -> str:
        if self.erro:
            return self.erro
        texto = self.plano.frase() if self.plano is not None else ""
        if self.avisos:
            texto += " " + " ".join(self.avisos)
        return texto


def rotulo_curto(titulo: str, limite: int = 45) -> str:
    """Nome curto para o grupo na revisão: a descrição de um assunto da memória costuma ser uma frase longa ("Investimentos
    do Vitor — aporte mensal de..."); fica o começo, até o primeiro travessão, parêntese ou dois-pontos."""
    t = " ".join((titulo or "").split())
    for corte in (" — ", " – ", " - ", " (", ": "):
        if corte in t:
            t = t.split(corte)[0]
    if len(t) > limite:
        t = t[:limite].rsplit(" ", 1)[0].rstrip(",;") + "…"
    return t or "Sem título"


@dataclass
class Grupo:
    uuid: str  # da conversa ou do assunto da memória
    rotulo: str
    data: str
    da_memoria: bool
    propostas: list[Proposta] = field(default_factory=list)


class Importador:
    def __init__(self, cfg: Any, banco: Any, grafo: Any, memorias: Any, registro: Any = None,
                 pastas: list[Path] | None = None, completador_fabrica: Callable[[str, int], Any] | None = None,
                 semelhanca_fabrica: Callable[[], Any] | None = None, antes_de_gravar: Callable[[], Any] | None = None) -> None:
        self.cfg, self.grafo, self.memorias, self.registro, self.pastas = cfg, grafo, memorias, registro, pastas
        self.estado = Importacoes(banco)
        self._completador_fabrica = completador_fabrica or self._completador_padrao
        self._semelhanca_fabrica = semelhanca_fabrica
        self._antes_de_gravar = antes_de_gravar or self._copia_do_banco
        self._semelhanca = None
        self._aprovador: Aprovador | None = None
        self.previa: Previa | None = None

    # --- peças (criadas sob demanda) -----------------------------------------------------------------------------------
    def _completador_padrao(self, categoria: str, max_tokens: int):
        from eco.grafo.higiene import completador_padrao

        return completador_padrao(self.cfg, self.registro, categoria, max_tokens)

    @staticmethod
    def _copia_do_banco() -> None:
        from eco import backup

        backup.antes_de("importar conversas do claude.ai")

    def semelhanca(self):
        if self._semelhanca is None:
            if self._semelhanca_fabrica is not None:
                self._semelhanca = self._semelhanca_fabrica()
            else:
                from eco.grafo.semelhanca import Semelhanca

                self._semelhanca = Semelhanca(self.grafo)
        return self._semelhanca

    def _higiene(self):
        from eco.grafo.higiene import Higiene
        from eco.uso import IMPORTACAO

        # sem teto próprio: o teto que vale é o da importação (a conferência confere), e o gasto não conta no teto do grafo
        return Higiene(self.grafo, self._completador_fabrica(IMPORTACAO, 1500), self.semelhanca(), memorias=self.memorias,
                       conta_no_teto=False)  # o gasto da importação não entra no teto mensal do grafo

    def aprovador(self) -> Aprovador:
        if self._aprovador is None:
            self._aprovador = Aprovador(self.estado, self.memorias, self._higiene(), self.semelhanca(), self._antes_de_gravar)
        return self._aprovador

    # --- 1. preparar ---------------------------------------------------------------------------------------------------
    def preparar(self) -> Previa:
        """Procura o export mais recente e planeja. Não grava nada e não usa a API."""
        previa = Previa(pendentes_de_revisao=len(self.estado.propostas("pendente")))
        try:
            export = localizar_export(self.pastas) if self.pastas is not None else localizar_export()
            if export is None:
                previa.erro = ("Não achei nenhum export de conversas em Downloads. No claude.ai: Configurações > Privacidade > "
                               "Exportar dados; baixe os arquivos pelo link que chega por e-mail e tente de novo.")
            else:
                previa.arquivos = list(export.arquivos)
                previa.avisos = list(export.avisos)
                previa.leitura = ler_export(export.arquivos)
                previa.avisos += list(previa.leitura.avisos)
                if export.memorias:
                    memoria = ler_memorias(export.memorias)
                    previa.leitura.conversas.extend(memoria.conversas)
                    previa.avisos += list(memoria.avisos)
                    if memoria.conversas:
                        previa.avisos.append(f"Inclui a memória do claude.ai: {len(memoria.conversas)} assunto(s), contados "
                                             "entre as conversas acima.")
                else:
                    previa.avisos.append("Não achei o memories-000.zip do mesmo export: baixe-o também para trazer a memória "
                                         "que o claude.ai guarda de você.")
                previa.plano = filtro.planejar(previa.leitura.conversas, self.estado, self.cfg.modelo_grafo, self.cfg.importar_teto_usd)
        except ExportInvalido as erro:
            previa.erro = str(erro)
        except OSError as erro:
            previa.erro = f"Não consegui ler o arquivo de export: {erro}"
        self.previa = previa
        return previa

    # --- 2. importar ---------------------------------------------------------------------------------------------------
    def importar(self, max_conversas: int | None = None, ao_progresso: Callable[[str], None] | None = None,
                 parar: Callable[[], bool] | None = None) -> str:
        """Extrai e confere. `max_conversas`: o ensaio com poucas conversas antes de importar tudo."""
        previa = self.previa or self.preparar()
        if not previa.pode_importar:
            return previa.frase() or "Nada novo para importar."
        from eco.uso import IMPORTACAO

        alvo = previa.plano.analisar if max_conversas is None else previa.plano.analisar[:max_conversas]
        nome = ", ".join(a.name for a in previa.arquivos)[:200]
        importacao_id = self.estado.registrar_importacao(nome, previa.leitura.hash, len(previa.leitura.conversas))
        extrator = Extrator(self._completador_fabrica(IMPORTACAO, 2000), self.estado, self.cfg.modelo_grafo,
                            self.cfg.importar_teto_usd, lambda: lembrancas_da_memoria(self.memorias))

        def progresso_extracao(n: int, total: int, titulo: str, _r: Any) -> None:
            if ao_progresso is not None:
                ao_progresso(f"Lendo conversa {n} de {total}: {titulo[:50]}")

        r1 = extrator.executar(alvo, importacao_id, ao_progresso=progresso_extracao, parar=parar)
        linhas = [r1.frase()]
        if not (r1.parou_por_erros or r1.interrompido) and (parar is None or not parar()):
            if ao_progresso is not None:
                ao_progresso("Conferindo os fatos com a memória…")
            r2 = Conferencia(self.estado, self._higiene(), self.cfg.importar_teto_usd).executar(
                ao_progresso=lambda n, t, _r: ao_progresso(f"Conferindo fato {n} de {t}…") if ao_progresso else None,
                parar=parar, gasto_antes=r1.custo)
            linhas.append(r2.frase())
        self.previa = None  # o plano mudou: o painel prepara de novo
        return " ".join(linhas)

    # --- 3. revisão ----------------------------------------------------------------------------------------------------
    def propostas(self) -> list[Proposta]:
        """As propostas pendentes, da conversa mais recente para a mais antiga. As ainda sem conferência (texto editado,
        conferência interrompida) entram: aprovar confere de novo antes de gravar."""
        return sorted(self.estado.propostas("pendente"), key=lambda p: p.conversa_data, reverse=True)

    def grupos(self) -> list[Grupo]:
        """As propostas pendentes juntas por origem (uma conversa ou um assunto da memória), a origem mais recente primeiro."""
        por_origem: dict[str, Grupo] = {}
        for p in self.propostas():
            g = por_origem.get(p.conversa_uuid)
            if g is None:
                g = por_origem[p.conversa_uuid] = Grupo(p.conversa_uuid, rotulo_curto(p.conversa_titulo), p.conversa_data,
                                                        p.conversa_uuid.startswith(PREFIXO_MEMORIA))
            g.propostas.append(p)
        return list(por_origem.values())

    def rejeitar_grupo(self, conversa_uuid: str) -> int:
        """Rejeita de uma vez todas as propostas pendentes de uma origem. Devolve quantas."""
        ids = [p.id for p in self.propostas() if p.conversa_uuid == conversa_uuid]
        for id_ in ids:
            self.aprovador().rejeitar(id_)
        return len(ids)

    def sem_conferencia(self) -> int:
        return len(self.estado.sem_conferencia())

    def aprovar(self, id_: str, texto: str | None = None, substituir: bool = False, mesmo_assim: bool = False) -> Resultado:
        return self.aprovador().aprovar(id_, texto, substituir, mesmo_assim)

    def rejeitar(self, id_: str) -> None:
        self.aprovador().rejeitar(id_)
