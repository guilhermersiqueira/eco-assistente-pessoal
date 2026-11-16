"""Ponto de entrada: `eco` (voz), `eco texto` (digitando, sem microfone) e `eco autorizar-google`."""

from __future__ import annotations

import os
import sys
import threading

from eco.agente import Agente
from eco.config import PASTA_ECO, Config, carregar_config
from eco.ferramentas import construir_ferramentas
from eco.painel import Painel
from eco.uso import FALA, TRANSCRICAO, RegistroUso

ARQUIVO_USO = PASTA_ECO / "uso.jsonl"
ARQUIVO_LEMBRETES = PASTA_ECO / "lembretes.json"


def _exigir_chave(nome: str) -> None:
    if not os.environ.get(nome, "").strip():
        print(
            f"Falta a chave {nome}. Cole-a no arquivo {PASTA_ECO / '.env'} depois de '{nome}=' "
            "(sem aspas nem espaços), salve o arquivo com Ctrl+S e rode o comando de novo."
        )
        sys.exit(1)


def _usd(valor: float, casas: int = 4) -> str:
    return f"US$ {valor:.{casas}f}".replace(".", ",")


def _linha_gasto(agente: Agente, registro: RegistroUso, cfg: Config) -> str:
    u = agente.gasto_ultimo
    if u is None:
        return ""
    partes = [u.modelo.replace("claude-", ""), f"{u.entrada} tokens de entrada", f"{u.saida} de saída"]
    if u.buscas:
        partes.append(f"{u.buscas} pesquisa(s) na web")
    if getattr(agente, "recuperados_ultimo", None):
        partes.append(f"{len(agente.recuperados_ultimo)} memória(s) recuperada(s)")
    partes.append(f"~{_usd(u.custo)}")
    mes = registro.total_mes()
    partes.append(f"mês: {_usd(mes, 2)} de {_usd(cfg.orcamento_usd, 2)}")
    aviso = " | atenção: perto do limite do mês" if mes >= 0.8 * cfg.orcamento_usd else ""
    return "  [" + " | ".join(partes) + "]" + aviso


def mostrar_gasto(cfg: Config) -> None:
    registro = RegistroUso(ARQUIVO_USO)
    resumo = registro.resumo_mes()
    if not resumo and not registro.resumo_categorias():
        print("Nenhum gasto registrado neste mês.")
        return
    for modelo, (chamadas, custo) in sorted(resumo.items()):
        print(f"{modelo}: {chamadas} chamada(s), {_usd(custo)}")
    openai = registro.total_openai_mes()
    claude = registro.total_mes() - openai
    print(f"Claude: {_usd(claude, 2)} | OpenAI (voz, estimado): {_usd(openai, 2)}")
    print(f"Total do mês: {_usd(registro.total_mes(), 2)} de {_usd(cfg.orcamento_usd, 2)}")
    print()
    print("Por uso:")
    for nome, r in sorted(registro.resumo_categorias().items(), key=lambda x: -x[1]["custo"]):
        extra = []
        if r["segundos"]:
            extra.append(f"{r['segundos'] / 60:.1f} min de áudio")
        if r["caracteres"]:
            extra.append(f"{int(r['caracteres'])} caracteres falados")
        valor = _usd(r["custo"])
        print(f"  {nome}: {int(r['chamadas'])} chamada(s), {valor}" + (f" ({', '.join(extra)})" if extra else ""))
    print(f"(estimativas: Claude pela tabela de preços; OpenAI a US$ {cfg.preco_transcricao_min}/min transcrito e "
          f"US$ {cfg.preco_fala_min}/min de voz gerada. Confira nos painéis da Anthropic e da OpenAI;"
          " a voz anterior a esta versão não tem custo registrado.)")


def modo_texto(cfg: Config) -> None:
    """Conversa digitando. Útil para testar o agente sem microfone nem OpenAI."""
    _exigir_chave("ANTHROPIC_API_KEY")

    def confirmar(descricao: str) -> bool:
        from eco.confirmacao import eh_sim

        return eh_sim(input(f"[confirmação] {descricao} Posso confirmar? "))

    registro = RegistroUso(ARQUIVO_USO)
    agente = Agente(cfg, construir_ferramentas(cfg), confirmar, registro=registro)
    print("Eco em modo texto. Linha vazia ou Ctrl+C para sair.")
    while True:
        try:
            pergunta = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not pergunta:
            break
        try:
            print("Eco:", agente.responder(pergunta))
            print(_linha_gasto(agente, registro, cfg))
        except Exception as erro:  # erro de rede ou da API: mostra e continua a conversa
            print(f"Erro: {type(erro).__name__}: {erro}")


