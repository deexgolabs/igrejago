import json
import logging
import re
from datetime import date, datetime, time
from io import StringIO

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.core.management import call_command
from django.db.models import Avg, Count, F, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView, View

from accounts.mixins import CanManagePeopleMixin, IsChurchManagerMixin, IsPlatformOwnerMixin
from core.billing import PLANOS
from core.forms import (
    ChurchConfigForm,
    ChurchOverrideForm,
    ChurchSignupForm,
    CustomReportForm,
    ShortLinkForm,
    WebhookSubscriptionForm,
)
from core.mercadopago_billing import consultar_assinatura, criar_assinatura
from core.models import AuditLog, Church, DataDeletionRequest, ShortLink, WebhookDelivery, WebhookSubscription
from core.ratelimit import RateLimitMixin
from core.reports import generate_general_report_pdf
from core.tenancy import TenantFormMixin
from core.tokens import gerar_token_confirmacao, verificar_token_confirmacao
from people.models import Person

logger = logging.getLogger(__name__)

GESTAO_COMMANDS = {
    "expirar_trials": {
        "label": "Expirar trials vencidos",
        "descricao": "Suspende igrejas cujo trial passou de trial_expira_em sem virar assinatura ativa.",
        "perigo": False,
    },
    "enviar_lembretes": {
        "label": "Enfileirar lembretes do dia",
        "descricao": (
            "Enfileira lembretes de aniversário e reunião de célula de todas as igrejas ativas "
            "(não envia — só enfileira; rode 'Processar fila de WhatsApp' depois pra enviar de fato)."
        ),
        "perigo": False,
    },
    "backup_banco": {
        "label": "Fazer backup agora",
        "descricao": "Copia o banco de dados e zipa a pasta media/ em backups/, mantendo os mais recentes.",
        "perigo": False,
    },
    "verificar_conexao_whatsapp": {
        "label": "Verificar conexões de WhatsApp",
        "descricao": "Checa a instância de cada igreja e avisa por e-mail quem caiu.",
        "perigo": False,
    },
    "processar_fila_whatsapp": {
        "label": "Processar fila de WhatsApp",
        "descricao": "Envia as mensagens pendentes de todas as igrejas, com intervalo entre cada envio.",
        "perigo": True,
        "aviso": (
            "Pode demorar bastante e consumir a cota de CPU do PythonAnywhere se a fila estiver "
            "grande — cada mensagem espera o intervalo configurado da igreja antes da próxima. "
            "Rode em horário de baixo uso e evite clicar de novo enquanto uma execução está em curso."
        ),
    },
    "gerar_escalas_mensais": {
        "label": "Gerar escalas do mês seguinte",
        "descricao": (
            "Sorteia por rodízio os voluntários de cada departamento nos domingos do mês seguinte "
            "e enfileira o aviso de confirmação por WhatsApp. Não roda em cron (mensal) — idempotente: "
            "rodar de novo não duplica escala já gerada ou ajustada manualmente."
        ),
        "perigo": False,
    },
    "processar_automacao_jornada": {
        "label": "Processar automação de jornada",
        "descricao": (
            "Enfileira as mensagens automáticas de acompanhamento de visitante configuradas em "
            "Pessoas → Automação de jornada, pra quem bateu o prazo hoje. Já roda diariamente via "
            "cron — use aqui só pra reprocessar/testar (idempotente, não duplica no mesmo dia)."
        ),
        "perigo": False,
    },
}


