# Church CRM

Sistema de gestão para igrejas (membros, visitantes, eventos, link-na-bio).
Django + SQLite (dev) + Tailwind (CDN) + HTMX. **Multi-tenente**: um único
deploy atende várias igrejas ao mesmo tempo, cada uma só enxergando os
próprios dados (ver seção "Multi-tenência" abaixo).

## Rodando localmente

```bash
cd church-crm
venv/Scripts/python.exe manage.py runserver 8100
```

Acesse `http://localhost:8100`. Login de teste: `pastor` / `teste12345`
(role Pastor da igreja seed, "Igreja Batista Exemplo" — pode ser
trocado/apagado no admin em `/admin/`). Criar uma igreja nova é feito
pelo dono da plataforma via Django admin/shell (ver seção "Multi-tenência")
— não existe ainda um formulário público de auto-cadastro de igreja.

Se o `venv` não existir (ex.: clonado de novo), recrie com:

```bash
python -m venv venv
venv/Scripts/pip.exe install -r requirements.txt
cp .env.example .env
venv/Scripts/python.exe manage.py migrate
venv/Scripts/python.exe manage.py createsuperuser
```

## Arquitetura

Um projeto Django (`church_crm/`) com 8 apps, cada uma responsável por um
módulo do sistema:

| App | Responsabilidade |
|---|---|
| `accounts` | Usuário customizado (`AUTH_USER_MODEL`) com campo `role` (Pastor/Admin/Líder/Membro) — é o RBAC do sistema. Login/logout, `TOTPDevice` pro 2FA opcional. |
| `people` | `Person` (membros e visitantes), `Department`, `Family`, `Tag`. CRUD completo + cadastro público + quadro kanban de acompanhamento de visitante. |
| `events` | `Event` e `Registration` — CRUD de eventos (com imagem de capa e cor/informações próprias), inscrição pública, lista de espera, check-in por QR code, pagamento via PIX/Mercado Pago. |
| `linkbio` | `BioPage` e `Link` — painel de administração + página pública estilo Linktree. |
| `finance` | `Transaction`, `Budget`, `RecurringPledge`, `Donation` — entradas (dízimo/oferta/doação) e saídas, orçamento, contribuição recorrente e doação avulsa pelo Portal. |
| `cells` | `Cell` e `CellMeeting` — pequenos grupos/células e presença semanal. |
| `notifications` | `WhatsAppMessage` (fila com intervalo/retry/confirmação de entrega), `MessageTemplate`, `PushSubscription` — tela de Conectar/Desconectar (QR code) fica aqui, simplificada pra igreja. |
| `custom_forms` | `CustomForm`/`FormField`/`FormResponse`/`FormAnswer` — formulários montados pela própria igreja, com disparo de WhatsApp opcional pra quem responde. |
| `core` | `Church` (o tenant — nome, marca, PIX, Mercado Pago, config do WhatsApp por igreja), `core.tenancy`/`core.tenant_context`/`TenantMiddleware` (isolamento multi-tenente), dashboard, auditoria e a tela de Configurações. |

## Multi-tenência

Um único deploy atende várias igrejas ao mesmo tempo — mesmo mecanismo já
validado no projeto irmão `crm-odonto`, adaptado ao estilo deste projeto:

- **`Church` é o tenant** (`core/models.py`) — nome, slug (usado nas URLs
  públicas), status (`trial`/`ativo`/`suspenso`, controlado manualmente
  pelo dono — sem cobrança automática ainda), marca, PIX, Mercado Pago e
  configuração da fila de WhatsApp. Cada igreja é uma linha, não mais um
  singleton (era `ChurchConfig`, `pk=1` fixo).
- **Isolamento automático, não por convenção**: toda tabela de dado
  (`Person`, `Event`, `Transaction`, `CustomForm` etc.) herda
  `core.tenancy.TenantModel`, que adiciona uma FK `church` e troca o
  manager padrão por `TenantManager` — `Model.objects.all()` já vem
  filtrado pela igreja da requisição atual sozinho, sem precisar lembrar
  de `.filter(church=...)` em toda view. Quem preenche "a igreja atual"
  é `core.middleware.TenantMiddleware`, a partir de `request.user.church`
  (um usuário sem igreja — `church=None` — é o dono da plataforma, vê
  tudo sem filtro). Pra consultas explicitamente sem filtro (páginas
  públicas, comandos de cron), cada model expõe `Model.todas_as_igrejas`.
- **Páginas públicas têm o slug da igreja na URL**:
  `/<slug-da-igreja>/eventos/<slug>/`, `/<slug-da-igreja>/formularios/<slug>/`,
  `/<slug-da-igreja>/links/<slug>/`, `/<slug-da-igreja>/pessoas/cadastro/`.
  `core.tenancy.PublicChurchMixin` resolve a igreja pelo slug (não tem
  usuário logado pra ler `request.user.church`) e popula tanto
  `request.church` quanto o thread-local, pro resto do código (inclusive
  `TenantFormMixin`, que seta `church` num `CreateView` antes de salvar)
  funcionar igual, público ou logado.
- **Cada app com tela pública tem dois arquivos de rotas**: `urls.py`
  (gestão, sem prefixo, ex.: `/eventos/`) e `public_urls.py` (público,
  montado com `<slug:church_slug>/` na frente em `church_crm/urls.py`).
  **Atenção**: os dois usam `app_name` DIFERENTES (`events` ×
  `events_public`, etc.) — dois `include()` com o MESMO `app_name` fazem
  o Django reverter (`reverse()`/`{% url %}`) só as rotas do primeiro
  registrado, quebrando silenciosamente as do segundo (bug real,
  encontrado e corrigido nesta rodada, não documentado assim no Django).
- **WhatsApp é um servidor Evolution API único, da plataforma**
  (`settings.EVOLUTION_API_URL`/`EVOLUTION_API_KEY`, não mais campo por
  igreja), com uma instância isolada por igreja (`Church.whatsapp_instance`,
  gerado do slug). A igreja continua só vendo Conectar/Desconectar com QR
  code, sem nunca ver URL/chave — isso não mudou.
- **Webhooks (Mercado Pago) resolvem a igreja por um `?church_id=`**
  embutido por NÓS na `notification_url` ao criar a preferência de
  checkout — o Mercado Pago chama de volta sem usuário logado e sem slug
  na URL, então a igreja precisa vir de algo que a própria plataforma
  controla, não do que o gateway decide. O webhook da Evolution API
  (WhatsApp) resolve pelo `X-Webhook-Secret` de cada igreja em vez disso
  (já existia um segredo por conexão).
- **Criar uma igreja é self-service** — `/cadastro-igreja/` (Fase 2, ver
  seção própria abaixo). Continua dando pra criar manualmente pelo
  Django admin/shell também, se preferir.

## Cadastro público de igreja (Fase 2)

