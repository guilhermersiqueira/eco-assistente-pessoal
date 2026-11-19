# DESIGN.md — Sistema de design da Eco

Referência do visual da Eco para quem for mexer na interface (o dono ou o Claude). Resume o documento "Eco — Design" (10/11/2026) e as decisões tomadas depois.

**A fonte da verdade é o código:** os valores ficam em `src/eco/visual/tema.py` e os componentes em `src/eco/visual/componentes.py`. Se este arquivo e o código discordarem, vale o código, e este arquivo deve ser corrigido.

## Regra de ouro

Nenhuma aba escreve cor, fonte, medida ou duração solta. Tudo sai de `tema.py` (tokens e `tema.folha()`). Para mudar o visual da Eco inteira, mude ali.

## Identidade

- **Tema escuro**, quase preto com um fundo esverdeado. Verde floresta e ciano são as cores da Eco.
- **Vidro leve, sem desfoque:** cartões 88% opacos (`OPACIDADE_CARTAO`) com um brilho que desce do topo e uma aresta de luz de 1 px (`VIDRO`, `VIDRO_BRILHO`, `VIDRO_FIO`). Desfoque de verdade fica de fora: no Qt ele custa caro e trava.
- **Leveza antes de efeito:** a janela principal não tem partículas de fundo (`ECO_FUNDO=desligado` por padrão desde 18/11/2026); no lugar, um degradê fixo com luz verde suave no alto (`BRILHO_FUNDO`), pintado uma vez. O grafo mantém o visual dele.
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
| `VIDRO` | `#FFFFFF` | **só reflexo** do vidro, sempre com alfa baixo (`VIDRO_BRILHO` 5%, `VIDRO_FIO` 8%); nunca texto ou contorno |

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
| `MICRO_MS` | 120 ms | hover de botões e abas |
| `HOVER_MS` | 150 ms | cartão ao ganhar foco |
| `TROCA_ABA_MS` | 150 ms | crossfade ao trocar de aba |
| `ABA_MS` | 200 ms | (reservado) |
| `ABRIR_MS` | 240 ms | abrir painel/diálogo |
| `NO_NOVO_MS` | 400 ms | ideia nova no grafo |
| `REORGANIZAR_MS` | 600 ms | grafo se reorganizando |
| `CURSOR_MS` | 800 ms | piscar do cursor |
| `FLUXO_VOLTA_S` | 6 s | uma volta do fluxo de luz, a 30 quadros/s |

- **Abrir a interface:** o círculo da orbe cresce até virar o painel (280 ms, OutQuart), com o conteúdo chegando de 96% a 100%; a orbe se apaga nos primeiros 40%. Fechar faz o inverso em 180 ms. A janela já nasce no tamanho final e a animação pinta uma **captura** do conteúdo pronto; no fim, a captura dá lugar aos widgets de verdade. A janela é montada e aquecida escondida 3 s depois de a Eco abrir.
- **Movimento reduzido:** com a animação do Windows desligada ou `ECO_MOVIMENTO_REDUZIDO=sim`, abrir é só fade (160 ms), fechar é imediato, o hover salta, a troca de aba não faz crossfade e não há fluxo de luz.
- **Desempenho manda:**
  - O fluxo de luz anda em **um cartão por vez**, só na moldura, e para sem foco ou com a janela escondida.
  - A orbe desenha o brilho em meia resolução e as faíscas nítidas por cima (~3 ms por quadro no tamanho médio). Ela roda a 60 quadros/s ouvindo e falando e a 30 no resto, e pausa enquanto a interface abre.
  - Animação nova deve seguir o mesmo princípio: pintar uma captura em vez de redimensionar ou repintar widgets a cada quadro.

## Estados de interação

| Estado | Visual |
|---|---|
| Hover em botão | fundo `ELEVADO`, borda `VERDE_LUZ` (a cor anda em `MICRO_MS`) |
| Foco por teclado | borda `VERDE_LUZ` |
| Seleção | fundo `VERDE` (menus, texto); em lista, fundo `ELEVADO` + barra esquerda `VERDE_LUZ` |
| Desabilitado | texto `TEXTO_APAGADO`, borda `BORDA` |
| Perigo (hover) | texto e borda `ALERTA` |
| Marcado | caixa preenchida em `VERDE_LUZ` |

## Componentes (`visual/componentes.py`)

