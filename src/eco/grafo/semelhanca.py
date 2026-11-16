"""Motor de semelhança do grafo (etapa E4, decisão D3): vetores locais + busca por palavra (FTS5 do SQLite).

- Modelo: intfloat/multilingual-e5-small (licença MIT, entende português), em ONNX, rodando no próprio computador com
  onnxruntime. Nada sai da máquina e não há custo por uso. Fica em ~/.eco/modelos (baixado com `eco modelo baixar`,
  com tamanho e sha256 conferidos; revisão fixa do Hugging Face).
- Cada ideia tem um vetor (384 números) guardado em `ideia_vetores`, com a marca do texto que o gerou: quando o nome ou
  o resumo mudam, a marca muda e o vetor é recalculado na próxima `atualizar()` (que `buscar` chama sozinha).
- `ideias_fts` é o índice de palavras (sem diferenciar acento), mantido por gatilhos na tabela `ideias`.
- `buscar` junta os dois: o vetor acha o que tem o mesmo sentido com outras palavras; o índice acha nomes próprios,
  siglas e números que o vetor confunde. Sem o modelo, a busca continua funcionando só por palavra.

Relevância, não cosseno cru: o e5 dá cossenos "apertados" (0,77 a 0,85 entre textos sem relação, e a faixa muda de
pergunta para pergunta), então um limiar fixo de cosseno não separa o que tem a ver do que não tem. A nota usada é o
cosseno menos a linha de base do texto: a média do cosseno dele com `ANCORAS`, frases neutras de assuntos variados.
Calibrado em 12/11/2026 com o conjunto de `tests/test_semelhanca.py`: perguntas com resposta na memória dão de 0,026 a
0,088; perguntas sem resposta, no máximo 0,012.
"""

from __future__ import annotations

import hashlib
import re
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from eco.config import PASTA_ECO
from eco.grafo.dados import Grafo, Ideia