`/cadastro-igreja/` deixa qualquer um criar uma igreja nova sozinho —
nome da igreja, nome do pastor, usuário/e-mail/senha do primeiro acesso
(que já nasce role Pastor). A igreja é criada em **trial de 30 dias**
com acesso completo (sem limite de pessoas, WhatsApp liberado — os
limites de plano só valem depois que vira `ativo`, ver seção
"Assinatura e planos" abaixo) e a pessoa já é autenticada na hora,
direto pro dashboard — sem esperar aprovação nem confirmar e-mail antes
de começar a usar.

Um e-mail de confirmação é enviado em paralelo (token hand-rolled com
`django.core.signing.TimestampSigner`, válido 3 dias — mesmo espírito
"sem dependência nova" do TOTP/PIX já implementados no projeto). Ele
**não bloqueia o uso do sistema** — só bloqueia o envio de WhatsApp
dessa igreja (`Church.email_confirmed`, checado em
`notifications.WhatsAppConnectionView` e pulado silenciosamente pelo
comando `processar_fila_whatsapp`) até confirmar. Reenviar o e-mail de
confirmação é um botão na própria tela de Conectar WhatsApp.

`manage.py expirar_trials` (cron diário, ver `DEPLOY.md`) suspende
sozinho quem passou de `trial_expira_em` sem virar `ativo`. Uma igreja
`suspensa` (trial vencido ou assinatura cancelada/pagamento falhou) é
bloqueada por completo — `core.middleware.TenantMiddleware` redireciona
qualquer requisição pra `/conta-suspensa/`, exceto uma pequena allowlist
(login/logout, a própria tela de assinatura, admin, estáticos) — sem
isso ninguém suspenso conseguiria nem ver por que foi bloqueado nem
assinar um plano pra se desbloquear. O dono da plataforma (`church=None`)
nunca é afetado por essa trava.

## LGPD (Fase 3)

Três coisas: consentimento na coleta pública, política de privacidade, e
autoatendimento pra quem já tem dado no sistema.

- **Consentimento**: os 3 pontos de coleta pública (cadastro de
  visitante, inscrição em evento, resposta de formulário customizado)
  ganharam uma checkbox obrigatória "Li e concordo com a Política de
  Privacidade" (`core.lgpd.privacy_consent_label`, um link real pra
  `/privacidade/`) — sem marcar, a submissão é rejeitada com erro, igual
  qualquer outro campo obrigatório. Quando marcada, grava um timestamp
  (`privacy_consent_at`) em `Person`/`Registration`/`FormResponse` — é
  a prova de quando/que a pessoa consentiu, não só um booleano solto.
- **Política de privacidade**: `/privacidade/`, texto padrão genérico em
  português (dados coletados, finalidade, base legal, direitos, contato)
  — deliberadamente marcado no próprio template como ponto de partida,
  não aconselhamento jurídico; cada igreja deve adaptar e validar com um
  advogado antes de tratar como aviso legal definitivo.
- **Autoatendimento no Portal** (`/meus-dados/`, exige login e conta
  vinculada a uma `Person`): **baixar os próprios dados** (JSON —
  portabilidade é um formato machine-readable, não um PDF pra ler;
  inclui os campos da pessoa mais as próprias inscrições em evento e
  doações) e **solicitar exclusão**. A exclusão NUNCA é automática:
  `core.DataDeletionRequest` só cria um pedido `PENDING`; a secretaria
  confirma numa tela própria (`/privacidade/solicitacoes/`, linkada em
  Configurações) antes da `Person` ser apagada de verdade — uma ação
  destrutiva demais pra acontecer sem revisão humana. O pedido guarda um
  **snapshot do nome** (`person_name`) porque a FK pra `Person` é
  `SET_NULL`, não `CASCADE` — apagar a pessoa não pode apagar junto a
  prova de que o pedido foi processado.

## Assinatura e planos (Fase 4)

Dois planos fixos (`core/billing.py`, dict no código — ajustar preço/
limite é redeploy, não uma tela de admin): **Básico** (R$ 49/mês, até
100 pessoas cadastradas, sem WhatsApp) e **Pro** (R$ 99/mês, pessoas
ilimitadas, WhatsApp liberado). Durante o **trial** (Fase 2) o acesso é
sempre completo, independente de plano — os limites acima só valem
depois que `Church.status` vira `ativo` com um `plano` reconhecido.
`suspenso` já bloqueia o sistema inteiro (ver seção anterior), então nem
chega a consultar plano.

`/assinatura/` mostra o status atual + botões pra assinar cada plano —
cria uma **assinatura recorrente** via API de Preapproval do Mercado
Pago (`core/mercadopago_billing.py`, chamadas cruas via `requests`,
mesmo estilo sem SDK de `events/mercadopago.py`/`finance/mercadopago.py`)
e redireciona pro checkout. A conta usada é a da **PLATAFORMA**
(`settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN`), diferente da conta de
cada igreja (`Church.mercadopago_access_token`, usada só pra receber
pagamento de evento/doação daquela igreja) — são dois Mercado Pago
completamente separados, um por igreja pra receber dela, um da
plataforma pra cobrar dela. O webhook (`/assinatura/webhook/mercadopago/`)
identifica a igreja/plano pelo `external_reference`
(`CHURCH-<pk>-<plano>`) que a própria plataforma embute ao criar a
assinatura — sempre reconsulta a API antes de mudar `Church.status`
(nunca confia no corpo do POST, mesma disciplina de todo webhook já
implementado neste projeto). **Controle manual pelo dono continua
funcionando em paralelo** — a Fase 4 automatiza, não substitui; nada
impede o dono de mudar `status`/`plano` direto no Django admin a
qualquer momento.

Decisões de design que valem explicar:

- **RBAC simples, sem `django.contrib.auth` groups/permissions.** O campo
  `role` em `accounts.User` (`PASTOR`, `ADMIN`, `LEADER`, `MEMBER`) mais
  properties (`is_pastor`, `is_church_admin`, `can_manage_people`) resolvem o
  controle de acesso do Módulo 1 sem a complexidade de grupos/permissões
  granulares — não há necessidade disso com só 4 papéis fixos. Aplicado via
  `accounts.mixins.CanManagePeopleMixin` em toda view de gestão (people,
  events, linkbio).
- **`Person` não depende de login.** Um visitante cadastrado pelo formulário
  público (`/pessoas/cadastro/`) não tem usuário — só ganha um
  `accounts.User` (via `Person.user_account`) se/quando alguém decide dar
  acesso ao sistema a ele. Isso separa "está na igreja" de "tem login".
- **`Registration` guarda um snapshot de nome/telefone/e-mail**, mesmo
  quando já existe `person` vinculado. Assim a inscrição pública em eventos
  não exige que o visitante esteja cadastrado antes — o registro fica
  completo por si só.
