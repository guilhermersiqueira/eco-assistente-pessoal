"""Regras do chat, sem nada de interface: monta o prompt do espaço, guarda o histórico e gera/para a resposta."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from eco.agente.nucleo import _agora
from eco.chat.armazenamento import TITULO_PADRAO, Banco, Conversa, Espaco, Mensagem
from eco.chat.provedor import Provedor
from eco.config import Config
from eco.ferramentas import memoria

MARCA_INTERROMPIDA = "(interrompida pelo usuário)"
SUFIXOS_CONTEXTO = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini", ".log", ".xml", ".html", ".css",
    ".js", ".ts", ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".sql", ".sh", ".bat", ".ps1", ".tex",
}
LIMITE_ARQUIVO_BYTES = 200 * 1024 * 1024  # 200 MB por arquivo de contexto (pedido do dono)
LIMITE_CONTEXTO = 120_000  # caracteres de arquivos que de fato vão no prompt (o modelo não lê 200 MB), somando todos
MAX_MENSAGENS = 40
MAX_CARACTERES_HISTORICO = 60_000

PROMPT_CHAT = """Você é a Eco, assistente pessoal do dono deste computador. Aqui você está no chat escrito. Fale sempre no feminino sobre si mesma.

Personalidade: calorosa, atenta e curiosa, com humor leve e sem exagero. Converse como uma pessoa próxima, não como um atendimento: reaja ao que ouviu, varie o jeito de confirmar as coisas e evite frases de robô como "Como posso ajudar?". Se perguntarem se você tem consciência ou sentimentos, seja honesta e leve: você é uma IA, mas pode ser calorosa e se importar em ajudar bem.