class DashboardView(LoginRequiredMixin, TemplateView):
    """Painel do pastor/secretaria (totais, distribuição por cargo/
    departamento, aniversariantes, gráfico de crescimento) para quem pode
    gerenciar pessoas; para um Membro comum, mostra em vez disso o Portal
    do Membro — só os próprios dados, eventos e célula (Módulo 1: "acesso
    restrito apenas para ver seus dados e eventos")."""

    def get(self, request, *args, **kwargs):
        # Dono da plataforma (sem igreja) não tem "portal de membro" nem
        # dashboard de igreja nenhuma pra ver aqui — a home dele é a
        # Gestão da plataforma.
        if request.user.is_authenticated and request.user.is_platform_owner:
            return redirect("core:gestao_dashboard")
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if self.request.user.can_manage_people:
            return ["core/dashboard.html"]
        return ["core/member_portal.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.can_manage_people:
            context.update(self._admin_context())
        else:
            context.update(self._member_context())
        return context

    def _admin_context(self):
        # `Person.objects` (o TenantManager) já filtra sozinho pela
        # igreja do usuário logado — nada aqui precisa de `church=...`.
        today = date.today()
        context = {
            "total_members": Person.objects.filter(is_member=True).count(),
            "total_visitors": Person.objects.filter(is_visitor=True).count(),
            "visitors_this_month": Person.objects.filter(
                is_visitor=True, created_at__year=today.year, created_at__month=today.month
            ).count(),
            "birthdays_this_month": Person.objects.filter(
                birth_date__month=today.month
            ).order_by("birth_date__day"),
            "growth_chart": json.dumps(self._growth_last_6_months(today)),
        }

        role_labels = dict(Person.Role.choices)
        members_by_role = (
            Person.objects.filter(is_member=True)
            .values("role")
            .annotate(total=Count("id"))
            .order_by("-total")
        )
        context["members_by_role"] = [
            {"label": role_labels.get(row["role"], row["role"]), "total": row["total"]}
            for row in members_by_role
        ]
        context["members_by_department"] = (
            Person.objects.filter(is_member=True, department__isnull=False)
            .values("department__name")
            .annotate(total=Count("id"))
            .order_by("-total")
        )
        context["members_by_role_chart"] = json.dumps(context["members_by_role"])
        context["members_by_department_chart"] = json.dumps([
            {"label": row["department__name"], "total": row["total"]}
            for row in context["members_by_department"]
        ])
        context["pipeline_funnel"] = json.dumps(self._pipeline_funnel())
        # Financeiro é mais sensível — só pra quem NÃO é um Líder de
        # Departamento escopado (mesmo corte já usado pra esconder o
        # próprio módulo Financeiro do menu desse perfil).
        if self.request.user.is_unrestricted_manager:
            context.update(self._financeiro_context(today))
        context["confirmacao_escala"] = self._taxa_confirmacao_escala(today)
        return context

    @staticmethod
    def _financeiro_context(today):
        """Total arrecadado no mês + evolução de 6 meses (receita ×
        despesa) — mesma técnica mês a mês de `_growth_last_6_months`,
        só que somando `Transaction.amount` em vez de contar `Person`.
        Puramente agregação nova, nenhum campo/model novo."""
        from finance.models import Transaction

        month_start = today.replace(day=1)
        arrecadado_mes = Transaction.objects.filter(
            type=Transaction.Type.INCOME, date__gte=month_start, date__lte=today,
        ).aggregate(total=Sum("amount"))["total"] or 0

        ticket_medio_doacao = Transaction.objects.filter(
            type=Transaction.Type.INCOME,
            category__in=[Transaction.Category.TITHE, Transaction.Category.OFFERING, Transaction.Category.DONATION],
        ).aggregate(media=Avg("amount"))["media"] or 0

        labels, receitas, despesas = [], [], []
        for i in range(5, -1, -1):
            inicio = month_start - relativedelta(months=i)
            fim = inicio + relativedelta(months=1)
            labels.append(inicio.strftime("%b/%Y"))
            receitas.append(float(
                Transaction.objects.filter(type=Transaction.Type.INCOME, date__gte=inicio, date__lt=fim)
                .aggregate(total=Sum("amount"))["total"] or 0
            ))
            despesas.append(float(
                Transaction.objects.filter(type=Transaction.Type.EXPENSE, date__gte=inicio, date__lt=fim)
                .aggregate(total=Sum("amount"))["total"] or 0
            ))
        return {
            "arrecadado_mes": arrecadado_mes,
            "ticket_medio_doacao": round(ticket_medio_doacao, 2),
            "financeiro_chart": json.dumps({"labels": labels, "receitas": receitas, "despesas": despesas}),
        }

    @staticmethod
    def _taxa_confirmacao_escala(today):
        """% de voluntários já CONFIRMADOS entre as escalas de hoje em
        diante — só conta o que ainda faz sentido cobrar confirmação
        (escala passada não precisa mais)."""
        from escalas.models import EscalaVoluntario

        futuras = EscalaVoluntario.objects.filter(escala__date__gte=today)
        total = futuras.count()
        if not total:
            return None
        confirmados = futuras.filter(status=EscalaVoluntario.Status.CONFIRMED).count()
        return round(confirmados * 100 / total)

    @staticmethod
    def _pipeline_funnel():
        # Sempre as 4 etapas, na ordem do funil (mesmo sem ninguém numa
        # etapa) — pra barra não "sumir" e o gráfico ficar sempre
        # comparável mês a mês.
        stage_labels = dict(Person.PipelineStage.choices)
        counts = dict(
            Person.objects.values("pipeline_stage").annotate(total=Count("id")).values_list(
                "pipeline_stage", "total"
            )
        )
        return [
            {"label": label, "total": counts.get(stage, 0)}
            for stage, label in stage_labels.items()
        ]

    def _member_context(self):
        person = self.request.user.person
        context = {"person": person}
        if person is not None:
            context["my_registrations"] = person.event_registrations.select_related("event").order_by(
                "-registered_at"
            )
            context["my_cells"] = person.cells.all()
        return context

    @staticmethod
    def _growth_last_6_months(today):
        labels, counts = [], []
        for i in range(5, -1, -1):
            month_start = (today.replace(day=1) - relativedelta(months=i))
            month_end = month_start + relativedelta(months=1)
            labels.append(month_start.strftime("%b/%Y"))
            counts.append(
                Person.objects.filter(
                    is_member=True,
                    created_at__gte=timezone.make_aware(datetime.combine(month_start, time.min)),
                    created_at__lt=timezone.make_aware(datetime.combine(month_end, time.min)),
                ).count()
            )
        return {"labels": labels, "counts": counts}


def _relatorio_pessoas(dados):
    """Filtra + agrupa `Person` conforme `CustomReportForm.cleaned_data`
    — função solta (não método) pra ser reaproveitada tanto pela tela
    (`CustomReportView`) quanto pela exportação (`CustomReportExportView`),
    sem duplicar a lógica de filtro/agrupamento entre as duas."""
    qs = Person.objects.all()
    if dados.get("department"):
        qs = qs.filter(department=dados["department"])
    if dados.get("role"):
        qs = qs.filter(role=dados["role"])
    if dados.get("tipo") == "membro":
        qs = qs.filter(is_member=True)
    elif dados.get("tipo") == "visitante":
        qs = qs.filter(is_visitor=True)
    if dados.get("data_inicio"):
        qs = qs.filter(created_at__date__gte=dados["data_inicio"])
    if dados.get("data_fim"):
        qs = qs.filter(created_at__date__lte=dados["data_fim"])

    agrupar_por = dados.get("agrupar_por") or "department"
    if agrupar_por == "department":
        rows = qs.values("department__name").annotate(total=Count("id")).order_by("-total")
        rows = [{"label": r["department__name"] or "Sem departamento", "total": r["total"]} for r in rows]
    elif agrupar_por == "role":
        role_labels = dict(Person.Role.choices)
        rows = qs.values("role").annotate(total=Count("id")).order_by("-total")
        rows = [{"label": role_labels.get(r["role"], r["role"]) or "Sem cargo", "total": r["total"]} for r in rows]
    elif agrupar_por == "mes_cadastro":
        from django.db.models.functions import TruncMonth

        rows = (
            qs.annotate(mes=TruncMonth("created_at")).values("mes")
            .annotate(total=Count("id")).order_by("mes")
        )
        rows = [{"label": r["mes"].strftime("%b/%Y") if r["mes"] else "—", "total": r["total"]} for r in rows]
    else:  # faixa_etaria — não dá pra agrupar isso na query (depende de birth_date × hoje), faz em Python
        faixas = {"0-12": 0, "13-17": 0, "18-25": 0, "26-40": 0, "41-60": 0, "61+": 0, "Sem data de nascimento": 0}
        for person in qs.only("birth_date"):
            idade = person.age
            if idade is None:
                faixas["Sem data de nascimento"] += 1
            elif idade <= 12:
                faixas["0-12"] += 1
            elif idade <= 17:
                faixas["13-17"] += 1
            elif idade <= 25:
                faixas["18-25"] += 1
            elif idade <= 40:
                faixas["26-40"] += 1
            elif idade <= 60:
                faixas["41-60"] += 1
            else:
                faixas["61+"] += 1
        rows = [{"label": k, "total": v} for k, v in faixas.items() if v]

    return rows, sum(r["total"] for r in rows)


class CustomReportView(CanManagePeopleMixin, View):
    """Relatório customizável de Pessoas — filtra e agrupa na hora,
    escopado a Pessoas nesta rodada (caso de uso citado pelo usuário:
    "pessoas por departamento X período X status"). Mesmo mixin de
    `PersonListView` — quem já vê a lista de pessoas já pode gerar
    relatório dela."""

    template_name = "core/custom_report.html"

    def get(self, request):
        form = CustomReportForm(request.GET or None)
        context = {"form": form, "rows": None, "total": None, "chart_data": "[]"}
        if request.GET and form.is_valid():
            rows, total = _relatorio_pessoas(form.cleaned_data)
            context.update({"rows": rows, "total": total, "chart_data": json.dumps(rows)})
        return render(request, self.template_name, context)


class CustomReportExportView(CanManagePeopleMixin, View):
    def get(self, request):
        from openpyxl import Workbook

        form = CustomReportForm(request.GET or None)
        rows, total = _relatorio_pessoas(form.cleaned_data) if form.is_valid() else ([], 0)

        wb = Workbook()
        ws = wb.active
        ws.title = "Relatório"
        ws.append(["Grupo", "Total"])
        for row in rows:
            ws.append([row["label"], row["total"]])
        ws.append(["Total geral", total])
        for column_cells in ws.columns:
            width = max(len(str(cell.value)) for cell in column_cells) + 2
            ws.column_dimensions[column_cells[0].column_letter].width = width

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="relatorio-pessoas.xlsx"'
        wb.save(response)
        return response


class AuditLogListView(IsChurchManagerMixin, ListView):
    """Versão dentro do próprio sistema do que antes só existia no Django
    admin — "quem mudou o quê e quando", sem precisar dar acesso ao admin
    pra alguém só pra isso. Mesmos dados de `core.AuditLog`, só filtro e
    paginação a mais — `AuditLog.objects` já filtra pela igreja logada."""

    model = AuditLog
    template_name = "core/audit_log_list.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related("user")
        model_name = self.request.GET.get("model_name", "")
        if model_name:
            qs = qs.filter(model_name=model_name)
        action = self.request.GET.get("action", "")
        if action:
            qs = qs.filter(action=action)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["model_choices"] = AuditLog.objects.order_by().values_list("model_name", flat=True).distinct()
        context["action_choices"] = AuditLog.Action.choices
        context["current_filters"] = {
            "model_name": self.request.GET.get("model_name", ""),
            "action": self.request.GET.get("action", ""),
        }
        return context


class GeneralReportPDFView(IsChurchManagerMixin, View):
    def get(self, request, *args, **kwargs):
        pdf_bytes = generate_general_report_pdf(request.church)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="relatorio-geral.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response


def health_check(request):
    """Endpoint sem autenticação pra monitoramento externo (uptime robot,
    healthcheck do Docker/Contabo etc.) — confirma que o processo responde
    E que o banco está acessível, não só que o Django subiu. Genérico
    (não é sobre uma igreja específica): só faz uma query trivial."""
    try:
        Church.objects.exists()
        db_ok = True
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "error", "database": db_ok}, status=status)


