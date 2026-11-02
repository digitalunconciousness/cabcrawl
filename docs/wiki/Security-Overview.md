# Security overview

- **Sign-in**: passwords are hashed. Failed sign-ins are limited per address, and repeated
  failures lock the account for a while. The visitor's address comes from Cloudflare, so it can't
  be faked to dodge the limit. New and reset accounts get a one-time password that must be
  changed.
- **Roles**: every page and action checks the [role](Roles-and-Permissions) it needs, on the
  server. The menu only offers what a role can open, and tests check that the two agree.
- **Forms**: every form carries a CSRF token. A second tap on a button while the first is still
  sending is ignored.
- **Pages**: a strict content security policy allows only the site's own scripts. User text is
  escaped everywhere, including in PDFs and CSV exports, where a cell can't become a formula.
- **Uploads**: photos are re-encoded, so only real images are stored, with a size and pixel limit.
- **Work-order photos** are shown only to signed-in users. They used to be public files; the old
  addresses now ask for a login.
  An SVG logo is cleaned and served so it can't run a script.
- **Cookies**: Secure through the tunnel, HttpOnly, SameSite.
- **Machines**: each GATBOX device has its own token, can be limited to certain machines, and can
  be switched off at once.
- **The one public form**: the coin-door report takes no sign-in, so it accepts only a problem
  description and a name, for the machine its label belongs to, and is rate-limited.

The code has had two full security reviews, in October 2026. Each finding was fixed with a test
that fails without the fix; `docs/FEATURES.md` (F-43 and F-44) lists them.

To report a security problem, open a private security advisory on the repository rather than a
public issue.
