from django import forms

from custom_forms.models import CustomForm, FormField


class CustomFormForm(forms.ModelForm):
    class Meta:
        model = CustomForm
        fields = [
            "title", "description", "is_active", "send_whatsapp_confirmation", "whatsapp_message_template",
            "sync_to_person", "notify_staff_emails",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "whatsapp_message_template": forms.Textarea(attrs={"rows": 2}),
        }


class FormFieldForm(forms.ModelForm):
    class Meta:
        model = FormField
        fields = ["label", "field_type", "options", "required", "order", "is_name_field", "is_phone_field"]
        widgets = {"options": forms.Textarea(attrs={"rows": 3, "placeholder": "Uma opção por linha"})}
