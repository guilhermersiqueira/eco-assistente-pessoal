"""Aprovação dos fatos propostos (M6): o que o dono aceita vira lembrança, o resto fica registrado como rejeitado.

Regras:
- a lembrança nasce com a data da conversa de origem e marcada como importada: fica fora do bloco fixo do prompt e só
  aparece pela busca por semelhança (fixar a transforma em lembrança comum);
- o texto pode ser editado antes de aprovar (e então é conferido de novo com a memória);
- se a conferência achou conflito, aprovar exige uma escolha: `substituir=True` (a antiga vai para o arquivo, ligada
  como "substituída") ou `mesmo_assim=True` (as duas valem);
- antes da primeira gravação de cada rodada sai uma cópia do banco (`antes_de_gravar`, injetável nos testes);
- tudo fica no histórico do grafo com o autor "importação do claude.ai".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from eco.importacao.estado import Importacoes, chave_do_fato

AUTOR = "importação do claude.ai"


class ConflitoPendente(Exception):
    """Aprovar um fato que contradiz uma lembrança, sem dizer o que fazer com a antiga."""

    def __init__(self, texto_antigo: str, relacao: str, motivo: str) -> None:
        super().__init__(f"Este fato {'atualiza' if relacao == 'atualiza' else 'contradiz'} a lembrança \"{texto_antigo}\". "
                         "Escolha: substituir a antiga ou manter as duas.")
        self.texto_antigo, self.relacao, self.motivo = texto_antigo, relacao, motivo


@dataclass
class Resultado:
    ideia_id: str = ""
    texto: str = ""
    substituiu: str = ""  # texto da lembrança que foi para o arquivo
    ja_existia: bool = False  # o texto já estava na memória: nada foi criado
    custo: float = 0.0


class Aprovador:
    def __init__(self, estado: Importacoes, memorias: Any, higiene: Any | None = None, semelhanca: Any | None = None,
                 antes_de_gravar: Callable[[], Any] | None = None) -> None:
        self.estado, self.memorias, self.higiene, self.semelhanca = estado, memorias, higiene, semelhanca
        self.antes_de_gravar = antes_de_gravar
        self._copia_feita = False

    def nova_rodada(self) -> None:
        """A próxima aprovação volta a fazer a cópia de segurança do banco."""
        self._copia_feita = False

    def _copia(self) -> None:
        if not self._copia_feita and self.antes_de_gravar is not None:
            self.antes_de_gravar()
        self._copia_feita = True

    def aprovar(self, id_: str, texto: str | None = None, substituir: bool = False, mesmo_assim: bool = False) -> Resultado:
        p = self.estado.proposta(id_)
        if p is None:
            raise ValueError("Não achei essa proposta.")
        if p.estado != "pendente":
            raise ValueError("Esta proposta já foi decidida.")
        if texto is not None and " ".join(texto.split()) != p.texto:
            self.estado.editar_texto(id_, texto)
            p = self.estado.proposta(id_)
        resultado = Resultado(texto=p.texto)
        # o que mudou na memória desde a conferência (ou a edição do texto) é visto de novo antes de gravar
        existente = next((i for i in self.memorias.grafo.listar_ideias()
                          if i.origem == "fato" and chave_do_fato(i.resumo) == chave_do_fato(p.texto)), None)
        if existente is not None:
            self.estado.decidir(id_, "duplicada", f"Já está na memória: \"{existente.resumo}\"", existente.id)
            resultado.ideia_id, resultado.ja_existia = existente.id, True
            return resultado
        conflito_id, relacao, motivo, texto_antigo = p.conflito_ideia_id, p.conflito_relacao, p.conflito_motivo, p.conflito_texto
        if not p.conferida_em and self.higiene is not None:
            v = self.higiene.verificar_escrita(p.texto)
            resultado.custo += v.custo
            if v.tipo == "igual" and v.existente is not None:
                self.estado.decidir(id_, "duplicada", f"Já está na memória: \"{v.existente.resumo}\"", v.existente.id)
                resultado.ideia_id, resultado.ja_existia = v.existente.id, True
                return resultado
            if v.tipo in ("conflito", "pendencia") and v.existente is not None:
                conflito_id, texto_antigo, relacao, motivo = v.existente.id, v.existente.resumo, v.relacao, v.motivo
            self.estado.marcar_conferida(id_, conflito_id if conflito_id else "", texto_antigo if conflito_id else "", relacao, motivo)
        antiga = None
        if conflito_id:
            try:
                antiga = self.memorias.grafo.ideia(conflito_id)
            except Exception:
                antiga = None
            if antiga is not None and antiga.arquivada:  # já foi resolvida por outro caminho
                antiga = None
        if antiga is not None and not (substituir or mesmo_assim):
            raise ConflitoPendente(antiga.resumo, relacao, motivo)
        self._copia()
        g = self.memorias.grafo
        with g.como(AUTOR):
            nova = self.memorias.lembrar_no(p.texto, p.duracao, p.validade, importada=True, quando=p.conversa_data)
            if antiga is not None and substituir:
                g.arquivar(antiga.id, f"substituída por \"{nova.resumo}\" (importação do claude.ai)")
                g.ligar_classificada(nova.id, antiga.id, "substitui", 1.0, "o dono confirmou a versão nova", "ativa",
                                     de=nova.id, tipo="lateral", forca=0.2)
                resultado.substituiu = antiga.resumo
        self.memorias.espelhar()
        self.estado.decidir(id_, "aprovada", f"Importada da conversa \"{p.conversa_titulo}\"", nova.id)
        resultado.ideia_id = nova.id
        if self.semelhanca is not None:
            try:
                self.semelhanca.atualizar()
            except Exception:  # sem o modelo de semelhança a lembrança só some da busca por significado até a próxima vez
                pass
        return resultado

    def rejeitar(self, id_: str, motivo: str = "") -> None:
        p = self.estado.proposta(id_)
        if p is None:
            raise ValueError("Não achei essa proposta.")
        if p.estado != "pendente":
            raise ValueError("Esta proposta já foi decidida.")
        self.estado.decidir(id_, "rejeitada", motivo or "Rejeitada na revisão")