def manifest_json(request):
    """PWA manifest — servido dinamicamente (não como arquivo estático) só
    pra usar o nome/cor de marca reais da igreja em vez de valores fixos.
    Só é referenciado a partir de `templates/base.html` (área logada), mas
    a rota em si não exige login — sem `request.church` (usuário sem
    igreja, ou hit direto anônimo), cai num manifest genérico.

    Ícone: com `Church.logo` cadastrado, aponta pra `church_icon` (gerado
    na hora a partir do logo real) — sem logo, cai nos PNGs genéricos
    estáticos de sempre. É esse ícone que aparece na tela inicial do
    celular quando a igreja "instala" o sistema — o "app com a marca da
    própria igreja" pedido, sem precisar publicar nada em loja nenhuma."""
    config = getattr(request, "church", None)
    if config and config.logo:
        icons = [
            {"src": reverse("core:church_icon", args=[config.pk, 192]), "sizes": "192x192", "type": "image/png"},
            {"src": reverse("core:church_icon", args=[config.pk, 512]), "sizes": "512x512", "type": "image/png"},
        ]
    else:
        icons = [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ]
    return JsonResponse({
        "name": config.name if config else "Church CRM",
        "short_name": config.name[:12] if config else "Church CRM",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": config.brand_color if config else "#2563eb",
        "icons": icons,
    })


def church_icon(request, church_id, size):
    """Ícone quadrado do PWA gerado a partir de `Church.logo` — a maioria
    dos logos enviados não é quadrada, então encaixa ("contain", sem
    distorcer) num canvas `size×size` com fundo `Church.brand_color`.
    Cacheado (`django.core.cache`, chave inclui o nome do arquivo do
    logo — troca de logo já vira uma chave nova sozinha, sem precisar
    invalidar nada na mão) porque isso roda a cada instalação/atualização
    de manifest, não a cada request normal.

    Sem igreja/logo, devolve 404 — `manifest_json` só aponta pra cá
    quando `config.logo` existe, então isso só aconteceria com uma URL
    montada à mão/desatualizada."""
    from io import BytesIO

    from django.core.cache import cache
    from PIL import Image

    church = get_object_or_404(Church, pk=church_id)
    if not church.logo:
        raise Http404("Esta igreja não tem logo cadastrado.")
    if size not in (192, 512):
        raise Http404("Tamanho de ícone não suportado.")

    cache_key = f"church-icon:{church.pk}:{size}:{church.logo.name}"
    cached = cache.get(cache_key)
    if cached is not None:
        return HttpResponse(cached, content_type="image/png")

    bg = church.brand_color or "#2563eb"
    canvas = Image.new("RGB", (size, size), bg)
    with church.logo.open("rb") as f:
        logo = Image.open(f).convert("RGBA")
        logo.thumbnail((int(size * 0.8), int(size * 0.8)))
        offset = ((size - logo.width) // 2, (size - logo.height) // 2)
        canvas.paste(logo, offset, logo)

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    conteudo = buffer.getvalue()
    cache.set(cache_key, conteudo, timeout=60 * 60 * 24 * 30)  # 30 dias — barato de regerar se expirar
    return HttpResponse(conteudo, content_type="image/png")


def assetlinks_json(request):
    """Digital Asset Links — necessário pro Android validar um app TWA
    (Trusted Web Activity) publicado na Play Store como "dono" deste
    domínio. Terreno pronto, não publicação de verdade: fica vazio até
    uma igreja preencher `android_package_name`/`android_sha256_fingerprint`
    (ver DEPLOY.md pra publicar de verdade). Como só existe UM domínio
    pra todas as igrejas, isso lista TODAS as igrejas que já preencheram
    — cada uma validaria o pacote Android dela própria."""
    igrejas_configuradas = Church.objects.exclude(android_package_name="").exclude(
        android_sha256_fingerprint=""
    )
    entries = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": church.android_package_name,
                "sha256_cert_fingerprints": [church.android_sha256_fingerprint],
            },
        }
        for church in igrejas_configuradas
    ]
    return JsonResponse(entries, safe=False)


