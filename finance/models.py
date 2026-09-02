from django.conf import settings
from django.db import models

from core.tenancy import TenantModel


class ContaContabil(TenantModel):
    """Plano de contas configurável pela PRÓPRIA igreja — camada
    ADICIONAL sobre `Transaction.category` (o enum fixo de 11 valores
    continua exatamente como sempre foi, sem migração de dado). Pra
    igreja com contador exigente, que precisa de uma árvore de contas
    no formato que o contador reconhece; pra quem não precisa disso, o
    campo em `Transaction` fica em branco e o DRE simples por categoria
    (`finance/dre.py`) continua sendo o padrão."""

    class Tipo(models.TextChoices):
        RECEITA = "RECEITA", "Receita"
        DESPESA = "DESPESA", "Despesa"
        ATIVO = "ATIVO", "Ativo"
        PASSIVO = "PASSIVO", "Passivo"
        PATRIMONIO_LIQUIDO = "PATRIMONIO_LIQUIDO", "Patrimônio líquido"

    code = models.CharField(
        "Código", max_length=20,
        help_text="Livre — numere do jeito que seu contador pedir (ex.: 3.1.01).",
    )
    name = models.CharField("Nome da conta", max_length=150)
    tipo = models.CharField("Tipo", max_length=20, choices=Tipo.choices)
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="children", verbose_name="Conta superior",
    )
    is_active = models.BooleanField("Ativa", default=True)

    class Meta:
        verbose_name = "Conta contábil"
        verbose_name_plural = "Plano de contas"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Transaction(TenantModel):
    """Um lançamento financeiro — entrada (dízimo/oferta/doação) ou saída
    (despesa). "Financeiro Simples" por design: um único model de lançamento,
    sem contas a pagar/receber, conciliação bancária ou centro de custo."""

    class Type(models.TextChoices):
        INCOME = "INCOME", "Entrada"
        EXPENSE = "EXPENSE", "Saída"

    class Category(models.TextChoices):
        TITHE = "TITHE", "Dízimo"
        OFFERING = "OFFERING", "Oferta"
        DONATION = "DONATION", "Doação"
        EVENT = "EVENT", "Evento"
        OTHER_INCOME = "OTHER_INCOME", "Outra entrada"
        SALARY = "SALARY", "Salário / pró-labore"
        MAINTENANCE = "MAINTENANCE", "Manutenção"
        UTILITIES = "UTILITIES", "Água / luz / internet"
        MATERIALS = "MATERIALS", "Materiais"
        RENT = "RENT", "Aluguel"
        OTHER_EXPENSE = "OTHER_EXPENSE", "Outra saída"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Dinheiro"
        PIX = "PIX", "PIX"
        TRANSFER = "TRANSFER", "Transferência"
        CARD = "CARD", "Cartão"
        OTHER = "OTHER", "Outro"

    type = models.CharField("Tipo", max_length=10, choices=Type.choices)
    category = models.CharField("Categoria", max_length=20, choices=Category.choices)
    amount = models.DecimalField("Valor (R$)", max_digits=10, decimal_places=2)
    date = models.DateField("Data")
    description = models.CharField("Descrição", max_length=255, blank=True)
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="Contribuinte",
        help_text="Opcional — quem deu o dízimo/oferta, para histórico individual.",
    )
    payment_method = models.CharField(
        "Forma de pagamento", max_length=10, choices=PaymentMethod.choices, blank=True
    )
    conta_contabil = models.ForeignKey(
        ContaContabil, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transactions", verbose_name="Conta contábil",
        help_text="Opcional — só pra quem já montou um plano de contas (Financeiro → Plano de contas).",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_created",
        verbose_name="Lançado por",
    )
    created_at = models.DateTimeField("Lançado em", auto_now_add=True)

    class Meta:
        verbose_name = "Lançamento"
        verbose_name_plural = "Lançamentos"
        ordering = ["-date", "-id"]

    def __str__(self):
        sign = "+" if self.type == self.Type.INCOME else "-"
        return f"{sign} R$ {self.amount} — {self.get_category_display()} ({self.date})"


