from django import forms

from core.lgpd import privacy_consent_label
from events.models import Event, Registration

DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title", "description", "image", "location",
            "start_datetime", "end_datetime", "is_paid", "price",
            "capacity", "brand_color", "extra_info", "status",
        ]
        widgets = {
            "start_datetime": DATETIME_INPUT,
            "end_datetime": DATETIME_INPUT,
            "description": forms.Textarea(attrs={"rows": 4}),
            "extra_info": forms.Textarea(attrs={"rows": 3}),
            "brand_color": forms.TextInput(attrs={"placeholder": "#2563eb (em branco = cor da igreja)"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("start_datetime", "end_datetime"):
            self.fields[field_name].input_formats = ["%Y-%m-%dT%H:%M"]


class PublicRegistrationForm(forms.ModelForm):
    """Formulário público de inscrição em evento — não exige cadastro
    prévio como membro/visitante (ver docstring de `Registration`)."""

    privacy_consent = forms.BooleanField(required=True)

    class Meta:
        model = Registration
        fields = ["full_name", "phone", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = True
        self.fields["privacy_consent"].label = privacy_consent_label()
