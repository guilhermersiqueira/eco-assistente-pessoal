"""Consultas exatas ao grafo (etapa E5, requisitos 19.6.6 e 19.6.16): contagens, relações diretas e indiretas com a
hierarquia, histórico e relevância. Tudo sai do banco, sem IA; o modelo só transforma o texto em fala.
"""

from __future__ import annotations

from collections import Counter

from eco.chat.armazenamento import _sem_acento
from eco.grafo.dados import Grafo, Ideia, chave_da_ideia
from eco.grafo.recuperacao import origem_legivel, peso_do_no

NOMES_ORIGEM = {"fato": "lembranças", "chat": "ideias de conversas", "notas": "ideias do bloco de notas",
                "metas": "ideias das metas", "manual": "ideias criadas à mão"}
ACOES = {"criar": "criada", "atualizar": "alterada", "renomear": "renomeada", "apagar": "apagada", "arquivar": "arquivada",
         "restaurar": "restaurada", "proteger": "protegida", "desproteger": "deixou de ser protegida", "importar": "importada",
         "duracao": "duração definida", "reforcar": "reforçada (dita de novo)", "fundir": "juntada com outra"}


def resumo(grafo: Grafo) -> str:
    """Quantas memórias e ideias há, de onde vêm, protegidas, arquivadas e ligações."""
    ativas = grafo.listar_ideias()
    por_origem = Counter(i.origem for i in ativas)
    ligacoes = grafo.listar_ligacoes()
    revisar = sum(1 for l in ligacoes if l.status == "revisar")
    partes = [f"{n} {NOMES_ORIGEM.get(o, o)}" for o, n in sorted(por_origem.items(), key=lambda x: -x[1])]
    texto = (f"O grafo tem {len(ativas)} ideias ativas ({', '.join(partes) or 'nenhuma'}), "
             f"{sum(1 for i in ativas if i.protegido)} protegidas, {len(grafo.arquivadas())} arquivadas "
             f"e {len(ligacoes)} ligações ativas")
    texto += f", {revisar} delas esperando revisão." if revisar else "."
    if grafo.banco._consultar("SELECT 1 FROM sqlite_master WHERE name = 'propostas'"):
        n = len([l for l in grafo.banco._consultar("SELECT 1 FROM propostas WHERE estado = 'pendente'")])
        if n:
            texto += f" Há {n} proposta(s) de limpeza da memória esperando decisão."
    return texto


def custos(grafo: Grafo, cfg, registro) -> str:
    """19.6.20 e 19.6.23: gasto do mês com o grafo por operação, teto, armazenamento e sugestões (nada é apagado)."""
    from eco.uso import GRAFO_CATEGORIAS

    nomes = {"grafo-atualizacao": "análise de conversas, notas e metas", "grafo-ligacao": "ligação automática",
             "grafo-escrita": "conferência ao guardar lembranças", "grafo-higiene": "revisão da memória"}
    por_cat = registro.resumo_categorias()
    linhas = []
    total = 0.0
    for c in GRAFO_CATEGORIAS:
        r = por_cat.get(c, {"chamadas": 0, "custo": 0.0})
        total += r["custo"]
        linhas.append(f"- {nomes[c]}: US$ {r['custo']:.4f} em {int(r['chamadas'])} chamada(s)")
    linhas.append("- busca e recuperação de memórias nas conversas: US$ 0 (roda no computador)")
    teto = cfg.teto_grafo_usd
    gasto = grafo.gasto_do_mes()
    texto = (f"Gasto do mês com o grafo: US$ {gasto:.4f} de US$ {teto:.2f} ({(gasto / teto * 100) if teto else 0:.0f}% do teto, "
             "limite financeiro).\n" + "\n".join(linhas))
    arquivo = getattr(grafo.banco, "arquivo", None)
    mb = arquivo.stat().st_size / 1e6 if arquivo is not None and arquivo.exists() else 0.0
    texto += (f"\nArmazenamento (limite físico): banco com {mb:.1f} MB de {cfg.grafo_limite_mb:.0f} MB, "
              f"{len(grafo.listar_ideias())} ideias ativas e {len(grafo.arquivadas())} arquivadas.")
    dicas = []
    if teto and gasto >= 0.8 * teto:
        dicas.append("o gasto passou de 80% do teto: dá para espaçar a revisão (ECO_HIGIENE_DIAS), desligar a conferência "
                     "ao guardar (ECO_MEMORIA_CONFERIR=nao) ou a ligação automática (ECO_LIGACAO_AUTO=nao), ou aumentar o "
                     "teto (ECO_GRAFO_TETO_USD)")
    if cfg.grafo_limite_mb and mb >= 0.8 * cfg.grafo_limite_mb:
        dicas.append("o banco passou de 80% do limite de tamanho: revise a memória (revisar_memoria) e arquive o que "
                     "não usa mais; nada importante é apagado sozinho")
    return texto + ("\nSugestões: " + "; ".join(dicas) + "." if dicas else "\nTudo dentro dos limites.")


def achar(grafo: Grafo, termo: str, semelhanca=None) -> Ideia:
    """A ideia pelo nome (igual, depois contendo o termo) ou, se não achar, pela semelhança."""
    termo = " ".join((termo or "").split())
    if not termo:
        raise ValueError("Diga o nome da ideia ou da lembrança.")
    ideias = grafo.listar_ideias()
    chave = chave_da_ideia(termo)
    iguais = [i for i in ideias if chave_da_ideia(i.nome) == chave]
    if iguais:
        return max(iguais, key=lambda i: peso_do_no(i))
    alvo = _sem_acento(termo)
    contem = [i for i in ideias if alvo in _sem_acento(i.nome)] or [i for i in ideias if alvo in _sem_acento(i.resumo)]
    if len(contem) == 1:
        return contem[0]
    if semelhanca is not None:
        achados = semelhanca.buscar(termo, limite=1)
        if achados:
            return achados[0].ideia
    if contem:
        return max(contem, key=lambda i: peso_do_no(i))
    raise ValueError(f"Não achei nenhuma ideia sobre \"{termo}\" no grafo.")