# --- o modelo ------------------------------------------------------------------------------------------------------
REPO = "intfloat/multilingual-e5-small"
REVISAO = "614241f622f53c4eeff9890bdc4f31cfecc418b3"  # revisão fixa: o arquivo baixado é sempre o mesmo
NOME_MODELO = "multilingual-e5-small"
ARQUIVOS = {  # nome: (bytes, sha256)
    "model.onnx": (470268510, "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665"),
    "tokenizer.json": (17082730, "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
}
DIMENSAO = 384
MAX_TOKENS = 256  # ideias e pedidos são curtos; textos maiores são cortados
PASTA_MODELOS = PASTA_ECO / "modelos"

# Relevância (cosseno menos a linha de base; ver o topo) a partir da qual algo "tem a ver".
MINIMO_RELEVANTE = 0.02  # pedido x ideia: abaixo disso a ideia não entra na recuperação
PARECIDAS_ALTA = 0.04  # ideia x ideia: mesmo assunto com boa certeza
PARECIDAS_MEDIA = 0.015  # ideia x ideia: talvez (palavras em comum também sobem a nota); a E6 manda revisar
# Atenção (medido na E5): entre lembranças do dono, o nome dele em quase todas puxa os pares para cima (0,03 a 0,06 sem
# relação de assunto). Para ligar ou fundir lembranças, a E6/E7 precisam de outra linha de base (a média do próprio
# acervo) e da confirmação do Haiku; estes limiares sozinhos não bastam ali.
PESO_PALAVRA = 0.02  # bônus máximo de quem também casa por palavra

# Frases neutras, de assuntos que não são da vida do dono, para medir a linha de base de cada texto.
ANCORAS = [
    "O preço do petróleo subiu nesta semana.", "A fotossíntese transforma luz em energia química.",
    "O time venceu o campeonato estadual de futebol.", "Instruções para trocar o pneu do carro.",
    "A Revolução Francesa começou em 1789.", "Como regar suculentas no inverno.",
    "O vulcão entrou em erupção na Islândia.", "Regras do jogo de xadrez para iniciantes.",
    "A taxa de câmbio do iene caiu.", "Manual de instalação da impressora.",
    "O museu reabre com exposição de pintura barroca.", "Receita de pão de queijo mineiro.",
    "Previsão do tempo: chuva forte no litoral.", "Os planetas do sistema solar e suas luas.",
    "Dicas para limpar o tapete da sala.", "Horário de funcionamento da farmácia do bairro.",
    "A história da escrita cuneiforme.", "Lista de compras do supermercado.",
    "Como funciona um motor a combustão.", "Letra traduzida de uma canção antiga.",
]


def pasta_do_modelo(base: Path | None = None) -> Path:
    return (base or PASTA_MODELOS) / NOME_MODELO


def modelo_presente(pasta: Path | None = None) -> bool:
    p = pasta or pasta_do_modelo()
    return all((p / nome).is_file() and (p / nome).stat().st_size == tam for nome, (tam, _) in ARQUIVOS.items())


def baixar(pasta: Path | None = None, progresso: Callable[[str], None] = print, abrir: Callable = urllib.request.urlopen) -> Path:
    """Baixa os arquivos do modelo para a pasta, conferindo tamanho e sha256. Baixa para `.parcial` e só renomeia
    depois de conferido: um download interrompido nunca vira um modelo quebrado. Arquivos já certos não são baixados."""
    pasta = pasta or pasta_do_modelo()
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, (tamanho, sha) in ARQUIVOS.items():
        final = pasta / nome
        if final.is_file() and final.stat().st_size == tamanho:
            progresso(f"{nome}: já está aqui.")
            continue
        url = f"https://huggingface.co/{REPO}/resolve/{REVISAO}/onnx/{nome}"
        parcial = pasta / (nome + ".parcial")
        h, feito, ultimo = hashlib.sha256(), 0, -1
        progresso(f"{nome}: baixando {tamanho / 1e6:.0f} MB de huggingface.co ...")
        with abrir(url, timeout=60) as resp, open(parcial, "wb") as saida:
            while bloco := resp.read(1 << 20):
                saida.write(bloco)
                h.update(bloco)
                feito += len(bloco)
                pct = feito * 100 // tamanho
                if pct // 10 != ultimo:
                    ultimo = pct // 10
                    progresso(f"  {pct}%")
        if feito != tamanho or h.hexdigest() != sha:
            parcial.unlink(missing_ok=True)
            raise ValueError(f"{nome}: o arquivo baixado não confere (tamanho ou sha256). Nada foi instalado.")
        parcial.replace(final)
        progresso(f"{nome}: conferido (sha256 OK).")
    return pasta


# --- o codificador -------------------------------------------------------------------------------------------------
class Codificador:
    """Transforma textos em vetores normalizados (cosseno = produto escalar). Carrega o modelo na primeira vez que é
    usado (uns 500 MB de RAM) e é seguro entre threads."""

    def __init__(self, pasta: Path | None = None, threads: int = 4) -> None:
        self.pasta = pasta or pasta_do_modelo()
        self.threads = threads
        self._lock = threading.Lock()
        self._sessao: Any = None
        self._tok: Any = None
        self._entradas: list[str] = []
        self._ancoras: Any = None

    @property
    def disponivel(self) -> bool:
        if self._sessao is not None:
            return True
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
        except ImportError:
            return False
        return modelo_presente(self.pasta)

    def _carregar(self) -> None:
        if self._sessao is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(self.pasta / "tokenizer.json"))
        tok.enable_truncation(MAX_TOKENS)
        tok.enable_padding(pad_id=tok.token_to_id("<pad>") or 1, pad_token="<pad>")
        opcoes = ort.SessionOptions()
        opcoes.intra_op_num_threads = self.threads
        opcoes.inter_op_num_threads = 1
        opcoes.log_severity_level = 3
        sessao = ort.InferenceSession(str(self.pasta / "model.onnx"), opcoes, providers=["CPUExecutionProvider"])
        self._entradas = [e.name for e in sessao.get_inputs()]
        self._tok, self._sessao = tok, sessao

    def codificar(self, textos: list[str], consulta: bool = False, lote: int = 16):
        """Vetores (n x 384, float32, norma 1). `consulta=True` para o pedido do dono; False para o que é guardado
        (o e5 foi treinado com os prefixos "query: " e "passage: ")."""
        import numpy as np

        if not textos:
            return np.zeros((0, DIMENSAO), dtype=np.float32)
        prefixo = "query: " if consulta else "passage: "
        with self._lock:
            self._carregar()
            saida = []
            for i in range(0, len(textos), lote):
                cod = self._tok.encode_batch([prefixo + " ".join(t.split()) for t in textos[i : i + lote]])
                ids = np.array([c.ids for c in cod], dtype=np.int64)
                mascara = np.array([c.attention_mask for c in cod], dtype=np.int64)
                dados = {"input_ids": ids, "attention_mask": mascara, "token_type_ids": np.zeros_like(ids)}
                ocultos = self._sessao.run(None, {k: v for k, v in dados.items() if k in self._entradas})[0]
                m = mascara[..., None].astype(np.float32)
                media = (ocultos * m).sum(1) / np.clip(m.sum(1), 1e-9, None)  # média dos tokens reais
                saida.append(media / np.clip(np.linalg.norm(media, axis=1, keepdims=True), 1e-12, None))
        return np.vstack(saida).astype(np.float32)


