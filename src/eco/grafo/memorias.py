"""Memória de longo prazo no banco (decisão D2 do dono, 12/11/2026).

O banco é a fonte da verdade: cada lembrança é um nó `fato` do grafo (texto no `resumo`, fixa = protegida), com
histórico de alterações. O `memoria.md` vira uma cópia de leitura, regravada a cada mudança no mesmo formato de antes
(`- [fixo] texto (dd/mm/aaaa)`), para o dono continuar lendo no OneDrive.

Se o dono editar o `memoria.md` à mão, nada é aplicado sozinho: `edicao_manual()` mostra o que mudou (a Eco avisa e
pergunta); `importar_edicao()` aplica no banco, e `descartar_edicao()` regrava o arquivo a partir do banco.

Na primeira vez (`inicializar`), as lembranças do arquivo entram no banco, na mesma ordem, com as datas e fixas, e
uma cópia do arquivo original fica em `memoria.md.antes-do-banco`.

O texto que vai para o prompt (`ler`) é o mesmo de antes: mesmo formato, mesmo limite, fixas sempre dentro.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from eco.ferramentas import memoria
from eco.ferramentas.memoria import Fato
from eco.grafo.dados import Grafo, Ideia
from eco.grafo.fatos import ORIGEM, titulo_provisorio

TOPICO_IMPORTADA = "importada"  # lembrança vinda das conversas do claude.ai: fora do bloco fixo do prompt
ARQUIVO_IMPORTADAS = "memoria-importadas.md"  # cópia só de leitura das importadas (o memoria.md fica só com o bloco fixo)
MARCA_NO_BANCO = "memoria:no_banco"  # (tabela analises) a memória já foi passada para o banco
MARCA_ESPELHO = "memoria:espelho"  # hash do último memoria.md que a Eco gravou
CABECALHO = (
    "# Memória da Eco\n\n"
    "<!-- Cópia gerada pela Eco a partir do banco. Se você editar aqui, a Eco percebe e pergunta antes de importar. -->\n\n"
)


@dataclass
class Edicao:
    """O que mudou no memoria.md desde a última cópia que a Eco gravou."""

    novas: list[Fato] = field(default_factory=list)
    removidas: list[Fato] = field(default_factory=list)
    fixadas: list[Fato] = field(default_factory=list)  # mudou só o [fixo]

    @property
    def vazia(self) -> bool:
        return not (self.novas or self.removidas or self.fixadas)

    def frase(self) -> str:
        partes = []
        if self.novas:
            partes.append(f"{len(self.novas)} lembrança(s) nova(s) ou reescrita(s)")
        if self.removidas:
            partes.append(f"{len(self.removidas)} apagada(s) ou reescrita(s)")
        if self.fixadas:
            partes.append(f"{len(self.fixadas)} com o [fixo] trocado")
        return "O memoria.md foi editado à mão: " + ", ".join(partes) + "."


def _hash(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()


def _iso_do_dia(data: str) -> str | None:
    """dd/mm/aaaa -> data ISO ao meio-dia UTC (a data não muda com o fuso)."""
    try:
        d = datetime.strptime(data, "%d/%m/%Y")
    except ValueError:
        return None
    return d.replace(hour=12, tzinfo=timezone.utc).isoformat(timespec="microseconds")


def _data_de(i: Ideia) -> str:
    try:
        return datetime.fromisoformat(i.criado_em).astimezone().strftime("%d/%m/%Y")
    except ValueError:
        return ""


class Memorias:
    def __init__(self, grafo: Grafo, pasta: Path) -> None:
        self.grafo, self.pasta = grafo, pasta
        self.ao_mudar = None  # E6: chamado com o id da lembrança criada ou editada (a ligação automática usa)

    def _avisar(self, ideia_id: str) -> None:
        if self.ao_mudar is not None:
            try:
                self.ao_mudar(ideia_id)
            except Exception:
                pass  # ligar é extra: nunca impede guardar

    @property
    def arquivo(self) -> Path:
        return self.pasta / "memoria.md"

    # --- leitura ---------------------------------------------------------------------------------------------------
    def nos(self) -> list[Ideia]:
        """Todas as lembranças ativas, na ordem em que foram guardadas (a mais nova por último), importadas inclusive."""
        linhas = self.grafo.banco._consultar("SELECT * FROM ideias WHERE origem = ? AND estado != 'arquivado' ORDER BY rowid", (ORIGEM,))
        return [self.grafo._linha(l) for l in linhas]

    def nos_do_prompt(self) -> list[Ideia]:
        """As lembranças que competem por lugar no bloco fixo do prompt: todas menos as importadas do claude.ai."""
        return [i for i in self.nos() if i.topico != TOPICO_IMPORTADA]

    def importadas(self) -> list[Ideia]:
        """As lembranças importadas das conversas do claude.ai. Ficam fora do bloco fixo (para não empurrar as outras para
        fora) e só aparecem pela busca por semelhança; fixar uma a transforma em lembrança comum, sempre presente."""
        return [i for i in self.nos() if i.topico == TOPICO_IMPORTADA]

    def fatos(self) -> list[Fato]:
        """As lembranças do bloco fixo (as mesmas do memoria.md)."""
        return [Fato(i.resumo, _data_de(i), bool(i.protegido)) for i in self.nos_do_prompt()]

    def ler(self, limite: int = memoria.LIMITE_CARACTERES) -> str:
        """Texto do prompt: igual ao `memoria.ler` de antes, agora a partir do banco."""
        return "\n".join(memoria._linha(Fato(f.texto, f.data)) for f in memoria._selecionadas(self.fatos(), limite))

    def usado(self) -> int:
        return len("\n".join(memoria._linha(Fato(f.texto, f.data)) for f in self.fatos()))

    def fora_do_prompt(self, limite: int = memoria.LIMITE_CARACTERES) -> set[str]:
        """Textos que não vão no bloco fixo: o que não coube no limite e as importadas (a recuperação por semelhança as
        procura, porque o núcleo é só o que está no prompt)."""
        lista = self.fatos()
        dentro = {f.texto for f in memoria._selecionadas(lista, limite)}
        return {f.texto for f in lista if f.texto not in dentro} | {i.resumo for i in self.importadas()}

    def aviso_de_limite(self, limite: int = memoria.LIMITE_CARACTERES) -> str:
        n = self.usado()
        if n > limite:
            return (f" Atenção: a memória passou do limite ({n} de {limite} caracteres) e as lembranças mais antigas que não são "
                    "fixas já não vão para a conversa. Sugira ao dono juntar ou resumir lembranças parecidas (editar_lembranca) "
                    "ou esquecer o que não importa mais.")
        if n >= limite * memoria.AVISAR_A_PARTIR:
            return f" Aviso: a memória está em {n} de {limite} caracteres. Se o dono quiser, sugira juntar lembranças parecidas antes que encha."
        return ""

    # --- primeira vez e cópia no arquivo ---------------------------------------------------------------------------
    def inicializar(self) -> bool:
        """Passa a memória do arquivo para o banco, uma vez só. Devolve True se passou agora."""
        if self.grafo.marca(MARCA_NO_BANCO):
            return False
        if self.arquivo.exists():
            reserva = self.pasta / "memoria.md.antes-do-banco"
            if not reserva.exists():
                shutil.copyfile(self.arquivo, reserva)
        existentes: dict[str, list[Ideia]] = {}
        for i in self.grafo.ideias_da_origem(ORIGEM, incluir_arquivadas=True):
            existentes.setdefault(i.resumo, []).append(i)
        with self.grafo.como("memória passada do memoria.md para o banco"):
            for f in memoria.fatos(self.pasta):
                ja = existentes.get(f.texto)
                if ja:
                    no = ja.pop(0)
                    if bool(no.protegido) != f.fixo:
                        self.grafo.proteger(no.id, f.fixo)
                    continue
                no = self.grafo.criar_fato(f.texto, titulo_provisorio(f.texto), f.fixo)
                quando = _iso_do_dia(f.data) if f.data else None
                if quando:
                    self.grafo.banco._executar("UPDATE ideias SET criado_em = ? WHERE id = ?", (quando, no.id))
        self.grafo.definir_marca(MARCA_NO_BANCO, datetime.now().isoformat(timespec="seconds"))
        self.espelhar()
        return True

    def texto_do_espelho(self) -> str:
        return CABECALHO + "".join(memoria._linha(f) + "\n" for f in self.fatos())

    def espelhar(self) -> None:
        """Regrava o memoria.md a partir do banco e guarda o hash, para perceber edições à mão depois. Se havia uma
        edição à mão ainda não decidida, o arquivo editado é guardado ao lado antes (nada do dono se perde)."""
        texto = self.texto_do_espelho()
        self.pasta.mkdir(parents=True, exist_ok=True)
        marca = self.grafo.marca(MARCA_ESPELHO)
        if marca and self.arquivo.exists():
            try:
                atual = self.arquivo.read_text(encoding="utf-8")
            except OSError:
                atual = None
            if atual is not None and _hash(atual) != marca and atual != texto:
                guardado = self.pasta / f"memoria.md.editado-a-mao-{datetime.now():%Y%m%d-%H%M%S}"
                guardado.write_text(atual, encoding="utf-8")
        self.arquivo.write_text(texto, encoding="utf-8")
        self.grafo.definir_marca(MARCA_ESPELHO, _hash(texto))
        self._espelhar_importadas()

    def _espelhar_importadas(self) -> None:
        """Cópia de leitura das importadas em outro arquivo: o memoria.md é lido de volta (edição à mão, chat da janela) e
        não pode ganhar essas linhas, senão elas entrariam no prompt e empurrariam as lembranças de sempre."""
        importadas = self.importadas()
        destino = self.pasta / ARQUIVO_IMPORTADAS
        if not importadas and not destino.exists():
            return
        cabecalho = ("# Lembranças importadas do claude.ai\n\n<!-- Cópia gerada pela Eco, só para leitura: editar aqui não muda nada. "
                     "Estas lembranças não vão em toda conversa; a Eco as traz quando o assunto aparece. -->\n\n")
        destino.write_text(cabecalho + "".join(memoria._linha(Fato(i.resumo, _data_de(i))) + "\n" for i in importadas), encoding="utf-8")

    # --- edição à mão no arquivo -----------------------------------------------------------------------------------
    def edicao_manual(self) -> Edicao | None:
        """O que mudou no arquivo desde a última cópia da Eco (None: nada mudou ou ainda não há cópia)."""
        marca = self.grafo.marca(MARCA_ESPELHO)
        if not marca or not self.arquivo.exists():
            return None
        try:
            texto = self.arquivo.read_text(encoding="utf-8")
        except OSError:
            return None
        if _hash(texto) == marca:
            return None
        no_arquivo = memoria.fatos(self.pasta)
        no_banco = self.fatos()
        textos_banco = {f.texto: f for f in no_banco}
        textos_arquivo = {f.texto: f for f in no_arquivo}
        e = Edicao(
            novas=[f for f in no_arquivo if f.texto not in textos_banco],
            removidas=[f for f in no_banco if f.texto not in textos_arquivo],
            fixadas=[f for f in no_arquivo if f.texto in textos_banco and f.fixo != textos_banco[f.texto].fixo],
        )
        return None if e.vazia else e

    def importar_edicao(self) -> str:
        """Aplica no banco o que o dono mudou à mão no arquivo (com histórico) e regrava a cópia."""
        e = self.edicao_manual()
        if e is None:
            return "O memoria.md não tem mudanças feitas à mão."
        por_texto = {i.resumo: i for i in self.nos()}
        with self.grafo.como("edição à mão do memoria.md"):
            for f in e.removidas:
                if f.texto in por_texto:
                    self.grafo.remover(por_texto[f.texto].id)
            for f in e.novas:
                self.grafo.criar_fato(f.texto, titulo_provisorio(f.texto), f.fixo)
            for f in e.fixadas:
                self.grafo.proteger(por_texto[f.texto].id, f.fixo)
        self.espelhar()
        return f"Importei a edição do memoria.md: {len(e.novas)} nova(s), {len(e.removidas)} removida(s), {len(e.fixadas)} fixa(s) trocada(s)."

    def descartar_edicao(self) -> str:
        self.espelhar()
        return "Desfiz a edição à mão: o memoria.md voltou a ser a cópia da memória guardada."

    # --- alterações (as mesmas operações de antes) -----------------------------------------------------------------
    def _por_trecho(self, trecho: str) -> Ideia:
        alvo = (trecho or "").strip().lower()
        achadas = [i for i in self.nos() if alvo and alvo in i.resumo.lower()]
        if not achadas:
            raise ValueError(f"Não encontrei nenhuma lembrança com '{trecho}'.")
        if len(achadas) > 1:
            raise ValueError(f"Mais de uma lembrança combina com '{trecho}'. Seja mais específico.")
        return achadas[0]

    def _por_texto(self, texto: str) -> Ideia:
        alvo = " ".join(texto.split())
        for i in self.nos():
            if " ".join(i.resumo.split()) == alvo:
                return i
        raise ValueError("Não encontrei essa lembrança.")

    def lembrar(self, fato: str, duracao: str = "", validade: str = "") -> str:
        self.lembrar_no(fato, duracao, validade)
        return "Guardado." + self.aviso_de_limite()

    def lembrar_no(self, fato: str, duracao: str = "", validade: str = "", importada: bool = False, quando: str = "") -> Ideia:
        """Guarda e devolve o nó. `duracao` (permanente, duradoura, temporaria) e `validade` (AAAA-MM-DD): 19.6.1.
        `importada`: vinda das conversas do claude.ai (fora do bloco fixo); `quando`: a data original (ISO)."""
        fato = " ".join((fato or "").split())
        if not fato:
            raise ValueError("Não há nada para lembrar.")
        no = self.grafo.criar_fato(fato, titulo_provisorio(fato), importada=importada, criado_em=quando)
        if duracao or validade:
            self.grafo.definir_duracao(no.id, duracao, validade)
        self.espelhar()
        self._avisar(no.id)
        return self.grafo.ideia(no.id)

    def adicionar(self, texto: str) -> Ideia:
        """Como `lembrar`, devolvendo o nó (a tela do grafo usa para pôr a lembrança onde o dono clicou)."""
        texto = " ".join((texto or "").split())
        if not texto:
            raise ValueError("Não há nada para lembrar.")
        no = self.grafo.criar_fato(texto, titulo_provisorio(texto))
        self.espelhar()
        self._avisar(no.id)
        return no

    def esquecer(self, trecho: str) -> str:
        if not self.nos():
            raise ValueError("Ainda não guardei nada.")
        self.grafo.remover(self._por_trecho(trecho).id)  # o conteúdo fica no histórico
        self.espelhar()
        return "Esquecido."

    def apagar(self, ideia_id: str) -> None:
        self.grafo.remover(ideia_id)
        self.espelhar()

    def editar(self, ideia_id: str, novo_texto: str) -> Ideia:
        novo_texto = " ".join((novo_texto or "").split())
        if not novo_texto:
            raise ValueError("O texto novo está vazio.")
        no = self.grafo.ideia(ideia_id)
        provisorio = no.nome == titulo_provisorio(no.resumo)  # o título que a análise deu continua
        self.grafo.atualizar_ideia(ideia_id, nome=titulo_provisorio(novo_texto) if provisorio else None, resumo=novo_texto)
        self.espelhar()
        self._avisar(ideia_id)
        return self.grafo.ideia(ideia_id)

    def editar_por_trecho(self, trecho: str, novo_texto: str) -> str:
        self.editar(self._por_trecho(trecho).id, novo_texto)
        return "Lembrança atualizada." + self.aviso_de_limite()

    def fixar(self, ideia_id: str, fixo: bool = True) -> None:
        self.grafo.proteger(ideia_id, fixo)
        self.espelhar()

    def fixar_por_trecho(self, trecho: str, fixo: bool = True) -> str:
        self.fixar(self._por_trecho(trecho).id, fixo)
        return "Lembrança fixada: nunca será cortada." if fixo else "Lembrança desafixada."