def _rotulo(l, outro: Ideia, grafo: Grafo | None = None) -> str:
    from eco.grafo.ligacao import nivel_legivel as nivel, rotulo

    relacao = rotulo(l, grafo)
    extra = " (esperando revisão)" if l.status == "revisar" else ""
    confianca = f", confiança {nivel(l.confianca)}" if l.origem != "manual" and l.relacao else ""
    return f"{outro.nome}: {relacao}{' (' + l.motivo + ')' if l.motivo else ''}{confianca}{extra}"


def relacoes(grafo: Grafo, termo: str, semelhanca=None, limite: int = 12) -> str:
    """Relações diretas e indiretas (até 2 saltos) e a hierarquia de uma ideia (19.6.6)."""
    no = achar(grafo, termo, semelhanca)
    ativas = {i.id: i for i in grafo.listar_ideias()}
    ligacoes = [l for l in grafo.listar_ligacoes() if l.status != "rejeitada"]
    vizinhos: dict[str, list] = {}
    for l in ligacoes:
        for x, y in ((l.a, l.b), (l.b, l.a)):
            if x in ativas and y in ativas:
                vizinhos.setdefault(x, []).append((y, l))
    linhas = [f"\"{no.nome}\" ({origem_legivel(grafo, no)}): {no.resumo}".rstrip(": ")]
    cadeia, atual = [], no
    while atual.pai and atual.pai in ativas and len(cadeia) < 5:
        atual = ativas[atual.pai]
        cadeia.append(atual.nome)
    if cadeia:
        linhas.append("Fica dentro de: " + " > ".join(reversed(cadeia)) + ".")
    filhos = [f.nome for f in grafo.filhos(no.id) if f.id in ativas]
    if filhos:
        linhas.append("Dentro dela: " + ", ".join(filhos[:limite]) + ".")
    diretos = vizinhos.get(no.id, [])
    if diretos:
        linhas.append("Ligações diretas: " + "; ".join(_rotulo(l, ativas[v], grafo) for v, l in diretos[:limite]) + ".")
    vistos = {no.id} | {v for v, _ in diretos}
    indiretos = []
    for v, _ in diretos:
        for w, _l in vizinhos.get(v, []):
            if w not in vistos:
                vistos.add(w)
                indiretos.append(f"{ativas[w].nome} (por {ativas[v].nome})")
    if indiretos:
        linhas.append("Ligações indiretas: " + ", ".join(indiretos[:limite]) + ".")
    if len(linhas) == 1 + bool(cadeia) and not filhos:
        linhas.append("Ela ainda não tem ligações nem ideias dentro.")
    return "\n".join(linhas)


def historico(grafo: Grafo, termo: str, semelhanca=None, limite: int = 10) -> str:
    """O que aconteceu com uma ideia, da mais antiga para a mais nova, com quem fez (19.6.4)."""
    no = achar(grafo, termo, semelhanca)
    eventos = grafo.historico(no.id)[-limite:]
    if not eventos:
        return f"\"{no.nome}\" não tem histórico registrado (foi criada antes do histórico existir, em {no.criado_em[:10]})."
    linhas = [f"Histórico de \"{no.nome}\":"]
    for e in eventos:
        mudou = ""
        if e["acao"] in ("atualizar", "renomear") and e["antes"] and e["depois"]:
            mudou = "; ".join(f"{k}: \"{e['antes'].get(k)}\" para \"{v}\"" for k, v in e["depois"].items() if k in e["antes"])
        linhas.append(f"- {e['quando'][:16].replace('T', ' ')}: {ACOES.get(e['acao'], e['acao'])} por {e['autor'] or 'sistema'}"
                      + (f" ({mudou})" if mudou else ""))
    return "\n".join(linhas)


def mais_relevantes(grafo: Grafo, limite: int = 10) -> str:
    ideias = sorted(grafo.listar_ideias(), key=lambda i: -peso_do_no(i))[:limite]
    if not ideias:
        return "O grafo ainda está vazio."
    return "As ideias de maior relevância: " + "; ".join(
        f"{i.nome} ({peso_do_no(i):.2f}, {origem_legivel(grafo, i)}, usada {i.usos} vez(es))" for i in ideias) + "."


def ligacoes_pendentes(grafo: Grafo, limite: int = 10) -> str:
    """As ligações marcadas para revisar e as sugestões (D6), numeradas, com motivo e confiança."""
    from eco.grafo.ligacao import nivel_legivel as nivel, rotulo

    ideias = {i.id: i for i in grafo.listar_ideias()}
    linhas = []
    for titulo, status in (("Para revisar (já valem)", "revisar"), ("Sugestões (ainda não valem)", "sugestao")):
        lista = [l for l in grafo.ligacoes_com_status(status) if l.a in ideias and l.b in ideias][:limite]
        if lista:
            linhas.append(titulo + ":")
            linhas += [f"- {ideias[l.a].nome} e {ideias[l.b].nome}: {rotulo(l, grafo)} ({l.motivo or 'sem motivo'}; "
                       f"confiança {nivel(l.confianca)})" for l in lista]
    return "\n".join(linhas) or "Não há ligações para revisar nem sugestões."


def arquivadas(grafo: Grafo, limite: int = 15) -> str:
    lista = grafo.arquivadas()
    if not lista:
        return "Não há ideias arquivadas."
    return f"{len(lista)} ideia(s) arquivada(s): " + ", ".join(i.nome for i in lista[:limite]) + "."
