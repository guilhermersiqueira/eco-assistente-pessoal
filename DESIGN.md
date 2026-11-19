# DESIGN.md — Sistema de design da Eco

Referência do visual da Eco para quem for mexer na interface (o dono ou o Claude). Resume o documento "Eco — Design" (10/11/2026) e as decisões tomadas depois.

**A fonte da verdade é o código:** os valores ficam em `src/eco/visual/tema.py` e os componentes em `src/eco/visual/componentes.py`. Se este arquivo e o código discordarem, vale o código, e este arquivo deve ser corrigido.

## Regra de ouro

Nenhuma aba escreve cor, fonte, medida ou duração solta. Tudo sai de `tema.py` (tokens e `tema.folha()`). Para mudar o visual da Eco inteira, mude ali.

## Identidade

- **Tema escuro**, quase preto com um fundo esverdeado. Verde floresta e ciano são as cores da Eco.
- **"Vidro escuro":** cartões 84% opacos (`OPACIDADE_CARTAO`), sem desfoque, deixando as partículas do fundo (`visual/fundo.py`) aparecerem de leve.
- **Estilo HUD:** rótulos em fonte mono, canto cortado em abas e botões, fluxo de luz verde na borda do cartão em foco.
- A Eco é **ela** (feminino) em todo texto da interface.

## Cores

| Token | Valor | Uso |
|---|---|---|
| `FUNDO` | `#050806` | base da janela; fundo de campos |
| `SUPERFICIE` | `#0B1210` | cartões e painéis |
| `ELEVADO` | `#111C18` | cartão em foco, menus, diálogos, dicas |
| `BORDA` | `#2A3A34` | contorno **só decorativo** (cartões, separadores) |
| `BORDA_FORTE` | `#557267` | contorno de campo, caixa de marcar e botão (≥ 3:1) |
| `TEXTO` | `#E6F2EE` | texto principal |
| `TEXTO_2` | `#8FA39C` | texto secundário, rótulos, área "Geral" |
| `TEXTO_APAGADO` | `#6B837B` | concluído ou desabilitado |
| `VERDE` | `#1F7A4D` | acento, **só preenchimento** (botão principal, seleção, barra) |
| `VERDE_LUZ` | `#2FBF71` | brilho, estado ativo, foco por teclado, texto verde |
| `CIANO` | `#3DD6E0` | destaque: confirmação, tarefa importante |
| `OURO` | `#E8C15A` | **só memória** (fatos, lembranças) |
| `ALERTA` | `#E0605A` | erro, atraso, ação destrutiva |
| `ALERTA_FUNDO` | `#2A1412` | fundo da mensagem de erro na conversa |

**Significados fixos**

- Ouro = memória. Nada mais usa ouro.
- Ciano = importante ou "precisa do seu sim". Tarefa importante é ciano (não ouro).
- Vermelho (`ALERTA`) = erro, atraso, apagar. Nunca decorativo.
- Verde = a Eco ativa: foco, seleção, progresso.

**Espaços** (`CORES_ESPACOS`): seis cores com a mesma saturação e luz (HSL 55%, 68%), longe do vermelho e do dourado para nenhum espaço parecer erro ou memória: `#7ED9AB`, `#7ED3D9`, `#7EADD9`, `#8A7ED9`, `#BF7ED9`, `#D97EB4`. Espaços antigos mantêm a cor gravada no banco.

**Grafo** (`COR_ORIGEM`): memória e fatos em ouro, notas `#7EADD9`, metas `VERDE_LUZ`, manual `TEXTO_2`. O que não é vizinho da ideia em foco escurece para ~30%.

### Acessibilidade (cobrado em `tests/test_tema.py`)

- Todo token de texto (`TEXTO`, `TEXTO_2`, `VERDE_LUZ`, `CIANO`, `OURO`, `ALERTA`) passa em **WCAG AA (4,5:1)** sobre os três fundos.
- `VERDE` **não** passa como texto: texto verde é sempre `VERDE_LUZ`.
- Campo, caixa e botão usam `BORDA_FORTE` (3:1). `BORDA` não serve para controles.
- `tema.contraste(a, b)` calcula a razão; use-o ao propor cor nova.

## Tipografia

| Papel | Fonte | Reserva |
|---|---|---|
| Texto | Segoe UI Variable Text | Segoe UI |
| Mono (rótulos, títulos HUD, números, código) | Cascadia Mono | Consolas |

Tamanhos em pixels: `ROTULO` 11 · `CORPO` 13 · `SUBTITULO` 15 · `TITULO` 20. Entrelinha 1,6; largura de leitura até 680 px.

- Títulos de tela em mono, seminegrito.
- Rótulos de grupo em mono; quando em maiúsculas, a caixa alta é só visual (`fonte_mono(maiusculas=True)`, espaçamento 1,2), o texto do widget fica como foi escrito.
- Use `tema.fonte_texto()` e `tema.fonte_mono()`, nunca `QFont` direto.

## Medidas

