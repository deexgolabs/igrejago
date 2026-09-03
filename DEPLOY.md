# Deploy — Church CRM

Guia rápido para colocar em produção. Funciona em qualquer VPS Linux
(DigitalOcean, Contabo etc.) ou no PythonAnywhere — os dois caminhos estão
aqui.

## 1. Checklist antes de ir ao ar

- [ ] `SECRET_KEY` trocada por uma chave nova e secreta (`.env`, não commitada)
- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` com o domínio real
- [ ] Banco de dados: SQLite serve para uma igreja pequena/média; para mais
      tráfego, configure PostgreSQL (`DB_ENGINE=postgresql` no `.env`)
- [ ] E-mail configurado (`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`) — sem
      isso, "esqueci minha senha" não envia nada de verdade
- [ ] `python manage.py check --deploy` sem avisos que você não entenda
- [ ] Backup rodando (`manage.py backup_banco`, ver seção 4)
- [ ] Multi-tenência: pelo menos uma `Church` criada (ver seção 1.1) — o
      sistema não funciona com zero igrejas cadastradas

## 1.1. Criando uma igreja (multi-tenência)

Um único deploy atende várias igrejas — cada uma é uma linha em `Church`,
isolada automaticamente do resto (ver `README.md`, seção "Multi-tenência").
**Existe cadastro público self-service** em `/cadastro-igreja/` (Fase 2)
— nasce em trial de 30 dias, sem precisar de você. Pra criar uma manualmente
mesmo assim (ex.: conta de teste, migração de dado existente), pelo Django
admin (`/admin/core/church/add/`) ou via shell:

```bash
venv/bin/python manage.py shell -c "
from core.models import Church
from accounts.models import User
church = Church.objects.create(name='Igreja Nova')
User.objects.create_user(username='pastor-novo', password='TROQUE-ISSO', role=User.Role.PASTOR, church=church)
"
```

`slug` é gerado sozinho a partir do nome no `save()`. O nome de cada
instância de WhatsApp (`WhatsAppInstance.whatsapp_instance`, gerado do
slug — `igreja-<slug>`, sufixado da 2ª em diante) só nasce quando a
igreja adiciona um número em `/mensagens/whatsapp/` — ver 5.1 abaixo,
agora "1 igreja → N instâncias". Um usuário **sem** igreja
(`church=None`, `is_staff=True`) é o dono da plataforma — vê e gerencia
TODAS as igrejas pelo Django admin, sem filtro nenhum; não crie isso por
engano numa conta pensada pra ser de uma igreja só.

Uma igreja criada pelo cadastro público (ou por você, se preencher
`trial_expira_em`) vence o trial sozinha via `manage.py expirar_trials`
(cron diário — ver seção 5 abaixo) — sem esse cron agendado, o trial
nunca vence automaticamente, só se você suspender manualmente.

## 2. VPS genérico (Ubuntu + Nginx + Gunicorn)

```bash
# No servidor
sudo apt install python3-venv nginx
git clone <seu-repositorio> church-crm
cd church-crm
python3 -m venv venv
venv/bin/pip install -r requirements.txt gunicorn
cp .env.example .env   # edite com os valores reais
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

Rode com Gunicorn atrás de um Nginx fazendo proxy reverso (Nginx cuida do
HTTPS via Let's Encrypt/certbot — é por isso que `SECURE_SSL_REDIRECT`
fica desligado por padrão no `settings.py`, o proxy já força HTTPS antes
de chegar no Django):

```bash
venv/bin/gunicorn church_crm.wsgi:application --bind 127.0.0.1:8000
```

Sirva `/static/` e `/media/` direto pelo Nginx (mais rápido que passar
pelo Django) ou confie no Whitenoise (já configurado, funciona sem Nginx
servir estáticos, só um pouco mais lento).

Systemd (opcional, pra manter o Gunicorn rodando sozinho) e configuração
de Nginx seguem o padrão de qualquer app Django — nada específico deste
projeto.

## 3. PythonAnywhere (mais simples, sem servidor pra administrar)

