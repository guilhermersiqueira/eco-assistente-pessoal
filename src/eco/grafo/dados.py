"""Tabelas do grafo de ideias, no mesmo banco do chat (os ids de espaço e conversa são os mesmos).

- ideia: um nó. Vem de conversas do chat, da memória, das notas, das metas ou é criada à mão.
- ideia_fontes: de quais conversas a ideia saiu (o cartão do grafo lista essas conversas).
- ligacao: aresta entre duas ideias, com o motivo. Desligar não apaga a linha: marca `ativa = 0`, e assim a análise
  automática não recria uma ligação que o dono desfez.
- ideia_ignorada: nomes de ideias apagadas pelo dono (por espaço), para a análise não trazê-las de volta.
- analise: o que já foi analisado (marca por fonte) e o gasto do mês com o grafo.

Hierarquia (escopo de 10/11/2026): cada ideia pode ter um `pai` (tronco → galho → folha, ex.: Investimentos → Ações →
BOVA11). `nivel` é o que a análise disse ("tronco", "galho", "folha"); `tipo` é "ideia" ou "entidade" (pessoa, projeto,
nicho). Cada ligação tem um `tipo` ("causal", "semantica", "lateral" ou "manual") e, na semântica, a `forca` (até 0,4).
O peso final de cada par é calculado em `grafo/pesos.py`, não fica gravado.
"""

from __future__ import annotations

import hashlib
import uuid
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable
from datetime import datetime