class FocoSemLembretes:
    """Modo foco quando os lembretes estão desligados (ECO_LEMBRETES=nao): só o estado, sem avisos para guardar."""

    def __init__(self) -> None:
        self.ativo = False

    def foco_ativo(self) -> bool:
        return self.ativo

    def ligar_foco(self) -> str:
        from eco.lembretes import FALA_FOCO_LIGADO

        if self.ativo:
            return "O modo foco já está ligado."
        self.ativo = True
        return FALA_FOCO_LIGADO

    def desligar_foco(self) -> str:
        if not self.ativo:
            return "O modo foco não estava ligado."
        self.ativo = False
        return "Modo foco desligado."

    def quantas_falas(self) -> int:
        return 0

    def descartar_falas(self) -> int:
        return 0


def _foco_por_voz(foco, acao: str) -> str:
    """Resposta falada para "entrar/sair do modo foco" (o estado vive nos lembretes, ou em `FocoSemLembretes`)."""
    try:
        return foco.ligar_foco() if acao == "ligar" else foco.desligar_foco()
    except Exception as erro:
        print("Erro no modo foco:", erro)
        return "Não consegui mudar o modo foco agora."


def pergunta_dos_avisos(n: int) -> str:
    return f"Enquanto você estava em foco, chegou 1 aviso. Quer ouvir?" if n == 1 else (
        f"Enquanto você estava em foco, chegaram {n} avisos. Quer ouvir?")