def service_worker_js(request):
    """Em `/sw.js` (raiz) de propósito — o *scope* de um service worker é
    limitado ao diretório de onde ele é servido, então servir de dentro de
    /static/ deixaria ele sem cobrir o site inteiro.

    `fetch`: cacheia SÓ estático (`/static/`, ícones, manifest) —
    NUNCA página HTML dinâmica. Isso é deliberado: cachear indiscriminadamente
    incluiria telas com dado pessoal (Pessoas, Financeiro...), e num
    aparelho compartilhado isso poderia mostrar tela de um usuário
    depois que outro logou — risco real de privacidade, não só
    hipotético, num sistema que lida com dado de LGPD. O resto do
    tráfego vai direto pra rede, sem cache — não é um app offline-first
    (é um CRM, depende de dado fresco do servidor), mas ter ESSE fetch
    handler de verdade (em vez do stub vazio de antes) já conta pro
    critério de instalabilidade de PWA."""
    js = (
        "const CACHE = 'church-crm-v1';\n"
        "const CACHEAVEL = /\\/(static|icone)\\//;\n"
        "self.addEventListener('install', e => self.skipWaiting());\n"
        "self.addEventListener('activate', e => {\n"
        "    e.waitUntil(caches.keys().then(keys => Promise.all(\n"
        "        keys.filter(k => k !== CACHE).map(k => caches.delete(k))\n"
        "    )));\n"
        "    self.clients.claim();\n"
        "});\n"
        "self.addEventListener('fetch', e => {\n"
        "    const url = new URL(e.request.url);\n"
        "    if (e.request.method !== 'GET' || url.origin !== self.location.origin || !CACHEAVEL.test(url.pathname)) return;\n"
        "    e.respondWith(\n"
        "        fetch(e.request).then(response => {\n"
        "            const copy = response.clone();\n"
        "            caches.open(CACHE).then(cache => cache.put(e.request, copy));\n"
        "            return response;\n"
        "        }).catch(() => caches.match(e.request))\n"
        "    );\n"
        "});\n"
        "self.addEventListener('push', e => {\n"
        "    let data = {title: 'Aviso', body: '', url: '/'};\n"
        "    try { data = e.data.json(); } catch (err) {}\n"
        "    e.waitUntil(self.registration.showNotification(data.title, {\n"
        "        body: data.body, icon: '/static/icons/icon-192.png', data: {url: data.url},\n"
        "    }));\n"
        "});\n"
        "self.addEventListener('notificationclick', e => {\n"
        "    e.notification.close();\n"
        "    e.waitUntil(clients.openWindow(e.notification.data.url || '/'));\n"
        "});\n"
    )
    return HttpResponse(js, content_type="application/javascript")


class SettingsView(IsChurchManagerMixin, View):
    """Tela de configuração do sistema dentro do próprio app — antes só
    dava pra editar `ChurchConfig` pelo Django admin, depois virou um
    singleton editável in-app; agora edita o registro `Church` (linha) da
    PRÓPRIA igreja logada (`request.church` — `IsChurchManagerMixin` já
    garante que existe). Um `ModelForm` comum (`core.forms.ChurchConfigForm`);
    a conexão do WhatsApp em si (QR code) tem tela própria em
    `notifications.WhatsAppConnectionView` — aqui só ficam os campos de
    texto/configuração."""

    template_name = "core/settings_form.html"

    def get(self, request):
        form = ChurchConfigForm(instance=request.church)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = ChurchConfigForm(request.POST, request.FILES, instance=request.church)
        if form.is_valid():
            form.save()
            messages.success(request, "Configurações salvas.")
            return redirect("core:settings")
        return render(request, self.template_name, {"form": form})


class ChurchSignupView(RateLimitMixin, View):
    """Cadastro público de uma igreja nova (Fase 2 — antes só o dono
    criava pelo admin/shell). A igreja nasce em `trial` (30 dias, acesso
    completo — ver `core.billing`) e já pode ser usada na hora; o e-mail
    de confirmação é enviado em paralelo, sem bloquear o cadastro (só
    bloqueia o envio de WhatsApp dessa igreja até confirmar, ver
    `notifications.views._connection_context`/`processar_fila_whatsapp`)."""

    template_name = "core/church_signup_form.html"
    rate_limit_key = "church_signup"
    rate_limit_max = 10
    rate_limit_window_seconds = 300

    def get(self, request):
        return render(request, self.template_name, {"form": ChurchSignupForm()})

    def post(self, request):
        form = ChurchSignupForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        # Honeypot — mesmo padrão de custom_forms.PublicFormView: finge
        # sucesso sem gravar nada, não dá dica nenhuma de que foi bloqueado.
        if form.cleaned_data["website"]:
            return redirect("core:church_signup_done")

        church, user = form.save()
        enviar_email_confirmacao(request, church, user.email, user.first_name or user.username)

        user.backend = "django.contrib.auth.backends.ModelBackend"
        auth_login(request, user)
        messages.success(
            request,
            f'Bem-vindo(a)! "{church.name}" já está pronta pra usar — confirme seu e-mail '
            "(link enviado agora) pra liberar o envio de WhatsApp.",
        )
        return redirect("core:dashboard")


class ChurchSignupDoneView(TemplateView):
    template_name = "core/church_signup_done.html"