def ancoras(cod) -> Any:
    """Vetores das frases neutras (calculados uma vez por codificador)."""
    a = getattr(cod, "_ancoras", None)
    if a is None:
        a = cod.codificar(ANCORAS)
        try:
            cod._ancoras = a
        except AttributeError:
            pass
    return a


_PADRAO: Codificador | None = None
_PADRAO_LOCK = threading.Lock()


def codificador_padrao() -> Codificador:
    """Um codificador só por processo (o painel, a voz e as ferramentas dividem o mesmo modelo carregado)."""
    global _PADRAO
    with _PADRAO_LOCK:
        if _PADRAO is None or _PADRAO.pasta != pasta_do_modelo():
            _PADRAO = Codificador()
        return _PADRAO


# --- índice no banco -----------------------------------------------------------------------------------------------
ESQUEMA = """
CREATE TABLE IF NOT EXISTS ideia_vetores (
    ideia_id TEXT PRIMARY KEY REFERENCES ideias(id) ON DELETE CASCADE,
    modelo TEXT NOT NULL, marca TEXT NOT NULL, vetor BLOB NOT NULL, quando TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS ideias_fts USING fts5(
    ideia_id UNINDEXED, nome, resumo, tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS ideias_fts_ai AFTER INSERT ON ideias BEGIN
    INSERT INTO ideias_fts (ideia_id, nome, resumo) VALUES (new.id, new.nome, new.resumo); END;
CREATE TRIGGER IF NOT EXISTS ideias_fts_ad AFTER DELETE ON ideias BEGIN
    DELETE FROM ideias_fts WHERE ideia_id = old.id; END;
CREATE TRIGGER IF NOT EXISTS ideias_fts_au AFTER UPDATE OF nome, resumo ON ideias BEGIN
    DELETE FROM ideias_fts WHERE ideia_id = old.id;
    INSERT INTO ideias_fts (ideia_id, nome, resumo) VALUES (new.id, new.nome, new.resumo); END;
"""

# Palavras que aparecem em quase tudo e só atrapalham a busca por palavra.
_VAZIAS = set(
    "a o as os um uma uns umas de da do das dos dela dele em na no nas nos por para pra pro com sem sob sobre e ou "
    "que se ao aos à às é ser foi era sou são tem ter ver meu minha meus minhas seu sua seus suas eu você voce ele ela "
    "isso isto esse essa este esta aquele aquela me te lhe nos já mais muito pouco qual quais quando onde como porque "
    "eco sabe lembra fala diz falar dizer sobre coisa coisas algo tudo".split()
)


def texto_da_ideia(i: Ideia) -> str:
    """O que vira vetor: nome e resumo (numa lembrança, o nome é o título e o resumo é a frase inteira)."""
    if i.resumo and i.nome.lower() not in i.resumo.lower():
        return f"{i.nome}. {i.resumo}"
    return i.resumo or i.nome


def _marca(texto: str) -> str:
    return hashlib.sha1(f"{NOME_MODELO}\n{texto}".encode("utf-8")).hexdigest()


def palavras_da_busca(texto: str) -> list[str]:
    from eco.chat.armazenamento import _sem_acento

    vistas, saida = set(), []
    for p in re.findall(r"\w+", texto, re.UNICODE):
        chave = _sem_acento(p)
        # números curtos ("17 vezes 23") casariam com "17h", "23/11" etc.: ficam de fora; anos e códigos (2026, BOVA11) ficam
        if len(chave) < 3 or (chave.isdigit() and len(chave) < 4) or chave in _VAZIAS or chave in vistas:
            continue
        vistas.add(chave)
        saida.append(p)
    return saida


@dataclass(frozen=True)
class Achado:
    ideia: Ideia
    nota: float  # o que ordena: relevância + bônus por palavra (só por palavra, sem modelo: 0 a 1)
    relevancia: float | None  # cosseno menos a linha de base; None quando o modelo não está disponível
    cosseno: float | None
    por_palavra: bool


