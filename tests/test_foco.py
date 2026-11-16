"""Modo foco (só por voz): em silêncio até "sair do modo foco"; avisos guardados e oferecidos ao sair; sem e-mails no
resumo. O foco ligado por evento da Agenda foi retirado a pedido do dono (12/11/2026)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from eco.comandos import comando_foco
from eco.lembretes import FALA_FOCO_LIGADO, FUSO, Evento, Lembretes, frase_de_emails


def _sp(texto):
    return datetime.fromisoformat(texto).replace(tzinfo=FUSO)


# --- comandos de voz -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "frase",
    ["Eco, ative o modo foco", "Echo, ative o modo foco.", "ativar modo foco", "liga o modo foco", "Ei eco, entre no modo foco", "inicie o modo de foco por favor"],
)
def test_comando_ligar_foco(frase):
    assert comando_foco(frase) == "ligar"


@pytest.mark.parametrize(
    "frase",
    ["Echo, desative o modo foco", "desativar modo foco", "desligue o modo foco", "Eco, saia do modo foco", "pare o modo de foco", "encerra o modo foco"],
)
def test_comando_desligar_foco(frase):
    assert comando_foco(frase) == "desligar"  # "desative" não pode cair em "ative"


@pytest.mark.parametrize(
    "frase",
    ["Estudo matéria x em foco", "o que é modo foco?", "não ative o modo foco", "ative o modo foco e me diga as horas", "foco", "", "ative o modo silencioso"],
)
def test_comando_foco_nao_pega_frase_parecida(frase):
    assert comando_foco(frase) is None


def test_frase_de_emails_aceita_outro_comeco():
    e = {"total": 3, "remetentes": ["Ana", "Bia"]}
    assert frase_de_emails(e, "Você está com") == "Você está com 3 e-mails não lidos na caixa principal, os mais recentes de Ana e Bia."
    assert frase_de_emails(e).startswith("E 3 e-mails")


# --- lembretes -------------------------------------------------------------------------------------------------
class Fonte:
    def __init__(self):
        self.agenda: list[Evento] = []
        self.emails_ = {"total": 2, "remetentes": ["Ana"]}
        self.chamadas_emails = 0

    def eventos(self, inicio, fim):
        return [e for e in self.agenda if not e.dia_inteiro and e.inicio < fim and e.fim > inicio]

    def pendencias(self):
        return []

    def emails(self):
        self.chamadas_emails += 1
        return self.emails_


def _l(tmp_path, fonte, avisos=None):
    return Lembretes(fonte, tmp_path / "lembretes.json", lambda t, m: avisos.append((t, m)) if avisos is not None else None)


def _falas(l):
    saida = []
    while l.tem_fala():
        saida.append(l.proxima_fala())
    return saida


def test_resumo_do_dia_traz_emails_fora_do_foco_e_omite_no_foco_manual(tmp_path):
    fonte = Fonte()
    l = _l(tmp_path, fonte)
    l.ciclo(_sp("2026-11-10T08:00:00"))
    assert "e-mails não lidos" in _falas(l)[0]

    outro = tmp_path / "outro"
    outro.mkdir()
    fonte2 = Fonte()
    l2 = _l(outro, fonte2)
    assert l2.ligar_foco() == FALA_FOCO_LIGADO
    l2.ciclo(_sp("2026-11-11T08:00:00"))
    assert "e-mail" not in _falas(l2)[0] and fonte2.chamadas_emails == 0  # nem consultou o Gmail


def test_desligar_foco_manual_da_o_resumo_dos_emails(tmp_path):
    fonte = Fonte()
    l = _l(tmp_path, fonte)
    assert l.desligar_foco() == "O modo foco não estava ligado."
    l.ligar_foco()
    assert l.ligar_foco() == "O modo foco já está ligado."
    assert l.desligar_foco() == "Modo foco desligado. Você está com 2 e-mails não lidos na caixa principal, os mais recentes de Ana."
    assert not l.foco_ativo()
    l.ciclo(_sp("2026-11-10T08:00:00"))
    assert not any("Seu foco acabou" in f for f in _falas(l))  # já deu o resumo na resposta: não repete


def test_foco_manual_sobrevive_a_reiniciar_a_eco(tmp_path):
    l = _l(tmp_path, Fonte())
    l.ligar_foco()
    l2 = _l(tmp_path, Fonte())
    assert l2.foco_ativo()


def test_google_fora_nao_derruba_o_foco_manual(tmp_path):
    class Quebrada(Fonte):
        def eventos(self, inicio, fim):
            raise ConnectionError("sem rede")

    l = _l(tmp_path, Quebrada())
    l.ligar_foco()
    l.ciclo(_sp("2026-11-10T08:00:00"))
    assert l.foco_ativo()


def test_foco_pela_voz_no_laco_principal():
    from eco.principal import _foco_por_voz

    class Falso:
        def ligar_foco(self):
            return "ligado"

        def desligar_foco(self):
            raise RuntimeError("falhou")

    assert _foco_por_voz(Falso(), "ligar") == "ligado"
    assert "Não consegui" in _foco_por_voz(Falso(), "desligar")


def test_foco_funciona_mesmo_com_os_lembretes_desligados():
    from eco.principal import FocoSemLembretes, _foco_por_voz

    f = FocoSemLembretes()
    assert _foco_por_voz(f, "ligar") == FALA_FOCO_LIGADO and f.foco_ativo()
    assert _foco_por_voz(f, "ligar") == "O modo foco já está ligado."
    assert _foco_por_voz(f, "desligar") == "Modo foco desligado." and not f.foco_ativo()
    assert f.quantas_falas() == 0


def test_evento_com_foco_no_titulo_nao_liga_mais_o_foco(tmp_path):
    fonte = Fonte()
    fonte.agenda = [Evento("e1", "Estudo de cálculo em foco", _sp("2026-11-10T14:00:00"), _sp("2026-11-10T16:00:00"))]
    l = _l(tmp_path, fonte)
    l.ciclo(_sp("2026-11-10T14:30:00"))
    assert not l.foco_ativo()


def test_avisos_no_foco_ficam_guardados_e_podem_ser_descartados(tmp_path):
    fonte, avisos = Fonte(), []
    l = _l(tmp_path, fonte, avisos)
    l.ligar_foco()
    l.ciclo(_sp("2026-11-10T08:00:00"))  # resumo do dia chega durante o foco
    assert l.quantas_falas() == 1 and avisos and avisos[0][0] == "Resumo do dia"  # o balão do sistema mostra na hora
    assert l.desligar_foco().startswith("Modo foco desligado.")
    assert l.quantas_falas() == 1  # continua guardado até o dono decidir
    assert l.descartar_falas() == 1 and l.quantas_falas() == 0


def test_pergunta_dos_avisos_no_singular_e_no_plural():
    from eco.principal import pergunta_dos_avisos

    assert pergunta_dos_avisos(1) == "Enquanto você estava em foco, chegou 1 aviso. Quer ouvir?"
    assert pergunta_dos_avisos(3) == "Enquanto você estava em foco, chegaram 3 avisos. Quer ouvir?"


@pytest.mark.parametrize("frase", ["Ei Eco, entrar em modo foco.", "Eco, entrar no modo foco"])
def test_frase_do_dono_para_entrar(frase):
    assert comando_foco(frase) == "ligar"


@pytest.mark.parametrize("frase", ["Ei Eco, sair do modo foco.", "Eco, sair do modo de foco"])
def test_frase_do_dono_para_sair(frase):
    assert comando_foco(frase) == "desligar"