def _laco_voz(cfg: Config, painel: Painel) -> None:
    """Fluxo de voz completo. Roda na thread principal (sem janela) ou numa thread (com o orbe)."""
    import threading
    import time

    from openai import OpenAI

    from eco import sons
    from eco.comandos import comando_foco, eh_abrir_interface
    from eco.confirmacao import confirmar_por_voz, eh_despedida, eh_sim
    from eco.erros import mensagem_de_erro
    from eco.voz.ativacao import criar_ativador
    from eco.voz.gravacao import gravar_ate_silencio
    from eco.voz.filtro import FiltroDeVoz
    from eco.voz.stt_tts import Fala, falar, segundos_wav, transcrever

    openai = OpenAI()
    ultima = {"texto": ""}
    filtro = FiltroDeVoz() if cfg.filtro_voz else None
    if filtro is not None:  # carrega o modelo já (leva ~2 s), para a primeira escuta não esperar
        threading.Thread(target=filtro.segundos_de_fala, args=(b"",), daemon=True, name="eco-filtro").start()

    def dizer(texto: str):
        """Fala o texto. Devolve a `Fala` (o que foi dito e se o dono calou a Eco no meio)."""
        print("Eco:", texto)
        ultima["texto"] = texto
        if painel.mudo:  # sem voz: a resposta aparece na tela pelo tempo de ler (um clique no orbe pula) e ela continua ouvindo
            painel.estado("idle")
            painel.legenda(texto, True)
            painel.interromper.clear()
            painel.interromper.wait(min(10.0, 1.5 + len(texto) / 16))
            painel.legenda(texto, False)
            return Fala(texto)
        painel.estado("speak")
        painel.legenda(texto, True)  # fica na tela enquanto ela fala
        painel.interromper.clear()  # um clique antigo não pode calar a fala nova
        try:
            fala = falar(openai, texto, cfg, painel.nivel, painel.interromper)
            _registrar_fala(fala, texto)
            return fala
        finally:
            painel.nivel(0.0)
            painel.legenda(texto, False)  # agora começa a contagem para sumir

    def _registrar_audio(categoria: str, modelo: str, **volume) -> None:
        try:
            registro.registrar_audio(categoria, modelo, **volume)
        except OSError:
            pass  # o contador nunca derruba a conversa

    def _registrar_fala(fala, texto: str) -> None:
        _registrar_audio(FALA, cfg.modelo_tts, segundos=fala.segundos_gerados, caracteres=len(texto), preco_min=cfg.preco_fala_min)

    def ouvir(estado: str = "listen", espera: float | None = None) -> str:
        painel.estado(estado)
        painel.legenda("Ouvindo…" if estado == "listen" else "Aguardando seu sim ou não…", True)
        if espera is None and not painel.mudo:  # na continuação da conversa não toca o sinal: ela já está ouvindo
            sons.inicio(cfg)
        wav = gravar_ate_silencio(cfg, painel.nivel, espera)
        painel.nivel(0.0)
        if not painel.mudo:
            sons.fim(cfg)
        if not wav:
            return ""
        if filtro is not None and not filtro.tem_fala(wav):  # barulho sem voz: nem transcreve (nem gasta)
            print(f"  [ruído] gravação sem voz descartada ({segundos_wav(wav):.1f}s de áudio)")
            painel.legenda("", False)
            return ""
        painel.estado("think")
        painel.legenda("", False)
        t0 = time.perf_counter()
        texto = transcrever(openai, wav, cfg)
        _registrar_audio(TRANSCRICAO, cfg.modelo_stt, segundos=segundos_wav(wav), preco_min=cfg.preco_transcricao_min)
        print(f"  [tempo] transcrição: {time.perf_counter() - t0:.1f}s")
        return texto

    def aos_efeitos(nome: str) -> None:
        if nome in ("lembrar", "salvar_anotacao", "definir_meta", "concluir_meta", "criar_tarefa", "editar_lembranca", "fixar_lembranca", "definir_topico_meta"):
            painel.memoria()

    from eco.cerebro import Cerebro, texto_para_falar, vai_para_janela
    from eco.chat.armazenamento import Banco

    from eco.grafo.dados import Grafo
    from eco.grafo.memorias import Memorias

    registro = RegistroUso(ARQUIVO_USO)
    banco = Banco(PASTA_ECO / "eco.db")
    memorias = Memorias(Grafo(banco), cfg.pasta_dados)  # a memória mora no banco (D2); o memoria.md é a cópia
    memorias.inicializar()
    iniciar_semelhanca(banco, cfg)  # E4/E6: vetores (local, sem custo) e ligação das ideias pendentes
    from eco.grafo.recuperacao import Recuperacao

    recuperacao = Recuperacao(memorias.grafo, memorias)  # E5: memória escolhida por assunto a cada pedido
    agente = Agente(
        cfg,
        construir_ferramentas(cfg, pedir_texto=painel.pedir_texto, banco=banco),  # janela do destinatário; grafo
        lambda d: confirmar_por_voz(d, dizer, lambda: ouvir("confirm")),
        registro=registro,
        ao_ferramenta=aos_efeitos,
        memorias=memorias,
        recuperacao=recuperacao,
    )
    cerebro = Cerebro(agente, banco)  # o mesmo agente atende a voz e a caixa de texto da aba Conversas
    painel.cerebro = cerebro
    ativador = criar_ativador(cfg)

    lembretes = None
    if cfg.lembretes:  # resumo do dia na 1ª abertura + aviso antes dos compromissos (thread só enfileira; quem fala é esta)
        from eco.lembretes import FonteGoogle, Lembretes

        lembretes = Lembretes(
            FonteGoogle(cfg.pasta_dados), ARQUIVO_LEMBRETES, painel.notificar, cfg.lembrete_antecedencia_min
        )
        lembretes.iniciar()

    foco = lembretes if lembretes is not None else FocoSemLembretes()

    def falar_pendentes() -> None:
        """Fala os lembretes que chegaram, um de cada vez, só quando a Eco está livre (nunca corta uma fala). No modo foco
        não fala nada: os avisos ficam na fila (o balão do sistema já mostrou) até o dono sair do foco."""
        while lembretes is not None and not foco.foco_ativo() and (texto := lembretes.proxima_fala()):
            try:
                fala = dizer(texto)  # sem voz (silenciada), ela só mostra o texto na tela; o balão do sistema também avisa
                agente.registrar_fala_automatica(texto)
                if fala.interrompida:
                    agente.marcar_interrompida(fala.falado)
            except Exception as erro:
                print("Erro no lembrete:", erro)
            repouso()

    def parar_espera() -> bool:
        # no foco os avisos esperam: não interrompe a escuta por eles (senão ficaria girando sem parar)
        return painel.pedido_repetir.is_set() or (lembretes is not None and lembretes.tem_fala() and not foco.foco_ativo())

    def repouso() -> None:
        if foco.foco_ativo():  # orbe apagada e a legenda lembrando que ela está em silêncio
            painel.estado("mute")
            painel.legenda("Modo foco", True)
            return
        painel.estado("mute" if painel.mudo else "idle")

    def sair_do_foco(pedido: str) -> None:
        """'Ei Eco, sair do modo foco': volta a falar, dá o resumo e oferece os avisos que chegaram no foco."""
        print("Você:", pedido)
        painel.legenda("", False)
        dizer(_foco_por_voz(foco, "desligar"))
        n = foco.quantas_falas()
        if not n:
            return
        dizer(pergunta_dos_avisos(n))
        if eh_sim(ouvir("confirm")):
            falar_pendentes()
        else:
            foco.descartar_falas()
            dizer("Tudo bem, deixei pra lá.")

    falhas_seguidas = 0  # erros do microfone em sequência (a espera entre tentativas cresce)
    try:
        repouso()
        while True:
            falar_pendentes()
            if painel.pedido_repetir.is_set():
                painel.pedido_repetir.clear()
                if ultima["texto"]:
                    try:
                        dizer(ultima["texto"])
                    except Exception as erro:
                        painel.estado("error")
                        painel.legenda(mensagem_de_erro(erro))
                        time.sleep(3)
                else:
                    painel.legenda("Ainda não falei nada.")
                repouso()
                continue
            try:
                ouviu_a_palavra = ativador.aguardar(parar_espera)
                falhas_seguidas = 0
            except KeyboardInterrupt:
                raise
            except Exception as erro:  # microfone desconectado ou trocado no Windows: tenta de novo em vez de fechar a Eco
                falhas_seguidas += 1
                espera = min(30.0, 2.0 * falhas_seguidas)
                print(f"[microfone] erro esperando a palavra de ativação ({type(erro).__name__}: {erro}); nova tentativa em {espera:.0f}s")
                if falhas_seguidas == 1:
                    import traceback

                    traceback.print_exc()
                try:  # o PortAudio guarda a lista de aparelhos de quando abriu: relê (cobre fone Bluetooth que volta)
                    import sounddevice as sd

                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
                painel.estado("error")
                painel.legenda("Não consegui ouvir o microfone. Tentando de novo…", True)
                time.sleep(espera)
                painel.legenda("", False)
                repouso()
                continue
            if not ouviu_a_palavra:
                continue
            try:
                pedido = ouvir()
                if not pedido:
                    painel.legenda("Não ouvi nada.")
                    repouso()
                    continue
                if foco.foco_ativo():  # em foco: só "sair do modo foco" faz ela falar; o resto é ignorado, sem chamar o Claude
                    if comando_foco(pedido) == "desligar":
                        sair_do_foco(pedido)
                    else:
                        print(f"  [foco] ignorado: {pedido}")
                    repouso()
                    continue
                if lembretes is not None:  # 1º contato depois das 20h30: o fechamento do dia entra na fila e é falado após a resposta
                    threading.Thread(target=lembretes.no_contato, daemon=True, name="eco-fechamento").start()
                while True:  # conversa: depois de cada resposta ela continua ouvindo, sem precisar do "ei eco"
                    local = None  # comando local: não passa pelo Claude (rápido e sem custo)
                    if eh_abrir_interface(pedido):
                        local = lambda: "Abrindo a interface." if painel.abrir_interface() else "A janela da interface está desligada."  # noqa: E731
                    elif (acao_foco := comando_foco(pedido)) is not None:
                        local = lambda: _foco_por_voz(foco, acao_foco)  # noqa: E731
                    if local is not None:
                        print("Você:", pedido)
                        painel.legenda(pedido, True)
                        painel.estado("think")
                        dizer(local())
                        if cfg.conversa_seg <= 0 or parar_espera() or foco.foco_ativo():  # entrou em foco: para de ouvir sem "ei eco"
                            break
                        pedido = ouvir("listen", cfg.conversa_seg)
                        if not pedido:
                            painel.legenda("", False)
                            break
                        continue
                    print("Você:", pedido)
                    painel.legenda(pedido, True)
                    painel.estado("think")
                    t0 = time.perf_counter()
                    encerrar = eh_despedida(pedido)  # ela se despede e só então volta a esperar o "ei eco"
                    resposta = cerebro.responder(pedido, "voz")
                    print(f"  [tempo] Claude: {time.perf_counter() - t0:.1f}s")
                    if vai_para_janela(resposta):  # código ou texto longo: vai para a tela; a voz não lê o código
                        painel.flutuante(resposta)
                    fala = dizer(texto_para_falar(resposta))
                    print(_linha_gasto(agente, registro, cfg))
                    if fala.interrompida:  # calada no meio: guarda só o que foi dito e volta a ouvir, sem "ei eco"
                        print(f"  [interrompida] falou: {fala.falado!r}")
                        agente.marcar_interrompida(fala.falado)
                        encerrar = False
                        pedido = ouvir("listen", cfg.conversa_seg or cfg.espera_fala_seg)
                        if not pedido:
                            painel.legenda("", False)
                            break
                        continue
                    if encerrar or cfg.conversa_seg <= 0 or parar_espera():
                        break
                    pedido = ouvir("listen", cfg.conversa_seg)
                    if not pedido:
                        painel.legenda("", False)
                        break
            except KeyboardInterrupt:
                raise
            except Exception as erro:  # um erro de rede não deve derrubar o assistente
                print("Erro:", erro)
                painel.nivel(0.0)
                painel.estado("error")
                painel.legenda(mensagem_de_erro(erro), True)
                try:
                    if not painel.mudo:
                        aviso = "Tive um problema. Tente de novo."
                        _registrar_fala(falar(openai, aviso, cfg), aviso)
                    else:
                        time.sleep(2.5)
                except Exception:
                    time.sleep(2.5)
                painel.legenda(mensagem_de_erro(erro), False)
            repouso()
    except KeyboardInterrupt:
        print("\nAté logo.")
    finally:
        ativador.fechar()


