from django import forms

from linkbio.models import BioPage, Link


class BioPageForm(forms.ModelForm):
    class Meta:
        model = BioPage
        fields = ["church_name", "headline", "avatar", "background_color", "accent_color", "is_active"]
        widgets = {
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
        }


class LinkForm(forms.ModelForm):
    class Meta:
        model = Link
        fields = ["title", "url", "link_type", "icon", "is_active"]
