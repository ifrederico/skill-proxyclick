# Proxyclick / Eptura Visitor private web-app API

Captured from https://app.proxyclick.com (Angular SPA, v14) on 2026-10-26 by recording the
browser's XHR traffic. These are the app's own endpoints, not the public api.proxyclick.com API,
so they can change without notice. Captured against one company; its custom fields appear below
only as an example of the shapes, and ids in the examples are placeholders. Placeholders: `{CO}` company id, `{US}` user id, `{V}` visitor id,
`{M}` meeting id, `{DAY}` = YYYY-MM-DD.

## Authentication

```
POST https://app.proxyclick.com/authenticate
Content-Type: application/json;charset=utf-8
Accept: application/json, text/plain, */*

{"username":"<email>","password":"<password>","directory":"proxyclick",
 "rememberMe":false,"destination":"/","submit":"Log+in"}

200 {"access_token":"<JWT, RS256, ~1300 chars>","token_type":"Bearer"}
```

No CSRF token or captcha is involved, but the login response also sets an **HttpOnly cookie
`__Secure-Fgp`** and the JWT's `fingerprint` claim is a hash of it: a call with the Bearer token
but without that cookie gets `401 {"error":{"type":"INVALID_AUTH"}}`. Every later call therefore
sends both `Authorization: Bearer <JWT>` and `Cookie: __Secure-Fgp=...`. The JWT payload has
`userId`, `accessToken`, `fingerprint`, `iat`, `exp` (exp = iat + 24 h). The SPA keeps it in localStorage key `pxc-access-token-3` as
`{"value": "<jwt>", "expiresAt": ...}`. A 401 means the token expired: log in again.

## Discovery

| Call | Returns |
|---|---|
| `GET /api/me` | the user: `id` (US-...), `firstname`, `lastname`, `email`, `originalCompanyId` (CO-...) |
| `GET /api/me/companies` | `[{object:"corporate", id, name, locale, timezone, ...}]` |
| `GET /api/app/{CO}/profile` | the company object |
| `GET /api/app/{CO}/vm/params` | visitor-module settings: `defaultVisitDuration`, `emailMandatory`, `maxCustomFields`, ... |
| `GET /api/app/{CO}/vm/custom-fields` | `[{id, name, mandatory, published, system, type:{label: LIST|TEXT, valueType}, defaultValue, values:[...], resetAtEveryVisit, showToAllInVisitForm, showOnKiosk, usedIn}]` |
| `GET /api/private/companies/{CO}/vm/visitorTypes` | `[{id, name, isDefault, isActive, ...}]` (one of them, typically "Guest", has `isDefault`) |

Custom fields of the captured company, as an example of the shapes (ids and names are per company;
read yours from `/vm/custom-fields`):

| name (example) | type | mandatory | default | where the app sends it |
|---|---|---|---|---|
| Parking pass yes/no | LIST, 2 values | yes | none | `visitors[].customFields` in POST meetings (`system:true`) |
| Meeting Location | LIST, ~60 values | yes | one of the values | PUT visit custom-fields |
| Preferred Entrance Gate | LIST, 7 values | no | "Any gate" | PUT visit custom-fields |
| Notes For Visitor | TEXT | no | | PUT visit custom-fields |
| Notes For Reception | TEXT | no | | PUT visit custom-fields |

The script sends every provided field in both places; the server keeps each where it belongs.

## People

| Call | Notes |
|---|---|
| `GET /api/app/{CO}/vm/visitors` | all visitors of the company (array; ~100 visitors was ~125 KB) |
| `GET /api/app/{CO}/vm/visitors?name=<q>` | prefix match on "firstname lastname", case-insensitive. `email=` is ignored |
| `GET /api/app/{CO}/vm/visitors/{V}` | visitor detail incl. `customFields:[{id,name,value}]`, `accessCode`, `visitorType`, `lastVisitAt` |
| `GET /api/me/{CO}/address-book?name=<q>&type=visitor&page=1&pageSize=5` | what the "New visit" picker uses: substring match, but only the user's private address book. `{contacts:[...], limit, total}` |
| `GET /api/me/{CO}/address-book?name=<q>&type=user&page=1&pageSize=5` | employees (hosts); contacts have ids `US-...` |
| `PUT /api/me/{CO}/address-book/{V}` body `{}` | add a visitor to the user's private address book (204) |

