---
name: proxyclick-visitors
description: Register visitors in Proxyclick / Eptura Visitor (app.proxyclick.com) by calling the web app's API directly. Use whenever the user asks to add, invite, pre-register, expect, announce or "put on the list" a visitor, guest, contractor, delivery or candidate, wants to know who is expected or checked in on a day, or mentions Proxyclick, Eptura, the visitor logbook, a visitor pass or front-desk check-in, even without naming the tool ("add Maria from Acme for tomorrow 10am"). Use this API instead of clicking through the website.
---

# Proxyclick visitors via API

Same calls the Eptura Visitor web app makes (captured 2026-10-26). Do not click through the website; call these endpoints.

## Session and login

- Base URL: `https://app.proxyclick.com`
- Auth is a Bearer token **plus a cookie**. The login response returns `access_token` (a JWT, valid 24 h) and sets an HttpOnly cookie `__Secure-Fgp`. Every call must send both: `Authorization: Bearer <access_token>` and `Cookie: __Secure-Fgp=<value>`. The token alone gets `401 {"error":{"type":"INVALID_AUTH"}}`.
- Proxyclick email and password are in the Secure Credentials Store as `proxyclick_email` and `proxyclick_password`. Never show them.
- Send `Accept: application/json, text/plain, */*`, and `Content-Type: application/json;charset=utf-8` when there is a body. No CSRF header, no captcha.

Login (only when there is no valid token, or after a 401):

1. `POST /authenticate` with body
   `{"username": "<email>", "password": "<password>", "directory": "proxyclick", "rememberMe": false, "destination": "/", "submit": "Log+in"}`
2. Keep `access_token` from the JSON and `__Secure-Fgp` from `Set-Cookie`. A 400/401/403 here means wrong credentials: stop and tell the user. Do not retry more than once.
3. `GET /api/me` → `id` is the user id (`US-...`, the default host), `originalCompanyId` is the company (`CO-...`), `firstname`, `lastname`.

If any call returns 401, log in once and repeat the call.

## Ids for this account (discover once, then reuse)

- Company `{CO}`: `originalCompanyId` from `/api/me`; its timezone comes from `GET /api/me/companies`. Every `{CO}` below is that value.
- Default host `{US}`: the logged-in user's `id` from `/api/me`.
- Custom fields: `GET /api/app/{CO}/vm/custom-fields` once; see **Custom fields** below.
- Id shapes: visitor `V-XXXXXXXXX`, meeting (visit booking) `M-XXXXXXXXX`, visit = `YYYY-MM-DD/M-.../V-...`.

## Rules

1. The front desk sees every visit the moment it is created, and the visitor gets an email only if you call **invite**. Before creating, confirm you have: name, date and time, host, and the parking answer. If the person is new or anything is unclear, show the plan (visitor, date/time, host, parking, location, invite yes/no) and get a yes.
2. Never create a duplicate person. Search by email first (exact), then by name; reuse the existing `V-` id when found. After creating a new person, always add them to the address book (see Create), otherwise searches will not find them.
3. A mandatory custom field with no default (for example a parking-pass yes/no question) cannot be skipped. If the user did not say, ask rather than guess.
4. Creating is not safe to repeat blindly. If the create call times out, read the day's logbook before trying again.
5. Times are in the company timezone. Send ISO 8601 with that zone's current UTC offset. Default length is the company's `defaultVisitDuration` (`GET /api/app/{CO}/vm/params`), typically 2 hours.
6. Keep replies short: visitor (new or existing), date and time window, host, parking and location, invite sent or not, meeting id.
7. For tests, never use a real person; use a made-up name and a plus address of the user's mailbox (`you+test@example.com`).

## Fast path: "add Maria Angelica tomorrow at 10"