def enviar_email_confirmacao(request, church, to_email, to_name):
    """Manda (ou reenvia — ver `notifications.views.ResendConfirmationEmailView`)
    o e-mail com o link de confirmação de `church`. Nunca derruba quem
    chamou por causa de falha de e-mail (`fail_silently=True` + try/except)."""
    token = gerar_token_confirmacao(church)
    url = request.build_absolute_uri(reverse("core:confirm_email", args=[token]))
    try:
        send_mail(
            subject="Confirme o e-mail da sua igreja — Church CRM",
            message=(
                f"Olá, {to_name}!\n\n"
                f'Confirme o e-mail de "{church.name}" clicando no link abaixo '
                "(válido por 3 dias):\n\n"
                f"{url}\n\n"
                "Enquanto não confirmar, o envio de WhatsApp dessa igreja fica "
                "bloqueado — o resto do sistema já funciona normalmente."
            ),
            from_email=None,
            recipient_list=[to_email],
            fail_silently=True,
        )
    except Exception:
        pass  # nunca derruba quem chamou por causa de e-mail


class ConfirmEmailView(View):
    def get(self, request, token):
        church_pk = verificar_token_confirmacao(token)
        church = get_object_or_404(Church, pk=church_pk) if church_pk else None
        if church is None:
            return render(request, "core/confirm_email_invalid.html", status=400)
        if not church.email_confirmed:
            church.email_confirmed = True
            church.save(update_fields=["email_confirmed"])
        return render(request, "core/confirm_email_done.html", {"church": church})


class PrivacyPolicyView(TemplateView):
    """Página pública de política de privacidade (LGPD, Fase 3) —
    linkada nas 3 checkboxes de consentimento (`core.lgpd.privacy_consent_label`)
    e no rodapé de `public_base.html`."""

    template_name = "core/privacy_policy.html"


class ManualView(LoginRequiredMixin, TemplateView):
    """Manual de configuração e uso do sistema, dentro do próprio app —
    não é uma página solta. Seções de "Gestão da plataforma"/domínios só
    aparecem pra quem é dono da plataforma (`user.is_platform_owner`);
    o resto é igual pra qualquer conta logada, independente de cargo."""

    template_name = "core/manual.html"


class ContaSuspensaView(TemplateView):
    """Tela mostrada quando `Church.esta_bloqueada` (trial vencido sem
    assinar, ou assinatura cancelada/pagamento falhou — ver
    `core.middleware.TenantMiddleware`). Pública de propósito: precisa
    renderizar mesmo para quem acabou de ser redirecionado pra cá."""

    template_name = "core/conta_suspensa.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["church"] = getattr(self.request, "church", None)
        return context


class MeusDadosView(LoginRequiredMixin, TemplateView):
    """Autoatendimento LGPD no Portal do Membro (Fase 3) — baixar os
    próprios dados ou solicitar exclusão. `person` pode ser `None`
    (conta ainda não vinculada a um cadastro), a tela trata isso."""

    template_name = "core/meus_dados.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = self.request.user.person
        context["person"] = person
        if person is not None:
            context["pending_deletion_request"] = DataDeletionRequest.objects.filter(
                person=person, status=DataDeletionRequest.Status.PENDING
            ).exists()
        return context


class MeusDadosExportView(LoginRequiredMixin, View):
    """JSON com os próprios dados — portabilidade (LGPD) é um formato
    machine-readable, não um PDF pra ler; inclui os campos da própria
    `Person` mais as próprias inscrições em evento e doações."""

    def get(self, request):
        person = request.user.person
        if person is None:
            raise Http404
        data = {
            "nome_completo": person.full_name,
            "telefone": person.phone,
            "email": person.email,
            "data_nascimento": str(person.birth_date) if person.birth_date else None,
            "endereco": person.address,
            "cidade": person.city,
            "estado": person.state,
            "cep": person.zip_code,
            "sexo": person.get_gender_display() if person.gender else None,
            "estado_civil": person.get_marital_status_display() if person.marital_status else None,
            "cargo": person.get_role_display(),
            "status": person.get_status_display(),
            "membro_desde": str(person.member_since) if person.member_since else None,
            "cadastrado_em": person.created_at.isoformat(),
            "consentimento_lgpd_em": person.privacy_consent_at.isoformat() if person.privacy_consent_at else None,
            "inscricoes_em_eventos": [
                {
                    "evento": r.event.title,
                    "status_pagamento": r.get_payment_status_display(),
                    "inscrito_em": r.registered_at.isoformat(),
                }
                for r in person.event_registrations.select_related("event")
            ],
            "doacoes": [
                {"valor": str(d.amount), "status": d.get_status_display(), "criado_em": d.created_at.isoformat()}
                for d in person.donations.all()
            ],
        }
        response = JsonResponse(data, json_dumps_params={"ensure_ascii": False, "indent": 2})
        response["Content-Disposition"] = 'attachment; filename="meus-dados.json"'
        response["Cache-Control"] = "private, no-store"
        return response


class SolicitarExclusaoView(LoginRequiredMixin, View):
    """Cria a solicitação (`core.DataDeletionRequest`) — nunca deleta
    nada sozinho, vira fila pra secretaria confirmar
    (`DataDeletionRequestProcessView`). Trava contra duplicar pedido."""

    def post(self, request):
        person = request.user.person
        if person is None:
            messages.error(request, "Sua conta não está vinculada a um cadastro de pessoa.")
            return redirect("core:meus_dados")
        ja_pendente = DataDeletionRequest.objects.filter(
            person=person, status=DataDeletionRequest.Status.PENDING
        ).exists()
        if ja_pendente:
            messages.info(request, "Você já tem uma solicitação de exclusão pendente.")
        else:
            DataDeletionRequest.objects.create(
                church=request.church, person=person, person_name=person.full_name,
            )
            messages.success(request, "Solicitação registrada — a secretaria vai processar em breve.")
        return redirect("core:meus_dados")


class DataDeletionRequestListView(IsChurchManagerMixin, ListView):
    """Fila de solicitações de exclusão pendentes, pra secretaria
    processar (`DataDeletionRequestProcessView`)."""

    model = DataDeletionRequest
    template_name = "core/data_deletion_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        return DataDeletionRequest.objects.filter(
            status=DataDeletionRequest.Status.PENDING
        ).select_related("person")