class Semelhanca:
    """Busca e comparação sobre as ideias do grafo. Use uma por banco; é segura entre threads."""

    def __init__(self, grafo: Grafo, codificador: Codificador | None = None) -> None:
        self.grafo = grafo
        self.banco = grafo.banco
        self.cod = codificador if codificador is not None else codificador_padrao()
        self._lock = threading.RLock()
        self._ids: list[str] = []
        self._matriz: Any = None
        self._base: Any = None  # linha de base de cada vetor guardado
        self._preparar()

    def _preparar(self) -> None:
        """Cria o índice e os gatilhos (só acrescenta tabelas: nenhum dado existente muda) e repõe o índice de palavras
        se ele não bate com as ideias (banco restaurado de um backup antigo, ideias criadas antes dos gatilhos)."""
        con = self.banco._con
        with self.banco._lock:
            novo = not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'ideias_fts'").fetchone()
            if novo and getattr(self.banco, "arquivo", None) is not None and con.execute("SELECT 1 FROM ideias LIMIT 1").fetchone():
                from eco import backup  # cuidado 2: cópia conferida antes de mexer na estrutura do banco real

                backup.antes_de("criar-indice-de-semelhanca", self.banco.arquivo, self.banco.arquivo.parent / "backups")
            with con:
                con.executescript(ESQUEMA)
                n_ideias = con.execute("SELECT count(*) FROM ideias").fetchone()[0]
                n_fts = con.execute("SELECT count(*) FROM ideias_fts").fetchone()[0]
                if n_ideias != n_fts:
                    con.execute("DELETE FROM ideias_fts")
                    con.execute("INSERT INTO ideias_fts (ideia_id, nome, resumo) SELECT id, nome, resumo FROM ideias")

    @property
    def com_modelo(self) -> bool:
        return self.cod.disponivel

    # --- vetores ---------------------------------------------------------------------------------------------------
    def atualizar(self) -> int:
        """Calcula os vetores que faltam ou ficaram velhos (ideia mudou de texto). Devolve quantos calculou."""
        import numpy as np

        if not self.com_modelo:
            return 0
        with self._lock:
            ideias = self.grafo.listar_ideias(incluir_arquivadas=True)
            marcas = {l["ideia_id"]: l["marca"] for l in self.banco._consultar("SELECT ideia_id, marca FROM ideia_vetores")}
            pendentes = [(i, texto_da_ideia(i)) for i in ideias]
            pendentes = [(i, t) for i, t in pendentes if marcas.get(i.id) != _marca(t)]
            if pendentes:
                vetores = self.cod.codificar([t for _, t in pendentes])
                agora = self.banco._relogio()
                with self.banco._lock, self.banco._con:
                    self.banco._con.executemany(
                        "INSERT OR REPLACE INTO ideia_vetores (ideia_id, modelo, marca, vetor, quando) VALUES (?,?,?,?,?)",
                        [(i.id, NOME_MODELO, _marca(t), v.astype(np.float32).tobytes(), agora) for (i, t), v in zip(pendentes, vetores)],
                    )
            if pendentes or self._matriz is None or len(self._ids) != len(marcas):
                self._carregar_matriz()
            return len(pendentes)

    def _carregar_matriz(self) -> None:
        import numpy as np

        linhas = self.banco._consultar("SELECT ideia_id, vetor FROM ideia_vetores WHERE modelo = ?", (NOME_MODELO,))
        self._ids = [l["ideia_id"] for l in linhas]
        self._matriz = (
            np.vstack([np.frombuffer(l["vetor"], dtype=np.float32) for l in linhas]) if linhas else np.zeros((0, DIMENSAO), np.float32)
        )
        self._base = (self._matriz @ ancoras(self.cod).T).mean(axis=1) if linhas else np.zeros(0, np.float32)

    def vetor(self, ideia_id: str):
        self.atualizar()
        with self._lock:
            return self._matriz[self._ids.index(ideia_id)] if ideia_id in self._ids else None

    def comparar_textos(self, a: str, b: str) -> float:
        """Relevância entre dois textos guardados: cosseno menos a média das linhas de base dos dois."""
        v = self.cod.codificar([a, b])
        base = (v @ ancoras(self.cod).T).mean(axis=1)
        return float(v[0] @ v[1] - (base[0] + base[1]) / 2)

    # --- busca -----------------------------------------------------------------------------------------------------
    def _por_palavra(self, texto: str, limite: int) -> dict[str, float]:
        """ideia_id -> nota de 0 a 1 (1 = melhor casamento por palavra; o bm25 do FTS5 é negativo: menor é melhor)."""
        palavras = palavras_da_busca(texto)
        if not palavras:
            return {}
        consulta = " OR ".join(f'"{p}"*' for p in palavras)
        try:
            linhas = self.banco._consultar(
                "SELECT ideia_id, bm25(ideias_fts, 2.0, 1.0) AS r FROM ideias_fts WHERE ideias_fts MATCH ? ORDER BY r LIMIT ?",
                (consulta, limite),
            )
        except Exception:  # consulta que o FTS5 não aceita: sem resultado por palavra, a busca segue pelo vetor
            return {}
        if not linhas:
            return {}
        melhor = linhas[0]["r"] or -1e-9
        return {l["ideia_id"]: max(0.0, min(1.0, (l["r"] or 0.0) / melhor)) for l in linhas}

    def buscar(
        self, texto: str, limite: int = 8, minimo: float | None = MINIMO_RELEVANTE, incluir_arquivadas: bool = False,
        excluir: Iterable[str] = (),
    ) -> list[Achado]:
        """As ideias mais parecidas com o texto, da melhor para a pior. Com o modelo, só volta quem passa do `minimo`
        de relevância (ou casa por palavra e fica quase lá); sem o modelo, só por palavra."""
        excluir = set(excluir)
        ativas = {i.id: i for i in self.grafo.listar_ideias(incluir_arquivadas=incluir_arquivadas) if i.id not in excluir}
        palavra = {k: v for k, v in self._por_palavra(texto, 4 * limite + 20).items() if k in ativas}
        if not self.com_modelo:
            achados = [Achado(ativas[k], v, None, None, True) for k, v in palavra.items()]
            return sorted(achados, key=lambda a: -a.nota)[:limite]
        self.atualizar()
        consulta = self.cod.codificar([texto], consulta=True)[0]
        base = float((ancoras(self.cod) @ consulta).mean())
        with self._lock:
            ids, matriz = list(self._ids), self._matriz
        notas = matriz @ consulta if len(ids) else []
        achados = []
        for id_, cos in zip(ids, notas):
            if id_ not in ativas:
                continue
            cos = float(cos)
            rel = cos - base
            bonus = PESO_PALAVRA * palavra.get(id_, 0.0)
            if minimo is not None and rel < minimo and not (bonus and rel >= minimo - 0.01):
                continue
            achados.append(Achado(ativas[id_], rel + bonus, rel, cos, id_ in palavra))
        return sorted(achados, key=lambda a: -a.nota)[:limite]

    def parecidas(self, ideia_id: str, limite: int = 5, minimo: float = PARECIDAS_MEDIA) -> list[tuple[Ideia, float]]:
        """Ideias com sentido parecido com esta, com a relevância do par (para sugerir ligações e achar duplicatas nas
        etapas seguintes: acima de `PARECIDAS_ALTA` é o mesmo assunto com boa certeza; entre as duas, talvez)."""
        if not self.com_modelo:
            return []
        self.atualizar()
        with self._lock:
            if ideia_id not in self._ids:
                return []
            ids, matriz, base = list(self._ids), self._matriz, self._base
        k = ids.index(ideia_id)
        notas = matriz @ matriz[k] - (base + base[k]) / 2
        ativas = {i.id: i for i in self.grafo.listar_ideias()}
        pares = [(ativas[i], float(n)) for i, n in zip(ids, notas) if i != ideia_id and i in ativas and n >= minimo]
        return sorted(pares, key=lambda p: -p[1])[:limite]

    def candidatas(self, ideia_id: str, limite: int = 6, minimo: float = 0.015,
                   excluir: Iterable[str] = ()) -> list[tuple[Ideia, float]]:
        """Candidatas a ligação com esta ideia (E6). A linha de base aqui é o PRÓPRIO ACERVO: a média do cosseno de cada
        ideia com todas as outras. Entre lembranças do dono, o nome dele em quase todas faz tudo parecer parecido com
        as frases neutras; contra o acervo, só sobe o que é mais parecido que o normal. Quem decide se a relação existe
        é o Haiku (`grafo/ligacao.py`); isto só escolhe quem ele vai olhar."""
        if not self.com_modelo:
            return []
        self.atualizar()
        excluir = set(excluir)
        with self._lock:
            if ideia_id not in self._ids or len(self._ids) < 3:
                return []
            ids, matriz = list(self._ids), self._matriz
        n = len(ids)
        soma = matriz.sum(axis=0)
        media = (matriz @ soma - 1.0) / (n - 1)  # cosseno médio de cada ideia com as outras (sem ela mesma)
        k = ids.index(ideia_id)
        notas = matriz @ matriz[k] - (media + media[k]) / 2
        ativas = {i.id: i for i in self.grafo.listar_ideias()}
        pares = [(ativas[i], float(v)) for i, v in zip(ids, notas)
                 if i != ideia_id and i in ativas and i not in excluir and v >= minimo]
        return sorted(pares, key=lambda p: -p[1])[:limite]

    def parecidas_com_texto(self, texto: str, origem: str | None = None, limite: int = 4,
                            minimo: float = 0.015) -> list[tuple[Ideia, float]]:
        """Ideias parecidas com um texto que AINDA NÃO é ideia (uma lembrança prestes a ser gravada: E7), com a mesma
        linha de base do acervo de `candidatas`, mais até 2 pela busca por palavra (nota 0). `origem` filtra."""
        ativas = {i.id: i for i in self.grafo.listar_ideias() if origem is None or i.origem == origem}
        achadas: list[tuple[Ideia, float]] = []
        if self.com_modelo:
            self.atualizar()
            with self._lock:
                ids, matriz = list(self._ids), self._matriz
            if len(ids) >= 2:
                v = self.cod.codificar([texto])[0]
                n = len(ids)
                soma = matriz.sum(axis=0)
                media = (matriz @ soma - 1.0) / (n - 1)
                media_v = float(v @ soma) / n
                notas = matriz @ v - (media + media_v) / 2
                achadas = sorted(((ativas[i], float(x)) for i, x in zip(ids, notas) if i in ativas and x >= minimo),
                                 key=lambda p: -p[1])[:limite]
        vistas = {i.id for i, _ in achadas}
        extra = [k for k in self._por_palavra(texto, 10) if k in ativas and k not in vistas][:2]
        return achadas + [(ativas[k], 0.0) for k in extra]


