from datetime import date

from django import forms

from finance.models import RecurringPledge, Transaction
from people.models import Person

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["type", "category", "amount", "date", "description", "person", "payment_method"]
        widgets = {"date": DATE_INPUT, "description": forms.TextInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%d"]
        if not self.instance.pk:
            self.fields["date"].initial = date.today()
        # `Person` é `TenantModel` — mesmo motivo do `PersonForm`
        # (`people/forms.py`): refaz o queryset aqui pra não vazar pessoas
        # de outras igrejas (fixado na classe do form, sem igreja nenhuma
        # no thread-local ainda).
        self.fields["person"].queryset = Person.objects.all()


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
