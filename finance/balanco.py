"""Partida dobrada de verdade — diferente de `finance/dre.py` (escopado
a "estilo demonstração de resultado", conforme o próprio docstring de
lá): aqui a lógica é por DÉBITO/CRÉDITO de cada `ContaContabil`, não por
categoria de entrada/saída.

Convenção usada (a mesma de qualquer contabilidade): uma ENTRADA
credita `Transaction.conta_contabil` e debita `conta_contrapartida`;
uma SAÍDA debita `conta_contabil` e credita `conta_contrapartida`. Só
lançamentos com AS DUAS contas preenchidas entram nessa conta — ver
`finance.forms.TransactionForm.clean()`, que já barra um lançamento com
só um lado (senão o balanço fecharia torto sem aviso nenhum)."""

from decimal import Decimal

from django.db.models import Q, Sum

from finance.models import ContaContabil, Transaction

# Ativo/Despesa têm saldo NORMALMENTE devedor (aumenta com débito);
# Passivo/Patrimônio líquido/Receita têm saldo NORMALMENTE credor
# (aumenta com crédito) — convenção contábil padrão, não uma escolha
# deste projeto.
_SALDO_DEVEDOR = {ContaContabil.Tipo.ATIVO, ContaContabil.Tipo.DESPESA}


def saldo_da_conta(conta, ate_data):
    """Saldo de `conta` em `ate_data` (inclusive) = saldo de abertura +
    movimentação de todos os lançamentos até a data, com o sinal certo
    pro tipo da conta."""
    debito = Transaction.objects.filter(
        Q(conta_contabil=conta, type=Transaction.Type.EXPENSE)
        | Q(conta_contrapartida=conta, type=Transaction.Type.INCOME),
        church=conta.church, date__lte=ate_data,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    credito = Transaction.objects.filter(
        Q(conta_contabil=conta, type=Transaction.Type.INCOME)
        | Q(conta_contrapartida=conta, type=Transaction.Type.EXPENSE),
        church=conta.church, date__lte=ate_data,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    if conta.tipo in _SALDO_DEVEDOR:
        return conta.saldo_inicial + debito - credito
    return conta.saldo_inicial + credito - debito


def balanco_patrimonial(church, data):
    """Devolve `{"ativo": [(conta, saldo), ...], "passivo": [...],
    "pl": [...], "resultado_acumulado": Decimal, "saldo_inicial_liquido":
    Decimal, "total_ativo": ..., "total_passivo_pl": ..., "diferenca": ...}`.

    Dois ajustes automáticos dentro do Patrimônio líquido, sem exigir
    lançamento manual de ninguém — senão o balanço nunca fecharia:

    1. `resultado_acumulado` (receita − despesa de tudo com
       `conta_contabil` setada até `data`) — Receita/Despesa são contas
       temporárias; o jeito contábil formal de zerá-las é um lançamento
       de encerramento no fim do período, avançado demais pra pedir da
       secretaria.
    2. `saldo_inicial_liquido` (soma dos `saldo_inicial` de Ativo menos
       os de Passivo) — quando alguém registra "o Caixa já tinha
       R$500" (`ContaContabil.saldo_inicial`) sem lançar uma conta de
       Patrimônio líquido correspondente pra isso, esse valor vira
       patrimônio implícito automaticamente.

    `diferenca` deveria ser sempre 0 — a validação do form já impede
    lançamento com só um lado; se não for zero, é sinal de dado editado
    direto no admin sem as duas pontas, e o relatório MOSTRA isso em
    vez de esconder."""
    contas = ContaContabil.objects.filter(church=church, is_active=True)
    contas_ativo = contas.filter(tipo=ContaContabil.Tipo.ATIVO)
    contas_passivo = contas.filter(tipo=ContaContabil.Tipo.PASSIVO)

    ativo = [(c, saldo_da_conta(c, data)) for c in contas_ativo]
    passivo = [(c, saldo_da_conta(c, data)) for c in contas_passivo]
    pl = [(c, saldo_da_conta(c, data)) for c in contas.filter(tipo=ContaContabil.Tipo.PATRIMONIO_LIQUIDO)]

    receitas = Transaction.objects.filter(
        church=church, date__lte=data, conta_contabil__tipo=ContaContabil.Tipo.RECEITA,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    despesas = Transaction.objects.filter(
        church=church, date__lte=data, conta_contabil__tipo=ContaContabil.Tipo.DESPESA,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    resultado_acumulado = receitas - despesas

    saldo_inicial_ativo = sum((c.saldo_inicial for c in contas_ativo), Decimal("0"))
    saldo_inicial_passivo = sum((c.saldo_inicial for c in contas_passivo), Decimal("0"))
    saldo_inicial_liquido = saldo_inicial_ativo - saldo_inicial_passivo

    total_ativo = sum((saldo for _, saldo in ativo), Decimal("0"))
    total_passivo = sum((saldo for _, saldo in passivo), Decimal("0"))
    total_pl = sum((saldo for _, saldo in pl), Decimal("0")) + resultado_acumulado + saldo_inicial_liquido
    total_passivo_pl = total_passivo + total_pl

    return {
        "ativo": ativo, "passivo": passivo, "pl": pl,
        "resultado_acumulado": resultado_acumulado,
        "saldo_inicial_liquido": saldo_inicial_liquido,
        "total_ativo": total_ativo, "total_passivo": total_passivo, "total_pl": total_pl,
        "total_passivo_pl": total_passivo_pl,
        "diferenca": total_ativo - total_passivo_pl,
    }


def livro_razao(conta, inicio, fim):
    """Extrato de UMA conta — débito/crédito/saldo corrente por
    lançamento, mesmo princípio de um extrato bancário. Saldo de
    abertura do período = `saldo_da_conta` até o dia anterior a
    `inicio`."""
    from datetime import timedelta

    saldo = saldo_da_conta(conta, inicio - timedelta(days=1))
    lancamentos = Transaction.objects.filter(
        Q(conta_contabil=conta) | Q(conta_contrapartida=conta),
        church=conta.church, date__gte=inicio, date__lte=fim,
    ).order_by("date", "id")

    linhas = []
    for t in lancamentos:
        eh_debito = (
            (t.conta_contabil_id == conta.id and t.type == Transaction.Type.EXPENSE)
            or (t.conta_contrapartida_id == conta.id and t.type == Transaction.Type.INCOME)
        )
        debito = t.amount if eh_debito else Decimal("0")
        credito = t.amount if not eh_debito else Decimal("0")
        if conta.tipo in _SALDO_DEVEDOR:
            saldo += debito - credito
        else:
            saldo += credito - debito
        linhas.append({
            "data": t.date, "descricao": t.description or t.get_category_display(),
            "debito": debito, "credito": credito, "saldo": saldo,
        })

    return {"conta": conta, "saldo_abertura": saldo_da_conta(conta, inicio - timedelta(days=1)), "linhas": linhas, "saldo_final": saldo}
