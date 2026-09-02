"""Camada de agrupamento por cima de `Transaction.Category` — NÃO um
plano de contas contábil de verdade (não existe nenhum model de conta
configurável no sistema hoje; migrar pra isso tocaria filtro/orçamento/
export/gráfico em cascata, fora de escopo desta rodada). `DRE_GROUPS`
só agrupa os 11 valores fixos que já existem em 2 blocos (Receitas /
Despesas operacionais) — o suficiente pra montar uma Demonstração de
Resultado (receita - despesa = resultado do período), sem exigir
migração de dado nenhuma."""

from decimal import Decimal

from django.db.models import Sum

from finance.models import Transaction

DRE_GROUPS = {
    Transaction.Category.TITHE: "Receitas",
    Transaction.Category.OFFERING: "Receitas",
    Transaction.Category.DONATION: "Receitas",
    Transaction.Category.EVENT: "Receitas",
    Transaction.Category.OTHER_INCOME: "Receitas",
    Transaction.Category.SALARY: "Despesas operacionais",
    Transaction.Category.MAINTENANCE: "Despesas operacionais",
    Transaction.Category.UTILITIES: "Despesas operacionais",
    Transaction.Category.MATERIALS: "Despesas operacionais",
    Transaction.Category.RENT: "Despesas operacionais",
    Transaction.Category.OTHER_EXPENSE: "Despesas operacionais",
}


def dre_breakdown(church, inicio, fim):
    """Agrega os lançamentos de `church` entre `inicio`/`fim` (inclusive)
    por grupo DRE + categoria. Devolve um dict pronto pro template/PDF:
    `{"grupos": [{"nome": ..., "total": ..., "categorias": [(label, total), ...]}, ...],
      "receitas": Decimal, "despesas": Decimal, "resultado": Decimal}`."""
    linhas = (
        Transaction.objects.filter(church=church, date__gte=inicio, date__lte=fim)
        .values("category")
        .annotate(total=Sum("amount"))
        .order_by("category")
    )
    por_categoria = {linha["category"]: linha["total"] for linha in linhas}

    grupos_ordem = ["Receitas", "Despesas operacionais"]
    grupos = {nome: {"nome": nome, "total": Decimal("0"), "categorias": []} for nome in grupos_ordem}

    for category, label in Transaction.Category.choices:
        total = por_categoria.get(category)
        if not total:
            continue
        grupo_nome = DRE_GROUPS[category]
        grupos[grupo_nome]["total"] += total
        grupos[grupo_nome]["categorias"].append((label, total))

    receitas = grupos["Receitas"]["total"]
    despesas = grupos["Despesas operacionais"]["total"]
    return {
        "grupos": [grupos[nome] for nome in grupos_ordem],
        "receitas": receitas,
        "despesas": despesas,
        "resultado": receitas - despesas,
    }


def saldo_acumulado(church, inicio, fim):
    """"Balancete" simplificado — não é um balanço patrimonial de
    verdade (ativo=passivo+PL): o sistema não rastreia conta bancária/
    ativo/passivo, só lançamentos de entrada/saída. O que dá pra montar
    honestamente com o que existe é um SALDO ACUMULADO: quanto já tinha
    antes do período (saldo de abertura) + entradas/saídas mês a mês +
    saldo final. Devolve `{"abertura": Decimal, "meses": [{"mes": date,
    "entradas": ..., "saidas": ..., "saldo_mes": ..., "saldo_acumulado": ...}, ...],
    "saldo_final": Decimal}`."""
    from dateutil.relativedelta import relativedelta

    def _saldo(qs):
        entradas = qs.filter(type=Transaction.Type.INCOME).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        saidas = qs.filter(type=Transaction.Type.EXPENSE).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return entradas, saidas

    abertura_entradas, abertura_saidas = _saldo(Transaction.objects.filter(church=church, date__lt=inicio))
    abertura = abertura_entradas - abertura_saidas

    meses = []
    saldo_corrente = abertura
    mes_atual = inicio.replace(day=1)
    fim_mes = fim.replace(day=1)
    while mes_atual <= fim_mes:
        proximo_mes = mes_atual + relativedelta(months=1)
        qs_mes = Transaction.objects.filter(church=church, date__gte=mes_atual, date__lt=proximo_mes)
        entradas, saidas = _saldo(qs_mes)
        saldo_mes = entradas - saidas
        saldo_corrente += saldo_mes
        meses.append({
            "mes": mes_atual, "entradas": entradas, "saidas": saidas,
            "saldo_mes": saldo_mes, "saldo_acumulado": saldo_corrente,
        })
        mes_atual = proximo_mes

    return {"abertura": abertura, "meses": meses, "saldo_final": saldo_corrente}
