# The design system, and how to add a page

Cabcrawl's look comes from GATBOX, the bench tool it works beside: a dark purple ground, magenta
for the one thing to do, cyan for links, and green, amber and red only for status.
`docs/design/gatbox-style-notes.md` records where each choice came from. Every component, in
every state, is shown at `/styleguide` (admins). Look there before writing CSS.

## The pieces

| File | What's in it |
|---|---|
| `static/css/tokens.css` | Every colour, size, radius and font, as custom properties. Nothing else in the CSS uses a raw colour. |
| `static/css/base.css` | Fonts, the page, type, links, focus rings, form controls, reduced motion. |
| `static/css/components.css` | Every component (`.btn`, `.card`, `.row`, `.chip`, `.readout`, `.table`, `.field`...). |
| `static/js/ui.js` | All shared behaviour: menu, confirm dialogs, select-all, one submit per tap, print, scan focus. |
| `templates/layout.html` | The one base template: header, nav, banners, footer. |
| `templates/ui.html` | Macros that build components the same way every time. |

There's no build step and no framework. CSS and JavaScript are served as written, with a hash in
the URL for cache-busting (`asset()` in templates).

## Tokens

- **Colour**: the GATBOX palette (`--c-bg`, `--c-panel`, `--c-mag`, `--c-cyan`...) is mapped to
  roles, and the components use only the roles: `--surface`, `--surface-raised`, `--text`,
  `--text-dim`, `--link`, `--border`, `--focus`, `--accent`.
- **Status** colours are the only ones that carry meaning: `--ok`, `--warn`, `--fault`,
  `--info`, `--muted`, each with a `-tint` for backgrounds. A status is never shown by colour
  alone: there's always a word.
- **Type**: `--font-display` (Chakra Petch) for headings and buttons, `--font-body` (Share Tech
  Mono) for everything else. Sizes `--fs-xs` to `--fs-xl`. Both fonts are served from
  `static/fonts/`, with no third-party requests.
- **Space and size**: `--gutter`, `--content-max`, radii `--r-sm` to `--r-pill`, and `--tap`,
  the minimum height of anything you press (44 px). Pages are used on phones at the cabinet.
- **Glow** effects are rationed: the brand, the primary button and focus.

## Macros (`templates/ui.html`)

```jinja
{% import "ui.html" as ui %}

{{ ui.page_head("Parts", sub="12 in the catalogue", back=url_for("dashboard.home"), back_label="Dashboard") }}
{{ ui.readout("In stock", 10, detail="reorder at 2", tone="ok") }}
{{ ui.status(game.status) }}            {# a stored status, as a chip with the right tone #}
{{ ui.chip("warn", "3 machines") }}     {# a chip with an explicit tone #}
{{ ui.field(form.quantity, help="For a count, the number in that place.") }}
{{ ui.empty("No devices yet", "Make one on the server.") }}
{{ ui.danger_form(url_for("places.delete", place_id=p.id), "Delete place", "Delete this place?") }}
```

Also: `error_state` (a failed load, with retry), `machine_down_choice` (the "can it still be
played?" radios) and `per_unit` (price per unit wording).

## Layout classes

- `.stack` puts space between its children, top to bottom; `.cluster` lays them out in a
  wrapping row.
- `.grid`, `.grid--pair`, `.tiles` and `.dash__grid` are for grids.
- `.card` is a panel. `.rows` is a list of `.row` links, each with `.row__main` (`.row__title`,
  `.row__meta`) and `.row__end` for chips and numbers.
- `.measure` limits a paragraph's line length; `.phone-width` limits a form to phone width on
  a desk too.
- `.num` sets digits as tabular figures, so columns of numbers line up.

## Rules

- **No inline scripts, styles or event handlers.** The content security policy refuses them.
  Behaviour hooks onto `data-` attributes: `data-confirm="Question?"` on a form opens the
  confirm dialog, `data-select-all` ticks a group, `data-print` opens the print dialog.
- **Phone first.** Check every page at 390 px wide. Tables that can't fit go in `.table-wrap` or
  become `.rows`.
- **One primary action per page** (`.btn--primary`). Destructive actions use `danger_form`,
  which asks first.
- **Words over icons**, and plain words: "Take, add, count or move", not "Adjust inventory".
- **Forms** use Flask-WTF with the CSRF token, `ui.field` for each input, and errors shown next
  to the field. A failed form is redrawn with a 400, not a 500.

## Adding a page

1. **The route**, in the right blueprint in `app/routes/`, guarded by `@login_required` and
   `@requires_role(...)`. Keep it thin: the work goes in `app/services/`.
2. **The template**: extend `layout.html`, set `{% block title %}`, put `ui.page_head(...)` in
   `{% block page_head %}`, and the page in `{% block content %}`. `templates/devices.html` is a
   short example.
3. **The nav**: if the page belongs in the menu, add it in `layout.html` for exactly the roles
   the route allows. A role must never be offered a page it can't open.
4. **Tests**:
   - add the page to `PAGES` in `tests/characterization/test_routes_get.py`, and its form to
     `POSTS` in `test_routes_post.py` if it has one;
   - regenerate the visibility snapshot (`UPDATE_SNAPSHOTS=1`) and check the diff;
   - add a test of what the page does.
5. **Check it in a browser**: `tests/screenshots/take.py --only <endpoint>` for 390 and 1280,
   and `tests/screenshots/csp_check.py` for script errors and policy violations. A machine page
   must also not scroll sideways at 390 or 360: `tests/screenshots/overflow_check.py --library`.
6. **If the component is new**, add it to `/styleguide` (`templates/styleguide.html`) in every
   state.