1. Find the person (see **Search**). One clear match → use its `id`. Several → show them (name, company, email) and ask. None → create inline in step 3.
2. Work out `startAt`/`endAt` (default 2 h), the host (default: me), and every mandatory custom field (ask when it has no default and the user did not say; use the default otherwise).
3. **Create** (one call creates the visitor too when no `id` is given).
4. **Store visit fields** (the non-`system` custom fields: location, gate, notes and the like). Send every field that has a default, not only the mandatory ones; the invitation email prints values such as the entrance and the location from them.
5. New person: **add to address book** (see Create). Skip this and the person cannot be found next time, so the next booking creates a duplicate.
6. **Invite** only if the user wants the visitor emailed.
7. Reply with the summary and the meeting id.

Several dates for one person (e.g. every Tuesday and Friday): create the first visit, take `visitors[0].id` from its response, and send that `id` in every following create call. Do not rely on search between the calls.

## Search

- Company-wide, by name: `GET /api/app/{CO}/vm/visitors?name=Mar` → JSON array of visitors. `name` is a case-insensitive **prefix** of "Firstname Lastname" ("Tiffany" and "Tiffany Ch" match Tiffany Chen, "Chen" does not).
- By email or last name: `GET /api/app/{CO}/vm/visitors` (no params) returns every visitor (about 100). Match `email` exactly, case-insensitive, or search the names yourself.
- Visitor fields: `id`, `firstname`, `lastname`, `email`, `companyName`, `phone`, `mobile`, `language.code`, `lastVisitAt`, `customFields[]` (`{id, name, value}`), `visitorType.id`.
- One visitor: `GET /api/app/{CO}/vm/visitors/<V-id>`.
- Hosts (employees): `GET /api/me/{CO}/address-book?name=Mar&type=user&page=1&pageSize=5` → `contacts[]` with `id` (`US-...`), `firstname`, `lastname`, `email`. Substring match on names.

## Create a visit

`POST /api/app/{CO}/vm/meetings` → `201` with the meeting.

```json
{
  "title": "Maria Angelica",
  "hostId": "{US}",
  "visitors": [{
    "id": "V-XXXXXXXXX",
    "firstName": "Maria", "lastName": "Angelica",
    "email": "maria@example.com", "companyName": "Example Co",
    "language": "en", "phone": "",
    "customFields": [{"id": <system-field-id>, "value": "<one of its values>"}],
    "addToAddressBook": false, "recognizeOnKiosk": false, "frequent": false, "smsReminder": false
  }],
  "startAt": "2026-10-27T10:00:00-04:00",
  "endAt": "2026-10-27T12:00:00-04:00",
  "recurrence": null,
  "checkinNotificationUsers": [], "checkoutNotificationUsers": [], "checkoutReminderNotificationUsers": [],
  "checkinNotificationType": "FIRST_LAST"
}
```

- Existing person: include `"id"` and copy `firstName`, `lastName`, `email`, `companyName` from the record (add `"visitorTypeId": <visitorType.id>` if the record has a visitor type).
- New person: omit `"id"`. `firstName`, `lastName` and `email` are required (the company has email mandatory on). The visitor record is created by this call; there is no separate create-visitor endpoint.
- `title` is the visitor's full name. `hostId` is a `US-` id.
- Response: `id` (`M-...`), `status` `"EXPECTED"`, `startAt`, `endAt`, `visitors[0].id` (the `V-` id, new or reused), `host`.

Then store the visit-level fields (always, with at least Meeting Location):

`PUT /api/app/{CO}/vm/visits/<YYYY-MM-DD>/<M-id>/<V-id>/custom-fields`
```json
{"customFields": [{"id": <location-field-id>, "value": "Main Lobby"}, {"id": <gate-field-id>, "value": "Any gate"}], "auditChanges": false}
```
→ `204`. The date is the visit day in the company timezone.

New person only, right after the create: `PUT /api/me/{CO}/address-book/<V-id>` with body `{}` → `204`. The company visitor list and the name search only contain visitors that are in an address book; a freshly created visitor is invisible to them until this call is made.

