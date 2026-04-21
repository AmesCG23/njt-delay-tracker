# Next Steps: Getting the Website Live

The `docs/index.html` page is fully designed and coded. Below is everything
that still needs to be done, in the order you should do it.

---

## 1. Fill in the Google Sheet ID (required — page shows $— without this)

In `docs/index.html`, find this line near the bottom of the file:

```js
const SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID_HERE';
```

Replace `YOUR_SPREADSHEET_ID_HERE` with the long ID string from your
Google Sheet URL. It's the part between `/d/` and `/edit`:

```
https://docs.google.com/spreadsheets/d/  THIS_PART_HERE  /edit
```

---

## 2. Create and populate the `for_web` tab in Google Sheets (required)

The page reads two numbers from a tab called `for_web`. This tab does not
exist yet and must be created manually:

1. Open your Google Sheet
2. Add a new tab, name it exactly: `for_web`
3. Put yesterday's total cost (a plain number, e.g. `742040`) in cell **A1**
4. Put the running cumulative total since launch (also a plain number) in cell **A2**
5. These values should be updated automatically by the daily pipeline
   (see note below), or you can enter them manually at first

**Publish the tab to the web (required for the page to read it):**
1. File → Share → Publish to web
2. Choose the `for_web` tab
3. Choose CSV format
4. Click Publish

Without this step, the browser cannot fetch the data and will show `$—`.

**Pipeline automation:** The `for_web` tab is currently updated manually.
To automate it, `logger.py` would need a new function that writes to A1 and A2
at the end of each daily run. This is a future code task.

---

## 3. Add your email address (optional but recommended)

In `docs/index.html`, find:

```html
<a href="mailto:YOUR_EMAIL_HERE" ...>
```

Replace `YOUR_EMAIL_HERE` with a real contact email address.
If you prefer not to show an email, delete the entire `<a>` block for the email icon.

---

## 4. Confirm the Bluesky handle (verify, don't change unless wrong)

The page already links to `njtdelaycost.bsky.social`. Confirm this is the
correct handle for the bot account. If it's different, update this line:

```html
<a href="https://bsky.app/profile/njtdelaycost.bsky.social" ...>
```

---

## 5. Add a logo (optional)

The header has a gray placeholder box labeled "LOGO". To add a real logo:

1. Add your image file to the `docs/` folder (e.g. `docs/logo.png`)
2. In `index.html`, replace this block:
   ```html
   <div class="logo">
     <div class="logo-placeholder">LOGO</div>
   </div>
   ```
   With:
   ```html
   <div class="logo">
     <img src="logo.png" alt="NJ Transit Delay Cost Tracker">
   </div>
   ```

If you don't have a logo yet, the placeholder is invisible to most users
and can be left as-is.

---

## 6. Enable GitHub Pages (required — nothing is live until this is done)

1. Go to your GitHub repo → **Settings** → **Pages**
2. Under "Build and deployment", set:
   - **Source:** Deploy from a branch
   - **Branch:** `main`
   - **Folder:** `/docs`
3. Click **Save**
4. GitHub will give you a URL like `https://amescg23.github.io/njt-delay-tracker/`
   — the site will be live there within a few minutes

---

## 7. Custom domain (do this after you buy one)

Once you have a domain name:

1. Edit `docs/CNAME` — replace `your-domain-here.com` with your actual domain
   (e.g. `residualdelays.com` or whatever you choose)
2. In your domain registrar (GoDaddy), add these DNS records:

   | Type | Name | Value |
   |------|------|-------|
   | A | @ | 185.199.108.153 |
   | A | @ | 185.199.109.153 |
   | A | @ | 185.199.110.153 |
   | A | @ | 185.199.111.153 |
   | CNAME | www | amescg23.github.io |

3. In GitHub Pages settings, enter your custom domain and check
   "Enforce HTTPS" (available after DNS propagates, usually within an hour)

---

## Summary checklist

- [ ] Fill in `SPREADSHEET_ID` in `docs/index.html`
- [ ] Create `for_web` tab in Google Sheet with A1 and A2 values
- [ ] Publish `for_web` tab to the web (CSV format)
- [ ] Add contact email to `docs/index.html`
- [ ] Confirm Bluesky handle is correct
- [ ] Enable GitHub Pages (Settings → Pages → `/docs` on `main`)
- [ ] *(Optional)* Add logo to `docs/`
- [ ] *(Later)* Buy domain, update `docs/CNAME`, add DNS records
- [ ] *(Later)* Automate `for_web` tab writes in `logger.py`