- **PIX gerado localmente, sem gateway de pagamento.** `events/pix.py` monta
  o payload "Copia e Cola" (BR Code, padrão EMV do Banco Central) e o QR
  Code a partir da chave PIX cadastrada em `Church` — nenhuma conta de
  gateway (Stripe/MercadoPago) é necessária para receber via PIX. A
  contrapartida: **a confirmação do pagamento é manual** (a secretaria
  confere o recebimento e clica "Confirmar pagamento" em
  `/eventos/<slug>/inscritos/`), já que sem um gateway real não existe
  webhook para confirmar automaticamente. `Church.pix_configured`
  controla se o QR aparece ou se a página de pagamento pede para a pessoa
  contatar a secretaria.
- **`Church` guarda nome da igreja, cor de marca, logo, templates de
  mensagem (ausência/aniversário), chave PIX, token do Mercado Pago e a
  instância/token do WhatsApp DAQUELA igreja** — ver seção
  "Multi-tenência" acima pro que mudou com a virada de singleton pra uma
  linha por igreja. Disponível em todo template via
  `core.context_processors.church_config` (lê `request.church`, resolvido
  por `TenantMiddleware`/`PublicChurchMixin`). Editável pelo Django admin
  com fieldsets organizados por assunto (`/admin/core/church/<id>/change/`)
  e, pra própria igreja, pela tela de Configurações in-app
  (`/configuracoes/`, só os campos não-técnicos).
- **Cor de marca → paleta completa via `colorsys`** (`core/colors.py`,
  técnica idêntica à do crm-odonto): a igreja escolhe UMA cor em
  `Church.brand_color`, o sistema gera as 11 tonalidades Tailwind
  (50-950) mantendo hue/saturação e variando luminosidade. Injetado no
  `tailwind.config` do `base.html` e usado via `style="color:{{ paleta_marca.700 }}"`
  nas páginas públicas (que não carregam a config dinâmica do Tailwind).
- **Portal do Membro é a MESMA URL do dashboard (`/`)**, não uma rota
  separada — `core.DashboardView.get_template_names()` decide entre
  `core/dashboard.html` (quem pode gerenciar pessoas) e
  `core/member_portal.html` (quem não pode), fechando o requisito do
  Módulo 1 de acesso restrito para Membro. Um Membro só vê seus próprios
  dados/eventos/célula quando `accounts.User.person` está vinculado — hoje
  esse vínculo só é feito pelo Django admin, não há autoatendimento.
- **Nenhum envio de WhatsApp é síncrono — tudo passa por uma fila
  (`notifications.WhatsAppMessage`).** Campanha em massa, lembrete
  automático e mensagem avulsa agendada todas só CRIAM uma
  `WhatsAppMessage` (status `PENDING`); quem manda de verdade é o
  `manage.py processar_fila_whatsapp`, um por um, esperando
  `Church.whatsapp_send_interval_seconds` (padrão 6s) entre cada
  envio, até um limite de `whatsapp_batch_size` por execução. Isso não é
  um detalhe de arquitetura por elegância — mandar uma campanha inteira de
  uma vez, sem pausa, numa request HTTP síncrona, é o jeito mais garantido
  de (a) a request estourar o timeout do servidor e (b) o número real ser
  marcado como spam/banido pelo WhatsApp. `enviar_lembretes` também roda
  via cron 1x/dia, mas hoje só enfileira — não envia nada sozinho.
  Sem `Church.whatsapp_api_*` configurado, cada mensagem processada
  só é impressa no terminal (`core/whatsapp.py`, mesmo fallback de sempre).
- **Retry automático + reenvio manual.** Uma mensagem que falha volta pro
  lote do próximo `processar_fila_whatsapp` até `Church.whatsapp_max_retries`
  (padrão 3) tentativas — depois disso, para de tentar sozinha, mas
  continua reenviável manualmente (botão "Reenviar" em `/mensagens/`, que
  zera `retry_count` e volta pra `PENDING`).
- **Confirmação de entrega via webhook.** `WhatsAppMessage.external_id`
  guarda o id que a Evolution API devolve no envio; `notifications.WhatsAppWebhookView`
  (`/mensagens/webhook/evolution/`) recebe o evento `messages.update` da
  Evolution e atualiza `delivery_status`/`delivered_at`/`read_at` casando
  pelo `external_id`. Sem assinatura própria da Evolution API pra
  webhooks, a autenticação é um segredo compartilhado
  (`Church.whatsapp_webhook_secret`, conferido no cabeçalho
  `X-Webhook-Secret`) — sem segredo configurado, rejeita tudo. Mapeamento
  de status (`DELIVERY_ACK`/`READ`/códigos numéricos do Baileys) é
  best-effort, nunca confirmado contra um payload real.
- **Duas telas fazem a MESMA coisa pra conectar o WhatsApp — a do
  `notifications.WhatsAppConnectionView` (estilizada, no fluxo normal do
  app, pensada pro pastor usar) e a do Django admin
  (`ChurchAdmin.get_urls()`, mais crua, útil como fallback).** As
  duas chamam exatamente as mesmas funções em `core/whatsapp.py`
  (criar instância / obter QR code / checar status), então um bug
  corrigido ali corrige nos dois lugares. Criar a instância usa a chave
  GLOBAL do servidor Evolution (`whatsapp_api_key`); enviar
  mensagem/checar status usa a chave da PRÓPRIA instância
  (`whatsapp_instance_token`, preenchida automaticamente ao criar — ou
  cai pra chave global se vazia). Formato exato da resposta da API é
  best-effort (nunca testado contra um servidor Evolution real neste
  ambiente) — só os caminhos de erro foram verificados ao vivo
  (URL/instância inexistente → falha de DNS capturada e mostrada como
  aviso, sem quebrar a página, nas duas telas).
- **Tela de Configurações in-app (`/configuracoes/`)**: um `ModelForm`
  comum sobre `Church.get_solo()` — antes só dava pra editar pelo
  Django admin. Existe pra quem administra o sistema no dia a dia sem
  precisar entrar no `/admin/`; o admin continua funcionando igual, os
  dois editam o mesmo registro.
- **Modelos de mensagem reutilizáveis** (`notifications.MessageTemplate`):
  puramente texto, sem lógica — um `<select>` com um `onchange` em JS
  puro copia o texto do modelo escolhido pra caixa de mensagem (campanha
  e mensagem avulsa), editável antes de confirmar. Nenhum campo novo no
  form em si, só açúcar de UI.
- **Fuso horário do agendamento não precisa de configuração extra.** Com
  `USE_TZ=True` e `TIME_ZONE="America/Sao_Paulo"` (já em `settings.py`),
  Django interpreta o horário digitado no campo `scheduled_for` como
  horário de Brasília automaticamente (`timezone.get_current_timezone()`
  cai pro `TIME_ZONE` do settings quando nada mais ativa uma timezone por
  request, que é o caso aqui — um fuso só pra TODAS as igrejas do mesmo
  servidor, `Church` não tem campo de timezone próprio). O rótulo
  do campo deixa isso explícito pra quem agenda não ficar em dúvida.