- **`Cartao`** — painel de vidro leve com título HUD; contorno grafite. Em foco sobe para `ELEVADO`, o brilho do vidro aumenta e ganha o fluxo de luz verde na borda.
- **`Caixa`** — caixa de marcar de uma linha: título em destaque + detalhes separados por `SEPARADOR` (" · ").
- **`Linha`** — linha de lista (evento, e-mail) com partes de estilos diferentes: `hora`, `texto`, `discreto`, `direita`.
- **`Botao`** — canto inferior direito cortado. Tipos: `normal`, `principal` (a ação da tela, fundo `VERDE`), `mini` (ação de linha, transparente), além dos estados de perigo e "parar". O hover interpola as cores (`_Hover`).
- **`Aba`** — rótulo mono; a ativa tem canto cortado, marcação técnica e fluxo de luz. O texto clareia no hover (`_Hover`).
- **`rotulo()`** — rótulo pequeno em mono.
- **Diálogos** (`visual/dialogos.py`): `perguntar`, `avisar`, `texto`, `texto_longo`, `escolher`, todos HUD. Ação que apaga, troca ou junta sempre pede confirmação.

Nomes de objeto da folha de estilo: `titulo`, `subtitulo`, `discreto`, `rotulo`, `contador`, `principal`, `perigo`, `mini`.

## A orbe

Partículas em anéis que giram em planos diferentes, núcleo brilhante e linhas finas irradiando, em verde floresta e ciano (tom padrão 148, `ECO_ORBE_TOM`). Motor em `visual/particulas.py` (números em `ESTADOS` e `GRUPOS`), referência em `design/orbe-prototipo.html`.

| Estado | Comportamento |
|---|---|
| `idle` | pequena, lenta, fraca |
| `listen` | cresce e acende; reage ao volume do microfone |
| `think` | gira rápido, anel tracejado; o tom desliza para o ciano, o brilho respira (0,8 Hz) e uma onda de cor passa pelos anéis |
| `speak` | maior; reage ao envelope do áudio da fala |
| `confirm` | arco girando, no ciano do token `CIANO` (~184°); esperando o "sim" |
| `error` | coral, com tremor |
| `mute` | cinza, sempre visível |

Mais o brilho de memória guardada (ouro) quando ela lembra, anota, define meta ou cria tarefa, e um brilho 8% maior com o mouse em cima. Ela fica invisível dormindo, surge com fade quando chamada e some depois da legenda.

**Posição:** centro horizontal, embaixo, logo acima da barra de tarefas (`MARGEM_BAIXO` 24 px). Clicar e arrastar move (a partir de 6 px; um clique rápido parado abre a interface, segurar parado puxa as faíscas; Ctrl ou Shift move de imediato), e a posição fica no `~/.eco/orbe.json` (`versao_posicao` 2: posições salvas antes do centro foram ignoradas uma vez). O menu tem "Voltar ao centro". Trocar o tamanho no centro mantém a base fixa.

## Telas

**Janela principal:** sem a barra de título do Windows, mas com o comportamento de uma janela comum. Redimensiona por uma faixa de 6 px nas bordas e cantos (`MARGEM_BORDA`, `startSystemResize`) e tem tamanho mínimo de 900×580, que cede em telas menores. O cabeçalho move a janela; duplo clique nele maximiza. No canto ficam os botões minimizar, maximizar/restaurar e fechar (46 px cada; hover em `ELEVADO`, o de fechar em `ALERTA`). Maximizada, ela ocupa a área útil da tela e perde os cantos arredondados; arrastá-la pelo cabeçalho a restaura sob o mouse. Tamanho, lugar e se estava maximizada ficam em `~/.eco/janela.json` e valem na próxima abertura.

Janela principal com abas: **Painel** (duas colunas de cartões: Agenda, Tarefas, E-mails pendentes, Metas), **Conversas** (3 cartões), **Bloco** (2 cartões), **Grafo** (orbe 3D, com "Ver em 2D" e o painel Gerenciar). Respostas longas e código abrem na janela flutuante. Imagens de conferência em `design/`.

## Ao mexer no visual

1. Precisa de cor ou medida nova? Acrescente o token em `tema.py`, não no widget.
2. Texto novo sobre fundo escuro: confira o contraste (os testes de tema devem continuar passando).
3. Respeite os significados fixos (ouro, ciano, vermelho).
4. Ofereça versão sem animação para movimento reduzido.
5. Atualize este arquivo e, se for função visível ao dono, a seção dela em `src/eco/ajuda.md`.

## Decisões a validar

- Intensidade do vidro (`VIDRO_BRILHO` 5%, `VIDRO_FIO` 8%) e do degradê do fundo (`BRILHO_FUNDO` 7%) na tela real.
- Ritmo da respiração e da onda de cor da orbe pensando (`RESPIRA_HZ`, `ONDA_VEL` em `visual/particulas.py`).

Decididas em 18/11/2026: vidro sem desfoque, só nos cartões; hover animado em 120 ms; sem partículas de fundo na janela principal (o grafo mantém o visual); orbe no centro embaixo.
