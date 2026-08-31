# Putting the scheduler online

The app stores real accounts and saved schedules in a SQLite file
(`scheduler.db`). **That file has to survive restarts**, or everyone's login and
saved schedules disappear. That single requirement decides which host to use.

## Which host

| | PythonAnywhere (free) | Render (free) |
|---|---|---|
| Keeps `scheduler.db` | **Yes** | **No** - wiped on every restart/deploy |
| Sleeps when idle | No | Yes, ~50s to wake |
| Deploys from GitHub | Manual pull | Automatic on push |
| URL | `you.pythonanywhere.com` | `your-app.onrender.com` |
| HTTPS | Yes | Yes |

**Use PythonAnywhere** if Mary and others will actually keep accounts and saved
schedules. Use Render only for a demo, or on a paid plan with a disk attached.

---

## Option A: PythonAnywhere (recommended)

1. Sign up for a free "Beginner" account at
   [pythonanywhere.com](https://www.pythonanywhere.com/registration/register/beginner/).

2. Open a **Bash console** (Consoles tab) and clone the repo:

   ```bash
   git clone https://github.com/connorswartz/class-schedule-generator.git
   ```

   For a private repo it will ask for your username and a
   [personal access token](https://github.com/settings/tokens) as the password.

3. Create the environment:

   ```bash
   cd class-schedule-generator && python3.10 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

4. Go to the **Web** tab, **Add a new web app**, choose **Manual configuration**
   and **Python 3.10**.

5. In **Virtualenv**, enter:

   ```
   /home/YOURNAME/class-schedule-generator/.venv
   ```

6. Click the **WSGI configuration file** link and replace its contents with:

   ```python
   import os, sys

   path = '/home/YOURNAME/class-schedule-generator'
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ['FLASK_SECRET_KEY'] = 'PASTE_A_LONG_RANDOM_STRING_HERE'

   from app import app as application
   ```

   Generate the secret with `python3 -c "import secrets; print(secrets.token_hex(32))"`.

7. Press **Reload**. The app is live at `https://YOURNAME.pythonanywhere.com`.

To update later: `git pull` in the console, then **Reload** on the Web tab.

---

## Option B: Render

1. Sign up at [render.com](https://render.com) and connect your GitHub account.
2. **New +** > **Blueprint**, pick this repo. `render.yaml` supplies the settings
   and generates `FLASK_SECRET_KEY` automatically.
3. Deploy.

Accounts and saved schedules reset on every restart until you move to a paid
plan and uncomment the `disk:` block in `render.yaml`.

---

## Before you share the link

- [ ] `FLASK_SECRET_KEY` is set to a long random value, and is **not** in the repo.
- [ ] `FLASK_DEBUG` is unset. The debugger can run arbitrary code on the server.
- [ ] The URL is `https://`, not `http://` - passwords are posted in form fields.
- [ ] `scheduler.db` is on storage that survives a restart.
- [ ] You have a copy of `scheduler.db` somewhere safe; there is no backup built in.

## Known limits

- **No password reset.** A forgotten password means the account is unreachable;
  you would have to clear it from the database by hand.
- **No rate limiting** on the sign-in form, so it is open to password guessing.
  Worth adding before sharing the link widely.
- **SQLite handles light traffic well.** A class or a staff room is fine; hundreds
  of simultaneous users would need a real database.
- **Your local `scheduler.db` does not travel with the deploy** - it is
  gitignored on purpose. Accounts on the hosted copy start empty.