1. Suba o código (git clone ou upload manual)
2. Crie um virtualenv e instale `requirements.txt`
3. Na aba **Web**, aponte o WSGI file pra `church_crm.wsgi.application`
4. Configure `ALLOWED_HOSTS` com `seuusuario.pythonanywhere.com`
5. Mapeie `/static/` e `/media/` nas configurações de "Static files" da aba Web
6. PythonAnywhere já força HTTPS na borda — não precisa mexer em
   `SECURE_SSL_REDIRECT`

## 4. Backup

```bash
python manage.py backup_banco --keep 14
```

Copia `db.sqlite3` **e** zipa a pasta `media/` (fotos de pessoas, capas de
evento, anexos de formulário, comprovantes de doação) pra `backups/`, com
timestamp, mantendo só os 14 mais recentes de cada um — um restore do
banco sozinho, sem a mídia junto, deixaria tudo isso com link quebrado.
Agende via cron (`crontab -e`):

```
0 3 * * * cd /caminho/church-crm && venv/bin/python manage.py backup_banco
```

Em Postgres, use `pg_dump` diretamente pro banco (`--no-media` pula a
parte de mídia se quiser separar os dois; sem a flag, `backup_banco`
recusa fazer o backup do banco em Postgres mas ainda cuida da mídia).

## 5. WhatsApp — fila de mensagens e lembretes

Nada é enviado na hora que uma campanha/mensagem avulsa/lembrete é criado
— tudo vira uma `WhatsAppMessage` PENDING. Quem manda de verdade é o
`processar_fila_whatsapp` — **processa a fila de TODAS as igrejas
cadastradas, uma de cada vez**, um por um dentro de cada igreja,
esperando o intervalo configurado (`Church.whatsapp_send_interval_seconds`,
padrão 6s, por igreja) entre cada envio. **Esse intervalo importa de
verdade** — mandar uma campanha inteira de uma vez, sem pausa, é o jeito
mais comum de um número real ser marcado como spam e banido pelo
WhatsApp. Um único cron desses cobre todas as igrejas do deploy — não
precisa de um cron por igreja.

```
# processa a fila de TODAS as igrejas a cada minuto (pega o que estiver pendente/vencido)
* * * * * cd /caminho/church-crm && venv/bin/python manage.py processar_fila_whatsapp

# enfileira os lembretes do dia de TODAS as igrejas (aniversário + reunião de célula), 1x/dia
0 8 * * * cd /caminho/church-crm && venv/bin/python manage.py enviar_lembretes

# checa se o WhatsApp de CADA igreja continua conectado a cada 20min —
# avisa por e-mail (Church.admin_alert_emails, em Configurações) se
# caiu, uma vez só por queda, por igreja
*/20 * * * * cd /caminho/church-crm && venv/bin/python manage.py verificar_conexao_whatsapp

# suspende quem passou do trial de 30 dias sem assinar (Fase 2), 1x/dia
0 3 * * * cd /caminho/church-crm && venv/bin/python manage.py expirar_trials
```

Sem `EVOLUTION_API_URL`/`EVOLUTION_API_KEY` no `.env` da plataforma
(seção 5.1) — ou sem a instância daquela igreja criada ainda — a fila só
imprime cada mensagem no log do cron — nada é enviado de verdade, mas o
fluxo continua 100% testável. Mensagens que falham são reenviadas
automaticamente até `whatsapp_max_retries` tentativas (configurável por
igreja em `/configuracoes/`).

### 5.1. Self-hosting da Evolution API no Contabo

A Evolution API é open-source e conecta a um número de WhatsApp real via
QR code (sem aprovação de conta comercial da Meta). Rodando num VPS
Contabo separado (ou na mesma máquina do Django, em containers distintos):

```bash
# no VPS Contabo, com Docker instalado
docker run -d \
  --name evolution-api \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=troque-por-uma-chave-forte \
  -e DATABASE_ENABLED=false \
  -v evolution_instances:/evolution/instances \
  atendai/evolution-api:latest
```

Coloque um Nginx + Let's Encrypt na frente pra expor com HTTPS (ex.:
`https://evolution.suaigreja.com`).

Essa parte é **só do dono do sistema** — a igreja (Pastor/Admin/Líder)
nunca vê nem preenche nada disso; `ChurchConfigForm` (o form por trás de
`/configuracoes/`) nem inclui esses campos. O servidor é **único,
compartilhado por TODAS as igrejas** do deploy — configure no `.env` da
plataforma, uma vez só:

```
EVOLUTION_API_URL=https://evolution.suaigreja.com
EVOLUTION_API_KEY=o-mesmo-valor-de-AUTHENTICATION_API_KEY-acima
```

**Desde a rodada de "múltiplas instâncias por igreja": não é mais "1
igreja = 1 número" — é "1 igreja → N instâncias"** (ex.: "WhatsApp da
igreja" + "WhatsApp do pastor"), cada uma uma linha em
`notifications.WhatsAppInstance`, cada uma seu próprio nome nesse
servidor compartilhado (gerado sozinho do slug da igreja — ex.:
`igreja-igreja-nova`, sufixado `-2`/`-3`... a partir da 2ª instância da
mesma igreja — não precisa digitar nada), próprio
`whatsapp_instance_token`, `webhook_secret` e ritmo de envio
(`send_interval_seconds`/`batch_size`/`max_retries`, independentes por
número). Quantas instâncias uma igreja pode ter é
`Church.whatsapp_max_instancias` — ajuste manual do dono da plataforma
(sem regra fixa de plano por enquanto), padrão 1.

Pra criar/conectar uma instância pela primeira vez, use os botões
"Criar/recriar instância" e "Ver QR code" no painel de CADA
`WhatsAppInstance` no Django admin
(`/admin/notifications/whatsappinstance/<id>/change/`, exige
`is_staff` — a Evolution devolve o token da instância, que o admin já
salva sozinho em `whatsapp_instance_token`). A partir daí, o dia a dia
da igreja é só a tela `/mensagens/whatsapp/`: uma linha por número
conectado, cada uma com **Conectar** (mostra o QR code na hora —
reaproveita a instância já criada) e **Desconectar**, mais **Adicionar
número** (até o limite de `whatsapp_max_instancias`) — sem nenhum campo
técnico visível. Se um número cair (ex.: trocou de aparelho), a igreja
mesma resolve clicando em Conectar de novo naquela linha — só precisa
voltar ao dono/admin se a própria instância no servidor Evolution
precisar ser recriada do zero.

**Nota de honestidade sobre a migração**: passar de "campo direto na
`Church`" pra "linha em `WhatsAppInstance`" foi uma migração de DADO de
verdade (não só de schema) — o nome real da instância já registrada no
servidor Evolution (crítico: renomear desconectaria quem já escaneou o
QR code) foi copiado tal e qual pra uma `WhatsAppInstance` nova, e só
depois os campos antigos (`Church.whatsapp_instance` etc.) foram
removidos. Em produção, **rode `python manage.py backup_banco` antes de
aplicar essa migração** (`python manage.py migrate`) — é a mesma
recomendação de sempre pra qualquer migração que mexe em dado real, não
só estrutura.

### 5.2. Confirmação de entrega (webhook)

Isso já é automático: ao clicar em "Criar/recriar instância" (Django
admin, agora em `WhatsAppInstance`), `core.whatsapp.criar_instancia()`
gera sozinho um `WhatsAppInstance.webhook_secret` (se a instância ainda
não tiver um) e já embute a configuração do webhook — URL própria
(`https://seudominio.com/mensagens/webhook/evolution/`, calculada de
`request.build_absolute_uri`) + o evento `messages.update` + o cabeçalho
`X-Webhook-Secret` — na própria chamada de criação da instância. Não
precisa mexer no painel da Evolution API manualmente. O segredo é único
POR INSTÂNCIA (não mais por igreja — uma igreja com 2 números tem 2
segredos) porque a URL do webhook é a mesma pra todas; é ele que diz de
qual instância (e daí qual igreja) é o evento — a Evolution API não
assina os webhooks por conta própria, então sem um segredo que bata com
alguma instância, toda chamada é rejeitada.

**Nota de honestidade**: o formato da resposta da API (criação de
instância, QR code, status, e o payload do webhook) foi **confirmado ao
vivo** contra um servidor Evolution API v2.3.7 real (deploy próprio via
Docker Compose numa Contabo VPS, ver histórico do projeto) — criação,
QR code e status bateram com o parsing já existente sem precisar de
ajuste nenhum; só o payload do webhook (`messages.update`) veio num
formato diferente do documentado (`data.keyId`/`data.status` direto, não
aninhado em `key`/`update`) e foi corrigido em
`notifications/views.py::WhatsAppWebhookView` depois de capturar um
evento real. Se sua versão do servidor responder diferente ainda assim,
a tela de conexão mostra a resposta crua da API na mensagem de aviso pra
você ajustar `core/whatsapp.py`/`notifications/views.py::WhatsAppWebhookView`
se precisar.