## Custom fields (per company)

`GET /api/app/{CO}/vm/custom-fields` returns `[{id, name, mandatory, system, type:{label: LIST|TEXT}, defaultValue, values:[...]}]`. Ids and names differ for every company; read them once per session. Where each goes:

| Kind | Goes in | Notes |
|---|---|---|
| `system: true` fields (visitor-level, e.g. a parking-pass question) | `visitors[].customFields` of the POST meetings call | mandatory ones with no default must be asked |
| all other fields (visit-level, e.g. meeting location, entrance gate, notes) | PUT `.../custom-fields` after the create | send every field that has a default too |

LIST values must match one of `values` exactly. If a mandatory LIST has no default and the user named nothing, ask which option.

## Invite (email the visitor)

`POST /api/app/{CO}/vm/meetings/<M-id>/invite` with body `{}` → `204`. Sends "Your visit to <Company> on MM/DD/YYYY" from notifications@proxyclick.com with a QR pass, calendar and directions. Needs an email on the visitor. Do this only when the user wants the visitor notified; it can be called again later to resend.

## Logbook and verification

- A day: `GET /api/app/{CO}/vm/visits/<YYYY-MM-DD>` → `{total, visits[]}`. Each visit: `id` (`day/M/V`), `visitor{id, firstname, lastname, companyName, email, customFields[]}` (parking lives here), `meeting{id}`, `host{firstname, lastname}`, `status.value` (`Expected`, ...), `expectedAt`, `leavingAt`, `arrivedAt`, `leftAt`, `checkedInLocation`, `lastInvitationSentAt`, `customFields[]` (location, gate, notes).
- One booking: `GET /api/app/{CO}/vm/meetings/<M-id>`.
- One person's visits (what the profile page shows): `GET /api/app/{CO}/vm/visitors/<V-id>/visits?mode=me&sortBy=time&sort=desc` → `{limit, total, visits[]}`. If `total` is larger than the number of visits returned, the list is capped; check a specific date with the day endpoint above instead.
- "Who is here / expected today?": list the day, show visitor, time window, host, `arrivedAt` if checked in.

## Fixing a duplicate: move a visit to another card

If a person ended up with two cards, keep one and move the other card's visits onto it. For each visit (`M-id`) on the card to remove:

1. `GET /api/app/{CO}/vm/meetings/<M-id>`; note its visit-level fields from the day's logbook first (Meeting Location, gate, notes), because the move drops them.
2. `PUT /api/app/{CO}/vm/meetings/<M-id>` with the same body shape as Create, `visitors[0].id` = the card to keep (name, email, company copied from that card), same `startAt`/`endAt`/`hostId`. → `200`.
3. `PUT /api/app/{CO}/vm/visits/<day>/<M-id>/<kept V-id>/custom-fields` with the fields from step 1 (at least Meeting Location).
4. If an invitation had been sent: send it again (`POST .../meetings/<M-id>/invite`). The QR pass encodes `accessCode|M-id,V-id,CO-id|accessCode`, so the earlier email points at the old card and will not scan. Tell the user the person will get a new email and must use the latest one.
5. When the old card has no visits left, delete it (next section).

Tested 2026-10-26: this worked for 9 visits in a row.

## Deleting

- `DELETE /api/app/{CO}/vm/visitors/<V-id>` → `204` removes the person **and every visit they have**. Ask first, name the visits that will disappear, and never do it for a person with past visits you want to keep.
- Deleting or cancelling a single visit may not be granted: on the account tested, `DELETE /api/app/{CO}/vm/meetings/<M-id>` returned `403 Right vm_visitor_delete is not granted`. If so, tell the user to cancel it in the web app (`https://app.proxyclick.com/{CO}/dashboard`).

## Not available through this skill

Check-in/out, editing a visitor profile and group visits were not captured. Send the user to the web app for those rather than guessing endpoints.
