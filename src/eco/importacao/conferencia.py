"""Conferência dos fatos propostos com a memória atual (M5), reaproveitando a higiene da escrita (E7).

Para cada proposta pendente ainda não conferida:
- igual a uma lembrança que já existe: vira "duplicada" e sai da revisão (a memória não é tocada nem reforçada);
- contradiz ou atualiza uma lembrança: continua pendente, com a lembrança em conflito anotada, para a revisão mostrar as
  duas lado a lado (a decisão é do dono);
- qualquer outro caso: segue pendente, marcada como conferida.

A semelhança é local; só quando há lembranças parecidas o Haiku decide (a mesma chamada de quando o dono pede para
lembrar algo). O custo conta no teto da importação. Uma falha de rede deixa a proposta sem conferir, para tentar de novo.
Limite conhecido: confere contra a memória, não uma proposta contra a outra; dois fatos iguais com palavras diferentes
vindos de conversas diferentes só se encontram depois de aprovados (a revisão semanal da memória sugere juntar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from eco.importacao.estado import Importacoes, chave_do_fato

MAX_ERROS_SEGUIDOS = 3


@dataclass
class Resumo:
    conferidas: int = 0
    novas: int = 0
    duplicadas: int = 0
    conflitos: int = 0
    custo: float = 0.0
    erros: list[str] = field(default_factory=list)
    parou_por_teto: bool = False
    parou_por_erros: bool = False
    interrompido: bool = False
    restantes: int = 0

    def frase(self) -> str:
        texto = (f"Conferi {self.conferidas} fato(s) com a memória: {self.duplicadas} já estavam guardados (descartados), "
                 f"{self.conflitos} entram em conflito com uma lembrança, {self.novas} são novos. Custo: US$ {self.custo:.4f}.")
        if self.erros:
            texto += f" {len(self.erros)} não consegui conferir e ficam para a próxima vez."
        if self.parou_por_teto:
            texto += f" Parei no teto de custo; {self.restantes} fato(s) ficaram sem conferir."
        if self.parou_por_erros:
            texto += f" Parei depois de {MAX_ERROS_SEGUIDOS} erros seguidos; {self.restantes} fato(s) ficaram sem conferir."
        if self.interrompido:
            texto += f" Interrompido; {self.restantes} fato(s) ficaram sem conferir."
        return texto


class Conferencia:
    def __init__(self, estado: Importacoes, higiene: Any, teto_usd: float = 0.0) -> None:
        """`higiene`: uma `grafo.higiene.Higiene` (use teto_usd=None nela: o teto aqui é o da importação)."""
        self.estado, self.higiene, self.teto_usd = estado, higiene, teto_usd

    def executar(self, ao_progresso: Callable[[int, int, Resumo], None] | None = None,
                 parar: Callable[[], bool] | None = None, gasto_antes: float = 0.0) -> Resumo:
        """Confere as propostas pendentes. `gasto_antes`: o que a extração já gastou na mesma importação, para o teto."""
        resumo = Resumo()
        pendentes = self.estado.sem_conferencia()
        # o mesmo fato com outras letras, acentos ou ponto final: resolvido aqui, sem custo (a higiene só pega o texto idêntico)
        iguais = {chave_do_fato(i.resumo): i for i in self.higiene.grafo.listar_ideias() if i.origem == "fato"}
        seguidos = 0
        for n, p in enumerate(pendentes):
            restantes = len(pendentes) - n
            if parar is not None and parar():
                resumo.interrompido, resumo.restantes = True, restantes
                break
            if self.teto_usd > 0 and gasto_antes + resumo.custo >= self.teto_usd:
                resumo.parou_por_teto, resumo.restantes = True, restantes
                break
            if (existente := iguais.get(chave_do_fato(p.texto))) is not None:
                self.estado.decidir(p.id, "duplicada", f"Já está na memória: \"{existente.resumo}\"", existente.id)
                self.estado.marcar_conferida(p.id)
                resumo.conferidas += 1
                resumo.duplicadas += 1
                if ao_progresso is not None:
                    ao_progresso(n + 1, len(pendentes), resumo)
                continue
            try:
                v = self.higiene.verificar_escrita(p.texto)
            except Exception as erro:
                resumo.erros.append(f"{p.texto[:60]}: {type(erro).__name__}: {erro}")
                seguidos += 1
                if seguidos >= MAX_ERROS_SEGUIDOS:
                    resumo.parou_por_erros, resumo.restantes = True, restantes - 1
                    break
                continue
            seguidos = 0
            resumo.custo += v.custo
            resumo.conferidas += 1
            if v.tipo == "igual" and v.existente is not None:
                self.estado.decidir(p.id, "duplicada", f"Já está na memória: \"{v.existente.resumo}\"", v.existente.id)
                self.estado.marcar_conferida(p.id)
                resumo.duplicadas += 1
            elif v.tipo in ("conflito", "pendencia") and v.existente is not None:
                self.estado.marcar_conferida(p.id, v.existente.id, v.existente.resumo, v.relacao, v.motivo)
                resumo.conflitos += 1
            else:
                self.estado.marcar_conferida(p.id)
                resumo.novas += 1
            if ao_progresso is not None:
                ao_progresso(n + 1, len(pendentes), resumo)
        return resumo
