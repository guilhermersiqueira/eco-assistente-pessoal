"""Tokens de design da Eco: cores, tipos, medidas e tempos num lugar só.

Fonte: documento "Eco — Design" (10/11/2026). Toda aba deve tirar cor, fonte e duração daqui, nunca
escrever um valor solto. Para mudar o visual da Eco inteira, mude aqui.

Regras que os testes cobram (tests/test_tema.py):
- todo token de TEXTO passa em WCAG AA (4,5:1) sobre os três fundos;
- `VERDE` é só preenchimento (faixa, botão, barra): como texto ele NÃO passa em AA. Texto verde usa `VERDE_LUZ`;
- `BORDA` é decorativa (cartões). Campo de digitação, caixa de marcar e botão usam `BORDA_FORTE` (3:1).
"""

from __future__ import annotations

# --- cores -------------------------------------------------------------------------------------------------------
FUNDO = "#050806"  # base da janela, quase preto
SUPERFICIE = "#0B1210"  # cartões e painéis
ELEVADO = "#111C18"  # cartão em foco, menus, diálogos
BORDA = "#2A3A34"  # contorno decorativo (cinza grafite)
BORDA_FORTE = "#557267"  # contorno de campo, caixa e botão (>= 3:1 sobre os fundos)
TEXTO = "#E6F2EE"  # texto principal
TEXTO_2 = "#8FA39C"  # texto secundário e rótulos
TEXTO_APAGADO = "#6B837B"  # item concluído ou desabilitado
VERDE = "#1F7A4D"  # acento principal (só preenchimento)
VERDE_LUZ = "#2FBF71"  # brilho, estado ativo, texto verde
CIANO = "#3DD6E0"  # destaque (confirmação, tarefa importante)
OURO = "#E8C15A"  # memória (fatos)
ALERTA = "#E0605A"  # erro, atraso e ações destrutivas

FUNDOS = (FUNDO, SUPERFICIE, ELEVADO)
CORES_DE_TEXTO = (TEXTO, TEXTO_2, VERDE_LUZ, CIANO, OURO, ALERTA)
# Espaços: seis cores com a mesma saturação e luz (HSL 55%, 68%), uma por espaço. Ficam longe do vermelho
# (`ALERTA`, erro) e do dourado (`OURO`, memória) para nenhum espaço parecer erro ou memória.
CORES_ESPACOS = ("#7ED9AB", "#7ED3D9", "#7EADD9", "#8A7ED9", "#BF7ED9", "#D97EB4")
COR_GERAL = TEXTO_2  # área "Geral", sem espaço
ALERTA_FUNDO = "#2A1412"  # fundo da mensagem de erro na conversa
# Grafo: cor de cada origem de ideia (memória é sempre ouro, como no documento)
COR_ORIGEM = {"memoria": OURO, "fato": OURO, "notas": "#7EADD9", "metas": VERDE_LUZ, "manual": TEXTO_2}
BRILHO_FUNDO = 0.07  # luz verde suave no alto da janela principal (degradê fixo, desenhado uma vez)
OPACIDADE_CARTAO = 0.84  # "vidro escuro" sem desfoque: deixa as partículas de fundo aparecerem de leve

# --- tipografia (tamanhos em pixels) -----------------------------------------------------------------------------
FONTE_TEXTO = "Segoe UI Variable Text"
FONTE_TEXTO_RESERVA = "Segoe UI"
FONTE_MONO = "Cascadia Mono"
FONTE_MONO_RESERVA = "Consolas"
ROTULO, CORPO, SUBTITULO, TITULO = 11, 13, 15, 20
ENTRELINHA = 1.6
LARGURA_LEITURA = 680

# --- medidas -----------------------------------------------------------------------------------------------------
GRADE = 4
RAIO_CARTAO = 12
RAIO_HUD = 4

# --- movimento (ms, salvo indicação) -----------------------------------------------------------------------------
HOVER_MS = 150
ABA_MS = 200
ABRIR_MS = 240
NO_NOVO_MS = 400
REORGANIZAR_MS = 600
CURSOR_MS = 800
FLUXO_VOLTA_S = 6.0  # uma volta do fluxo de luz na borda
FLUXO_QUADROS_S = 30  # o fluxo é lento: 30 quadros por segundo bastam e poupam o processador


def cor(valor: str, alfa: float = 1.0):
    from PySide6.QtGui import QColor

    c = QColor(valor)
    c.setAlphaF(max(0.0, min(1.0, alfa)))
    return c


def misturar(a: str, b: str, p: float):
    """Cor entre `a` (p=0) e `b` (p=1)."""
    from PySide6.QtGui import QColor

    ca, cb = QColor(a), QColor(b)
    p = max(0.0, min(1.0, p))
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * p),
        round(ca.green() + (cb.green() - ca.green()) * p),
        round(ca.blue() + (cb.blue() - ca.blue()) * p),
    )


def fonte_texto(px: int = CORPO, negrito: bool = False):
    from PySide6.QtGui import QFont

    f = QFont([FONTE_TEXTO, FONTE_TEXTO_RESERVA])
    f.setPixelSize(px)
    if negrito:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def fonte_mono(px: int = ROTULO, negrito: bool = False, maiusculas: bool = False):
    from PySide6.QtGui import QFont

    f = QFont([FONTE_MONO, FONTE_MONO_RESERVA])
    f.setPixelSize(px)
    if negrito:
        f.setWeight(QFont.Weight.DemiBold)
    if maiusculas:  # só na tela: o texto do widget continua como foi escrito
        f.setCapitalization(QFont.Capitalization.AllUppercase)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
    return f