- Grade de **4 px**: margens e espaços em múltiplos de 4.
- Raio do cartão: **12 px**. Raio de campo, caixa, menu e item de lista: **4 px** (`RAIO_HUD`).
- **Canto cortado só em abas e botões.** Cartões são arredondados.

## Movimento

| Token | Valor | Onde |
|---|---|---|
| `HOVER_MS` | 150 ms | cartão ao ganhar foco (o hover dos botões é instantâneo) |
| `ABA_MS` | 200 ms | troca de aba |
| `ABRIR_MS` | 240 ms | abrir painel/diálogo |
| `NO_NOVO_MS` | 400 ms | ideia nova no grafo |
| `REORGANIZAR_MS` | 600 ms | grafo se reorganizando |
| `CURSOR_MS` | 800 ms | piscar do cursor |
| `FLUXO_VOLTA_S` | 6 s | uma volta do fluxo de luz, a 30 quadros/s |

- Janela principal abre crescendo a partir da orbe (380 ms).
- **Movimento reduzido:** com a animação do Windows desligada ou `ECO_MOVIMENTO_REDUZIDO=sim`, só fade (160 ms) e sem fluxo de luz.
- Desempenho manda: o fluxo de luz anda em **um cartão por vez**, só na moldura, e para sem foco ou com a janela escondida.

## Estados de interação

| Estado | Visual |
|---|---|
| Hover em botão | fundo `ELEVADO`, borda `VERDE_LUZ` |
| Foco por teclado | borda `VERDE_LUZ` |
| Seleção | fundo `VERDE` (menus, texto); em lista, fundo `ELEVADO` + barra esquerda `VERDE_LUZ` |
| Desabilitado | texto `TEXTO_APAGADO`, borda `BORDA` |
| Perigo (hover) | texto e borda `ALERTA` |
| Marcado | caixa preenchida em `VERDE_LUZ` |

## Componentes (`visual/componentes.py`)

- **`Cartao`** — painel com título HUD; contorno grafite. Em foco sobe para `ELEVADO` e ganha o fluxo de luz verde na borda.
- **`Caixa`** — caixa de marcar de uma linha: título em destaque + detalhes separados por `SEPARADOR` (" · ").
- **`Linha`** — linha de lista (evento, e-mail) com partes de estilos diferentes: `hora`, `texto`, `discreto`, `direita`.
- **`Botao`** — canto inferior direito cortado. Tipos: `normal`, `principal` (a ação da tela, fundo `VERDE`), `mini` (ação de linha, transparente), além dos estados de perigo e "parar".
- **`Aba`** — rótulo mono; a ativa tem canto cortado, marcação técnica e fluxo de luz.
- **`rotulo()`** — rótulo pequeno em mono.
- **Diálogos** (`visual/dialogos.py`): `perguntar`, `avisar`, `texto`, `texto_longo`, `escolher`, todos HUD. Ação que apaga, troca ou junta sempre pede confirmação.

Nomes de objeto da folha de estilo: `titulo`, `subtitulo`, `discreto`, `rotulo`, `contador`, `principal`, `perigo`, `mini`.

## A orbe

Partículas em anéis que giram em planos diferentes, núcleo brilhante e linhas finas irradiando, em verde floresta e ciano (tom padrão 148, `ECO_ORBE_TOM`). Motor em `visual/particulas.py` (números em `ESTADOS` e `GRUPOS`), referência em `design/orbe-prototipo.html`.

| Estado | Comportamento |
|---|---|
| `idle` | pequena, lenta, fraca |
| `listen` | cresce e acende; reage ao volume do microfone |
| `think` | gira rápido, anel tracejado |
| `speak` | maior; reage ao envelope do áudio da fala |
| `confirm` | arco girando; esperando o "sim" |
| `error` | coral, com tremor |
| `mute` | cinza, sempre visível |

Mais o brilho de memória guardada (ouro) quando ela lembra, anota, define meta ou cria tarefa. Ela fica invisível dormindo, surge com fade quando chamada e some depois da legenda.

## Telas

Janela principal com abas: **Painel** (duas colunas de cartões: Agenda, Tarefas, E-mails pendentes, Metas), **Conversas** (3 cartões), **Bloco** (2 cartões), **Grafo** (orbe 3D, com "Ver em 2D" e o painel Gerenciar). Respostas longas e código abrem na janela flutuante. Imagens de conferência em `design/`.

## Ao mexer no visual

1. Precisa de cor ou medida nova? Acrescente o token em `tema.py`, não no widget.
2. Texto novo sobre fundo escuro: confira o contraste (os testes de tema devem continuar passando).
3. Respeite os significados fixos (ouro, ciano, vermelho).
4. Ofereça versão sem animação para movimento reduzido.
5. Atualize este arquivo e, se for função visível ao dono, a seção dela em `src/eco/ajuda.md`.

## Decisões a validar

- "Vidro escuro" sem desfoque (decisão técnica, por desempenho).
- Hover dos botões instantâneo; só o cartão anima.
- As partículas de fundo aparecem pouco porque os cartões cobrem quase tudo: decidir se aumenta.
