from django import forms

from sermons.models import Sermon

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class SermonForm(forms.ModelForm):
    class Meta:
        model = Sermon
        fields = [
            "title", "preacher_name", "date", "series", "description",
            "audio_file", "youtube_url", "external_video_url", "is_published",
        ]
        widgets = {
            "date": DATE_INPUT,
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%d"]