class DataDeletionRequestProcessView(IsChurchManagerMixin, View):
    """Confirma a exclusão — apaga a `Person` de verdade (ação
    destrutiva, por isso passa por essa tela em vez de acontecer sozinha
    quando a pessoa pede) e marca a solicitação `DONE`."""

    def post(self, request, pk):
        deletion_request = get_object_or_404(
            DataDeletionRequest, pk=pk, status=DataDeletionRequest.Status.PENDING
        )
        if deletion_request.person is not None:
            deletion_request.person.delete()
            # `.delete()` zera o pk do objeto Python em memória — salvar a
            # FK ainda apontando pra esse objeto agora "sem pk" é
            # bloqueado pelo Django (`prohibited to prevent data loss`).
            # Como o campo já é `SET_NULL`, isso só antecipa em memória o
            # que o banco já fez de verdade na exclusão.
            deletion_request.person = None
        deletion_request.status = DataDeletionRequest.Status.DONE
        deletion_request.processed_by = request.user
        deletion_request.processed_at = timezone.now()
        deletion_request.save(update_fields=["status", "processed_by", "processed_at"])
        messages.success(request, f"Dados de {deletion_request.person_name} excluídos.")
        return redirect("core:data_deletion_requests")


class AssinaturaView(IsChurchManagerMixin, View):
    """Status da assinatura da própria igreja + botões pra assinar um
    plano (Fase 4). Controle manual pelo dono continua funcionando em
    paralelo — esta tela só cobre o caminho automático."""

    template_name = "core/assinatura.html"

    def get(self, request):
        return render(request, self.template_name, {"planos": PLANOS, "church": request.church})


class AssinaturaCheckoutView(IsChurchManagerMixin, View):
    def post(self, request, plano_key):
        if plano_key not in PLANOS:
            messages.error(request, "Plano inválido.")
            return redirect("core:assinatura")
        if not settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN:
            messages.error(request, "Cobrança automática ainda não está configurada — fale com o suporte.")
            return redirect("core:assinatura")

        base_url = request.build_absolute_uri("/")[:-1]
        try:
            preapproval_id, checkout_url = criar_assinatura(
                access_token=settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN,
                plano_key=plano_key, plano_info=PLANOS[plano_key], church=request.church,
                payer_email=request.user.email or "sememail@example.com",
                back_url=base_url + reverse("core:assinatura"),
                notification_url=base_url + reverse("core:assinatura_webhook"),
            )
        except Exception:
            logger.exception("Falha ao criar assinatura no Mercado Pago para a igreja %s", request.church.pk)
            messages.error(request, "Não foi possível iniciar a assinatura agora. Tente novamente mais tarde.")
            return redirect("core:assinatura")

        return redirect(checkout_url)


_EXTERNAL_REFERENCE_RE = re.compile(r"^CHURCH-(\d+)-(\w+)$")


@method_decorator(csrf_exempt, name="dispatch")
class AssinaturaWebhookView(View):
    """Webhook da assinatura — chamado pelo Mercado Pago sem usuário
    logado. A igreja/plano vêm do `external_reference`
    (`CHURCH-<pk>-<plano>`) embutido por NÓS ao criar a assinatura
    (`criar_assinatura`), não de nada que o Mercado Pago decida sozinho.
    Sempre reconsulta a API antes de mudar `Church.status` — nunca confia
    no corpo do POST."""

    def post(self, request):
        preapproval_id = request.GET.get("data.id") or request.GET.get("id")
        if not preapproval_id:
            return HttpResponseBadRequest("missing preapproval id")
        if not settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN:
            return HttpResponseBadRequest("billing not configured")

        try:
            data = consultar_assinatura(
                access_token=settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN, preapproval_id=preapproval_id
            )
        except Exception:
            logger.exception("Falha ao reconsultar assinatura %s no Mercado Pago", preapproval_id)
            return HttpResponse(status=502)

        match = _EXTERNAL_REFERENCE_RE.match(data.get("external_reference", ""))
        if not match:
            return HttpResponse(status=200)  # não é uma referência nossa — ignora sem erro
        church = Church.objects.filter(pk=match.group(1)).first()
        if church is None:
            return HttpResponse(status=200)
        plano_key = match.group(2)

        status = data.get("status")
        if status == "authorized":
            church.status = Church.Status.ACTIVE
            church.plano = plano_key
            church.gateway_subscription_id = preapproval_id
            church.save(update_fields=["status", "plano", "gateway_subscription_id"])
        elif status in ("cancelled", "paused"):
            church.status = Church.Status.SUSPENDED
            church.save(update_fields=["status"])
        return HttpResponse(status=200)


class GestaoDashboardView(IsPlatformOwnerMixin, TemplateView):
    """Home do dono da plataforma — visão geral de todas as igrejas
    (contagem por status, MRR estimado, trials perto de vencer,
    WhatsApp desconectado). `Church` não é `TenantModel`, então
    `Church.objects` aqui já é sempre "todas as igrejas", sem precisar
    de nenhum `todas_as_igrejas`."""

    template_name = "core/gestao/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status_labels = dict(Church.Status.choices)
        by_status = Church.objects.values("status").annotate(total=Count("id")).order_by("status")
        context["churches_by_status"] = [
            {"status": row["status"], "label": status_labels.get(row["status"], row["status"]), "total": row["total"]}
            for row in by_status
        ]
        context["total_churches"] = Church.objects.count()

        limite = date.today() + relativedelta(days=14)
        context["trials_expiring_soon"] = Church.objects.filter(
            status=Church.Status.TRIAL, trial_expira_em__isnull=False, trial_expira_em__lte=limite,
        ).order_by("trial_expira_em")

        # MRR estimado: só igrejas ATIVAS com um plano reconhecido — trial
        # não paga nada ainda, e um `plano` em branco/desconhecido (dado
        # legado ou digitado errado no admin) não entra na conta em vez de
        # quebrar a página.
        mrr = 0
        planos_ativos = Church.objects.filter(status=Church.Status.ACTIVE).values("plano").annotate(total=Count("id"))
        for row in planos_ativos:
            info = PLANOS.get(row["plano"])
            if info:
                mrr += info["preco"] * row["total"]
        context["mrr_estimado"] = mrr

        # Lê o flag que `verificar_conexao_whatsapp` já mantém, em vez de
        # checar a API de cada igreja ao vivo aqui — isso seria lento e
        # gastaria a mesma cota de CPU que a tela de comandos existe pra
        # poupar. Reflete a última vez que aquele comando rodou. Conta
        # IGREJAS (não instâncias) — uma igreja com 2 números e só 1
        # desconectado ainda entra aqui, é sinal de atenção mesmo assim.
        from notifications.models import WhatsAppInstance

        context["whatsapp_disconnected_count"] = (
            WhatsAppInstance.todas_as_igrejas.filter(disconnect_alert_sent=True).values("church").distinct().count()
        )
        return context