- **Mercado Pago real, não simulado** — `events/mercadopago.py` chama a
  API REST direto (sem SDK, mesmo estilo do crm-odonto), cria uma
  preferência de checkout e confirma pagamento via webhook
  (`MercadoPagoWebhookView`) que **sempre reconsulta a API** antes de
  marcar como pago (nunca confia no corpo do POST recebido). Só aparece
  como opção quando `Church.mercadopago_configured`; sem token, só o
  PIX local fica disponível. Nenhuma chamada real foi feita com
  credenciais verdadeiras nesta sessão — só o caminho de erro (token
  inválido) foi verificado ao vivo.
- **Reordenar links sem drag-and-drop/JS**: `Link.order` + botões ↑/↓
  (`linkbio.LinkMoveView`) trocam o `order` com o vizinho — resolve o
  requisito de reordenar do Módulo 5 sem precisar de uma lib de
  drag-and-drop.
- **Tailwind via CDN (`cdn.tailwindcss.com`), sem Node/build step** — mesmo
  padrão usado no [crm-odonto](../crm-odonto), mais rápido para iterar em
  dev.
- **Duas bases de template**: `base.html` (área logada, com nav) e
  `public_base.html` (páginas sem login: cadastro de visitante, inscrição
  em evento, pagamento PIX) — layouts bem diferentes o suficiente para não
  valer a pena forçar um só template condicional.
- **Financeiro é só um model (`Transaction`)**, sem contas a pagar/receber,
  conciliação bancária ou centro de custo — "Financeiro Simples" por
  design. `type` (entrada/saída) + `category` (dízimo, oferta, salário,
  manutenção etc.) cobrem os casos de uso pedidos; um `person` opcional
  liga o lançamento a quem contribuiu, para histórico individual futuro.
- **Presença de célula via M2M, não um contador solto**: `CellMeeting.attendees`
  é `ManyToManyField` para `Person` (então dá pra saber *quem* faltou, não
  só quantos vieram) + `visitors_count` (inteiro) para quem ainda não tem
  cadastro. `total_present` soma os dois.
- **Criar acesso é "meio self-service"**: a secretaria clica "Criar acesso"
  no cadastro de uma pessoa (`people.PersonCreateAccessView`), que cria o
  `User` mas quem escolhe a senha é a própria pessoa, via e-mail de
  redefinição (reaproveita o `PasswordResetForm` do Django). O `User` é
  criado com uma senha **aleatória**, não "unusable" — `PasswordResetForm.get_users()`
  ignora silenciosamente usuários com senha unusable, então usar
  `set_unusable_password()` aqui faria o e-mail nunca ser enviado (bug real
  encontrado e corrigido nesta rodada).
- **Cliques de link são rastreados via redirect**, não incrementados
  direto no template: a página pública aponta para
  `/links/click/<id>/` (`linkbio.link_click`), que soma 1 em
  `Link.click_count` via `F("click_count") + 1` (evita race condition) e
  só depois redireciona pro destino real — sem isso, `click_count`
  existia no model desde o Passo 1 mas nunca era incrementado por nada
  (campo morto).
- **Fallback de console do WhatsApp usa `flush=True` explícito.** Sob
  `runserver` de longa duração, stdout é um pipe (não um terminal) e o
  Python bufferiza em blocos — sem flush, uma campanha "enviada com
  sucesso" podia não aparecer no log nenhuma vez até o buffer encher.
  Bug real, encontrado ao vivo: uma campanha de teste simplesmente não
  apareceu no log até esse fix.
- **Auditoria via signal + thread-local**, não middleware por-view:
  `core.middleware.CurrentUserMiddleware` guarda `request.user` numa
  variável de thread-local; `core.signals.register_audit_log()` conecta
  `post_save`/`post_delete` de `Person`/`Event`/`Transaction`/`Cell` (
  chamado uma vez por model em `CoreConfig.ready()`) que lê esse
  thread-local pra saber quem fez a mudança — necessário porque signals
  de model não recebem a `request`.
- **PWA mínimo, sem cache offline de verdade**: `manifest.json` e `sw.js`
  são views Django (não arquivos estáticos) pra usar nome/cor reais da
  igreja; o service worker só existe pra tornar o site instalável
  ("Adicionar à tela inicial") — o listener de `fetch` é vazio de
  propósito, não finge ter uma estratégia de cache que não foi construída.
- **Financeiro Simples ganhou orçamento, mas continua simples**:
  `finance.Budget` (categoria + mês + valor previsto) é comparado contra a
  soma real de `Transaction` daquele mês/categoria — sem centro de custo,
  sem aprovação, só "previsto x realizado" por categoria.
- **Contribuição recorrente não guarda pagamento nenhum, só o compromisso.**
  `finance.RecurringPledge` (pessoa + valor mensal + dia de vencimento) só
  registra que alguém se comprometeu a contribuir todo mês; o pagamento em
  si continua sendo um `Transaction` (categoria Dízimo) lançado à parte.
  `/financeiro/recorrentes/` cruza os dois: "em dia" se já existe um
  `Transaction` daquela pessoa no mês corrente, "em atraso" se não.
- **Doação avulsa do Portal usa o mesmo PIX local de sempre, mais Mercado
  Pago quando configurado.** `finance.Donation` fica `PENDING` até
  confirmar — via Mercado Pago o webhook confirma sozinho e já cria o
  `Transaction`; via PIX manual, a secretaria confere o extrato e confirma
  em `/financeiro/doacoes/`. Duplica (não generaliza) as funções de
  Mercado Pago de `events/mercadopago.py` em `finance/mercadopago.py` de
  propósito — dois fluxos pequenos e independentes, sem acoplamento
  desnecessário entre os apps.
- **Lista de espera não bloqueia a inscrição, só marca `on_waitlist`.**
  Quando `Event.is_full`, `EventRegistrationView` cria a inscrição normal
  só que com `on_waitlist=True` em vez de rejeitar — `_confirmed_registrations()`
  exclui quem está na espera do cálculo de vagas ocupadas. A promoção é
  manual (`RegistrationPromoteView`, botão "Promover" em
  `/eventos/<slug>/inscritos/`) e enfileira um aviso pela fila de
  WhatsApp — nada de promoção automática por ordem de chegada, a
  secretaria decide.
- **Check-in por QR code sem nenhuma lib de leitura de câmera.** O QR
  gerado em `RegistrationDoneView` só encoda uma URL
  (`/eventos/checkin/<token>/`) — quem escaneia é sempre um membro da
  equipe já logado no navegador do próprio celular, então abrir a URL (com
  qualquer app de câmera comum) já é o check-in. `Registration.checkin_token`
  é um UUID separado do `pk` de propósito, pra não expor o id sequencial
  da inscrição num QR que vai ser fotografado/compartilhado.
