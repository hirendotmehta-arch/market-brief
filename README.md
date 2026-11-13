# Market News Brief: free setup (about 15 minutes)

The app runs on Streamlit Community Cloud (free hosting) and uses Google Gemini's
free API tier for the analysis. Nothing runs on your computer. You get a web link
that works on your phone and laptop.

## Step 1: Get a free Gemini API key
1. Open https://aistudio.google.com and sign in with your Google account.
2. Click **Get API key**, then **Create API key**.
3. Copy the key and keep it private.

## Step 2: Put the files on GitHub
1. Create a free account at https://github.com if you don't have one.
2. Click **New repository**. Name it `market-brief` and choose **Private**.
3. Click **uploading an existing file** and upload `app.py`, `requirements.txt` and `README.md`.
4. Add the theme file: **Add file → Create new file**, type `.streamlit/config.toml`
   as the name, paste the contents of that file, then click **Commit**.

## Step 3: Deploy on Streamlit Cloud
1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **Create app**, pick your `market-brief` repo, and set the main file to `app.py`.
3. Open **Advanced settings → Secrets** and paste the following:

```toml
GEMINI_API_KEY = "paste-your-key-here"
APP_PASSWORD = "choose-a-password"
```

4. Click **Deploy**. The first build takes 2–3 minutes.
5. Bookmark the link. On a phone, use **Add to Home Screen** so it opens like an app.

## Using it
- Press **Refresh** to fetch the latest news and filings and run a new analysis.
  Each refresh takes about a minute.
- Opening the link without pressing Refresh shows the last brief and uses no API quota.
- **Settings** lets you change the look-back window (3–24 hours) and how many items are analysed.

## Good to know
- **Free limits.** Gemini's free tier allows plenty of refreshes per day for one person.
  If you hit the limit, the app tells you to wait a minute.
- **Sleeping.** Streamlit puts free apps to sleep after several days without visits.
  Click "Yes, get this app back up" and it wakes in about 30 seconds.
- **NSE vs BSE.** NSE often blocks cloud servers, so NSE filings may show as "failed"
  under **Sources**. BSE filings cover nearly all the same companies.
- **Changing the model.** If Google renames its models and the app reports "model not
  found", add `GEMINI_MODEL = "<current flash model name>"` to Secrets.
- **Adding or removing news sources.** Edit `INDIA_FEEDS` and `WORLD_FEEDS` near the top of
  `app.py`. Any RSS link works.
- **Filing filter.** `SKIP_WORDS` and `KEEP_WORDS` in `app.py` decide which filings count as
  important.
- **Privacy.** On the free tier, Google may use prompts to improve its products. The app
  only sends public news and filings, so nothing private is shared.
