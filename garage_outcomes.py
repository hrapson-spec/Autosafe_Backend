"""Read-only confirmation pages for the bearer links sent to garages."""
from html import escape
from urllib.parse import quote, urlsplit

OUTCOMES = {"won": "Booked with our garage", "lost": "Did not book with our garage", "no_response": "No response yet"}


def is_outcome_path(path: str) -> bool:
    return path.lower().startswith("/api/garage/outcome/")


def same_origin_submission(request) -> bool:
    # Modern browsers supply this even when the page suppresses Referer.
    site = request.headers.get("sec-fetch-site")
    if site == "cross-site":
        return False
    if site == "same-origin":
        return True
    origin = request.headers.get("origin", "")
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme in ("http", "https") and parsed.netloc == request.url.netloc
                and not parsed.path and not parsed.username and not parsed.password)
    except ValueError:
        return False


def confirmation_page(assignment_id: str, assignment: dict, requested: str | None) -> str:
    vehicle = escape(" ".join(str(assignment.get(k) or "") for k in
                              ("vehicle_year", "vehicle_make", "vehicle_model")))
    garage = escape(str(assignment.get("garage_name") or "Your garage"))
    current = escape(OUTCOMES.get(assignment.get("outcome"), "No outcome recorded"))
    selected = requested if requested in OUTCOMES else ""
    options = '<option value="">Choose an outcome</option>' + "".join(
        f'<option value="{key}"{" selected" if key == selected else ""}>{label}</option>'
        for key, label in OUTCOMES.items()
    )
    action = "/api/garage/outcome/" + quote(assignment_id, safe="")
    return f'''<!doctype html><html lang="en-GB"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">
<title>Confirm enquiry outcome | AutoSafe</title>
<style>body{{font:18px/1.6 system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1.5rem;color:#172033}}
label,select,button{{display:block;margin:1rem 0}}select,button{{font:inherit;padding:.65rem}}button{{cursor:pointer}}
</style></head><body><main><h1>Confirm enquiry outcome</h1><p>{garage} · {vehicle}</p>
<p>Current outcome: <strong>{current}</strong>.</p>
<p>Opening this page does not record or change an outcome. Select the result and confirm it below.</p>
<form method="post" action="{action}">
<label for="outcome">What happened with this enquiry?</label><select id="outcome" name="outcome" required>{options}</select>
<label><input type="checkbox" name="confirmed" value="yes" required> I confirm this is the outcome reported by our garage.</label>
<button type="submit">Record confirmed outcome</button></form>
<p>A booking reported here is not evidence that payment was received.</p></main></body></html>'''