- **Evento pode ter cor e informações próprias**, além da imagem de capa
  que já existia. `Event.brand_color` (opcional) sobrescreve a paleta da
  igreja só na página pública daquele evento
  (`EventDetailView.get_context_data`, reaproveitando
  `core.colors.generate_palette`); `Event.extra_info` é um campo de texto
  livre pra "o que levar"/ponto de encontro/contato, mostrado como um
  bloco à parte na descrição.
- **Tags e famílias são taxonomia solta, não hierarquia.** `people.Tag`
  (nome + cor) e `people.Family` (só um nome, agrupando `Person` via FK)
  existem pra segmentar/relacionar pessoas além dos campos fixos — CRUD
  bem simples (`/pessoas/tags/`, `/pessoas/familias/`), sem regra de
  negócio além de "excluir a tag/família não exclui a pessoa".
- **Quadro de acompanhamento é um kanban de verdade (drag-and-drop), sem
  lib externa.** `/pessoas/acompanhamento/` usa a API nativa de
  drag-and-drop do HTML5 (`draggable`, `ondragstart`/`ondrop`) + um
  `fetch()` puro pra `PipelineMoveView` — arrastar um cartão pra outra
  coluna já move `Person.pipeline_stage`. Novo campo, não reaproveita
  `status`/`role` porque acompanhamento de visitante é um eixo diferente
  (uma pessoa pode ser "Integrado" no pipeline e continuar com `role`
  Visitante até a secretaria formalizar a membresia).
- **Fallback de e-mail só depois de esgotar os retries automáticos do
  WhatsApp.** `processar_fila_whatsapp` manda um e-mail (se a pessoa tiver
  `email` cadastrado) só quando uma mensagem `FAILED` bate
  `whatsapp_max_retries` — nunca na primeira falha, que ainda pode ser só
  uma instabilidade momentânea resolvida no próximo lote.
- **Rate limit simples baseado no cache do Django** (`core.ratelimit.RateLimitMixin`),
  aplicado a login, cadastro público de visitante e inscrição pública em
  evento — conta só POSTs por IP, numa janela de tempo. Usa o cache padrão
  (`LocMemCache`, por processo) de propósito: simples o bastante pra um
  VPS pequeno de um worker só, documentado em `DEPLOY.md` que múltiplos
  workers dividem o limite real por worker.
- **Notificação push e Sentry seguem o mesmo padrão "prepared, not
  integrated" do WhatsApp/Mercado Pago**: sem `VAPID_PRIVATE_KEY`/`SENTRY_DSN`
  configurados (e as libs `pywebpush`/`sentry-sdk` instaladas), nenhum dos
  dois faz nada — nem quebra, nem aparece na interface
  (`core.push.enviar_push_para_usuario` devolve 0 silenciosamente; o botão
  "Ativar notificações" do Portal só renderiza quando
  `vapid_public_key` existe no contexto).
- **`/health/` é infraestrutura, não uma feature de negócio**: sem
  autenticação, só confirma processo + banco acessíveis, pensado pra
  monitor de uptime externo (ver `DEPLOY.md` seção 6).

## Rotas principais

**Área logada** (exige `can_manage_people`, exceto dashboard/detalhe/portal):
- `/` — dashboard (admin) ou Portal do Membro, dependendo do role · `/accounts/login/`, `/accounts/logout/` · `/accounts/2fa/` (status/ativar/desativar 2FA)
- `/pessoas/` — lista + filtros · CRUD · `/pessoas/importar/` (+ `modelo/`) · `/pessoas/campanha/` (WhatsApp em massa) · `/pessoas/<id>/criar-acesso/` · `/pessoas/acompanhamento/` (kanban) · `/pessoas/familias/` · `/pessoas/tags/`
- `/eventos/` — lista de gestão · CRUD · `/eventos/<slug>/inscritos/` (+ `exportar/`, confirmar pagamento, promover da lista de espera) · `/eventos/checkin/<token>/` (staff escaneia o QR do inscrito)
- `/links/admin/` — editar página + gerenciar links (+ cliques por link)
- `/financeiro/` — lançamentos + totais + gráfico · `/financeiro/orcamento/` · `/financeiro/exportar/` (CSV/Excel) · `/financeiro/recorrentes/` · `/financeiro/doacoes/` (confirmar doação PIX)
- `/celulas/` — lista de células · CRUD · `/celulas/<id>/reuniao/nova/` (registrar presença)
- `/mensagens/` — fila de WhatsApp (status, cancelar, reenviar) · `/mensagens/nova/` (avulsa) · `/mensagens/modelos/` (CRUD) · `/mensagens/whatsapp/` (Conectar/Desconectar — só isso, sem campo técnico) · `/mensagens/whatsapp/conectar/`, `/mensagens/whatsapp/desconectar/` (POST-only)
- `/formularios/` — CRUD de formulários customizados · `/formularios/<id>/campos/` (adicionar/editar/excluir pergunta) · `/formularios/<id>/respostas/` (+ `exportar/` em CSV)
- `/configuracoes/` — configuração que a própria igreja edita (nome, templates de mensagem, intervalo/lote/retries da fila, alertas administrativos, PIX, Mercado Pago) — **não** inclui URL/chave/instância da Evolution API
- `/auditoria/` — quem criou/editou/excluiu o quê (mesmos dados do Django admin, tela própria)
- `/financeiro/doacoes/<id>/recibo.pdf` — recibo de doação confirmada (dono da doação ou staff)
- `/relatorio.pdf` — relatório geral em PDF
- `/meus-dados/` — autoatendimento LGPD (Portal): `/meus-dados/baixar/` (JSON), `/meus-dados/solicitar-exclusao/` (POST)
- `/privacidade/solicitacoes/` — fila de exclusão LGPD pra secretaria confirmar · `/privacidade/solicitacoes/<id>/confirmar/` (POST, destrutivo)
- `/assinatura/` — status do plano + assinar Básico/Pro · `/assinatura/assinar/<plano>/` (POST, cria a assinatura no Mercado Pago)
- `/admin/` — Django admin (todos os models, incl. `Church`, de TODAS as igrejas para quem não tem igreja própria — o dono da plataforma —, e o log de auditoria)