class Budget(TenantModel):
    """Meta financeira mensal por categoria — comparado contra o realizado
    (soma dos `Transaction` daquele mês/categoria) na tela de orçamento.
    Não é um orçamento por conta contábil de verdade, só "quanto eu
    esperava gastar/receber nessa categoria esse mês"."""

    category = models.CharField("Categoria", max_length=20, choices=Transaction.Category.choices)
    year = models.PositiveIntegerField("Ano")
    month = models.PositiveSmallIntegerField("Mês")
    target_amount = models.DecimalField("Valor previsto (R$)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Orçamento"
        verbose_name_plural = "Orçamentos"
        constraints = [
            models.UniqueConstraint(
                fields=["church", "category", "year", "month"], name="unique_budget_per_month"
            )
        ]
        ordering = ["-year", "-month", "category"]

    def __str__(self):
        return f"{self.get_category_display()} {self.month:02d}/{self.year} — R$ {self.target_amount}"


class RecurringPledge(TenantModel):
    """Compromisso de dízimo/contribuição mensal recorrente. Duas origens
    possíveis: cadastrado à mão pela secretaria (`mercadopago_preapproval_id`
    em branco — pagamento de cada mês lançado manualmente como
    `Transaction`), ou assinado pelo próprio membro no Portal via Mercado
    Pago (`mercadopago_preapproval_id` preenchido — a cobrança e o
    `Transaction` de cada mês são automáticos, ver
    `RecurringPledgeMercadoPagoWebhookView`). O relatório em
    `RecurringPledgeListView` cruza com `Transaction` pra mostrar quem
    está em dia, dos dois jeitos."""

    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE,
        related_name="recurring_pledges", verbose_name="Pessoa",
    )
    monthly_amount = models.DecimalField("Valor mensal (R$)", max_digits=10, decimal_places=2)
    due_day = models.PositiveSmallIntegerField(
        "Dia de vencimento", default=10, help_text="Dia do mês (1 a 28).",
    )
    active = models.BooleanField("Ativo", default=True)
    mercadopago_preapproval_id = models.CharField(
        "ID da assinatura no Mercado Pago", max_length=100, blank=True,
        help_text="Preenchido só quando o membro assinou pelo Portal — em branco = compromisso manual.",
    )
    mercadopago_status = models.CharField(
        "Status da assinatura no Mercado Pago", max_length=30, blank=True,
        help_text="Espelha o status real da API (authorized/paused/cancelled) — nunca decidido localmente.",
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Contribuição recorrente"
        verbose_name_plural = "Contribuições recorrentes"
        ordering = ["person__full_name"]

    def __str__(self):
        return f"{self.person.full_name} — R$ {self.monthly_amount}/mês"

    @property
    def is_mercadopago(self):
        return bool(self.mercadopago_preapproval_id)


class Donation(TenantModel):
    """Doação avulsa iniciada pelo membro no Portal (`core.DashboardView`
    → tela de doação). Via Mercado Pago o webhook confirma sozinho e já
    gera o `Transaction`; via PIX manual, fica PENDING até a secretaria
    conferir o extrato e confirmar (mesmo padrão do PIX de evento)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando confirmação"
        PAID = "PAID", "Confirmada"

    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="donations", verbose_name="Pessoa",
    )
    amount = models.DecimalField("Valor (R$)", max_digits=10, decimal_places=2)
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    payment_reference = models.CharField("Referência de pagamento", max_length=100, blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Doação"
        verbose_name_plural = "Doações"
        ordering = ["-created_at"]

    def __str__(self):
        quem = self.person.full_name if self.person else "Anônimo"
        return f"{quem} — R$ {self.amount} ({self.get_status_display()})"