Visitor object fields: `id, firstname, lastname, companyName, email, phone, mobile,
language:{code,name}, about, licensePlate, function, picture*, customFields, visitorType,
frequent, firstVisitAt, lastVisitAt, smsReminder, mailReminder`.

## Visits

### Create (new or existing visitor)

```
POST /api/app/{CO}/vm/meetings            -> 201 meeting object
{
  "title": "Test Visitor",                 // the app uses the visitor's full name
  "hostId": "{US}",
  "visitors": [{
    "id": "{V}",                           // OMIT for a new visitor: it is created in this call
    "firstName": "Test", "lastName": "Visitor",
    "email": "you+test@example.com", "companyName": "Example Co",
    "language": "en", "phone": "", "mobile": "",            // mobile/licensePlate/about/function only echoed for existing
    "customFields": [{"id": <system-field-id>, "value": "<one of its values>"}],
    "addToAddressBook": false, "recognizeOnKiosk": false, "frequent": false, "smsReminder": false,
    "visitorTypeId": <visitorType.id>      // optional
  }],
  "startAt": "2026-10-26T13:45:00-04:00", "endAt": "2026-10-26T15:45:00-04:00",   // ISO with the company offset
  "recurrence": null,
  "checkinNotificationUsers": [], "checkoutNotificationUsers": [], "checkoutReminderNotificationUsers": [],
  "checkinNotificationType": "FIRST_LAST"
}
```

Response (meeting): `id` (M-...), `title`, `startAt`, `endAt`, `showFrom`, `timezone`, `status`
("EXPECTED"), `uid`, `company{}`, `host{id,...}`, `visitors:[{id (V-...), ...visitor fields, accessCode}]`,
`createdBy`, `createdVia`, `createdAt`.

### Follow-up calls the app makes

```
PUT  /api/app/{CO}/vm/visits/{DAY}/{M}/{V}/custom-fields   {"customFields":[{"id":<location-field-id>,"value":"Main Lobby"},{"id":<gate-field-id>,"value":"Any gate"}],"auditChanges":false}  -> 204
POST /api/app/{CO}/vm/meetings/{M}/invite                  {}  -> 204   (sends the invitation email with QR pass; only when the user ticked "Send an invitation")
PUT  /api/me/{CO}/address-book/{V}                         {}  -> 204   (only when "add to my private address book" was ticked, new visitors)
```

### Update (move a visit to another visitor, change times)

`PUT /api/app/{CO}/vm/meetings/{M}` with the same body as the POST (visitors[0].id = target visitor) → 200.
Side effects: the visit-level custom fields stored under `{DAY}/{M}/{old V}` are dropped; re-apply them
with the PUT custom-fields call under the new V. The emailed QR pass is
`https://app.proxyclick.com/qr-code?...&content=<accessCode>|<M>,<V>,<CO>|<accessCode>`, so after a move
the old email is stale; POST invite again.

### Read

| Call | Returns |
|---|---|
| `GET /api/app/{CO}/vm/meetings/{M}` | the meeting object above |
| `GET /api/app/{CO}/vm/visits/{DAY}` | `{limit, total, visits:[{id:"DAY/M/V", visitor{}, meeting{}, host{}, status{}, expectedAt, leavingAt, arrivedAt, leftAt, checkedInVia, checkedInLocation, lastInvitationSentAt, customFields:[...]}]}` |

### Delete

| Call | Result |
|---|---|
| `DELETE /api/app/{CO}/vm/visitors/{V}` | 204; removes the visitor and all their visits |
| `DELETE /api/app/{CO}/vm/meetings/{M}` | 403 `Right vm_visitor_delete is not granted` for the account captured |

Visibility rule (verified 2026-10-26): `GET /vm/visitors` and `?name=` only return visitors that are
in some address book. A visitor created through POST meetings is invisible to both until
`PUT /api/me/{CO}/address-book/{V}` is called (the web app does this by default).

Not captured: cancelling a single visit, check-in/out, group visits, editing a visitor profile.

## Email the visitor receives

From `notifications@proxyclick.com`, subject `Your visit to <Company> on MM/DD/YYYY`, body
"Meeting with <Host>" with the date, a QR pass to scan on arrival, Add-to-Calendar and Directions.

## Visitor history

`GET /api/app/{CO}/vm/visitors/{V}/visits?mode=me&sortBy=time&sort=desc` → `{limit, total, visits[]}` (same visit objects as the day logbook). Backs the visitor-profile page. The list can be capped (`total` > `len(visits)`); use `/vm/visits/{day}` for a specific date.