**Público (sem login) — a maioria com o slug da igreja na frente da
URL**, ex.: `/igreja-batista-exemplo/eventos/culto-de-natal/`:
- `/cadastro-igreja/` — cadastro público de igreja nova (Fase 2, não é por igreja — é ANTES de uma igreja existir) · `/cadastro-igreja/confirmar/<token>/` — confirmação de e-mail
- `/conta-suspensa/` — mostrada quando o acesso da igreja está suspenso (trial vencido ou assinatura cancelada)
- `/privacidade/` — política de privacidade (LGPD, genérica, não é por igreja)
- `/<slug-da-igreja>/pessoas/cadastro/` — cadastro de visitante / pedido de membresia
- `/accounts/senha/esqueci/` — recuperação de senha (não é por igreja — o e-mail já identifica a conta)
- `/<slug-da-igreja>/eventos/<slug>/` — página do evento (cor/informações próprias se definidas) · `/<slug-da-igreja>/eventos/<slug>/inscricao/` — formulário de inscrição (entra na lista de espera se lotado; pagamento PIX/Mercado Pago se pago)
- `/<slug-da-igreja>/links/` e `/<slug-da-igreja>/links/<slug>/` — página de link-na-bio · `/<slug-da-igreja>/links/click/<id>/` — redirect rastreado
- `/manifest.json`, `/sw.js` — PWA (genéricos, não por igreja)
- `/health/` — health check sem autenticação (monitoramento externo, não é por igreja)
- `/mensagens/webhook/evolution/` — confirmação de entrega (a Evolution API chama esta; identifica a igreja pelo cabeçalho `X-Webhook-Secret`, não por slug)
- `/financeiro/doacoes/webhook/mercadopago/` — confirmação automática de doação via Mercado Pago (identifica a igreja por `?church_id=`, embutido por nós na notification_url)
- `/assinatura/webhook/mercadopago/` — confirmação automática de assinatura (Fase 4; identifica a igreja pelo `external_reference`, não por slug)
- `/<slug-da-igreja>/formularios/<slug>/` — formulário customizado (+ `obrigado/`) — só existe se `is_active=True`

Rotas de GESTÃO (login) continuam sem slug na URL (`/eventos/`,
`/formularios/`, `/links/admin/` etc.) — a igreja vem de
`request.user.church`, não da URL; cada app com tela pública tem
`urls.py` (gestão) e `public_urls.py` (público) separados, com
`app_name` diferente (`events` × `events_public` etc. — ver seção
"Multi-tenência").

**Portal do Membro (logado, sem `can_manage_people`)**:
- `/financeiro/doacoes/nova/` — fazer uma doação avulsa (PIX/Mercado Pago)
- `/mensagens/push/inscrever/` — registra a inscrição de notificação push do navegador (botão "Ativar notificações" no Portal)

## Importação de planilha

`/pessoas/importar/` é um fluxo de duas etapas: (1) upload de .csv/.xlsx —
tem um link para baixar um **modelo de exemplo** (`/pessoas/importar/modelo/`,
gerado on-the-fly via openpyxl); (2) uma **tela de revisão** onde cada linha
lida vira um formulário editável (nome, telefone, e-mail, nascimento, cargo,
status, incluir/pular) — nada é gravado no banco até confirmar essa etapa.
Linhas cujo telefone (dígitos normalizados) ou nome exato já existem no
cadastro vêm **desmarcadas automaticamente** com um aviso, pra evitar
duplicar gente sem querer — a secretaria decide se quer mesmo importar
mesmo assim.

## Fila de WhatsApp

Campanha em massa, mensagem avulsa (agendada ou imediata) e lembrete
automático **nunca enviam nada na hora** — tudo cria uma
`notifications.WhatsAppMessage` (status `PENDING`). Quem envia de verdade
é `manage.py processar_fila_whatsapp`, rodando via cron a cada 1-5
minutos — **processa a fila de TODAS as igrejas ativas, uma de cada vez**
(`tenant_context(church)`), um por um dentro de cada igreja, esperando
`Church.whatsapp_send_interval_seconds` (padrão 6s, por igreja) entre
cada mensagem — mandar tudo de uma vez é o jeito mais rápido de um número
real ser banido pelo WhatsApp. Mensagens que falham são reenviadas
automaticamente até `whatsapp_max_retries` tentativas (e manualmente
depois disso, botão "Reenviar"). Ver `/mensagens/` pra acompanhar status
(aguardando/enviada/falhou/entregue/lida) e cancelar o que ainda não
saiu. Modelos de mensagem reutilizáveis em `/mensagens/modelos/` —
aparecem como um seletor que preenche a caixa de texto tanto na campanha
quanto na mensagem avulsa.

O servidor Evolution é **único, compartilhado por todas as igrejas**
(`settings.EVOLUTION_API_URL`/`EVOLUTION_API_KEY`, no `.env` da
plataforma — não é mais um campo de igreja nenhuma) — só o **dono do
sistema** mexe nisso. Cada igreja só tem sua própria instância nesse
servidor (`Church.whatsapp_instance`, gerado do slug,
`whatsapp_instance_token`, `whatsapp_webhook_secret`), editável pelo dono
no Django admin (`/admin/core/church/<id>/change/` — exige `is_staff`;
`ChurchConfigForm`, o form usado em `/configuracoes/`, nem inclui esses
campos no `Meta.fields`, então um POST direto na tela da igreja não
altera nada disso). A igreja (Pastor/Admin/Líder) só vê
`/mensagens/whatsapp/`: um botão "Conectar" (que já mostra o QR code na
hora, criando a instância sozinho se for a primeira vez) e "Desconectar"
— nenhum campo técnico aparece nessa tela. O restante da config da fila
(templates de mensagem, intervalo, lote, retries, PIX, Mercado Pago) fica
em `/configuracoes/`, editável pela própria igreja. Ver `DEPLOY.md` seção
5.1 pro passo a passo completo de self-hosting com Docker no Contabo.

## Formulários customizados

`/formularios/` deixa a igreja montar qualquer formulário (pedido de
oração, inscrição de batismo, pesquisa, atualização cadastral) sem
precisar de código novo: título/descrição + uma lista de perguntas
(`custom_forms.FormField`), cada uma com sua própria ordem e
obrigatoriedade. Tipos de campo disponíveis (`FormField.FieldType`) —
texto curto/longo; os "dados pessoais" no mesmo vocabulário de
`people.Person` (nome completo, e-mail, telefone/WhatsApp, CPF, data de
nascimento, endereço, cidade, estado — `<select>` com as 27 UFs —, CEP,
sexo e estado civil — estes dois reaproveitam `Person.Gender`/
`Person.MaritalStatus.choices` direto, sem digitar opção nenhuma); e
genéricos (data, horário, número, link, sim/não, arquivo/anexo, escolha
única ou múltipla escolha com opções livres). A resposta pública fica em
`/<slug-da-igreja>/formularios/<slug>/` e é renderizada e validada na mão a partir dos
campos cadastrados (não um `django.forms.Form` fixo — o conjunto de
perguntas muda por formulário, definido em runtime); anexos vão pra
`FormAnswer.file` (upload real, `enctype="multipart/form-data"`) e
aparecem como link de download em `/formularios/<id>/respostas/`.

O disparo de WhatsApp pra quem responde é **opcional**
(`send_whatsapp_confirmation`, desligado por padrão) — quando ligado, cai
na mesma fila de sempre (`notifications.WhatsAppMessage`, nunca envia na
hora) usando o número do campo marcado como "telefone"
(`FormField.is_phone_field`); a tela de edição do formulário bloqueia
ligar o disparo se nenhum campo estiver marcado assim, já que não teria
pra quem mandar. A mensagem é um template livre com `{nome}` (do campo
marcado como "nome", `is_name_field`) e `{formulario}` (o título). Ver
respostas e exportar em CSV em `/formularios/<id>/respostas/`.