# --- linha de comando ----------------------------------------------------------------------------------------------
def comando_modelo(argumentos: list[str]) -> int:
    """`eco modelo`: diz se o modelo está instalado. `eco modelo baixar`: baixa e confere (uns 490 MB)."""
    pasta = pasta_do_modelo()
    if argumentos and argumentos[0] == "baixar":
        try:
            baixar(pasta)
        except Exception as erro:
            print(f"Não consegui baixar o modelo: {type(erro).__name__}: {erro}")
            return 1
        print(f"Modelo pronto em {pasta}.")
        return 0
    if modelo_presente(pasta):
        print(f"Modelo de semelhança instalado: {REPO} em {pasta}.")
        return 0
    print(f"O modelo de semelhança não está instalado (a busca do grafo usa só palavras). Para instalar: eco modelo baixar")
    return 1


def comando_semelhanca(argumentos: list[str]) -> int:
    """`eco semelhanca "texto"`: mostra as ideias do seu grafo mais parecidas com o texto (para conferir a busca)."""
    import time

    from eco.chat.armazenamento import Banco

    if not argumentos:
        print('Uso: eco semelhanca "o que procurar"')
        return 2
    texto = " ".join(argumentos)
    s = Semelhanca(Grafo(Banco(PASTA_ECO / "eco.db")))
    t = time.perf_counter()
    feitos = s.atualizar()
    if feitos:
        print(f"({feitos} vetor(es) calculado(s) em {time.perf_counter() - t:.1f} s)")
    t = time.perf_counter()
    achados = s.buscar(texto, limite=10, minimo=None)
    print(f"Busca em {(time.perf_counter() - t) * 1000:.0f} ms ({'vetor + palavra' if s.com_modelo else 'só palavra'}):")
    for a in achados:
        marca = "" if a.relevancia is None or a.relevancia >= MINIMO_RELEVANTE else "  (abaixo do mínimo)"
        cos = "" if a.cosseno is None else f"rel {a.relevancia:+.3f} cos {a.cosseno:.3f}"
        print(f"  {a.nota:.3f}  {cos}{' +palavra' if a.por_palavra else ''}  {a.ideia.nome}: {a.ideia.resumo[:70]}{marca}")
    return 0