from eco.chat.armazenamento import Banco, _sem_acento

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ideias (
    id TEXT PRIMARY KEY, nome TEXT NOT NULL, resumo TEXT NOT NULL DEFAULT '',
    espaco_id TEXT REFERENCES espacos(id) ON DELETE SET NULL,
    origem TEXT NOT NULL DEFAULT 'chat', topico TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ideia_fontes (
    ideia_id TEXT NOT NULL REFERENCES ideias(id) ON DELETE CASCADE,
    conversa_id TEXT NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    PRIMARY KEY (ideia_id, conversa_id));
CREATE TABLE IF NOT EXISTS ideia_dias (
    ideia_id TEXT NOT NULL REFERENCES ideias(id) ON DELETE CASCADE,
    dia TEXT NOT NULL,
    PRIMARY KEY (ideia_id, dia));
CREATE TABLE IF NOT EXISTS ligacoes (
    id TEXT PRIMARY KEY,
    a TEXT NOT NULL REFERENCES ideias(id) ON DELETE CASCADE,
    b TEXT NOT NULL REFERENCES ideias(id) ON DELETE CASCADE,
    motivo TEXT NOT NULL DEFAULT '', origem TEXT NOT NULL DEFAULT 'auto',
    ativa INTEGER NOT NULL DEFAULT 1, criado_em TEXT NOT NULL,
    UNIQUE (a, b), CHECK (a < b));
CREATE TABLE IF NOT EXISTS ideias_ignoradas (
    espaco_id TEXT NOT NULL DEFAULT '', chave TEXT NOT NULL, PRIMARY KEY (espaco_id, chave));
CREATE TABLE IF NOT EXISTS analises (
    fonte TEXT PRIMARY KEY, marca TEXT NOT NULL, quando TEXT NOT NULL);
"""

# Colunas que chegaram depois (bancos antigos ganham com ALTER TABLE).
COLUNAS_NOVAS = {
    "ideias": [("pai", "TEXT REFERENCES ideias(id) ON DELETE SET NULL"), ("nivel", "TEXT NOT NULL DEFAULT ''"),
               ("tipo", "TEXT NOT NULL DEFAULT 'ideia'")],
    "ligacoes": [("tipo", "TEXT NOT NULL DEFAULT 'semantica'"), ("forca", "REAL NOT NULL DEFAULT 0.4"),
                 # E6: de qual ideia a relação parte ("A depende de B": de = A); vazio nas relações sem sentido
                 ("de", "TEXT NOT NULL DEFAULT ''")],
}
# Versão 2 (12/11/2026, etapa E3): campos para relevância, expiração, proteção, arquivamento, origem detalhada e uso
# (ideias); relação, confiança, status e autor (ligações, usados a partir da E6); histórico de alterações. A migração
# roda uma vez, numa transação, com backup conferido antes (`backup.antes_de`) quando o banco já tem dados.
VERSAO_ESQUEMA = 2
COLUNAS_V2 = {
    "ideias": [
        ("relevancia", "REAL NOT NULL DEFAULT 0.5"),
        ("durabilidade", "TEXT NOT NULL DEFAULT ''"),  # permanente | duradoura | temporaria | '' (não se sabe)
        ("validade", "TEXT NOT NULL DEFAULT ''"),  # AAAA-MM-DD para informação temporária
        ("protegido", "INTEGER NOT NULL DEFAULT 0"),  # processos automáticos nunca removem, arquivam nem fundem
        ("estado", "TEXT NOT NULL DEFAULT 'ativo'"),  # ativo | arquivado
        ("origem_detalhe", "TEXT NOT NULL DEFAULT ''"),  # processo que criou (análise, voz, clique, importação...)
        ("usos", "INTEGER NOT NULL DEFAULT 0"),  # quantas vezes foi recuperada para uma conversa (E5)
        ("ultimo_uso", "TEXT NOT NULL DEFAULT ''"),
    ],
    "ligacoes": [
        ("relacao", "TEXT NOT NULL DEFAULT ''"),  # categoria do dono (relacionado a, depende de...): E6
        ("confianca", "REAL NOT NULL DEFAULT 1.0"),
        ("status", "TEXT NOT NULL DEFAULT 'ativa'"),  # ativa | revisar | sugestao | rejeitada
        ("criado_por", "TEXT NOT NULL DEFAULT ''"),
    ],
}
ESQUEMA_V2 = """
CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ideia_id TEXT, ligacao_id TEXT, acao TEXT NOT NULL,
    antes TEXT NOT NULL DEFAULT '', depois TEXT NOT NULL DEFAULT '', autor TEXT NOT NULL DEFAULT '', quando TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS historico_ideia ON historico (ideia_id, id);
CREATE TABLE IF NOT EXISTS versoes (modulo TEXT PRIMARY KEY, versao INTEGER NOT NULL);
"""
DURABILIDADES = ("permanente", "duradoura", "temporaria")

NIVEIS = ("tronco", "galho", "folha")
TIPOS_LIGACAO = ("causal", "semantica", "lateral", "manual")


@dataclass(frozen=True)
class Ideia:
    id: str
    nome: str
    resumo: str
    espaco_id: str | None
    origem: str  # chat | fato (uma lembrança da memória) | notas | metas | manual
    topico: str
    criado_em: str
    atualizado_em: str
    pai: str | None = None
    nivel: str = ""  # tronco | galho | folha | "" (a análise não disse)
    tipo: str = "ideia"  # ideia | entidade
    relevancia: float = 0.5
    durabilidade: str = ""
    validade: str = ""
    protegido: int = 0
    estado: str = "ativo"
    origem_detalhe: str = ""
    usos: int = 0
    ultimo_uso: str = ""

    @property
    def arquivada(self) -> bool:
        return self.estado == "arquivado"


@dataclass(frozen=True)
class Ligacao:
    id: str
    a: str
    b: str
    motivo: str
    origem: str  # auto | manual
    ativa: bool
    tipo: str = "semantica"  # causal | semantica | lateral | manual
    forca: float = 0.4  # só conta na semântica
    relacao: str = ""
    confianca: float = 1.0
    status: str = "ativa"
    criado_por: str = ""
    de: str = ""


@dataclass(frozen=True)
class FonteConversa:
    conversa_id: str
    titulo: str
    espaco_id: str | None
    espaco_nome: str
    atualizado_em: str


def chave_da_ideia(nome: str) -> str:
    """Duas ideias com o mesmo nome (sem acento, caixa ou espaços extras) são a mesma ideia."""
    return " ".join(_sem_acento(nome).split())


def marca_de_texto(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()


def _par(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


class Grafo:
    """Repositório do grafo. Usa a conexão e o lock do `Banco` do chat."""

    def __init__(self, banco: Banco, backup_antes_de_migrar: Callable[[], Any] | None = None) -> None:
        self.banco = banco
        self._local = threading.local()
        with banco._lock, banco._con:
            banco._con.executescript(ESQUEMA)
            for tabela, colunas in COLUNAS_NOVAS.items():
                existentes = {l[1] for l in banco._con.execute(f"PRAGMA table_info({tabela})")}
                for nome, definicao in colunas:
                    if nome not in existentes:
                        banco._con.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {definicao}")
                        if (tabela, nome) == ("ligacoes", "tipo"):  # ligações feitas pelo dono antes das regras de peso
                            banco._con.execute("UPDATE ligacoes SET tipo = 'manual' WHERE origem = 'manual'")
        if backup_antes_de_migrar is None and getattr(banco, "arquivo", None) is not None:
            # cuidado 2 do dono: cópia conferida do banco antes de migrar, na pasta backups ao lado dele
            def backup_antes_de_migrar() -> Any:
                from eco import backup

                return backup.antes_de(f"migrar-grafo-v{VERSAO_ESQUEMA}", banco.arquivo, banco.arquivo.parent / "backups")
        migrar(banco, backup_antes_de_migrar)

    # --- autor das alterações (vai para o histórico e para a origem detalhada) ------------------------------------
    @property
    def autor(self) -> str:
        return getattr(self._local, "autor", "") or "sistema"

    @contextmanager
    def como(self, autor: str):
        """`with grafo.como("análise do grafo"):` tudo o que for alterado dentro fica com esse autor no histórico."""
        antigo = getattr(self._local, "autor", "")
        self._local.autor = autor
        try:
            yield self
        finally:
            self._local.autor = antigo

    def _registrar(self, acao: str, ideia_id: str | None = None, ligacao_id: str | None = None,
                   antes: dict | None = None, depois: dict | None = None) -> None:
        self.banco._executar(
            "INSERT INTO historico (ideia_id, ligacao_id, acao, antes, depois, autor, quando) VALUES (?,?,?,?,?,?,?)",
            (ideia_id, ligacao_id, acao, json.dumps(antes, ensure_ascii=False) if antes else "",
             json.dumps(depois, ensure_ascii=False) if depois else "", self.autor, self.banco._relogio()),
        )

    def _linha_crua(self, id_: str) -> dict | None:
        l = self.banco._consultar("SELECT * FROM ideias WHERE id = ?", (id_,))
        return dict(l[0]) if l else None

    def historico(self, ideia_id: str) -> list[dict]:
        """Criação, alterações, arquivamento e exclusão de uma ideia, da mais antiga para a mais nova (19.6.4)."""
        saida = []
        for l in self.banco._consultar("SELECT * FROM historico WHERE ideia_id = ? ORDER BY id", (ideia_id,)):
            d = dict(l)
            d["antes"] = json.loads(d["antes"]) if d["antes"] else None
            d["depois"] = json.loads(d["depois"]) if d["depois"] else None
            saida.append(d)
        return saida

    def historico_recente(self, limite: int = 50) -> list[dict]:
        return [dict(l) for l in self.banco._consultar("SELECT * FROM historico ORDER BY id DESC LIMIT ?", (limite,))]

    # --- ideias -------------------------------------------------------------------------------------
    def _linha(self, l) -> Ideia:
        return Ideia(**dict(l))

    def ideia(self, id_: str) -> Ideia:
        linhas = self.banco._consultar("SELECT * FROM ideias WHERE id = ?", (id_,))
        if not linhas:
            raise KeyError(id_)
        return self._linha(linhas[0])

    def listar_ideias(self, incluir_arquivadas: bool = False) -> list[Ideia]:
        """As ideias ativas (as arquivadas só com `incluir_arquivadas`): é o que a tela, a análise e o prompt veem."""
        filtro = "" if incluir_arquivadas else " WHERE estado != 'arquivado'"
        return [self._linha(l) for l in self.banco._consultar(f"SELECT * FROM ideias{filtro} ORDER BY criado_em, rowid")]  # noqa: S608

    def ideias_do_espaco(self, espaco_id: str | None) -> list[Ideia]:
        if espaco_id is None:
            linhas = self.banco._consultar("SELECT * FROM ideias WHERE espaco_id IS NULL AND origem = 'chat' AND estado != 'arquivado' ORDER BY nome")
        else:
            linhas = self.banco._consultar("SELECT * FROM ideias WHERE espaco_id = ? AND estado != 'arquivado' ORDER BY nome", (espaco_id,))
        return [self._linha(l) for l in linhas]

    def ideias_da_origem(self, origem: str, incluir_arquivadas: bool = False) -> list[Ideia]:
        """Ideias de memória, notas ou metas (não pertencem a nenhum espaço)."""
        filtro = "" if incluir_arquivadas else " AND estado != 'arquivado'"
        return [self._linha(l) for l in self.banco._consultar(f"SELECT * FROM ideias WHERE origem = ?{filtro} ORDER BY nome", (origem,))]  # noqa: S608

    def arquivadas(self) -> list[Ideia]:
        return [self._linha(l) for l in self.banco._consultar("SELECT * FROM ideias WHERE estado = 'arquivado' ORDER BY atualizado_em DESC")]

    def localizar(self, nome: str, espaco_id: str | None, origem: str = "chat") -> Ideia | None:
        """A ideia com esse nome no espaço e origem (inclusive arquivada: quem cria decide se restaura)."""
        chave = chave_da_ideia(nome)
        for i in self.listar_ideias(incluir_arquivadas=True):
            if i.espaco_id == espaco_id and i.origem == origem and chave_da_ideia(i.nome) == chave:
                return i
        return None

    def ignorada(self, nome: str, espaco_id: str | None) -> bool:
        return bool(
            self.banco._consultar(
                "SELECT 1 FROM ideias_ignoradas WHERE espaco_id = ? AND chave = ?", (espaco_id or "", chave_da_ideia(nome))
            )
        )

    def criar_ideia(
        self, nome: str, espaco_id: str | None = None, origem: str = "chat", resumo: str = "", topico: str = "",
        nivel: str = "", tipo: str = "ideia", origem_detalhe: str = "",
    ) -> Ideia:
        """Cria a ideia, ou devolve a que já existe com esse nome no mesmo espaço (completa resumo e nível se faltavam e
        marca a atividade, que conta no decaimento das ligações). Uma ideia arquivada que volta a aparecer é restaurada."""
        nome = " ".join(nome.split())
        if not nome:
            raise ValueError("A ideia precisa de um nome.")
        nivel = nivel if nivel in NIVEIS else ""
        tipo = "entidade" if tipo == "entidade" else "ideia"
        existente = self.localizar(nome, espaco_id, origem)
        agora = self.banco._relogio()
        if existente:
            antes = self._linha_crua(existente.id)
            self.banco._executar(
                "UPDATE ideias SET resumo = ?, nivel = ?, tipo = ?, estado = 'ativo', atualizado_em = ? WHERE id = ?",
                (existente.resumo or resumo.strip(), existente.nivel or nivel,
                 "entidade" if "entidade" in (existente.tipo, tipo) else "ideia", agora, existente.id),
            )
            depois = self._linha_crua(existente.id)
            if existente.arquivada:
                self._registrar("restaurar", existente.id, antes={"estado": "arquivado"}, depois={"estado": "ativo", "motivo": "voltou a aparecer"})
            mudou = {k: depois[k] for k in ("resumo", "nivel", "tipo") if antes[k] != depois[k]}
            if mudou:
                self._registrar("atualizar", existente.id, antes={k: antes[k] for k in mudou}, depois=mudou)
            return self.ideia(existente.id)
        id_ = uuid.uuid4().hex
        self.banco._executar(
            "INSERT INTO ideias (id, nome, resumo, espaco_id, origem, topico, criado_em, atualizado_em, nivel, tipo, origem_detalhe)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (id_, nome, resumo.strip(), espaco_id, origem, topico.strip(), agora, agora, nivel, tipo, origem_detalhe or self.autor),
        )
        self._registrar("criar", id_, depois=self._linha_crua(id_))
        return self.ideia(id_)

    # --- hierarquia ----------------------------------------------------------------------------------
    def definir_pai(self, id_: str, pai: str | None) -> None:
        """Põe a ideia dentro de outra (ou tira, com None). Recusa ciclos: o pai não pode estar abaixo da ideia."""
        if pai is not None:
            if pai == id_:
                raise ValueError("Uma ideia não fica dentro de si mesma.")
            atual: str | None = pai
            vistos = set()
            while atual is not None and atual not in vistos:
                if atual == id_:
                    raise ValueError("Essa ideia já está acima da outra; isso faria um círculo.")
                vistos.add(atual)
                atual = self.ideia(atual).pai
        anterior = self.ideia(id_).pai
        self.banco._executar("UPDATE ideias SET pai = ? WHERE id = ?", (pai, id_))
        if anterior != pai:
            self._registrar("hierarquia", id_, antes={"pai": anterior}, depois={"pai": pai})

    def filhos(self, id_: str) -> list[Ideia]:
        return [self._linha(l) for l in self.banco._consultar("SELECT * FROM ideias WHERE pai = ? ORDER BY nome", (id_,))]

    def profundidade(self, id_: str) -> int:
        """0 para quem não tem pai; 1 para o filho de um tronco, e assim por diante."""
        n, atual, vistos = 0, self.ideia(id_).pai, set()
        while atual is not None and atual not in vistos:
            vistos.add(atual)
            n += 1
            try:
                atual = self.ideia(atual).pai
            except KeyError:
                break
        return n

    def renomear_ideia(self, id_: str, nome: str) -> None:
        nome = " ".join(nome.split())
        if not nome:
            raise ValueError("A ideia precisa de um nome.")
        anterior = self.ideia(id_).nome
        self.banco._executar("UPDATE ideias SET nome = ?, atualizado_em = ? WHERE id = ?", (nome, self.banco._relogio(), id_))
        if anterior != nome:
            self._registrar("renomear", id_, antes={"nome": anterior}, depois={"nome": nome})

    def apagar_ideia(self, id_: str) -> None:
        """Apaga a ideia e suas ligações e lembra o nome, para a análise não recriá-la."""
        i = self.ideia(id_)
        antes = self._linha_crua(id_)
        self.banco._executar(
            "INSERT OR IGNORE INTO ideias_ignoradas (espaco_id, chave) VALUES (?, ?)", (i.espaco_id or "", chave_da_ideia(i.nome))
        )
        self.banco._executar("DELETE FROM ideias WHERE id = ?", (id_,))
        self._registrar("apagar", id_, antes=antes)  # o conteúdo inteiro fica no histórico

    def criar_fato(self, texto: str, titulo: str, fixo: bool = False, importada: bool = False, criado_em: str = "") -> Ideia:
        """Um nó para uma lembrança da memória. O resumo guarda o texto inteiro (é a chave que liga o nó à linha do arquivo);
        o nome é um título curto, e títulos iguais ganham um número para não virarem o mesmo nó. `importada` marca a
        lembrança vinda das conversas do claude.ai (tópico "importada": fica fora do bloco fixo do prompt); `criado_em`
        dá a data original (ISO) em vez de agora."""
        base = " ".join(titulo.split()) or "Lembrança"
        nome, n = base, 2
        usados = {chave_da_ideia(i.nome) for i in self.ideias_da_origem("fato")}
        while chave_da_ideia(nome) in usados:
            nome, n = f"{base} ({n})", n + 1
        id_ = uuid.uuid4().hex
        agora = self.banco._relogio()
        self.banco._executar(
            "INSERT INTO ideias (id, nome, resumo, espaco_id, origem, topico, criado_em, atualizado_em, protegido, origem_detalhe)"
            " VALUES (?,?,?,NULL,'fato',?,?,?,?,?)",
            (id_, nome, texto.strip(), "fixo" if fixo else ("importada" if importada else ""), criado_em or agora, agora,
             1 if fixo else 0, self.autor),
        )
        self._registrar("criar", id_, depois=self._linha_crua(id_))
        return self.ideia(id_)

    def atualizar_ideia(self, id_: str, nome: str | None = None, resumo: str | None = None, topico: str | None = None) -> None:
        atual = self.ideia(id_)
        novo = {"nome": nome if nome is not None else atual.nome, "resumo": resumo if resumo is not None else atual.resumo,
                "topico": topico if topico is not None else atual.topico}
        self.banco._executar(
            "UPDATE ideias SET nome = ?, resumo = ?, topico = ?, atualizado_em = ? WHERE id = ?",
            (novo["nome"], novo["resumo"], novo["topico"], self.banco._relogio(), id_),
        )
        if atual.origem == "fato" and topico is not None:  # lembrança fixa é lembrança protegida
            self.banco._executar("UPDATE ideias SET protegido = ? WHERE id = ?", (1 if topico == "fixo" else 0, id_))
        mudou = {k: v for k, v in novo.items() if getattr(atual, k) != v}
        if mudou:
            self._registrar("atualizar", id_, antes={k: getattr(atual, k) for k in mudou}, depois=mudou)

    def remover(self, id_: str) -> None:
        """Apaga o nó e as ligações sem lembrar o nome (a lembrança foi apagada de propósito na memória)."""
        antes = self._linha_crua(id_)
        self.banco._executar("DELETE FROM ideias WHERE id = ?", (id_,))
        if antes:
            self._registrar("apagar", id_, antes=antes)

    def limpar_orfas(self) -> int:
        """Ideias vindas do chat cujas conversas foram todas excluídas não fazem mais sentido. É limpeza automática:
        ideias protegidas ficam."""
        orfas = self.banco._consultar(
            "SELECT * FROM ideias WHERE origem = 'chat' AND protegido = 0 AND NOT EXISTS (SELECT 1 FROM ideia_fontes f WHERE f.ideia_id = ideias.id)"
        )
        for l in orfas:
            self.banco._executar("DELETE FROM ideias WHERE id = ?", (l["id"],))
            self._registrar("apagar", l["id"], antes={**dict(l), "motivo": "as conversas de origem foram excluídas"})
        return len(orfas)

    # --- arquivar, restaurar e proteger (19.6.9 e 19.6.17) -----------------------------------------------------------
    def arquivar(self, id_: str, motivo: str = "", automatico: bool = False) -> bool:
        """Tira a ideia da tela e do prompt sem apagar nada. Um processo automático nunca arquiva ideia protegida
        (devolve False)."""
        i = self.ideia(id_)
        if automatico and i.protegido:
            return False
        if i.arquivada:
            return True
        self.banco._executar("UPDATE ideias SET estado = 'arquivado', atualizado_em = ? WHERE id = ?", (self.banco._relogio(), id_))
        self._registrar("arquivar", id_, antes={"estado": "ativo"}, depois={"estado": "arquivado", "motivo": motivo})
        return True

    def definir_duracao(self, id_: str, durabilidade: str = "", validade: str = "") -> None:
        """Duração da informação (19.6.1): permanente, duradoura ou temporária, e a data em que deixa de valer."""
        from datetime import date

        durabilidade = durabilidade if durabilidade in DURABILIDADES else ""
        try:
            validade = date.fromisoformat(validade).isoformat() if validade else ""
        except ValueError:
            validade = ""
        i = self.ideia(id_)
        if (i.durabilidade, i.validade) == (durabilidade, validade):
            return
        self.banco._executar("UPDATE ideias SET durabilidade = ?, validade = ? WHERE id = ?", (durabilidade, validade, id_))
        self._registrar("duracao", id_, antes={"durabilidade": i.durabilidade, "validade": i.validade},
                        depois={"durabilidade": durabilidade, "validade": validade})

    def restaurar(self, id_: str) -> None:
        i = self.ideia(id_)
        if not i.arquivada:
            return
        self.banco._executar("UPDATE ideias SET estado = 'ativo', atualizado_em = ? WHERE id = ?", (self.banco._relogio(), id_))
        self._registrar("restaurar", id_, antes={"estado": "arquivado"}, depois={"estado": "ativo"})

    def proteger(self, id_: str, protegido: bool = True) -> None:
        """Protegida: limpeza, expiração e consolidação automáticas nunca a removem nem arquivam. Numa lembrança da
        memória, proteger é o mesmo que fixar (nunca sai do prompt)."""
        i = self.ideia(id_)
        if bool(i.protegido) == protegido:
            return
        self.banco._executar("UPDATE ideias SET protegido = ? WHERE id = ?", (1 if protegido else 0, id_))
        if i.origem == "fato":
            self.banco._executar("UPDATE ideias SET topico = ? WHERE id = ?", ("fixo" if protegido else "", id_))
        self._registrar("proteger" if protegido else "desproteger", id_, antes={"protegido": bool(i.protegido)}, depois={"protegido": protegido})

    def protegidas(self) -> list[Ideia]:
        return [self._linha(l) for l in self.banco._consultar("SELECT * FROM ideias WHERE protegido = 1 ORDER BY nome")]

    # --- fontes (conversas de onde a ideia saiu) --------------------------------------------------
    def adicionar_fonte(self, ideia_id: str, conversa_id: str) -> None:
        self.banco._executar("INSERT OR IGNORE INTO ideia_fontes (ideia_id, conversa_id) VALUES (?, ?)", (ideia_id, conversa_id))

    def fontes(self, ideia_id: str) -> list[FonteConversa]:
        linhas = self.banco._consultar(
            """SELECT c.id AS conversa_id, c.titulo, c.espaco_id, COALESCE(e.nome, 'Geral') AS espaco_nome, c.atualizado_em
               FROM ideia_fontes f JOIN conversas c ON c.id = f.conversa_id LEFT JOIN espacos e ON e.id = c.espaco_id
               WHERE f.ideia_id = ? ORDER BY c.atualizado_em DESC""",
            (ideia_id,),
        )
        return [FonteConversa(**dict(l)) for l in linhas]

    # --- dias do bloco de notas em que a ideia apareceu ---------------------------------------------
    def adicionar_dia(self, ideia_id: str, dia: str) -> None:
        self.banco._executar("INSERT OR IGNORE INTO ideia_dias (ideia_id, dia) VALUES (?, ?)", (ideia_id, dia))

    def dias(self, ideia_id: str) -> list[str]:
        """Dias (AAAA-MM-DD) do bloco de notas em que a ideia foi citada, do mais recente para o mais antigo."""
        return [l["dia"] for l in self.banco._consultar("SELECT dia FROM ideia_dias WHERE ideia_id = ? ORDER BY dia DESC", (ideia_id,))]

    def contagem_dias(self) -> dict[str, int]:
        return {l["ideia_id"]: l["n"] for l in self.banco._consultar("SELECT ideia_id, COUNT(*) AS n FROM ideia_dias GROUP BY ideia_id")}

    # --- ligações -----------------------------------------------------------------------------------
    def ligar(
        self, a: str, b: str, motivo: str = "", origem: str = "auto", tipo: str | None = None, forca: float = 0.4
    ) -> Ligacao | None:
        """Liga duas ideias. Uma ligação que o dono desfez só volta se ele mesmo a refizer (origem 'manual').
        Um par tem uma linha só: se chegar um tipo que pesa mais (ex.: causal sobre semântica), ele fica no lugar."""
        from eco.grafo.pesos import valor_do_tipo

        if a == b:
            raise ValueError("Uma ideia não se liga a si mesma.")
        tipo = tipo if tipo in TIPOS_LIGACAO else ("manual" if origem == "manual" else "semantica")
        forca = max(0.0, min(0.4, float(forca)))
        x, y = _par(a, b)
        atual = self.banco._consultar("SELECT * FROM ligacoes WHERE a = ? AND b = ?", (x, y))
        if atual:
            l = atual[0]
            if not l["ativa"]:
                if origem != "manual" and l["status"] != "sugestao":  # rejeitada pelo dono: só ele religa
                    return None
                self.banco._executar(
                    "UPDATE ligacoes SET ativa = 1, status = 'ativa', origem = 'manual', tipo = 'manual', motivo = ? WHERE id = ?",
                    (motivo or l["motivo"], l["id"]),
                )
                self._registrar("religar", ligacao_id=l["id"], depois={"a": x, "b": y, "motivo": motivo})
                return self._ligacao(l["id"])
            if valor_do_tipo(tipo, forca) > valor_do_tipo(l["tipo"], l["forca"]):
                self.banco._executar(
                    "UPDATE ligacoes SET tipo = ?, forca = ?, motivo = ? WHERE id = ?", (tipo, forca, motivo.strip() or l["motivo"], l["id"])
                )
            elif motivo and not l["motivo"]:
                self.banco._executar("UPDATE ligacoes SET motivo = ? WHERE id = ?", (motivo, l["id"]))
            return self._ligacao(l["id"])
        id_ = uuid.uuid4().hex
        self.banco._executar(
            "INSERT INTO ligacoes (id, a, b, motivo, origem, ativa, criado_em, tipo, forca, criado_por) VALUES (?,?,?,?,?,1,?,?,?,?)",
            (id_, x, y, motivo.strip(), origem, self.banco._relogio(), tipo, forca, self.autor),
        )
        self._registrar("ligar", ligacao_id=id_, depois={"a": x, "b": y, "tipo": tipo, "motivo": motivo.strip()})
        return self._ligacao(id_)

    @staticmethod
    def _de_linha(l) -> Ligacao:
        return Ligacao(l["id"], l["a"], l["b"], l["motivo"], l["origem"], bool(l["ativa"]), l["tipo"], float(l["forca"]),
                       l["relacao"], float(l["confianca"]), l["status"], l["criado_por"], l["de"])

    def _ligacao(self, id_: str) -> Ligacao:
        return self._de_linha(self.banco._consultar("SELECT * FROM ligacoes WHERE id = ?", (id_,))[0])

    def desligar(self, a: str, b: str) -> None:
        """Desfaz a ligação. Sem linha gravada (par só da mesma sessão), grava uma desligada para a regra não religar."""
        x, y = _par(a, b)
        atual = self.banco._consultar("SELECT id FROM ligacoes WHERE a = ? AND b = ?", (x, y))
        if atual:
            self.banco._executar("UPDATE ligacoes SET ativa = 0, status = 'rejeitada' WHERE a = ? AND b = ?", (x, y))
            id_ = atual[0]["id"]
        else:
            id_ = uuid.uuid4().hex
            self.banco._executar(
                "INSERT INTO ligacoes (id, a, b, motivo, origem, ativa, criado_em, tipo, forca, status, criado_por)"
                " VALUES (?,?,?,'','manual',0,?,'lateral',0,'rejeitada',?)",
                (id_, x, y, self.banco._relogio(), self.autor),
            )
        self._registrar("desligar", ligacao_id=id_, antes={"a": x, "b": y})

    # --- ligação automática classificada (E6: 19.6.24, 19.6.26, 19.6.27) -------------------------------------------
    def ligacao_do_par(self, a: str, b: str) -> Ligacao | None:
        x, y = _par(a, b)
        l = self.banco._consultar("SELECT * FROM ligacoes WHERE a = ? AND b = ?", (x, y))
        return self._de_linha(l[0]) if l else None

    def ligar_classificada(self, a: str, b: str, relacao: str, confianca: float, motivo: str, status: str,
                           de: str = "", tipo: str = "semantica", forca: float = 0.4) -> Ligacao | None:
        """Grava a ligação que a ligação automática decidiu. `status`: ativa (alta confiança), revisar (média) ou
        sugestao (baixa, ou média com aprovação exigida; fica desligada). Regras: um par tem uma linha só; par rejeitado
        pelo dono nunca volta; ligação feita pelo dono não é tocada; uma ligação nunca é rebaixada (sugestão vira revisar
        ou ativa, não o contrário); a antiga sem classificação ganha a relação e a confiança. Devolve None se nada mudou."""
        if a == b:
            raise ValueError("Uma ideia não se liga a si mesma.")
        x, y = _par(a, b)
        ordem = {"sugestao": 0, "revisar": 1, "ativa": 2}
        atual = self.ligacao_do_par(a, b)
        dados = {"relacao": relacao, "confianca": round(float(confianca), 3), "motivo": motivo.strip(), "status": status,
                 "de": de, "tipo": tipo, "forca": max(0.0, min(0.4, float(forca)))}
        if atual is not None:
            if atual.status == "rejeitada" or atual.origem == "manual":
                return None
            if atual.ativa and atual.relacao and ordem.get(status, 0) <= ordem.get(atual.status, 2):
                return None
            if atual.ativa and atual.relacao == "" and ordem.get(status, 0) < ordem["ativa"]:
                status = atual.status  # a antiga (sem classificação) já estava ativa: só ganha o rótulo
            if not atual.ativa and ordem.get(status, 0) <= ordem["sugestao"] and atual.relacao:
                return None
            antes = {k: getattr(atual, k) for k in ("relacao", "confianca", "status", "motivo", "de")}
            dados["status"] = status
            self.banco._executar(
                "UPDATE ligacoes SET relacao = ?, confianca = ?, motivo = ?, status = ?, ativa = ?, de = ?, criado_por = ?"
                " WHERE id = ?",
                (relacao, dados["confianca"], dados["motivo"] or atual.motivo, status, 0 if status == "sugestao" else 1, de,
                 atual.criado_por or self.autor, atual.id),
            )
            self._registrar("classificar", ligacao_id=atual.id, antes=antes, depois={**dados, "a": x, "b": y})
            return self._ligacao(atual.id)
        id_ = uuid.uuid4().hex
        self.banco._executar(
            "INSERT INTO ligacoes (id, a, b, motivo, origem, ativa, criado_em, tipo, forca, relacao, confianca, status, criado_por, de)"
            " VALUES (?,?,?,?,'auto',?,?,?,?,?,?,?,?,?)",
            (id_, x, y, dados["motivo"], 0 if status == "sugestao" else 1, self.banco._relogio(), tipo, dados["forca"], relacao,
             dados["confianca"], status, self.autor, de),
        )
        self._registrar("ligar", ligacao_id=id_, depois={**dados, "a": x, "b": y})
        return self._ligacao(id_)

    def ligacoes_com_status(self, status: str) -> list[Ligacao]:
        return [self._de_linha(l) for l in self.banco._consultar(
            "SELECT * FROM ligacoes WHERE status = ? ORDER BY criado_em, rowid", (status,))]

    def aceitar_ligacao(self, id_: str) -> Ligacao:
        """O dono confirmou: a ligação fica ativa e passa a valer como dele (nenhum processo automático a muda)."""
        l = self._ligacao(id_)
        self.banco._executar("UPDATE ligacoes SET ativa = 1, status = 'ativa', origem = 'manual', confianca = 1.0 WHERE id = ?", (id_,))
        self._registrar("aceitar", ligacao_id=id_, antes={"status": l.status}, depois={"status": "ativa"})
        return self._ligacao(id_)

    def mudar_relacao(self, id_: str, relacao: str, de: str = "") -> Ligacao:
        """O dono trocou o tipo da relação (19.6.26): fica como ele disse e conta como ligação dele."""
        l = self._ligacao(id_)
        self.banco._executar(
            "UPDATE ligacoes SET relacao = ?, de = ?, ativa = 1, status = 'ativa', origem = 'manual', confianca = 1.0 WHERE id = ?",
            (relacao, de, id_),
        )
        self._registrar("mudar_relacao", ligacao_id=id_, antes={"relacao": l.relacao, "de": l.de}, depois={"relacao": relacao, "de": de})
        return self._ligacao(id_)

    def listar_ligacoes(self, so_ativas: bool = True) -> list[Ligacao]:
        sql = "SELECT * FROM ligacoes" + (" WHERE ativa = 1" if so_ativas else "") + " ORDER BY criado_em, rowid"
        return [self._de_linha(l) for l in self.banco._consultar(sql)]

    def mesma_sessao(self) -> list[tuple[str, str]]:
        """Pares de ideias que saíram da mesma conversa ou do mesmo dia do bloco (regra "mesma sessão")."""
        linhas = self.banco._consultar(
            """SELECT DISTINCT x.ideia_id AS a, y.ideia_id AS b FROM ideia_fontes x
               JOIN ideia_fontes y ON x.conversa_id = y.conversa_id AND x.ideia_id < y.ideia_id
               UNION SELECT DISTINCT x.ideia_id, y.ideia_id FROM ideia_dias x
               JOIN ideia_dias y ON x.dia = y.dia AND x.ideia_id < y.ideia_id"""
        )
        return [(l["a"], l["b"]) for l in linhas]

    def ligar_iguais_entre_espacos(self) -> int:
        """Liga, sem gastar nada, as ideias de mesmo nome que aparecem em espaços diferentes (o mesmo tema em outro lugar)."""
        por_chave: dict[str, list[Ideia]] = {}
        for i in self.listar_ideias():
            por_chave.setdefault(chave_da_ideia(i.nome), []).append(i)
        antes = len(self.listar_ligacoes(so_ativas=False))
        for grupo in por_chave.values():
            for k, a in enumerate(grupo):
                for b in grupo[k + 1 :]:
                    if (a.espaco_id, a.origem) != (b.espaco_id, b.origem):
                        self.ligar(a.id, b.id, "O mesmo tema aparece em outro espaço", "auto", "semantica", 0.4)
        return len(self.listar_ligacoes(so_ativas=False)) - antes

    # --- o que já foi analisado e o gasto -------------------------------------------------------------
    def marca(self, fonte: str) -> str | None:
        l = self.banco._consultar("SELECT marca FROM analises WHERE fonte = ?", (fonte,))
        return l[0]["marca"] if l else None

    def definir_marca(self, fonte: str, marca: str) -> None:
        self.banco._executar(
            "INSERT INTO analises (fonte, marca, quando) VALUES (?,?,?) ON CONFLICT(fonte) DO UPDATE SET marca=excluded.marca, quando=excluded.quando",
            (fonte, marca, self.banco._relogio()),
        )

    @staticmethod
    def _fonte_gasto(agora: datetime | None = None) -> str:
        return f"gasto:{(agora or datetime.now()):%Y-%m}"

    def gasto_do_mes(self, agora: datetime | None = None) -> float:
        m = self.marca(self._fonte_gasto(agora))
        return float(m) if m else 0.0

    def somar_gasto(self, usd: float, agora: datetime | None = None) -> float:
        total = self.gasto_do_mes(agora) + usd
        self.definir_marca(self._fonte_gasto(agora), repr(total))
        return total


# --- migração do esquema -------------------------------------------------------------------------------------------
def versao_do_esquema(banco: Banco) -> int:
    tem = banco._consultar("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'versoes'")
    if not tem:
        return 1
    l = banco._consultar("SELECT versao FROM versoes WHERE modulo = 'grafo'")
    return int(l[0]["versao"]) if l else 1


def migrar(banco: Banco, backup_antes: Callable[[], Any] | None = None) -> bool:
    """Leva o banco à `VERSAO_ESQUEMA`. Só acrescenta colunas e tabelas (nada é apagado); os dados que já existem
    ganham os valores certos (lembrança fixa -> protegida; ligação desligada -> status rejeitada; autor = origem).
    Com dados no banco, faz o `backup_antes` primeiro. Tudo numa transação: ou migra inteiro, ou nada muda.
    Devolve True se migrou agora."""
    if versao_do_esquema(banco) >= VERSAO_ESQUEMA:
        with banco._lock, banco._con:
            banco._con.executescript(ESQUEMA_V2)  # garante as tabelas (bancos criados já na versão 2)
        return False
    tem_dados = bool(banco._consultar("SELECT 1 FROM ideias LIMIT 1"))
    if tem_dados and backup_antes is not None:
        backup_antes()
    with banco._lock:
        con = banco._con
        try:
            con.execute("BEGIN")
            for tabela, colunas in COLUNAS_V2.items():
                existentes = {l[1] for l in con.execute(f"PRAGMA table_info({tabela})")}
                for nome, definicao in colunas:
                    if nome not in existentes:
                        con.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {definicao}")
            for comando in [c.strip() for c in ESQUEMA_V2.split(";") if c.strip()]:
                con.execute(comando)
            con.execute("UPDATE ideias SET protegido = 1 WHERE origem = 'fato' AND topico = 'fixo'")
            con.execute("UPDATE ligacoes SET status = 'rejeitada' WHERE ativa = 0")
            con.execute("UPDATE ligacoes SET criado_por = origem WHERE criado_por = ''")
            con.execute("UPDATE ideias SET origem_detalhe = origem WHERE origem_detalhe = ''")
            con.execute(
                "INSERT INTO versoes (modulo, versao) VALUES ('grafo', ?) ON CONFLICT(modulo) DO UPDATE SET versao = excluded.versao",
                (VERSAO_ESQUEMA,),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return True