Outras três coisas também opcionais, uma por checkbox na edição do
formulário:

- **Criar/atualizar cadastro de pessoa** (`sync_to_person`) — usa os
  campos dos tipos "dados pessoais" (nome, telefone, e-mail, data de
  nascimento, endereço, cidade, estado, sexo, estado civil) pra achar (por
  telefone, ou pelo login de quem respondeu, se estiver logado) ou criar
  um `people.Person` — nunca sobrescreve com um campo deixado em branco.
- **Avisar a equipe por e-mail** (`notify_staff_emails`) — um ou mais
  endereços, separados por vírgula; manda um e-mail com as respostas
  assim que uma chega. Nunca derruba a submissão se o envio falhar.
- **Duplicar** um formulário existente (config + campos, sem as
  respostas) ou **começar com um modelo pronto** (Pedido de Oração,
  Inscrição para Batismo, Atualização de Cadastro — este já vem com
  `sync_to_person` ligado) — ambos criam o formulário **inativo**, pra dar
  tempo de revisar antes de divulgar.

A resposta pública também tem um honeypot (campo invisível que só um bot
preenche) — quem cai nele recebe a tela de "obrigado" normalmente, mas
nada é gravado.

## Backup, alerta de conexão, auditoria e recibo

- **Backup** (`manage.py backup_banco`) agora também zipa a pasta
  `media/` (fotos, capas de evento, anexos de formulário, comprovantes
  de doação) além do banco — um restore só do banco deixaria tudo isso
  com link quebrado. Rotação (`--keep`) e `--no-media` funcionam
  independente um do outro; em Postgres, pula o banco (avisa, não
  crasha) mas ainda cuida da mídia.
- **Alerta de desconexão do WhatsApp** (`manage.py verificar_conexao_whatsapp`,
  pensado pra cron a cada 15-30min) avisa por e-mail
  (`Church.admin_alert_emails`, em Configurações) se a instância
  caiu — só UM e-mail por queda (não repete a cada execução do cron
  enquanto continuar desconectado), reseta sozinho quando reconectar.
  Falha ao checar o status é tratada como "desconectado", não ignorada.
- **Auditoria** (`/auditoria/`) — o que já existia só no Django admin
  (`core.AuditLog`) agora tem tela própria dentro do sistema, com filtro
  por model e ação, sem precisar dar acesso ao admin só pra isso.
- **Recibo de doação em PDF** (`/financeiro/doacoes/<id>/recibo.pdf`,
  reaproveitando reportlab) — só existe pra doação já confirmada; quem
  doou baixa o próprio, staff baixa qualquer um, terceiro recebe 404 (não
  403, pra não confirmar que o ID existe).

## Autenticação em duas etapas (2FA)

Opcional, por conta — ninguém é obrigado a configurar. Ativa em
`/accounts/2fa/` (link "Segurança" no menu): escaneia um QR code com
qualquer app autenticador (Google Authenticator, Authy...) e confirma com
um código de 6 dígitos. TOTP puro (RFC 6238), implementado sem lib
externa (`accounts/totp.py` — mesmo espírito de `events/pix.py` fazer o
CRC16 do PIX na mão), compatível com qualquer app autenticador padrão.

Uma vez ativado numa conta, o login em `/accounts/login/` passa a exigir
o código como segunda etapa antes de autenticar de verdade (usuário/senha
corretos sozinhos não bastam mais — ficam "pendentes" na sessão até o
código bater). Como o Django admin tem sua própria tela de login separada
(`/admin/login/`), `accounts/apps.py` redireciona ela pra
`/accounts/login/` também — um único portão de entrada pro sistema
inteiro.

**Pra quem é `is_staff` (o "dono"), o 2FA é obrigatório, não opcional** —
essa conta guarda credenciais técnicas sensíveis (Evolution API) e vê
tudo no financeiro/formulários. `admin.site.has_permission` foi
sobrescrito (também em `accounts/apps.py`) pra negar acesso ao Django
admin sem um `TOTPDevice` confirmado, mesmo com usuário/senha corretos.
Sem loop de redirecionamento: quem já está logado mas ainda não tem 2FA
vai direto pra `/accounts/2fa/configurar/` (não pra tela de login de
novo, que reautenticaria e bateria na mesma trava outra vez); quem não é
`is_staff` e esbarra numa URL do admin volta pro próprio dashboard. Pro
resto das contas (Pastor/Admin/Líder/Membro sem `is_staff`) continua
opcional.

## Testes

```bash
venv/Scripts/python.exe -m pytest
```