def iniciar_backup_automatico(cfg: Config, dormir=None) -> threading.Thread | None:
    """Thread que faz o backup automático do eco.db: ao abrir a Eco e depois confere a cada hora (requisito 19.6.13)."""
    import time

    from eco import backup

    if cfg.backup_horas <= 0:
        return None
    dormir = dormir or time.sleep

    def laco() -> None:
        while True:
            try:
                copia = backup.backup_automatico(horas=cfg.backup_horas, manter=cfg.backup_manter)
                if copia is not None:
                    print(f"  [backup] cópia automática feita: {copia.name}")
            except Exception as erro:  # o registro já guardou o erro; tenta de novo na próxima hora
                print(f"  [backup] o backup automático falhou: {type(erro).__name__}: {erro}")
            if dormir(3600) is False:  # (os testes param o laço assim)
                return

    t = threading.Thread(target=laco, daemon=True, name="eco-backup")
    t.start()
    return t


def iniciar_semelhanca(banco, cfg: Config | None = None) -> threading.Thread | None:
    """Thread que deixa o índice de semelhança do grafo em dia ao abrir a Eco (E4) e liga as ideias novas ou alteradas
    desde a última vez (E6; Haiku, dentro do teto do grafo). Sem o modelo, só avisa."""
    from eco.grafo import semelhanca

    if not semelhanca.modelo_presente():
        print("  [semelhança] modelo local não instalado: a busca no grafo usa só palavras (eco modelo baixar).")
        return None

    def trabalho() -> None:
        try:
            from eco.grafo.dados import Grafo

            s = semelhanca.Semelhanca(Grafo(banco))
            feitos = s.atualizar()
            semelhanca.ancoras(s.cod)  # carrega o modelo já (~2 s), para a 1ª pergunta não esperar
            if feitos:
                print(f"  [semelhança] {feitos} ideia(s) indexada(s).")
            if cfg is not None and cfg.ligacao_automatica:
                from eco.grafo.ligacao import ligador_padrao
                from eco.uso import RegistroUso

                r = ligador_padrao(cfg, banco, RegistroUso(ARQUIVO_USO)).executar()
                if r.analisadas:
                    print(f"  [ligação] {r.frase()} (~US$ {r.custo:.4f})")
            if cfg is not None and cfg.higiene_dias > 0:  # E7: revisão automática, só quando venceu o prazo
                from eco.grafo.higiene import higiene_padrao
                from eco.uso import RegistroUso

                h = higiene_padrao(cfg, banco, None, RegistroUso(ARQUIVO_USO))
                if h.precisa_revisar(cfg.higiene_dias):
                    rh = h.revisar(automatica=True)
                    print(f"  [memória] {rh.frase(len(h.propostas()))} (~US$ {rh.custo:.4f})")
        except Exception as erro:
            print(f"  [semelhança] não consegui indexar o grafo: {type(erro).__name__}: {erro}")

    t = threading.Thread(target=trabalho, daemon=True, name="eco-semelhanca")
    t.start()
    return t