### 5.2.1. Webhook oficial da Meta (WhatsApp Cloud API)

Modelo BEM diferente do webhook da Evolution acima: aqui é **um único
registro**, feito uma vez no
[painel de desenvolvedor da Meta](https://developers.facebook.com/apps/)
(app do WhatsApp → Configuration → Webhooks), **compartilhado por
TODAS as igrejas** — não um segredo por igreja. Preencha no `.env` da
plataforma:

```
META_APP_SECRET=o-app-secret-do-seu-app-na-meta
META_WEBHOOK_VERIFY_TOKEN=qualquer-string-que-voce-escolher
```

No painel da Meta, configure a URL de callback:

```
https://seudominio.com/mensagens/webhook/meta/
```

usando o MESMO valor de `META_WEBHOOK_VERIFY_TOKEN` no campo "Verify
Token" — a Meta faz uma chamada `GET` de verificação na hora de salvar
(handshake `hub.challenge`), e todo `POST` depois disso vem assinado
com `X-Hub-Signature-256` (HMAC-SHA256 usando `META_APP_SECRET`) —
conferido de verdade a cada chamada, mais forte que o segredo simples
da Evolution. Assine os campos (webhook fields) `messages` (confirmação
de entrega/leitura) e `message_template_status_update` (aprovação/
rejeição de template automática, sem precisar clicar em "Atualizar
status" manualmente).

Como um app Meta serve várias igrejas, o evento é roteado por dado que
já vem DENTRO do payload — `phone_number_id` (mensagem, casado contra
`Church.whatsapp_meta_phone_number_id`) ou `message_template_id`
(template, casado contra `WhatsAppMetaTemplate.meta_template_id`) —
nunca por URL nem cabeçalho por igreja.

**Nota de honestidade**: implementado a partir da documentação pública
da Meta — não existe conta Meta Business real neste ambiente pra testar
ao vivo, mesma ressalva já dada pro resto da integração Meta (envio,
templates). Se o formato real vier diferente, ajuste
`notifications/views.py::MetaWhatsAppWebhookView`.

### 5.2.2. Assistente de IA no WhatsApp

Novo app `assistant/` — menu de atendimento (1. atualizar cadastro /
2. falar com a secretaria / 3. pergunta livre) e coleta de cadastro com
fila de aprovação (`/assistente/cadastros-pendentes/`), sem NENHUMA
credencial de plataforma nova: cada igreja traz a própria chave de IA
(Gemini ou ChatGPT) em Configurações, mesmo padrão de Mercado Pago/PagBank.

**Gotcha real pra quem já tinha número(s) Evolution conectado(s) antes
desta rodada**: `criar_instancia`/`configurar_webhook`
(`core/whatsapp.py`) agora inscrevem o webhook em `MESSAGES_UPDATE` E
`MESSAGES_UPSERT` (mensagem recebida) — mas isso só se aplica a
instâncias criadas/reconfiguradas DEPOIS do deploy. Uma instância que
já estava conectada continua inscrita só em `MESSAGES_UPDATE` até
alguém clicar em **"Criar/recriar instância"** de novo nela (Django
admin, `/admin/notifications/whatsappinstance/<id>/change/` — **não**
"Ver QR code": esse só busca o QR em si, `GET /instance/connect/`, e
não toca no webhook; numa instância já conectada nem devolve QR
nenhum, só o status. "Criar/recriar instância" é que, ao esbarrar no
403 "already in use" de uma instância que já existe, cai no fallback e
chama `configurar_webhook` de novo — reconfigura o evento SEM
desconectar o número). Sem isso, o assistente de IA simplesmente não recebe
mensagem nenhuma pra essa instância — nenhum erro visível, só silêncio.
Vale reconfigurar toda instância já conectada em produção depois deste
deploy, mesmo quem não for usar o assistente agora (não tem custo:
sem `Church.ia_chat_enabled` marcado, a mensagem recebida só é
registrada e ignorada — ver `assistant.engine.processar_mensagem_recebida`).

### 5.3. Cobrança automática de assinatura (Fase 4)

Diferente do Mercado Pago de CADA igreja (`Church.mercadopago_access_token`,
usado só pra receber pagamento de evento/doação daquela igreja), a
cobrança de assinatura usa a conta do **DONO da plataforma** — uma só,
pra todas as igrejas. Crie um Access Token de produção no
[painel de desenvolvedor do Mercado Pago](https://www.mercadopago.com.br/developers/panel)
e preencha no `.env` da plataforma:

```
PLATFORM_MERCADOPAGO_ACCESS_TOKEN=seu-token-de-producao
```

Sem isso configurado, `/assinatura/` continua mostrando os planos, mas
tentar assinar mostra erro — o resto do sistema funciona normal
(cobrança continua manual, direto no Django admin em `Church.status`/
`plano`).

Configure no mesmo painel um webhook (Notificações IPN/Webhooks) pra:

```
https://seudominio.com/assinatura/webhook/mercadopago/
```

com o tópico `preapproval` habilitado. Não precisa de segredo/cabeçalho
customizado aqui — o webhook identifica a igreja/plano pelo
`external_reference` (`CHURCH-<id>-<plano>`) que a própria plataforma
embutiu ao criar a assinatura, e sempre reconsulta a API antes de mudar
`Church.status` (nunca confia no corpo do POST).

**Nota de honestidade**: mesmo padrão das outras integrações de gateway
deste projeto — a API de Preapproval foi implementada seguindo a
documentação pública do Mercado Pago, mas nunca chamada com uma
credencial de produção real; só o caminho de erro (token inválido) foi
verificado ao vivo.

## 6. Monitoramento e endpoints extras

- **Health check**: `GET /health/` — sem autenticação, confirma processo +
  banco acessíveis (`{"status": "ok", "database": true}`, HTTP 503 se o
  banco falhar). Aponte o monitor de uptime (UptimeRobot, o healthcheck do
  Docker, etc.) pra cá.
- **Rastreamento de erros (Sentry, opcional)**: defina `SENTRY_DSN` no
  `.env` e instale `sentry-sdk` (`pip install sentry-sdk`, comentado em
  `requirements.txt`). Sem `SENTRY_DSN`, nada é importado/inicializado —
  zero impacto se você não usar.
- **Notificações push (opcional)**: gere um par de chaves VAPID (ex.: `npx
  web-push generate-vapid-keys` ou a lib Python `py-vapid`) e preencha
  `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_CLAIMS_EMAIL` no `.env`,
  além de instalar `pywebpush` (comentado em `requirements.txt`). Sem
  isso, o botão "Ativar notificações" do Portal do Membro nem aparece
  (`vapid_public_key` vazio no template).
- **Rate limit de login/formulários públicos**: usa o cache do Django
  (`LocMemCache`, por processo). Com um único worker (o cenário normal
  pra uma igreja num VPS pequeno) o limite funciona certinho; com vários
  workers/gunicorn cada um conta separado, então o limite real vira
  `limite × nº de workers` — troque por Redis (`django-redis` em
  `CACHES`) se isso importar no seu deploy.
- **Deliverability de e-mail em massa (campanhas)**: o app já manda o
  cabeçalho `List-Unsubscribe` (RFC 8058) e honra
  `Person.email_opted_out_at` — mas isso NÃO substitui SPF/DKIM/DMARC
  configurados no DNS do domínio de envio (`DEFAULT_FROM_EMAIL`). Sem
  esses registros, provedores como Gmail marcam campanhas como spam
  cedo ou tarde, independente de código — configure isso no provedor
  SMTP que você usa (Gmail Workspace, SES, SendGrid etc.), fora do
  escopo deste projeto (exige acesso ao DNS do domínio da igreja).

## 6.1. App com a marca da própria igreja (PWA + terreno pra Play Store)

O sistema já é instalável como PWA (Progressive Web App) com o nome/
ícone/cor DA IGREJA — quem tem `Church.logo` cadastrado em
Configurações vê, ao clicar em "Instalar app"/"Adicionar à tela
inicial" no navegador (Chrome/Safari/Edge), o ícone virar o logo real
da igreja, não um ícone genérico do sistema. Nada a configurar no
servidor — funciona automaticamente a partir do que a igreja já
preencheu.

**Publicar de verdade na Google Play Store é OPCIONAL e manual**,
via TWA (Trusted Web Activity — um Android nativo que só abre o PWA em
tela cheia, sem barra de navegador):

1. Instale a [Bubblewrap CLI](https://github.com/GoogleChromeLabs/bubblewrap) do Google (gratuita): `npm i -g @bubblewrap/cli`
2. `bubblewrap init --manifest https://seudominio.com/manifest.json` (rode isso já logado como a igreja, pro manifest vir com o ícone/nome certos)
3. `bubblewrap build` gera o `.aab`/`.apk` assinado — anote o SHA256 do certificado gerado (`bubblewrap` mostra no final, ou `keytool -list -v -keystore android.keystore`)
4. Preencha `Church.android_package_name` (ex.: `br.com.igrejago.nomedaigreja`) e `Church.android_sha256_fingerprint` (o SHA256 do passo 3) no Django admin dessa igreja — isso faz `/.well-known/assetlinks.json` validar o app automaticamente (sem isso, o Android mostra o app com a barra de endereço, não em tela cheia)
5. Suba o `.aab` no [Google Play Console](https://play.google.com/console/) — exige uma conta de desenvolvedor Google (taxa única de US$25)

**Decisão de negócio em aberto, não resolvida aqui**: cada igreja
publica sob a PRÓPRIA conta de desenvolvedor Google (paga a taxa ela
mesma, aparece como "desenvolvedora" do próprio app), ou a plataforma
publica em nome de todas sob uma conta só (a plataforma paga uma vez,
controla a publicação de todo mundo)? Os dois são possíveis com o
terreno já preparado aqui — só decida antes de publicar a primeira.

## 6.2. Revisão de segurança — pendências reais documentadas

Depois de uma revisão de segurança completa (14 achados, corrigidos
nesta rodada — ver histórico do projeto), 2 ficaram deliberadamente
fora do código, por decisão consciente, não por esquecimento:

**Mídia enviada (`/media/`) não tem controle de acesso de verdade.**
A seção 2 acima já orienta servir `/media/` direto pelo Nginx/hospedagem
estática, fora do Django — nenhuma autenticação controla quem acessa um
arquivo já enviado (foto de pessoa, anexo de formulário público, cifra,
áudio de sermão). O nome do arquivo agora é aleatório (UUID —
`core.uploads.random_upload_to`), o que fecha o vetor de "adivinhar a
URL", mas **não é controle de acesso**: quem TEM o link (vazado,
encaminhado, num print de tela) ainda vê o arquivo. Controle de acesso
de verdade exigiria servir mídia através de uma view Django autenticada
(`X-Accel-Redirect` no Nginx, ou equivalente no PythonAnywhere) — mudança
de infraestrutura de produção, fora do escopo desta rodada de correção,
com custo de performance a avaliar.

**Nenhuma credencial de igreja é criptografada em repouso.** Mercado
Pago, PagBank, chave de IA, Meta access token — tudo em `CharField`
texto puro no banco. Um vazamento de backup/dump expõe tudo de uma vez.
Não corrigido nesta rodada de propósito: as credenciais já salvas são
de igrejas reais, em uso ativo processando pagamento de verdade —
criptografar exige uma migração de dado real em produção + uma chave
nova (`ENCRYPTION_KEY`) cuja perda tornaria essas credenciais
inacessíveis (pagamento recorrente/PIX pararia de funcionar). Precisa
de uma rodada própria, dedicada, com plano de rollback testado antes de
tocar em produção — não algo pra fazer numa varredura de várias
correções ao mesmo tempo. Caminho sugerido quando for feito:
`cryptography` (Fernet), um campo customizado (`EncryptedCharField`) em
`core/`, `ENCRYPTION_KEY` nova no `.env` de plataforma, migração que
recriptografa cada valor já salvo com verificação de round-trip antes
de apagar o texto puro.

## 7. Depois de qualquer deploy novo

```bash
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart gunicorn   # ou reload na aba Web do PythonAnywhere
```