279 testes (`pytest-django`) cobrindo: multi-tenência (isolamento entre
duas igrejas — lista de gestão não vaza dado da outra, acesso direto por
ID a um registro de outra igreja dá 404, página pública com o slug da
igreja errada dá 404 — em `Person`, `Transaction`, `CustomForm` e
`Event`), permissões (403 para Membro em toda
view de gestão), CRUD básico de cada app, o fluxo de importação com edição
e detecção de duplicados, o cálculo do payload PIX (CRC conferido de forma
independente), a exportação CSV/Excel (regressão do bug de BOM duplicado),
a branch do dashboard/portal por role, o fallback de WhatsApp sem API
configurada, criação de acesso + e-mail de redefinição, PWA
(manifest/service worker), auditoria, orçamento, o relatório em PDF, a
fila de mensagens (envio, intervalo respeitado via mock, lote máximo,
retry automático, reenvio manual, agendamento futuro, fallback por e-mail
depois de esgotar os retries), a tela de Configurações (incluindo que os
campos técnicos da Evolution API não existem no form e não são alterados
nem enviando no POST direto), a tela de Conectar/Desconectar in-app (sem
detalhe técnico visível) e a mesma coisa no Django admin (`is_staff`-only),
modelos de mensagem e o webhook de confirmação de entrega (segredo
obrigatório, mapeamento de status, evento não mapeado ignorado sem erro),
o quadro de acompanhamento (mover etapa, permissão), famílias e tags
(CRUD, exclusão não apaga a pessoa), lista de espera de evento (inscrição
além da capacidade, promoção manual com aviso enfileirado), check-in por
QR code (marca uma vez, idempotente ao escanear de novo, exige
permissão), cor/informações próprias de evento sobrescrevendo a paleta da
igreja, contribuição recorrente (em dia/em atraso), doação avulsa (PIX
manual e confirmação pelo staff), inscrição de notificação push
(payload válido/inválido, exige login), health check e rate limit de
login (429 depois do limite, sem bloquear um login correto dentro dele),
formulários customizados (CRUD de formulário/campos, formulário inativo
dá 404, campo obrigatório vazio reexibe com erro sem gravar nada, campo
opcional vazio não bloqueia, múltipla escolha junta as opções marcadas,
disparo de WhatsApp bloqueado sem campo de telefone marcado e disparado
com telefone normalizado + template preenchido quando ligado, sexo/estado
civil usam as choices de `people.Person`, UF tem as 27 opções, upload de
arquivo grava no storage de verdade e aparece como link pra baixar,
honeypot finge sucesso sem gravar nada, sincronização com cadastro de
pessoa — cria como visitante/atualiza por telefone/atualiza a própria
pessoa de quem está logado/nunca sobrescreve com resposta em branco —,
notificação por e-mail à equipe, duplicar formulário e criar a partir de
modelo pronto), 2FA (algoritmo TOTP isolado, fluxo de configuração exige
código válido pra confirmar, login com dispositivo confirmado fica
pendente até o código bater — sem autenticar de verdade antes disso —,
login sem 2FA configurado funciona igual sempre funcionou, o login do
Django admin redireciona pro mesmo portão de entrada, e `is_staff` sem
2FA confirmado é barrado do admin e mandado pra tela de configurar — sem
loop de redirecionamento), backup (banco + mídia, rotação, pula banco
sem crashar em Postgres), alerta de desconexão do WhatsApp (um e-mail só
por queda, reseta ao reconectar, trata falha ao checar como
desconectado), tela de auditoria in-app (permissão, filtro por model) e
recibo de doação em PDF (dono da doação ou staff baixam, terceiro recebe
404, doação pendente ainda não tem recibo); cadastro público de igreja
(nasce em trial, já loga sozinho, e-mail duplicado é rejeitado, honeypot
finge sucesso sem criar nada, e-mail de confirmação é enviado),
confirmação de e-mail (token válido confirma, token inválido mostra erro
sem confirmar), vencimento de trial (`expirar_trials` suspende quem
passou da data, não mexe em quem ainda está dentro do prazo nem em quem
já é `ativo`), bloqueio de igreja suspensa (redireciona pra
`/conta-suspensa/`, mas a própria tela e `/assinatura/` continuam
acessíveis, trial não é bloqueado), consentimento LGPD obrigatório nos 3
formulários públicos (rejeita sem a checkbox, grava o timestamp quando
marcada), autoatendimento de dados (exporta JSON dos próprios dados,
exige conta vinculada, solicita exclusão sem duplicar pedido pendente),
fila de exclusão pra secretaria (lista, permissão, confirmar apaga a
`Person` de verdade e preserva o nome no histórico mesmo com a FK
virando null), limites de plano (trial sem limite, Básico bloqueia além
de 100 pessoas e sem WhatsApp, Pro libera os dois, tela de assinatura,
checkout com token ausente/inválido tratado sem quebrar) e o webhook de
assinatura do Mercado Pago (autorizada ativa a igreja, cancelada
suspende, referência desconhecida é ignorada, falha na API vira 502).

## Status

Todos os módulos do plano original (1-6) estão implementados, mais Portal
do Membro, personalização visual, recuperação de senha, criação de acesso,
detecção de duplicados na importação, rastreamento de cliques, auditoria,
PWA, hardening de produção + `DEPLOY.md`, backup automático, tela de
Configurações in-app, fila de WhatsApp completa (intervalo, retry
automático + reenvio manual, confirmação de entrega via webhook, modelos
de mensagem reutilizáveis, fallback por e-mail), conexão do WhatsApp com
permissões em duas camadas — infraestrutura (URL/chaves/instância) só
pelo dono via Django admin, Conectar/Desconectar simplificado (com QR
code inline) pra igreja in-app —, Mercado Pago real, relatório em PDF e
orçamento, quadro de acompanhamento de visitante (kanban), famílias,
tags, lista de espera de evento com promoção manual, check-in por QR
code, imagem/cor/informações próprias por evento, contribuição recorrente
com relatório de em-dia/em-atraso, doação avulsa pelo Portal (PIX +
Mercado Pago), notificação push do navegador, health check, rastreamento
de erros (Sentry) e rate limit de login/formulários públicos — as
últimas três seguem o padrão "prepared, not integrated": funcionam sem
config nenhuma (rate limit) ou simplesmente não fazem nada até você
configurar a variável de ambiente (Sentry, push) —, além de formulários
customizados com campos livres, disparo de WhatsApp opcional, sincronização
com cadastro de pessoa, aviso à equipe por e-mail, duplicar/modelos
prontos e honeypot anti-bot; autenticação em duas etapas (TOTP) —
**obrigatória pra `is_staff`** (o "dono"), opcional pro resto das contas
—, cobrindo tanto o login normal quanto o do Django admin; backup
automático agora inclui a pasta `media/` além do banco; alerta por
e-mail se o WhatsApp desconectar sozinho; tela de auditoria dentro do
próprio sistema (antes só existia no Django admin); e recibo de doação
em PDF; **multi-tenência real** (Fase 1) — um único deploy atende
várias igrejas ao mesmo tempo, isolamento automático por linha
(`core.tenancy.TenantModel`/`TenantManager`), páginas públicas com o
slug da igreja na URL, WhatsApp como servidor Evolution único
compartilhado com uma instância por igreja — ver seção "Multi-tenência"
acima; **cadastro público de igreja com trial de 30 dias** (Fase 2) —
self-service, confirmação de e-mail assíncrona, vencimento e suspensão
automáticos via cron; **LGPD** (Fase 3) — consentimento obrigatório nos
3 pontos de coleta pública, política de privacidade, autoatendimento no
Portal (baixar dados / solicitar exclusão) com fila de confirmação
manual pra secretaria; e **cobrança automática de assinatura** (Fase 4)
— 2 planos (Básico/Pro) via Mercado Pago (API de Preapproval, conta da
plataforma, separada da conta de cada igreja), webhook atualiza
`Church.status`/`plano` sozinho, controle manual pelo dono continua
funcionando em paralelo. Gaps conhecidos, só relevantes se o projeto
crescer bastante mais:
`processar_fila_whatsapp`/`enviar_lembretes`/`backup_banco`/
`verificar_conexao_whatsapp` precisam ser agendados manualmente (cron/Task
Scheduler — não vêm agendados sozinhos);
formato de resposta da Evolution API (criação de instância, QR code,
status, webhook) é best-effort, nunca testado contra um servidor real; o
service worker não implementa cache offline de verdade, só torna o site
instalável (só notificação push); `AuditLog` não é visível fora do Django
admin (sem tela própria); rate limit usa cache por processo
(`LocMemCache`) — com múltiplos workers/gunicorn o limite real vira
`limite × nº de workers` (ver `DEPLOY.md`); a API de Preapproval do
Mercado Pago (Fase 4) segue o mesmo "nunca testado contra credencial
real" das outras integrações — só os caminhos de erro (token
inválido/API fora do ar) foram verificados ao vivo; o texto da política
de privacidade é genérico, não redigido por um advogado.