def modo_voz(cfg: Config) -> None:
    _exigir_chave("ANTHROPIC_API_KEY")
    _exigir_chave("OPENAI_API_KEY")
    iniciar_backup_automatico(cfg)
    if cfg.visual:
        try:
            from eco.visual.janela import rodar_com_trabalho
        except ImportError:
            print("PySide6 não está instalado; rodando sem a janela do orbe.")
        else:
            rodar_com_trabalho(cfg, lambda painel: _laco_voz(cfg, painel))
            return
    _laco_voz(cfg, Painel())


def modo_orbe(cfg: Config) -> None:
    """Demonstração do orbe: percorre os estados sem usar nenhuma API."""
    from eco.visual.janela import demo

    demo(cfg)


_TRAVA = None  # mantém o arquivo de trava aberto enquanto a Eco roda


def _redirecionar_saida_para_log() -> None:
    """Sem terminal (pythonw, início com o Windows) não há onde imprimir: tudo vai para ~/.eco/eco.log."""
    from eco.saida import abrir_log, preparar_terminal

    sem_terminal = sys.stdout is None or sys.stderr is None or os.path.basename(sys.executable).lower() == "pythonw.exe"
    if not sem_terminal:
        preparar_terminal()  # um caractere fora do cp1252 (transcrição em outro alfabeto) não derruba mais o pedido
        return
    log = abrir_log(PASTA_ECO / "eco.log")  # UTF-8, hora em cada linha, gira acima de 5 MB
    sys.stdout = sys.stderr = log
    print(f"\n--- Eco iniciada em {__import__('datetime').datetime.now():%d/%m/%Y %H:%M:%S} ---")


