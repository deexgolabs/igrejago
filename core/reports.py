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