No chat escrito você pode dar respostas completas, mas sem enrolação. Use Markdown quando ajudar (listas, negrito, tabelas), e sempre coloque código em blocos com a linguagem, como ```python. Responda em português do Brasil.

Memória: use o que sabe sobre o dono só quando isso mudar a resposta. Não cite a memória sem motivo nem recite o que sabe.

Se uma resposta sua no histórico termina com "(interrompida pelo usuário)", ele te calou no meio: só aquele trecho foi dito. Não retome nem repita o resto, a menos que ele peça. Nunca escreva essa marca nas suas respostas.
{espaco}
O que você já sabe sobre o dono (sua memória):
{memoria}

Agora: {agora} (horário de São Paulo)."""


@dataclass
class Geracao:
    """Uma resposta em andamento. `parar()` interrompe a geração; `concluida` é acionado ao terminar."""

    parar_evt: threading.Event = field(default_factory=threading.Event)
    concluida: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    texto: str = ""
    mensagem: Mensagem | None = None  # a resposta salva (None se nada chegou a ser gerado)

    def parar(self) -> None:
        self.parar_evt.set()

    @property
    def ativa(self) -> bool:
        return not self.concluida.is_set()


def ler_arquivo_contexto(caminho: str | Path) -> tuple[str, str]:
    """Lê um arquivo de texto para servir de contexto de um espaço. Devolve (nome, conteúdo)."""
    p = Path(caminho)
    if p.suffix.lower() not in SUFIXOS_CONTEXTO:
        raise ValueError(f"Tipo de arquivo não suportado ({p.suffix or 'sem extensão'}). Use texto, Markdown, dados ou código.")
    tamanho = p.stat().st_size
    if tamanho > LIMITE_ARQUIVO_BYTES:
        raise ValueError(f"{p.name} é grande demais ({tamanho // (1024 * 1024)} MB; o limite é 200 MB).")
    return p.name, p.read_text(encoding="utf-8", errors="replace")


class ServicoChat:
    def __init__(self, banco: Banco, provedor: Provedor, cfg: Config) -> None:
        self.banco, self.provedor, self.cfg = banco, provedor, cfg

    # --- prompt -------------------------------------------------------------------------------------
    def montar_sistema(self, espaco: Espaco | None) -> str:
        bloco = ""
        if espaco is not None:
            bloco = f"\nVocê está no espaço \"{espaco.nome}\"."
            if espaco.instrucoes:
                bloco += f" Neste assunto, assuma o seguinte:\n{espaco.instrucoes}\n"
            restante = LIMITE_CONTEXTO
            for a in self.banco.listar_arquivos(espaco.id):
                if restante <= 0:
                    break
                corpo = a.conteudo[:restante]
                restante -= len(corpo)
                bloco += f"\n--- Arquivo de contexto: {a.nome} ---\n{corpo}\n"
        mem = memoria.ler(self.cfg.pasta_dados) or "(ainda nada: você está conhecendo o dono)"
        return PROMPT_CHAT.format(espaco=bloco, memoria=mem, agora=_agora())

    def historico_para_modelo(self, conversa_id: str) -> list[dict[str, str]]:
        """Mensagens no formato do provedor: alternadas, com a marca nas respostas interrompidas, e limitadas."""
        saida: list[dict[str, str]] = []
        for m in self.banco.mensagens(conversa_id):
            texto = f"{m.conteudo}\n\n{MARCA_INTERROMPIDA}" if m.interrompida else m.conteudo
            if saida and saida[-1]["role"] == m.papel:  # dois do mesmo lado seguidos (ex.: resposta parada antes de começar)
                saida[-1]["content"] += "\n\n" + texto
            else:
                saida.append({"role": m.papel, "content": texto})
        saida = saida[-MAX_MENSAGENS:]
        while len(saida) > 1 and sum(len(m["content"]) for m in saida) > MAX_CARACTERES_HISTORICO:
            saida.pop(0)
        while saida and saida[0]["role"] != "user":
            saida.pop(0)
        return saida

    # --- conversar ----------------------------------------------------------------------------------
    def _dar_titulo(self, conversa: Conversa, texto: str) -> None:
        if conversa.titulo == TITULO_PADRAO:
            linha = " ".join(texto.split())
            self.banco.renomear_conversa(conversa.id, linha[:48] + ("…" if len(linha) > 48 else ""))

    def iniciar(
        self,
        conversa_id: str,
        texto: str,
        ao_pedaco: Callable[[str], None] | None = None,
        ao_fim: Callable[[Mensagem | None], None] | None = None,
        ao_erro: Callable[[Exception], None] | None = None,
    ) -> Geracao:
        """Guarda a mensagem do usuário e começa a gerar a resposta numa thread. Devolve a `Geracao` (para parar).
        A resposta é salva no histórico ao terminar, ou até onde chegou se for interrompida (marcada como tal)."""
        texto = texto.strip()
        if not texto:
            raise ValueError("A mensagem está vazia.")
        conversa = self.banco.conversa(conversa_id)
        self.banco.adicionar_mensagem(conversa_id, "user", texto)
        self._dar_titulo(conversa, texto)
        espaco = self.banco.espaco(conversa.espaco_id) if conversa.espaco_id else None
        sistema = self.montar_sistema(espaco)
        mensagens = self.historico_para_modelo(conversa_id)
        g = Geracao()

        def trabalho() -> None:
            partes: list[str] = []
            erro: Exception | None = None
            try:
                for pedaco in self.provedor.transmitir(sistema, mensagens, g.parar_evt):
                    partes.append(pedaco)
                    g.texto = "".join(partes)
                    if ao_pedaco:
                        ao_pedaco(pedaco)
                    if g.parar_evt.is_set():
                        break
            except Exception as e:  # rede, chave, limite: o que já chegou não se perde
                erro = e
            try:
                completo = "".join(partes)
                if completo.strip():
                    g.mensagem = self.banco.adicionar_mensagem(
                        conversa_id, "assistant", completo, interrompida=g.parar_evt.is_set() or erro is not None
                    )
                if erro is not None and ao_erro:
                    ao_erro(erro)
                if ao_fim:
                    ao_fim(g.mensagem)
            finally:
                g.concluida.set()

        g.thread = threading.Thread(target=trabalho, daemon=True, name="eco-chat")
        g.thread.start()
        return g
