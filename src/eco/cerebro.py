"""O cérebro único da Eco: o mesmo Agente para a voz e para a caixa de texto, e a conversa atual.

Decisões do dono (10/11/2026):
- A conversa atual (voz e texto juntos) fica só na memória. Ela vai para o banco, num espaço, quando o dono diz
  "Guarde essa, Eco" ou aceita a sugestão da Eco (ela oferece quando há decisão, plano ou código). Daí em diante, o
  resto da conversa também é gravado lá.
- O espaço é escolhido pelo assunto, pela própria Eco; na dúvida ela pergunta (o prompt recebe a lista de espaços).
- Uma conversa nova começa depois de `PAUSA_NOVA_CONVERSA` sem falar com a Eco.

Voz e caixa de texto podem chegar ao mesmo tempo: uma trava faz um esperar o outro (o Agente não é thread-safe).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from eco.chat.armazenamento import GERAL, Banco, _sem_acento
from eco.ferramentas.base import Ferramenta

PAUSA_NOVA_CONVERSA = 10 * 60  # segundos sem falar com a Eco: a próxima fala começa outra conversa
TITULO_MAX = 60


@dataclass
class Fala:
    papel: str  # "user" ou "assistant"
    texto: str
    canal: str = "voz"  # "voz" ou "texto"


@dataclass
class Sessao:
    falas: list[Fala] = field(default_factory=list)
    conversa_id: str | None = None  # preenchido quando o dono manda guardar
    espaco_nome: str = ""
    ultima: float = 0.0  # time.monotonic() da última fala

    @property
    def guardada(self) -> bool:
        return self.conversa_id is not None


class Cerebro:
    def __init__(self, agente: Any, banco: Banco, relogio: Callable[[], float] = time.monotonic) -> None:
        self.agente, self.banco = agente, banco
        self._relogio = relogio
        self._trava = threading.RLock()
        self.sessao = Sessao()
        self._ouvintes: list[Callable[[], None]] = []
        agente.contexto = self.contexto_para_o_prompt
        agente.adicionar_ferramenta(self.ferramenta_guardar())

    # --- avisar a tela -----------------------------------------------------------------------------------------
    def ao_mudar(self, funcao: Callable[[], None]) -> None:
        """A tela se registra aqui para redesenhar a conversa atual (chamado na thread de quem falou)."""
        self._ouvintes.append(funcao)

    def _avisar(self) -> None:
        for f in list(self._ouvintes):
            try:
                f()
            except Exception:
                pass

    # --- conversar ---------------------------------------------------------------------------------------------
    def _talvez_nova_sessao(self) -> None:
        agora = self._relogio()
        if self.sessao.falas and agora - self.sessao.ultima > PAUSA_NOVA_CONVERSA:
            self.sessao = Sessao()
        self.sessao.ultima = agora

    def _registrar(self, papel: str, texto: str, canal: str) -> None:
        self.sessao.falas.append(Fala(papel, texto, canal))
        if self.sessao.conversa_id:
            try:
                self.banco.adicionar_mensagem(self.sessao.conversa_id, papel, texto)
            except KeyError:  # a conversa foi excluída na tela: volta a ser só da memória
                self.sessao.conversa_id = None

    def responder(self, texto: str, canal: str = "voz", confirmar: Callable[[str], bool] | None = None) -> str:
        with self._trava:
            self._talvez_nova_sessao()
            resposta = self.agente.responder(texto, canal=canal, confirmar=confirmar)
            self._registrar("user", texto, canal)
            self._registrar("assistant", resposta, canal)
            self.sessao.ultima = self._relogio()
        self._avisar()
        return resposta

    def nova_conversa(self) -> None:
        """"+ Nova conversa": a tela começa vazia E o modelo esquece as últimas trocas (antes ele ainda as via: D-04)."""
        with self._trava:
            self.sessao = Sessao()
            limpar = getattr(self.agente, "limpar_historico", None)
            if limpar is not None:
                limpar()
        self._avisar()

    # --- guardar -----------------------------------------------------------------------------------------------
    def _espacos(self) -> list[tuple[str | None, str]]:
        return [(GERAL, "Geral"), *((e.id, e.nome) for e in self.banco.listar_espacos())]

    def contexto_para_o_prompt(self) -> str:
        nomes = ", ".join(n for _, n in self._espacos())
        if self.sessao.guardada:
            estado = f"já guardada no espaço {self.sessao.espaco_nome} (o resto dela também entra lá)"
        else:
            estado = f"ainda não guardada ({len(self.sessao.falas) // 2} trocas até agora)"
        return f"Conversa atual: {estado}. Espaços existentes: {nomes}."

    def guardar(self, espaco: str, titulo: str) -> str:
        with self._trava:
            alvo = _sem_acento(" ".join((espaco or "").split()))
            achado = next(((i, n) for i, n in self._espacos() if _sem_acento(n) == alvo), None)
            if achado is None:
                nomes = ", ".join(n for _, n in self._espacos())
                raise ValueError(f"Não existe o espaço '{espaco}'. Espaços: {nomes}. Pergunte ao dono qual usar.")
            espaco_id, nome = achado
            titulo = " ".join((titulo or "").split())[:TITULO_MAX] or "Conversa guardada"
            s = self.sessao
            if s.guardada:
                if s.espaco_nome == nome:
                    return f"Esta conversa já está guardada em {nome}."
                self.banco.mover_conversa(s.conversa_id, espaco_id)
                s.espaco_nome = nome
                resposta = f"Mudei a conversa para o espaço {nome}."
            else:
                if not s.falas:
                    raise ValueError("Ainda não há nada nesta conversa para guardar.")
                c = self.banco.criar_conversa(espaco_id, titulo)
                for f in s.falas:
                    self.banco.adicionar_mensagem(c.id, f.papel, f.texto)
                s.conversa_id, s.espaco_nome = c.id, nome
                resposta = f"Guardei a conversa em {nome} como '{titulo}'. O resto dela também entra lá."
        self._avisar()
        return resposta

    def ferramenta_guardar(self) -> Ferramenta:
        return Ferramenta(
            "guardar_conversa",
            "Guarda a conversa atual (voz e texto) num espaço, para o dono rever depois na aba Conversas; o resto da conversa também "
            "entra lá. Use quando ele disser 'guarde essa' ou aceitar sua sugestão. Escolha o espaço pelo assunto entre os existentes; "
            "se ficar em dúvida entre dois, pergunte antes. Também serve para mudar o espaço de uma conversa já guardada.",
            {
                "type": "object",
                "properties": {
                    "espaco": {"type": "string", "description": "Nome exato de um espaço existente (ou Geral)."},
                    "titulo": {"type": "string", "description": "Título curto do assunto, até 60 caracteres."},
                },
                "required": ["espaco", "titulo"],
            },
            lambda a: self.guardar(a["espaco"], a["titulo"]),
        )


_BLOCO = re.compile(r"```[^\n]*\n.*?(?:```|$)", re.DOTALL)
LIMITE_TEXTO_CURTO = 700  # acima disso (ou com código), a resposta abre na janela flutuante


def tem_codigo(texto: str) -> bool:
    return bool(_BLOCO.search(texto))


def vai_para_janela(texto: str) -> bool:
    """Resposta com código ou longa: aparece na janela flutuante."""
    return tem_codigo(texto) or len(texto) > LIMITE_TEXTO_CURTO


def texto_para_falar(texto: str) -> str:
    """O que a voz lê: tira os blocos de código (eles ficam só na janela)."""
    sem = " ".join(_BLOCO.sub(" ", texto).split())
    return sem or "Pus o código na tela."
