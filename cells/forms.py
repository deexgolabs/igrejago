from datetime import date

from django import forms

from cells.models import Cell, CellMeeting
from people.models import Person

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class CellForm(forms.ModelForm):
    class Meta:
        model = Cell
        fields = ["name", "leader", "members", "meeting_weekday", "meeting_time", "address", "is_active"]
        widgets = {
            "members": forms.CheckboxSelectMultiple(),
            "meeting_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `Person` é `TenantModel` — mesmo motivo do `PersonForm`.
        self.fields["leader"].queryset = Person.objects.all()
        self.fields["members"].queryset = Person.objects.all()


class CellMeetingForm(forms.ModelForm):
    class Meta:
        model = CellMeeting
        fields = ["date", "attendees", "visitors_count", "notes"]
        widgets = {
            "date": DATE_INPUT,
            "attendees": forms.CheckboxSelectMultiple(),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, cell=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%d"]
        if not self.instance.pk:
            self.fields["date"].initial = date.today()
        if cell is not None:
            self.fields["attendees"].queryset = cell.members.all()
