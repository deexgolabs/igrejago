"""Relatório geral em PDF via reportlab — não WeasyPrint, que exige GTK
instalado à parte no Windows (documentado como fricção conhecida; reportlab
não tem essa dependência de sistema e é o que outros projetos deste
workspace, como o crm-odonto, já usam com sucesso)."""

import io
from datetime import date

from django.db.models import Q, Sum
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cells.models import Cell
from events.models import Event
from finance.models import Transaction
from people.models import Person


def generate_general_report_pdf(church_config):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(church_config.name or "Church CRM", styles["Title"]))
    story.append(Paragraph(f"Relatório geral — gerado em {date.today().strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Spacer(1, 1 * cm))

    story.append(Paragraph("Pessoas", styles["Heading2"]))
    total_members = Person.objects.filter(is_member=True).count()
    total_visitors = Person.objects.filter(is_visitor=True).count()
    people_table = [["Total de membros", str(total_members)], ["Total de visitantes", str(total_visitors)]]
    story.append(_styled_table(people_table))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("Eventos", styles["Heading2"]))
    events_table = [["Eventos publicados", str(Event.objects.filter(status=Event.EventStatus.PUBLISHED).count())],
                     ["Total de inscrições", str(sum(e.registrations.count() for e in Event.objects.all()))]]
    story.append(_styled_table(events_table))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("Financeiro (mês atual)", styles["Heading2"]))
    today = date.today()
    month_qs = Transaction.objects.filter(date__year=today.year, date__month=today.month)
    totals = month_qs.aggregate(
        income=Sum("amount", filter=Q(type=Transaction.Type.INCOME)),
        expense=Sum("amount", filter=Q(type=Transaction.Type.EXPENSE)),
    )
    income = totals["income"] or 0
    expense = totals["expense"] or 0
    finance_table = [
        ["Entradas", f"R$ {income}"], ["Saídas", f"R$ {expense}"], ["Saldo", f"R$ {income - expense}"],
    ]
    story.append(_styled_table(finance_table))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("Células", styles["Heading2"]))
    cells_table = [["Células ativas", str(Cell.objects.filter(is_active=True).count())]]
    story.append(_styled_table(cells_table))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_donation_receipt_pdf(church_config, donation):
    """Recibo simples de uma doação já confirmada (PIX ou Mercado Pago) —
    não é nota fiscal nem tem valor jurídico de recibo de doação dedutível
    (isso exigiria CNPJ/regras fiscais fora do escopo daqui), só um
    comprovante pra quem doou guardar."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=3 * cm, bottomMargin=3 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(church_config.name or "Church CRM", styles["Title"]))
    story.append(Paragraph("Recibo de doação", styles["Heading2"]))
    story.append(Spacer(1, 1 * cm))

    doador = donation.person.full_name if donation.person else "Doador não identificado"
    rows = [
        ["Doador", doador],
        ["Valor", f"R$ {donation.amount}"],
        ["Data", donation.created_at.strftime("%d/%m/%Y")],
        ["Referência", donation.payment_reference or f"#{donation.pk}"],
    ]
    story.append(_styled_table(rows))
    story.append(Spacer(1, 1.2 * cm))
    story.append(Paragraph(
        f"Comprovante gerado em {date.today().strftime('%d/%m/%Y')} — não substitui recibo fiscal.",
        styles["Normal"],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_annual_donation_receipt_pdf(church_config, person, year):
    """Recibo ANUAL consolidado de contribuições — soma tudo que a pessoa
    deu no ano (dízimo, oferta, doação avulsa — via `Transaction`, que já
    é gerado automaticamente tanto pro caminho manual quanto pelo webhook
    do Mercado Pago, ver `finance.views`), pra guardar/declarar no Imposto
    de Renda. Mesmo aviso de `generate_donation_receipt_pdf`: não é nota
    fiscal nem recibo dedutível de verdade, é só um comprovante."""
    lancamentos = Transaction.objects.filter(
        person=person, type=Transaction.Type.INCOME,
        category__in=[Transaction.Category.TITHE, Transaction.Category.OFFERING, Transaction.Category.DONATION],
        date__year=year,
    ).order_by("date")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=3 * cm, bottomMargin=3 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(church_config.name or "Church CRM", styles["Title"]))
    story.append(Paragraph(f"Recibo anual de contribuições — {year}", styles["Heading2"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(f"Contribuinte: {person.full_name}", styles["Normal"]))
    story.append(Spacer(1, 1 * cm))

    category_labels = dict(Transaction.Category.choices)
    rows = [["Data", "Categoria", "Valor (R$)"]]
    total = 0
    for lancamento in lancamentos:
        rows.append([
            lancamento.date.strftime("%d/%m/%Y"),
            category_labels.get(lancamento.category, lancamento.category),
            f"{lancamento.amount:.2f}",
        ])
        total += lancamento.amount
    rows.append(["", "Total", f"{total:.2f}"])
    story.append(_styled_table(rows))

    story.append(Spacer(1, 1.2 * cm))
    story.append(Paragraph(
        f"Comprovante gerado em {date.today().strftime('%d/%m/%Y')} — não substitui recibo fiscal "
        "nem tem valor jurídico de doação dedutível; é um registro pra sua própria guarda.",
        styles["Normal"],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_dre_pdf(church_config, inicio, fim):
    """DRE (Demonstração de Resultado) do período — agrupa os
    lançamentos já existentes por `finance.dre.DRE_GROUPS` (Receitas ×
    Despesas operacionais); resultado = receitas - despesas (superávit
    se positivo, déficit se negativo). Ver docstring de `finance/dre.py`
    pro porquê de não ser um plano de contas contábil de verdade."""
    from finance.dre import dre_breakdown

    breakdown = dre_breakdown(church_config, inicio, fim)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(church_config.name or "Church CRM", styles["Title"]))
    story.append(Paragraph(
        f"DRE — {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}", styles["Heading2"],
    ))
    story.append(Spacer(1, 1 * cm))

    for grupo in breakdown["grupos"]:
        story.append(Paragraph(grupo["nome"], styles["Heading3"]))
        rows = [[label, f"R$ {total:.2f}"] for label, total in grupo["categorias"]] or [["—", "R$ 0,00"]]
        rows.append(["Subtotal", f"R$ {grupo['total']:.2f}"])
        story.append(_styled_table(rows))
        story.append(Spacer(1, 0.5 * cm))

    resultado_label = "Resultado do período (superávit)" if breakdown["resultado"] >= 0 else "Resultado do período (déficit)"
    story.append(_styled_table([
        ["Total de receitas", f"R$ {breakdown['receitas']:.2f}"],
        ["Total de despesas", f"R$ {breakdown['despesas']:.2f}"],
        [resultado_label, f"R$ {breakdown['resultado']:.2f}"],
    ]))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        f"Gerado em {date.today().strftime('%d/%m/%Y')} — agrupamento simplificado sobre as categorias "
        "de lançamento já existentes, não substitui uma DRE contábil formal.",
        styles["Normal"],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_dre_contabil_pdf(church_config, inicio, fim):
    """Mesmo DRE de `generate_dre_pdf`, mas agrupado pelo plano de
    contas configurável da igreja (`finance.dre.dre_por_conta_contabil`)
    em vez da categoria fixa — ver docstring de lá."""
    from finance.dre import dre_por_conta_contabil

    breakdown = dre_por_conta_contabil(church_config, inicio, fim)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(church_config.name or "Church CRM", styles["Title"]))
    story.append(Paragraph(
        f"DRE por plano de contas — {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}", styles["Heading2"],
    ))
    story.append(Spacer(1, 1 * cm))

    for grupo in breakdown["grupos"]:
        story.append(Paragraph(grupo["nome"], styles["Heading3"]))
        rows = [[label, f"R$ {total:.2f}"] for label, total in grupo["contas"]] or [["—", "R$ 0,00"]]
        rows.append(["Subtotal", f"R$ {grupo['total']:.2f}"])
        story.append(_styled_table(rows))
        story.append(Spacer(1, 0.5 * cm))

    resultado_label = "Resultado do período (superávit)" if breakdown["resultado"] >= 0 else "Resultado do período (déficit)"
    story.append(_styled_table([
        ["Total de receitas", f"R$ {breakdown['receitas']:.2f}"],
        ["Total de despesas", f"R$ {breakdown['despesas']:.2f}"],
        [resultado_label, f"R$ {breakdown['resultado']:.2f}"],
    ]))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        f"Gerado em {date.today().strftime('%d/%m/%Y')} — agrupado pelo plano de contas da igreja; "
        "grupos Ativo/Passivo/Patrimônio líquido não entram no resultado (o sistema não rastreia saldo "
        "de ativo/passivo de verdade).",
        styles["Normal"],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_balancete_pdf(church_config, inicio, fim):
    """"Balancete" simplificado — saldo acumulado mês a mês (ver
    `finance.dre.saldo_acumulado` pro porquê de não ser um balanço
    patrimonial de verdade: o sistema não rastreia conta bancária/
    ativo/passivo, só entradas e saídas)."""
    from finance.dre import saldo_acumulado

    dados = saldo_acumulado(church_config, inicio, fim)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(church_config.name or "Church CRM", styles["Title"]))
    story.append(Paragraph(
        f"Saldo acumulado — {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}", styles["Heading2"],
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(f"Saldo de abertura (tudo antes do período): R$ {dados['abertura']:.2f}", styles["Normal"]))
    story.append(Spacer(1, 0.8 * cm))

    rows = [["Mês", "Entradas", "Saídas", "Saldo do mês", "Saldo acumulado"]]
    for linha in dados["meses"]:
        rows.append([
            linha["mes"].strftime("%m/%Y"), f"R$ {linha['entradas']:.2f}", f"R$ {linha['saidas']:.2f}",
            f"R$ {linha['saldo_mes']:.2f}", f"R$ {linha['saldo_acumulado']:.2f}",
        ])
    table = Table(rows)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f8fafc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(f"Saldo final: R$ {dados['saldo_final']:.2f}", styles["Heading3"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        f"Gerado em {date.today().strftime('%d/%m/%Y')} — saldo acumulado de entradas menos saídas, não é "
        "um balanço patrimonial (ativo/passivo/patrimônio líquido) formal.",
        styles["Normal"],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def _styled_table(data):
    table = Table(data, colWidths=[8 * cm, 6 * cm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table
