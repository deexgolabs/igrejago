from datetime import date

from django import forms

from finance.models import ContaContabil, RecurringPledge, Transaction
from people.models import Person

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["type", "category", "amount", "date", "description", "person", "payment_method", "conta_contabil"]
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
        self.fields["conta_contabil"].queryset = ContaContabil.objects.filter(is_active=True)
        self.fields["conta_contabil"].required = False


class ContaContabilForm(forms.ModelForm):
    class Meta:
        model = ContaContabil
        fields = ["code", "name", "tipo", "parent", "is_active"]

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
