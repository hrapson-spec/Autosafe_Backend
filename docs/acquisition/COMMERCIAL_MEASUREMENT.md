# Commercial outcome measurement

## Collection contract

`GET /api/garage/outcome/{assignment_id}` is a read-only, no-store confirmation page. The optional `result` query only preselects a choice. HEAD remains 405. A state change requires a same-origin POST with an allowed outcome and explicit confirmation: JSON `{ "outcome": "won", "confirmed": true }` or the page's form. The assignment link remains a bearer capability; possession does not prove the submitter's identity. Do not describe a confirmed submission as independently audited revenue.

The assignment is locked during an outcome update. Retrying an unchanged outcome does not update its timestamp or increment the garage counter. A change into `won` increments the counter once; a change out reverses that increment in the same transaction. Historical counters may already include duplicate writes and must not be used as the source of truth for pilot reporting.

## Distinct stages and reporting grains

- A supported result acknowledgement is a client-reported rendered result. It is not an enquiry.
- A submitted enquiry is one persisted `leads.id`, with the requested service and consent. Deduplicate obvious test/spam and repeated requests during operational review; do not call every form submission qualified.
- A qualified enquiry requires operational confirmation that the contact details are usable, the requested service is supported, the area can be served, and the request is genuine. The present schema has no audited qualification flag. Until a separate qualification register is completed, publish submitted/distributed counts and label the qualified count unknown. Do not infer qualification from a successful HTTP response, a `lead_assignment`, or an email timestamp: assignments are created before email delivery.
- Garage distribution is a separate measure. Count distinct enquiries with successful notification, not assignment rows; one enquiry can be sent to several garages. `email_sent_at` alone is not delivery evidence in the current implementation.
- Reported bookings are distinct `leads.id` with a currently `won` assignment, plus assignment counts separately if needed. Attribute to the enquiry's creation cohort; show reporting lag and the observation cutoff. Wins do not establish attendance, completed repair or payment.
- Commercial revenue requires a separately reconciled invoice/payment record. No revenue metric is available from result renders, enquiry submissions, or `won` alone.

## Historical boundary and privacy

Record the exact production cutover time before relying on newly changed `outcome_reported_at` values. Earlier outcomes cannot be distinguished from scanner-triggered GET writes and remain unverified. An unchanged legacy outcome keeps its old timestamp on retry and remains outside the newly confirmed series until operationally reviewed. This is a deliberate conservative coverage limit.

Operational enquiry records have a different purpose and retention basis from aggregate site-improvement analytics. Do not join random acquisition identifiers to email addresses, registrations, report tokens or assignment IDs. Pilot outcome reporting may use a separately allowlisted page/cohort label only after that operational collection and notice have been implemented and verified. Until then, qualified enquiries and bookings are site-level context, not causally attributable pilot conversions.
