from datetime import date

from django import forms

from finance.models import ContaContabil, RecurringPledge, Transaction
from people.models import Person

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = [
            "type", "category", "amount", "date", "description", "person", "payment_method",
            "conta_contabil", "conta_contrapartida",
        ]
        widgets = {"date": DATE_INPUT, "description": forms.TextInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%d"]
        if not self.instance.pk:
            self.fields["date"].initial = date.today()
        # `Person`/`ContaContabil` são `TenantModel` — mesmo motivo do
        # `PersonForm` (`people/forms.py`): refaz o queryset aqui pra não
        # vazar registro de outras igrejas (fixado na classe do form, sem
        # igreja nenhuma no thread-local ainda).
        self.fields["person"].queryset = Person.objects.all()
        contas_ativas = ContaContabil.objects.filter(is_active=True)
        self.fields["conta_contabil"].queryset = contas_ativas
        self.fields["conta_contabil"].required = False
        self.fields["conta_contrapartida"].queryset = contas_ativas
        self.fields["conta_contrapartida"].required = False

    def clean(self):
        cleaned_data = super().clean()
        conta = cleaned_data.get("conta_contabil")
        contrapartida = cleaned_data.get("conta_contrapartida")
        # Partida dobrada é tudo ou nada: com só um lado preenchido, o
        # balanço patrimonial fecharia torto (Ativo ≠ Passivo + PL) sem
        # nenhum aviso — melhor barrar aqui do que deixar silencioso.
        if bool(conta) != bool(contrapartida):
            raise forms.ValidationError(
                "Pra registrar em partida dobrada, informe as duas contas (a de origem e a de "
                "contrapartida) — ou deixe as duas em branco pra um lançamento simples."
            )
        if conta and contrapartida and conta == contrapartida:
            raise forms.ValidationError("A conta e a contrapartida não podem ser a mesma.")
        return cleaned_data


class ContaContabilForm(forms.ModelForm):
    class Meta:
        model = ContaContabil
        fields = ["code", "name", "tipo", "parent", "is_active", "saldo_inicial"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        parent_qs = ContaContabil.objects.all()
        if self.instance.pk:
            parent_qs = parent_qs.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = parent_qs
        self.fields["parent"].required = False


class RecurringPledgeForm(forms.ModelForm):
    """Sem o campo `active` de propósito — toda contribuição nova começa
    ativa (`RecurringPledge.active` já tem `default=True`); pausar/reativar
    é feito depois pelo botão dedicado (`RecurringPledgeToggleView`), não
    por um checkbox no formulário de criação."""

    class Meta:
        model = RecurringPledge
        fields = ["person", "monthly_amount", "due_day"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.all()


class DonationAmountForm(forms.Form):
    amount = forms.DecimalField(
        label="Valor (R$)", max_digits=10, decimal_places=2, min_value=1,
    )


class RecurringPledgeSubscribeForm(forms.Form):
    """Formulário do Portal pro MEMBRO assinar o próprio dízimo
    recorrente via Mercado Pago — diferente de `RecurringPledgeForm`
    (usado pela secretaria pra cadastrar um compromisso manual de
    qualquer pessoa)."""

    monthly_amount = forms.DecimalField(label="Valor mensal (R$)", max_digits=10, decimal_places=2, min_value=1)
    due_day = forms.IntegerField(
        label="Dia de vencimento", min_value=1, max_value=28, initial=10,
        help_text="Dia do mês em que o Mercado Pago vai cobrar.",
    )