def luminancia(valor: str) -> float:
    h = valor.lstrip("#")
    rgb = [int(h[i : i + 2], 16) for i in (0, 2, 4)]

    def canal(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    return 0.2126 * canal(rgb[0]) + 0.7152 * canal(rgb[1]) + 0.0722 * canal(rgb[2])


def contraste(a: str, b: str) -> float:
    """Razão de contraste WCAG entre duas cores (1 a 21). AA pede 4,5 para texto e 3 para contornos."""
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def folha() -> str:
    """Folha de estilo (QSS) base, montada a partir dos tokens. Cada aba soma só o que for dela."""
    fam_texto = f'"{FONTE_TEXTO}", "{FONTE_TEXTO_RESERVA}"'
    fam_mono = f'"{FONTE_MONO}", "{FONTE_MONO_RESERVA}"'
    return f"""
QWidget {{ color: {TEXTO}; font-family: {fam_texto}; font-size: {CORPO}px;
    selection-background-color: {VERDE}; selection-color: {TEXTO}; }}
QToolTip {{ background: {ELEVADO}; color: {TEXTO}; border: 1px solid {BORDA_FORTE}; padding: 4px 8px; }}

QLabel#titulo {{ font-family: {fam_mono}; font-size: {TITULO}px; font-weight: 600; }}
QLabel#subtitulo {{ font-size: {SUBTITULO}px; font-weight: 600; }}
QLabel#discreto {{ color: {TEXTO_2}; }}
QLabel#rotulo {{ color: {TEXTO_2}; font-family: {fam_mono}; font-size: {ROTULO}px; }}
QLabel#contador {{ color: {VERDE_LUZ}; font-family: {fam_mono}; font-size: {ROTULO}px; }}

QPushButton {{ background: {SUPERFICIE}; color: {TEXTO}; border: 1px solid {BORDA_FORTE};
    border-radius: {RAIO_HUD}px; padding: 6px 12px; }}
QPushButton:hover {{ background: {ELEVADO}; border-color: {VERDE_LUZ}; }}
QPushButton:focus {{ border: 1px solid {VERDE_LUZ}; }}
QPushButton:pressed {{ background: {FUNDO}; }}
QPushButton:disabled {{ color: {TEXTO_APAGADO}; border-color: {BORDA}; background: {SUPERFICIE}; }}
QPushButton#principal {{ background: {VERDE}; border-color: {VERDE_LUZ}; font-weight: 600; }}
QPushButton#principal:hover {{ background: #248C58; }}
QPushButton#perigo:hover, QPushButton#mini[perigo="true"]:hover {{ color: {ALERTA}; border-color: {ALERTA}; }}
QPushButton#mini {{ background: transparent; color: {TEXTO_2}; border: 1px solid transparent;
    font-family: {fam_mono}; font-size: {ROTULO}px; padding: 3px 8px; }}
QPushButton#mini:hover {{ color: {TEXTO}; border-color: {BORDA_FORTE}; background: {ELEVADO}; }}
QPushButton#mini:focus {{ color: {TEXTO}; border-color: {VERDE_LUZ}; }}

QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QListWidget {{
    background: {FUNDO}; color: {TEXTO}; border: 1px solid {BORDA_FORTE}; border-radius: {RAIO_HUD}px; padding: 5px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus {{
    border: 1px solid {VERDE_LUZ}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QTimeEdit:disabled,
QSpinBox:disabled {{ color: {TEXTO_APAGADO}; border-color: {BORDA}; }}
QComboBox QAbstractItemView {{ background: {ELEVADO}; border: 1px solid {BORDA_FORTE}; selection-background-color: {VERDE}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDA_FORTE}; border-radius: {RAIO_HUD}px; background: {FUNDO}; }}
QCheckBox::indicator:hover, QCheckBox::indicator:focus {{ border-color: {VERDE_LUZ}; }}
QCheckBox::indicator:checked {{ background: {VERDE_LUZ}; border-color: {VERDE_LUZ}; }}

QProgressBar {{ background: {FUNDO}; border: 1px solid {BORDA}; border-radius: 3px; }}
QProgressBar::chunk {{ border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {VERDE}, stop:1 {VERDE_LUZ}); }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDA}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDA_FORTE}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDA}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {BORDA_FORTE}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
QDialog, QMessageBox, QInputDialog {{ background: {ELEVADO}; }}
QMenu {{ background: {ELEVADO}; color: {TEXTO}; border: 1px solid {BORDA_FORTE}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: {RAIO_HUD}px; }}
QMenu::item:selected {{ background: {VERDE}; }}
QMenu::item:disabled {{ color: {TEXTO_APAGADO}; }}
QMenu::separator {{ height: 1px; background: {BORDA}; margin: 4px 6px; }}
QListWidget {{ outline: 0; }}
QListWidget::item {{ padding: 6px 8px; border-radius: {RAIO_HUD}px; }}
QListWidget::item:hover {{ background: {SUPERFICIE}; }}
QListWidget::item:selected {{ background: {ELEVADO}; color: {TEXTO}; border-left: 2px solid {VERDE_LUZ}; }}
QTextBrowser {{ padding: 10px; }}
"""