def _instancia_unica() -> bool:
    """False se já existe uma Eco rodando (duas ouvindo o mesmo microfone responderiam em dobro)."""
    global _TRAVA
    try:
        import msvcrt

        PASTA_ECO.mkdir(parents=True, exist_ok=True)
        _TRAVA = open(PASTA_ECO / "eco.lock", "w")
        msvcrt.locking(_TRAVA.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:  # fora do Windows: sem trava
        return True
    except OSError:
        return False
    return True


def main() -> None:
    _redirecionar_saida_para_log()
    try:  # uma queda nativa (Qt, áudio) deixa ao menos a pilha no log/terminal em vez de sumir calada
        import faulthandler

        faulthandler.enable()
    except Exception:
        pass
    comando = sys.argv[1] if len(sys.argv) > 1 else "voz"
    if comando == "voz" and not _instancia_unica():
        print("A Eco já está rodando (procure o ícone perto do relógio). Fechando esta cópia.")
        return
    if comando == "autorizar-google":
        from eco.ferramentas.google import GoogleNaoConfigurado, autorizar

        try:
            autorizar()
        except GoogleNaoConfigurado as erro:
            print(erro)
            sys.exit(1)
        print("Google autorizado.")
        return
    cfg = carregar_config()
    if comando == "texto":
        modo_texto(cfg)
    elif comando == "voz":
        modo_voz(cfg)
    elif comando == "gasto":
        mostrar_gasto(cfg)
    elif comando == "orbe":
        modo_orbe(cfg)
    elif comando == "vigiar":
        from eco.supervisor import comando_vigiar

        sys.exit(comando_vigiar())
    elif comando == "backup":
        from eco.backup import comando_backup

        sys.exit(comando_backup(sys.argv[2:]))
    elif comando == "modelo":
        from eco.grafo.semelhanca import comando_modelo

        sys.exit(comando_modelo(sys.argv[2:]))
    elif comando == "semelhanca":
        from eco.grafo.semelhanca import comando_semelhanca

        sys.exit(comando_semelhanca(sys.argv[2:]))
    else:
        print("Uso: eco [voz | vigiar | texto | orbe | gasto | backup | modelo | semelhanca | autorizar-google]")
        sys.exit(2)
