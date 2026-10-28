---
name: proxyclick-visitors
description: Register visitors in Proxyclick / Eptura Visitor (app.proxyclick.com) with no browser - log in with the account's email + password, find an existing visitor or create a new one, and book the visit for a host with the company's mandatory custom fields and an optional invitation email (QR pass). Use this whenever the user wants to add, invite, pre-register, expect, announce or "put on the list" a visitor, guest, contractor, delivery or candidate, wants to see who is expected in the logbook for a day, or mentions Proxyclick, Eptura Visitor, the visitor logbook, visitor passes or front-desk check-in - even when they do not name the tool ("add Maria from Acme for tomorrow 10am").
---

# Proxyclick visitors

Proxyclick (now branded Eptura Visitor) is the visitor-management system behind the front desk.
This skill drives the same private JSON API the web app uses, through one dependency-free
Python script, so a visit can be booked from a shell with nothing but the account credentials.
Everything below was captured from the live web app on 2026-10-26; `references/api.md` has the
raw endpoints if you ever need to go beyond the script.

## Setup (once per machine)

The script reads `PROXYCLICK_EMAIL` and `PROXYCLICK_PASSWORD` from the environment, or from
`~/.config/proxyclick/credentials.env` (two `KEY=VALUE` lines, `chmod 600`). Never put the
password on the command line or in chat; ask the user to create that file themselves if it is
missing. The first call logs in (`POST /authenticate`) and caches the 24-hour bearer token together with
the `__Secure-Fgp` cookie the login sets (the API rejects the token without that cookie) in
`~/.config/proxyclick/token.json`; later calls reuse both and re-login automatically on a 401.

```bash
python3 scripts/proxyclick.py login      # prints the user, the companies and the default company
python3 scripts/proxyclick.py config     # timezone, custom fields (ids, options, mandatory), visitor types
```

Run `config` the first time you work with a company and keep its output in mind: it tells you
which custom fields are mandatory and which values they accept. Pass `--company CO-XXXX` to
every command if the account belongs to several companies.

## Commands

| Command | What it does |
|---|---|
| `login` / `whoami` | authenticate or reuse the cached token; shows user id, companies |
| `config` | company timezone, custom fields with options/defaults, visitor types |
| `search <name or email>` | find visitors company-wide (exact email, else name prefix, else substring) |
| `hosts <name or email>` | find employees who can host a visit |
| `visits [today\|tomorrow\|YYYY-MM-DD]` | the logbook for a day: who is expected, arrived, with which host |
| `create-visit ...` | the main action, see below |
| `meeting M-ID`, `visitor V-ID` | raw objects for verification |
| `invite M-ID` | (re)send the invitation email for an existing visit |
| `move-visit M-ID --to V-ID [--invite]` | re-point a visit to another visitor card (duplicate clean-up); re-applies the visit fields |

Every command prints JSON; add `-v` before the command to see each HTTP call on stderr.

## Booking a visit

```bash
python3 scripts/proxyclick.py create-visit \
  --first Maria --last Angelica --email maria@example.com --company-name "Acme Foods" \
  --date tomorrow --start 10:00 --duration 90 \
  --host me \
  --field "parking=NO" --field "Meeting Location=Building 7" \   # names/values from `config`
  --invite
```

What happens, in order:

1. **Find or create the person.** With `--email` the script looks for a visitor with exactly that
   email and reuses it; without an email it reuses a visitor only when exactly one has the same
   first and last name. Otherwise the visitor is created inside the same call that books the visit
   (there is no separate "create visitor" endpoint; a visitor record is born with its first visit).
   Use `--visitor-id V-...` to pin a specific record, `--always-new` to force a new one.
2. **Resolve host, times and custom fields.** `--host` defaults to the logged-in account; it also
   accepts a `US-...` id, an employee email or a unique name. Times are in the company timezone;
   the default start is the next quarter hour today (09:00 on other days) and the default length is
   the company setting (`defaultVisitDuration`, often 2 h). Mandatory custom fields with a default are
   filled automatically; a mandatory field without a default must be given with `--field` or the
   script stops and tells you which one. LIST fields accept a unique prefix of an option
   (`parking=NO`), and field names can be a unique fragment (`parking`, `location`, `gate`).
3. **One `POST /vm/meetings`** creates the visit (and the visitor when needed), then a `PUT` stores
   the visit-level custom fields, then the person is added to the user's address book (needed:
   the company visitor list and the name search only contain address-book visitors, so without
   it the next booking would create a duplicate; `--no-address-book` opts out), then `--invite`
   triggers the invitation email. For several dates for one person, pass `--visitor-id` from the
   first result to the following calls.
4. The result JSON carries `meeting_id`, `visitor_id`, `visitor_created`, times, `invite_sent` and
   any warnings. Confirm with `visits <date>` and report the meeting id back to the user.

When the request is ambiguous or touches real people, run with `--dry-run` first: it prints the
exact plan and payload without sending anything.

## Things to get right

- **Emails are real side effects.** `--invite` sends the visitor a "Your visit to <Company>" email
  from notifications@proxyclick.com with a QR pass. Only pass it when the user wants the visitor
  notified. The host is notified by Proxyclick's own rules regardless.
- **The front desk sees entries immediately.** Reception checked in a test visitor minutes after it
  was created. Do not book test visits with real people's names or emails; for tests use a clearly
  fake name and a plus-address of the user's own mailbox (`you+test@example.com`).
- **Custom fields are per company.** `config` lists them with ids, options, defaults and which
  are mandatory. A typical setup: a mandatory LIST such as "parking pass yes/no" with no default
  (it must be asked, the script refuses to guess), a mandatory LIST such as "Meeting Location"
  with a default, and optional fields like an entrance gate or notes for the visitor and for
  reception. Field names in `--field` can be a unique fragment of the real name.
- **Search semantics.** The API's name search is a prefix match on "First Last"; the script falls
  back to a substring scan of all visitors, so "Chen" still finds "Tiffany Chen". Email matching
  is exact and case-insensitive. Prefer email for de-duplication; names repeat.
- **Dates.** Visits are keyed by day in the company timezone; a visit id looks like
  `2026-10-26/M-XXXXXXXXX/V-XXXXXXXXX` (day / meeting / visitor). `visits tomorrow` is the
  quickest way to show the user what is booked.
- **Permissions.** The account can only book for companies it belongs to and sees the private
  address book of that user; a visitor created by a colleague still shows up in `search` because
  it uses the company-wide list.
- **Duplicates and passes.** The emailed QR pass encodes `accessCode|M-id,V-id,CO-id|accessCode`. After
  `move-visit`, the old email no longer scans; pass `--invite` so the person gets a valid one.
- **Deleting.** The account may delete a visitor (`DELETE /api/app/{CO}/vm/visitors/{V}`), which
  also removes all of that visitor's visits, but not a single visit (403 `vm_visitor_delete`).
  Ask before deleting and name the visits that will disappear.
- **If an endpoint changes** (login stops returning `access_token`, a 4xx on the meetings call),
  read `references/api.md`, compare with the error body the script prints, and fix the script
  rather than working around it in the conversation.

## Reporting back

Tell the user: visitor name (new or existing), date and time window in local time, host, custom
field values that matter (parking, location), whether an invitation was emailed, and the meeting
id. If something was skipped (no email so no invite, a mandatory field you had to choose), say so.
