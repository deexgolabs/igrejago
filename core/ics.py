"""Geração de arquivo `.ics` (RFC 5545) pra calendário público de
eventos — usa a lib `icalendar` (pura Python, sem dependência
compilada) em vez de montar o texto VCALENDAR na mão, mesmo raciocínio
de usar ReportLab pra PDF em vez de gerar o binário à mão."""

from icalendar import Calendar, Event as ICalEvent


def eventos_para_ics(church, eventos):
    """Monta um `Calendar` com um `VEVENT` por `Event` — `UID` estável
    (`event-<pk>@<slug>.igrejago`) pra apps de calendário atualizarem em
    vez de duplicar quando o feed for consultado de novo."""
    cal = Calendar()
    cal.add("prodid", f"-//IgrejaGo//{church.slug}//PT-BR")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"{church.name} — Eventos")

    for evento in eventos:
        vevent = ICalEvent()
        vevent.add("uid", f"event-{evento.pk}@{church.slug}.igrejago")
        vevent.add("summary", evento.title)
        if evento.description:
            vevent.add("description", evento.description)
        if evento.location:
            vevent.add("location", evento.location)
        vevent.add("dtstart", evento.start_datetime)
        if evento.end_datetime:
            vevent.add("dtend", evento.end_datetime)
        vevent.add("dtstamp", evento.start_datetime)
        cal.add_component(vevent)

    return cal.to_ical()