class GestaoChurchListView(IsPlatformOwnerMixin, ListView):
    """Lista de todas as igrejas, com filtro/busca — mesmo padrão de
    `AuditLogListView`."""

    model = Church
    template_name = "core/gestao/church_list.html"
    context_object_name = "churches"
    paginate_by = 50

    def get_queryset(self):
        qs = Church.objects.all().order_by("-created_at")
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        plano = self.request.GET.get("plano", "")
        if plano:
            qs = qs.filter(plano=plano)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(pastor_name__icontains=q) | Q(slug__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = Church.Status.choices
        context["plano_choices"] = Church.Plano.choices
        context["current_filters"] = {
            "status": self.request.GET.get("status", ""),
            "plano": self.request.GET.get("plano", ""),
            "q": self.request.GET.get("q", ""),
        }
        return context


class GestaoChurchDetailView(IsPlatformOwnerMixin, View):
    """Detalhe de uma igreja + ajuste manual de status/plano/trial (casos
    de suporte). Campos técnicos (WhatsApp/PIX/Mercado Pago) continuam só
    no Django admin — aqui só o link pra lá."""

    template_name = "core/gestao/church_detail.html"

    def _context(self, request, church, form):
        return {
            "church": church,
            "form": form,
            # `Person.objects`/etc. não filtram sozinhos aqui — o dono da
            # plataforma tem `current_church = None`, que pro `TenantManager`
            # significa "sem filtro" (todas as igrejas). Tem que filtrar
            # explicitamente por ESTA igreja, senão os números somam tudo.
            "total_pessoas": Person.objects.filter(church=church).count(),
            "total_usuarios": church.users.count(),
            "admin_url": reverse("admin:core_church_change", args=[church.pk]),
        }

    def get(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        form = ChurchOverrideForm(instance=church)
        return render(request, self.template_name, self._context(request, church, form))

    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        form = ChurchOverrideForm(request.POST, instance=church)
        if form.is_valid():
            form.save()
            messages.success(request, "Dados da igreja atualizados.")
            return redirect("core:gestao_church_detail", pk=church.pk)
        return render(request, self.template_name, self._context(request, church, form))


class GestaoChurchDeleteView(IsPlatformOwnerMixin, View):
    """Exclusão definitiva de uma igreja — caso de suporte pra cadastro
    abandonado/de teste (ex.: igreja que nunca confirmou o e-mail porque
    a caixa de entrada estava cheia, e por isso nunca fica ativa nem some
    sozinha). Exige digitar o slug da igreja pra confirmar (ação
    irreversível: apaga TODOS os dados — pessoas, eventos, financeiro,
    mensagens etc. — em cascata, ver `Church.delete()`)."""

    template_name = "core/gestao/church_confirm_delete.html"

    def get(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        return render(request, self.template_name, {
            "church": church,
            "total_pessoas": Person.objects.filter(church=church).count(),
            "total_usuarios": church.users.count(),
        })

    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        if request.POST.get("confirmacao", "").strip() != church.slug:
            messages.error(request, "Slug digitado não confere — igreja NÃO foi excluída.")
            return redirect("core:gestao_church_delete", pk=church.pk)
        nome = church.name
        church.delete()
        messages.success(request, f'Igreja "{nome}" excluída definitivamente.')
        return redirect("core:gestao_church_list")


class GestaoCommandsView(IsPlatformOwnerMixin, TemplateView):
    """Lista os comandos de manutenção pra rodar manualmente — o
    PythonAnywhere free tier não tem *scheduled tasks*, então isso
    substitui o cron enquanto não houver upgrade de plano."""

    template_name = "core/gestao/commands.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["commands"] = GESTAO_COMMANDS
        # `.pop()`, não `.get()`: mostra o resultado uma vez só, logo depois
        # do redirect que rodou o comando — some se a página for recarregada.
        context["last_run"] = self.request.session.pop("gestao_command_output", None)
        return context


class GestaoCommandRunView(IsPlatformOwnerMixin, View):
    """Roda um `management command` síncrono, na própria request. Sem
    fila assíncrona — pra comandos pesados (`processar_fila_whatsapp`)
    isso pode demorar/estourar a cota de CPU do PythonAnywhere; o aviso
    fica só na tela (`GESTAO_COMMANDS[...]["aviso"]`), não é resolvido
    com infraestrutura nova aqui."""

    def post(self, request, command_name):
        if command_name not in GESTAO_COMMANDS:
            raise Http404
        out, err = StringIO(), StringIO()
        try:
            call_command(command_name, stdout=out, stderr=err)
            ok = True
        except Exception as exc:
            logger.exception("Falha ao rodar comando de gestão: %s", command_name)
            err.write(str(exc))
            ok = False
        output = (out.getvalue() + err.getvalue()).strip()
        request.session["gestao_command_output"] = {
            "command": command_name,
            "label": GESTAO_COMMANDS[command_name]["label"],
            "ok": ok,
            "output": output,
            "ran_at": timezone.now().isoformat(),
        }
        return redirect("core:gestao_commands")


def short_link_redirect(request, slug):
    """Resolve `igrejago.link/<slug>` — rota PÚBLICA registrada por
    ÚLTIMO em `core/urls.py` (só entra em jogo quando nada mais bateu
    antes, nunca disputa com uma rota real). Usa `todas_as_igrejas`
    (não `ShortLink.objects`) de propósito: aqui não tem igreja logada
    nem slug de igreja na própria URL pra restringir o manager — é
    assim mesmo pra uma página pública, ver `core.tenancy.TenantManager`.
    `no-store`: o destino pode ser editado depois de criado, então o
    redirect em si não pode ficar em cache (mesmo raciocínio dos PDFs
    pessoais, ver `finance.views`)."""
    link = get_object_or_404(ShortLink.todas_as_igrejas, slug=slug)
    ShortLink.todas_as_igrejas.filter(pk=link.pk).update(click_count=F("click_count") + 1)
    response = redirect(link.target_path)
    response["Cache-Control"] = "no-store"
    return response


class ShortLinkListView(IsChurchManagerMixin, ListView):
    model = ShortLink
    template_name = "core/shortlink_list.html"
    context_object_name = "shortlinks"


class ShortLinkCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    """`?slug=&label=&target_path=` na URL pré-preenchem o formulário —
    usado pelos atalhos "Criar link curto" de Link na Bio, Formulários e
    Eventos, que já sabem o caminho de destino e só pedem confirmação/
    ajuste do slug antes de gravar."""

    model = ShortLink
    form_class = ShortLinkForm
    template_name = "core/shortlink_form.html"
    success_url = reverse_lazy("core:shortlink_list")

    def get_initial(self):
        initial = super().get_initial()
        for key in ("slug", "label", "target_path"):
            if key in self.request.GET:
                initial[key] = self.request.GET[key]
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Link curto criado.")
        return super().form_valid(form)


class ShortLinkUpdateView(IsChurchManagerMixin, UpdateView):
    model = ShortLink
    form_class = ShortLinkForm
    template_name = "core/shortlink_form.html"
    success_url = reverse_lazy("core:shortlink_list")

    def form_valid(self, form):
        messages.success(self.request, "Link curto atualizado.")
        return super().form_valid(form)


class ShortLinkDeleteView(IsChurchManagerMixin, DeleteView):
    model = ShortLink
    template_name = "core/shortlink_confirm_delete.html"
    success_url = reverse_lazy("core:shortlink_list")

    def form_valid(self, form):
        messages.success(self.request, "Link curto removido.")
        return super().form_valid(form)


class SwitchChurchView(LoginRequiredMixin, View):
    """Troca a UNIDADE ativa (matriz ↔ filial) pra quem administra uma
    rede de igrejas — grava `active_church_id` na sessão, lido por
    `core.middleware.TenantMiddleware` a cada request daqui pra frente.
    Só aceita a própria igreja do usuário ou uma filial DELA — nunca uma
    igreja arbitrária (evita virar um jeito de "invadir" outro tenant)."""

    def post(self, request):
        user_church = request.user.church
        if user_church is None:
            return redirect("core:dashboard")
        try:
            target_id = int(request.POST.get("church_id", ""))
        except ValueError:
            messages.error(request, "Unidade inválida.")
            return redirect("core:dashboard")

        if target_id == user_church.pk:
            request.session.pop("active_church_id", None)
        elif Church.objects.filter(pk=target_id, matriz_id=user_church.pk).exists():
            request.session["active_church_id"] = target_id
        else:
            messages.error(request, "Você não tem acesso a essa unidade.")
        return redirect("core:dashboard")


class ChurchNetworkListView(IsChurchManagerMixin, View):
    """Lista as filiais da PRÓPRIA igreja do usuário (`request.user.church`
    — não `request.church`, que pode estar trocado pra dentro de uma
    filial no momento; a rede em si é sempre vista a partir da matriz)."""

    template_name = "core/church_network_list.html"

    def get(self, request):
        matriz = request.user.church
        filiais = matriz.filiais.all() if matriz else Church.objects.none()
        return render(request, self.template_name, {"matriz": matriz, "filiais": filiais})


class ChurchNetworkCreateView(IsChurchManagerMixin, View):
    """Cria uma filial nova — reaproveita `ChurchSignupForm` (mesmo
    formulário do cadastro público de igreja), só passando `matriz=` na
    hora de salvar. Cria a igreja E o primeiro usuário (Pastor) dela
    numa tacada só, sem o e-mail de confirmação do fluxo público (quem
    está criando já está autenticado e sabe o que está fazendo)."""

    template_name = "core/church_network_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": ChurchSignupForm()})

    def post(self, request):
        form = ChurchSignupForm(request.POST)
        if form.is_valid():
            filial, user = form.save(matriz=request.user.church)
            messages.success(
                request,
                f'Filial "{filial.name}" criada — peça pro responsável entrar com o usuário '
                f'"{user.username}" e a senha cadastrada.',
            )
            return redirect("core:church_network_list")
        return render(request, self.template_name, {"form": form})


class ChurchNetworkDashboardView(IsChurchManagerMixin, View):
    """Relatório consolidado da rede (matriz + filiais) — pessoas e
    receita do mês, somadas. Usa `todas_as_igrejas` DELIBERADAMENTE (não
    `Model.objects`, que filtraria só pela igreja atual): é exatamente o
    caso de "relatório cross-tenant intencional" que
    `core.tenancy.TenantManager` documenta como motivo válido pra usar o
    manager sem filtro, fora do padrão normal de código autenticado."""

    template_name = "core/church_network_dashboard.html"

    def get(self, request):
        from finance.models import Transaction
        from people.models import Person

        matriz = request.user.church
        rede = [matriz] + list(matriz.filiais.all()) if matriz else []
        hoje = timezone.now().date()

        linhas = []
        for church in rede:
            pessoas = Person.todas_as_igrejas.filter(church=church).count()
            receita_mes = Transaction.todas_as_igrejas.filter(
                church=church, type="INCOME", date__year=hoje.year, date__month=hoje.month,
            ).aggregate(total=Sum("amount"))["total"] or 0
            linhas.append({"church": church, "pessoas": pessoas, "receita_mes": receita_mes})

        context = {
            "linhas": linhas,
            "total_pessoas": sum(linha["pessoas"] for linha in linhas),
            "total_receita_mes": sum(linha["receita_mes"] for linha in linhas),
        }
        return render(request, self.template_name, context)


class WebhookSubscriptionListView(IsChurchManagerMixin, ListView):
    model = WebhookSubscription
    template_name = "core/webhook_list.html"
    context_object_name = "subscriptions"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["recent_deliveries"] = WebhookDelivery.objects.select_related("subscription").order_by("-created_at")[:20]
        return context


class WebhookSubscriptionCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = WebhookSubscription
    form_class = WebhookSubscriptionForm
    template_name = "core/webhook_form.html"
    success_url = reverse_lazy("core:webhook_list")

    def form_valid(self, form):
        messages.success(self.request, "Webhook cadastrado.")
        return super().form_valid(form)


class WebhookSubscriptionDeleteView(IsChurchManagerMixin, DeleteView):
    model = WebhookSubscription
    template_name = "core/webhook_confirm_delete.html"
    success_url = reverse_lazy("core:webhook_list")

    def form_valid(self, form):
        messages.success(self.request, "Webhook removido.")
        return super().form_valid(form)


class GenerateApiKeyView(IsChurchManagerMixin, View):
    """Gera (ou regenera) a chave de API da igreja — botão em
    Configurações. Regenerar invalida a chave anterior na hora (não tem
    "período de graça"): qualquer integração usando a chave antiga
    passa a receber 401 imediatamente."""

    def post(self, request):
        import secrets

        request.church.api_key = secrets.token_hex(32)
        request.church.save(update_fields=["api_key"])
        messages.success(request, "Nova chave de API gerada.")
        return redirect("core:settings")
